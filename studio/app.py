"""Same-origin FastAPI server. Run: uvicorn studio.app:app --host 127.0.0.1 --port 8890 --workers 1."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import uuid
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .creative import INSTRUCTIONS, STYLES
from .engine import JobEngine, asset_id, asset_url, atomic_json, safe_asset, utc_now
from .prompts import compile_prompt
from .regions import validate_rectangles

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000  # Match studio.vision's bounded CPU image processing.
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}

# Fixed regions apply only to these exact sample images. The workbench clears
# them when the source image is replaced and lets the user edit or remove them.
PROTECTED_PRESETS = {
    "scu_gate_mug": {
        "edit_regions": [[.28, .48, .70, .90]],
        "protect_regions": [],
        "seed": 123,
        "region_detail": True,
    },
    "scu_gate_tumbler": {
        "edit_regions": [[.20, .39, .78, .91]],
        "protect_regions": [[0, 0, 1, .40]],
        "seed": 123,
    },
    "cat_tumbler": {
        "edit_regions": [[.20, .39, .78, .91]],
        "protect_regions": [[0, 0, 1, .40]],
        "seed": 123,
    },
    "real_cat_mug": {
        "edit_regions": [[.28, .48, .70, .90]],
        "protect_regions": [],
        "seed": 123,
    },
}


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_a: str = Field(min_length=1, max_length=1024)
    image_b: str = Field(min_length=1, max_length=1024)
    recipe: Literal["ceramic", "sneaker", "plush", "crystal", "architecture", "meme", "custom"] = "ceramic"
    style: str = "original"
    mode: Literal["structured", "baseline"] = "structured"
    prompt: str = Field(default="", max_length=6000)
    isolate_subject: StrictBool = False
    lock_background: StrictBool = False
    edit_mode: Literal["global", "region"] = "global"
    region_detail: StrictBool = False
    edit_regions: list[list[float]] = Field(default_factory=list, max_length=12)
    protect_regions: list[list[float]] = Field(default_factory=list, max_length=12)
    candidates: int = Field(default=1, ge=1, le=3, strict=True)
    seed: int = Field(default=42, ge=0, le=0xFFFFFFFFFFFFFFFF, strict=True)
    megapixels: Literal[0.5, 1.0] = 0.5

    @field_validator("style")
    @classmethod
    def validate_style(cls, value):
        if value not in STYLES:
            raise ValueError("未知画风。")
        return value

    @field_validator("edit_regions", "protect_regions", mode="before")
    @classmethod
    def validate_regions(cls, value):
        return validate_rectangles(value)

    @model_validator(mode="after")
    def require_edit_region(self):
        if self.edit_mode == "region" and not self.edit_regions:
            raise ValueError("区域编辑需要至少一个有效修改区域。")
        if self.region_detail and self.edit_mode != "region":
            raise ValueError("局部高清需要先限定修改区域。")
        return self

    @field_validator("megapixels", mode="before")
    @classmethod
    def reject_boolean_resolution(cls, value):
        if isinstance(value, bool):
            raise ValueError("megapixels must be 0.5 or 1.0")
        return value


class MemePreset(JobRequest):
    id: str = Field(pattern=r"^meme_[a-z0-9_]+$", max_length=64)
    title: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(default="", max_length=120)
    recipe: Literal["meme"] = "meme"


class UploadTooLarge(Exception):
    pass


class UploadBodyLimit:
    """Bound multipart bytes before Starlette can spool an unlimited request."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/uploads":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid content length"}, status_code=400)(scope, receive, send)
        if declared_size > self.limit:
            return await JSONResponse({"detail": "图片不能超过 20 MB。"}, status_code=413)(scope, receive, send)
        received, response_started = 0, False

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise UploadTooLarge()
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except UploadTooLarge:
            if response_started:
                raise
            await JSONResponse({"detail": "图片不能超过 20 MB。"}, status_code=413)(scope, receive, send)


def inspect_image(path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format not in IMAGE_FORMATS or getattr(image, "n_frames", 1) != 1:
                raise ValueError("只支持静态 PNG、JPEG、WebP 图片。")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素过多，请缩小后再试。")
            image.verify()


def store_upload(root, content, filename):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in IMAGE_FORMATS or getattr(original, "n_frames", 1) != 1:
                raise ValueError("只支持静态 PNG、JPEG、WebP 图片。")
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素过多，请缩小后再试。")
            original.load()
            oriented = ImageOps.exif_transpose(original)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                normalized = Image.new("RGB", rgba.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = oriented.convert("RGB")
            normalized.info.clear()
            path = root / "data" / "uploads" / f"{uuid.uuid4().hex}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".png.tmp")
            normalized.save(temporary, format="PNG")
            temporary.replace(path)
            atomic_json(path.with_suffix(".json"), {
                "original_filename": Path(filename or "upload").name,
                "source_sha256": hashlib.sha256(content).hexdigest(), "source_bytes": len(content),
                "normalized_format": "RGB PNG", "exif_transposed": True,
                "width": normalized.width, "height": normalized.height, "created_at": utc_now(),
            })
            return {"id": asset_id(root, path), "url": asset_url(root, path), "width": normalized.width, "height": normalized.height}


def meme_presets(root):
    manifest = root / "input" / "meme_presets.json"
    if not manifest.is_file():
        return
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(entries, list):
        return
    source = root / "input" / "meme_sources.json"
    for entry in entries:
        try:
            preset = MemePreset.model_validate(entry)
            paths = [safe_asset(root, name) for name in (preset.image_a, preset.image_b)]
            if any(
                not path.is_relative_to(root / "input") or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
                for path in paths
            ):
                continue
        except ValueError:
            continue
        item = preset.model_dump()
        item.update(
            category="meme",
            image_a={"id": asset_id(root, paths[0]), "url": asset_url(root, paths[0])},
            image_b={"id": asset_id(root, paths[1]), "url": asset_url(root, paths[1])},
            preview_kind="previous_inference",
            preview_url=None,
        )
        if source.is_file():
            item["reference_source_url"] = asset_url(root, source)
        preview = root / "output" / "quality_presets" / f"{preset.id}.jpg"
        if preview.is_file():
            item["preview_url"] = asset_url(root, preview)
            item["preview_prompt"] = compile_prompt(
                preset.recipe, preset.mode, preset.prompt, preset.lock_background, style=preset.style,
            )
        yield item


def presets(root):
    definitions = [
        ("scu_gate_mug", "望江红门马克杯", "川大校园文创概念", "plain_mug.jpg", "scu_wangjiang.png", "architecture", "在白釉杯身正面中央做四川大学望江北门的浅浮雕。中间高、两边低的三座青灰飞檐由四根朱红方柱支撑，三处门洞留白，建筑造型与白釉自然融合。保留原杯口、茶水、右侧单把手和木桌。", "scu_gate_mug"),
        ("scu_gate_tumbler", "望江红门随行杯", "川大校园文创概念", "iced_drink_cup.png", "scu_wangjiang.png", "architecture", "用奶白釉杯身承托一座四川大学望江北门的浮雕。中间高、两边低的三座青灰瓦飞檐和四根朱红方柱构成唯一的主图案，放在杯身中央，保留清楚的屋檐轮廓和柱间留白。保留原透明杯盖和吸管，杯身无把手。", "scu_gate_tumbler"),
        ("cat_tumbler", "猫咪随行杯", "让角色细节成为杯子的造型", "iced_drink_cup.png", "comfy_cat.png", "ceramic", "保留原杯的盖子与吸管，用参考角色的藏蓝、奶白、橙色和眼镜细节做一个可用的杯子。只在正面有一张脸。", "cat_tumbler"),
        ("cat_sneaker", "猫咪运动鞋", "把性格织进鞋面", "sneaker_black.png", "comfy_cat.png", "sneaker", "保留鞋底、鞋带和穿着结构，把角色配色与眼镜细节融入鞋面。", "cat_sneaker"),
        ("real_cat_mug", "宠物纪念马克杯", "保留它独特的花纹", "plain_mug.jpg", "real_cat.png", "ceramic", "保持白色马克杯的敞口、右侧把手和木桌。只在正面做一张浅浮雕猫脸，两个绿眼睛、一个粉鼻子；黑白釉色匹配参考猫，侧面和背面纯白。不要盖子和吸管。", "real_cat_mug_refined"),
        ("plush_sneaker", "毛绒鞋精灵", "从运动鞋变成柔软收藏品", "sneaker_black.png", "comfy_cat.png", "plush", "保留运动鞋的整体轮廓与鞋带线索，以参考角色的配色和可见特征做成柔软、精致的收藏毛绒玩具。", "plush_sneaker"),
        ("crystal_tumbler", "水晶角色杯", "透明材质与角色配色相遇", "iced_drink_cup.png", "comfy_cat.png", "crystal", "保留原杯的高挑轮廓、杯盖和吸管，角色特征融入透明水晶杯的雕刻和色彩内含物中。", "crystal_tumbler"),
    ]
    result = []
    for preset_id, title, subtitle, a_name, b_name, recipe, prompt, preview_dir in definitions:
        a, b = root / "input" / a_name, root / "input" / b_name
        if not a.is_file() or not b.is_file():
            continue
        previews = sorted((root / "output" / preview_dir).glob("*/node_*.png"))
        item = {"id": preset_id, "title": title, "subtitle": subtitle, "image_a": {"id": asset_id(root, a), "url": asset_url(root, a)}, "image_b": {"id": asset_id(root, b), "url": asset_url(root, b)}, "recipe": recipe, "prompt": prompt, "preview_url": asset_url(root, previews[-1]) if previews else None, "preview_kind": "previous_inference"}
        prompt_file = root / "workflows" / f"{preview_dir}_api.json"
        if prompt_file.is_file():
            source = json.loads(prompt_file.read_text(encoding="utf-8"))
            item["preview_prompt"] = source.get("109", {}).get("inputs", {}).get("text")
        if preset_id in PROTECTED_PRESETS:
            item.update(PROTECTED_PRESETS[preset_id])
            preview = root / "output" / "quality_presets" / f"{preset_id}.jpg"
            if preview.is_file():
                item["preview_url"] = asset_url(root, preview)
                item["preview_prompt"] = compile_prompt(recipe, "structured", prompt)
        item["style"] = "original"
        item["category"] = "campus" if preset_id.startswith("scu_gate_") else "general"
        if item["category"] == "campus":
            source = root / "input" / "scu_sources.json"
            if source.is_file():
                item["reference_source_url"] = asset_url(root, source)
        result.append(item)
    result.extend(meme_presets(root))
    return result


def create_app(project_root=None, *, inference_runner=None, vision_module=None, backend_probe=None):
    root = Path(project_root or os.environ.get("ALCHEMY_PROJECT_ROOT", PROJECT_ROOT)).resolve()
    server = os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188")
    engine = JobEngine(root, server, runner=inference_runner, vision=vision_module, probe=backend_probe)

    @asynccontextmanager
    async def lifespan(app):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="Alchemy Studio", lifespan=lifespan)
    app.state.engine = engine
    app.add_middleware(UploadBodyLimit, limit=MAX_UPLOAD_BYTES + 128 * 1024)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Malformed JSON can contain NaN/Infinity; keep its rejection a valid 422
        # response instead of failing while serializing the validation details.
        def safe_detail(value):
            if isinstance(value, dict):
                return {key: safe_detail(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [safe_detail(item) for item in value]
            if isinstance(value, float) and not math.isfinite(value):
                return str(value)
            if isinstance(value, Exception):
                return str(value)
            return value
        return JSONResponse({"detail": safe_detail(exc.errors())}, status_code=422)

    def find_job(job_id):
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在。")
        return job

    @app.get("/api/status")
    async def status():
        backend = await engine.backend_status()
        return {**backend, "segmentation_available": engine.segmentation_available(), "active_jobs": sum(job["status"] in {"queued", "running"} for job in engine.jobs.values())}

    @app.get("/api/presets")
    async def get_presets():
        return presets(root)

    @app.get("/api/creative-options")
    async def get_creative_options():
        styles = []
        for key, style in STYLES.items():
            item = {"id": key, "name": style.name, "group": style.group}
            preview = root / "output" / "style_previews" / f"{key}.jpg"
            if preview.is_file():
                item["preview_url"] = asset_url(root, preview)
            styles.append(item)
        return {"styles": styles, "instructions": [instruction._asdict() for instruction in INSTRUCTIONS]}

    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        content = bytearray()
        try:
            while chunk := await file.read(1024 * 1024):
                content.extend(chunk)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "图片不能超过 20 MB。")
            if not content:
                raise HTTPException(400, "上传文件为空。")
            try:
                return await asyncio.to_thread(store_upload, root, bytes(content), file.filename)
            except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise HTTPException(400, f"无法读取图片：{exc}") from exc
        finally:
            await file.close()

    @app.post("/api/jobs", status_code=202)
    async def submit(config: JobRequest):
        values = config.model_dump()
        try:
            for field in ("image_a", "image_b"):
                path = safe_asset(root, values[field])
                await asyncio.to_thread(inspect_image, path)
        except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(400, f"输入图片无效：{exc}") from exc
        requires_segmentation = values["isolate_subject"] or (values["edit_mode"] == "global" and values["lock_background"])
        if requires_segmentation and not engine.segmentation_available():
            raise HTTPException(503, "本地分割模型尚未就绪，请关闭主体隔离和背景锁定，或先安装分割模型。")
        if not (await engine.backend_status())["comfy_connected"]:
            raise HTTPException(503, "ComfyUI 尚未连接，请先启动推理服务。")
        job = engine.create(values)
        return {"id": job["id"]}

    @app.get("/api/jobs")
    async def jobs(compact: bool = Query(False)):
        ordered = sorted(engine.jobs.values(), key=lambda job: job["created_at"], reverse=True)[:30]
        if compact:
            return [
                {
                    "id": job["id"], "created_at": job["created_at"], "status": job["status"],
                    "error": job.get("error"), "best_index": job.get("best_index", 0),
                    "config": {"recipe": job["config"]["recipe"]},
                    "candidates": [{"url": candidate["url"]} for candidate in job.get("candidates", [])],
                }
                for job in ordered
            ]
        return [engine.snapshot(job) for job in ordered]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        return engine.snapshot(find_job(job_id))

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        return engine.cancel(find_job(job_id))

    @app.get("/api/jobs/{job_id}/export")
    async def export(job_id: str):
        return JSONResponse(engine.snapshot(find_job(job_id)), headers={"Content-Disposition": f'attachment; filename="alchemy-{job_id}.json"'})

    @app.get("/api/files/{path:path}")
    async def get_file(path: str, download: bool = Query(False)):
        try:
            resolved = safe_asset(root, path)
        except ValueError as exc:
            raise HTTPException(404, "文件不存在或路径无效。") from exc
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".json": "application/json", ".log": "text/plain", ".txt": "text/plain"}.get(resolved.suffix.lower(), "application/octet-stream")
        return FileResponse(resolved, media_type=media, filename=resolved.name if download else None, headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Unknown API endpoint")
        dist = (root / "web" / "dist").resolve()
        requested = (dist / path).resolve()
        if not requested.is_relative_to(dist):
            raise HTTPException(404)
        if requested.is_file():
            return FileResponse(requested)
        if Path(path).suffix:
            raise HTTPException(404)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse("<h1>Alchemy Studio</h1><p>Frontend build is missing. Build web/dist first.</p>", status_code=503)

    return app


app = create_app()
