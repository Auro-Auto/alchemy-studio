"""Prompt enhancement using the bundled FLUX.2 Qwen3-4B text encoder (streaming & batch)."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
import json
import os
from pathlib import Path
import re
import sys

import anyio
import httpx

from .creative import STYLES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMFY_ROOT = PROJECT_ROOT / "backend" / "ComfyUI"
QWEN_ENCODER_PATH = COMFY_ROOT / "models" / "text_encoders" / "qwen_3_4b_fp4_flux2.safetensors"
DEFAULT_MODEL_NAME = "qwen_3_4b_fp4_flux2"
_ENHANCE_LOCK = asyncio.Lock()
# Conservative process-handoff gates, not measured model memory requirements.
# The physical-memory check also catches dynamic allocations outside PyTorch.
MAX_COMFY_TORCH_CACHE_BYTES = 256 * 1024**2
MIN_ENHANCE_FREE_VRAM_RATIO = .75

RECIPE_LABELS = {
    "ceramic": "陶瓷器皿融合",
    "sneaker": "球鞋改造",
    "plush": "毛绒公仔",
    "crystal": "水晶玻璃材质",
    "architecture": "建筑浮雕文创",
    "meme": "创意梗图",
    "custom": "自由双图融合",
}

_WORKER_SCRIPT = r"""
import gc, json, re, sys
from pathlib import Path

def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def clean_text(text):
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.replace("<|im_end|>", "").replace("<|endoftext|>", "")
    return cleaned.strip()

payload = json.loads(sys.stdin.read())
comfy_root = Path(payload["comfy_root"])
sys.path.insert(0, str(comfy_root))

emit({"type": "stage", "stage": "loading", "message": "准备增强…"})

import comfy.sd
import torch

clip = comfy.sd.load_clip(
    ckpt_paths=[payload["model_path"]],
    embedding_directory=str(comfy_root / "models" / "embeddings"),
    clip_type=comfy.sd.CLIPType.FLUX2,
)

emit({"type": "stage", "stage": "generating", "message": "正在增强…"})

stream_ids = []
last_emitted = ""
clip_name = getattr(clip.cond_stage_model, "clip", "qwen3_4b")
clip_model = getattr(clip.cond_stage_model, clip_name, None)
transformer = getattr(clip_model, "transformer", None)
if transformer is not None and hasattr(transformer, "sample_token"):
    orig_sample = transformer.sample_token
    def streaming_sample(*args, **kwargs):
        global last_emitted
        next_token = orig_sample(*args, **kwargs)
        token_id = int(next_token.view(-1)[0].item())
        stream_ids.append(token_id)
        decoded = clean_text(clip.tokenizer.decode(stream_ids))
        if decoded and decoded != last_emitted and not decoded.endswith("\ufffd"):
            last_emitted = decoded
            emit({"type": "delta", "text": decoded})
        return next_token
    transformer.sample_token = streaming_sample

tokens = clip.tokenize(payload["chat_prompt"], llama_template="{}")
out_ids = clip.generate(
    tokens,
    do_sample=True,
    max_length=int(payload.get("max_length", 300)),
    temperature=0.55,
    top_p=0.85,
    repetition_penalty=1.1,
    seed=int(payload.get("seed", 42)),
)
final_text = clean_text(clip.tokenizer.decode(out_ids))
del clip
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()
emit({"type": "done", "text": final_text})
"""


class PromptEnhanceError(RuntimeError):
    """Raised when the local prompt enhancer is unavailable or fails."""


def _clean_response(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.replace("<|im_end|>", "").replace("<|endoftext|>", "").strip()
    if len(cleaned) >= 2 and (cleaned[0], cleaned[-1]) in {('"', '"'), ("'", "'"), ("“", "”")}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _build_chat_prompt(prompt: str, *, recipe: str, style: str, edit_mode: str) -> str:
    recipe_label = RECIPE_LABELS.get(recipe, recipe)
    style_name = STYLES[style].name if style in STYLES else style
    scope_note = "局部区域修改（选区属于图A；仅改选区，保留功能结构与背景）" if edit_mode == "region" else "整图双参考编辑"
    system = (
        "你是图像编辑工作台的文字指令整理助手。"
        "你收到的是用户文字、用途和画风，请依据这些信息改写。"
        "原词优先于任务类别和示例，逐项保留原词的操作、保留项和禁止项。"
        "原词中的情绪、动作、数量、颜色和相对位置都要明确保留，不能换成近似含义。"
        "按原词确定A/B角色；只有原词要求时才使用图B，单图清理保持单图操作。"
        "局部选区属于图A，不要把图A的修改对象写成图B。"
        "不扩大或缩小修改范围，不自行限定上半身等身体部位。"
        "未指定的材质、颜色和光照变化保持原样，不添加湿痕、光晕或新物品。"
        "不要虚构未提供的图像细节，也不要复制示例里的杯子、猫等内容到其他任务。"
        "场景和梗图描述人物与物品的互动、摆放及表情反差；只有原词要求时才将它们连成一体。"
        "严格按三段输出，禁用空洞套话：\n"
        "【融合目标】明确原词中的操作和对象，融合、换装、清理都可，不强制物理融合。\n"
        "【材质与光影】描述原词已有的材质及所选画风；未要求改变的部分沿用原图。\n"
        "【保留与约束】完整列出用户的保留项和禁止项。"
    )
    example_user = "任务：陶瓷；画风：原样；局部修改\n原词：把猫的脸做到白瓷杯子上，保留杯口、茶水、右侧把手和桌面背景，不加文字"
    example_assistant = (
        "【融合目标】将图B猫咪五官与花纹以浅浮雕融入图A白瓷杯身正面，仅保留单张正脸。\n"
        "【材质与光影】杯身呈温润白釉陶瓷质感，五官带细腻釉下彩与柔和陶瓷高光。\n"
        "【保留与约束】严格保留图A杯口、茶水、右侧把手及桌面背景，无多余文字。"
    )
    cleanup_user = "任务：建筑浮雕文创；画风：原样；图A局部修改\n原词：只去掉牌匾的错字，用附近灰色釉面填平，保留边框和灯光，不添加东西。"
    cleanup_assistant = (
        "【融合目标】只清除图A选中牌匾内的错字，以附近灰色釉面自然补齐。\n"
        "【材质与光影】沿用原图灰色釉面的颜色、纹理和灯光。\n"
        "【保留与约束】保留牌匾边框、原有光照和选区外内容，不添加新物品或装饰。"
    )
    user = f"任务：{recipe_label}；画风：{style_name}；{scope_note}\n原词：{prompt.strip()}"
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{example_user}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n{example_assistant}<|im_end|>\n"
        f"<|im_start|>user\n{cleanup_user}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n{cleanup_assistant}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


async def _drain_stderr(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    pending = ""
    while True:
        raw = await stream.read(4096)
        if not raw:
            if pending.strip():
                tail.append(pending.strip())
            return
        pending += raw.decode("utf-8", errors="replace")
        lines = pending.split("\n")
        pending = lines.pop()[-16384:]
        tail.extend(line[-16384:].strip() for line in lines if line.strip())


async def release_comfy_models(server: str, *, timeout: float = 30.0) -> None:
    """Unload the dedicated Comfy server's caches before the child uses CUDA.

    Comfy's /free response only acknowledges flags. Wait for allocator caches
    and physical free-memory headroom before loading another process. The caller
    holds JobEngine.gpu_lock throughout this handoff and enhancement cleanup.
    """
    base = server.rstrip("/")
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        async with httpx.AsyncClient(timeout=min(timeout, 10.0)) as client:
            async def require_idle_queue():
                response = await client.get(base + "/queue")
                response.raise_for_status()
                queue = response.json()
                if not isinstance(queue, dict) or not isinstance(queue.get("queue_running"), list) or not isinstance(queue.get("queue_pending"), list):
                    raise PromptEnhanceError("ComfyUI 返回了无效的队列状态，未启动提示词增强。")
                if queue["queue_running"] or queue["queue_pending"]:
                    raise PromptEnhanceError("ComfyUI 仍有运行中或排队的任务，请完成后再增强提示词。")

            async def cache_state():
                response = await client.get(base + "/system_stats")
                response.raise_for_status()
                info = response.json()
                if not isinstance(info, dict) or not isinstance(info.get("system"), dict):
                    raise PromptEnhanceError("ComfyUI 返回了无效的服务状态。")
                if info["system"].get("comfyui_version") != "0.44.0":
                    raise PromptEnhanceError("提示词增强需要项目固定的 ComfyUI 0.44.0。")
                entries = info.get("devices")
                if not isinstance(entries, list) or any(not isinstance(device, dict) for device in entries):
                    raise PromptEnhanceError("ComfyUI 返回了无效的设备状态。")
                devices = [device for device in entries if device.get("type") == "cuda"]
                fields = ("torch_vram_total", "torch_vram_free", "vram_total", "vram_free")
                if not devices or any(
                    not isinstance(device.get(field), (int, float))
                    for device in devices for field in fields
                ):
                    raise PromptEnhanceError("ComfyUI 未返回可核验的 CUDA 显存状态。")
                return devices

            await require_idle_queue()
            await cache_state()
            response = await client.post(base + "/free", json={"unload_models": True, "free_memory": True})
            response.raise_for_status()
            while asyncio.get_running_loop().time() < deadline:
                await require_idle_queue()
                devices = await cache_state()
                # Dynamic VRAM can live outside PyTorch's allocator. Subtract
                # its reusable cache from Comfy's reported free value to recover
                # driver-visible free memory, then reserve room for the child.
                if all(
                    0 <= device["torch_vram_total"] <= MAX_COMFY_TORCH_CACHE_BYTES
                    and device["vram_total"] > 0
                    and device["vram_free"] - device["torch_vram_free"]
                    >= MIN_ENHANCE_FREE_VRAM_RATIO * device["vram_total"]
                    for device in devices
                ):
                    return
                await asyncio.sleep(.2)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise PromptEnhanceError("显存释放失败，请检查服务连接后重试。") from exc
    raise PromptEnhanceError(
        "显存尚未释放，请稍后重试或关闭占用显存的程序。"
    )


async def enhance_prompt_stream(
    prompt: str,
    *,
    recipe: str = "custom",
    style: str = "original",
    edit_mode: str = "global",
    idle_timeout: float = 45.0,
    max_idle_warnings: int = 3,
) -> AsyncIterator[dict[str, str]]:
    """Stream stage and incremental text events while enhancing a prompt.

    Serializes GPU access via _ENHANCE_LOCK and resets the idle timer whenever
    a stage or token arrives so active generation is never interrupted.
    """
    text = (prompt or "").strip()
    if not text:
        raise ValueError("请先输入需要增强的提示词。")

    model_path = Path(os.environ.get("ALCHEMY_ENHANCE_MODEL_PATH", QWEN_ENCODER_PATH))
    if not COMFY_ROOT.is_dir() or not model_path.is_file():
        raise PromptEnhanceError("增强模型未安装，请完成模型安装后重试。")

    payload = {
        "comfy_root": str(COMFY_ROOT),
        "model_path": str(model_path),
        "chat_prompt": _build_chat_prompt(text, recipe=recipe, style=style, edit_mode=edit_mode),
        "max_length": 300,
        "seed": 42,
    }
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    async with _ENHANCE_LOCK:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            _WORKER_SCRIPT,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stderr_tail: deque[str] = deque(maxlen=40)
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr, stderr_tail))
        final_text = ""
        idle_warnings = 0

        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

            while True:
                try:
                    raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    idle_warnings += 1
                    if idle_warnings > max_idle_warnings:
                        raise
                    yield {"type": "stalled", "message": "响应较慢，正在等待…"}
                    continue
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype in {"stage", "delta", "done"}:
                    idle_warnings = 0
                if etype == "stage":
                    yield {"type": "stage", "stage": str(event.get("stage", "")), "message": str(event.get("message", ""))}
                elif etype == "delta":
                    cleaned = _clean_response(str(event.get("text") or ""))
                    if cleaned:
                        yield {"type": "delta", "text": cleaned}
                elif etype == "done":
                    final_text = _clean_response(str(event.get("text") or ""))

            returncode = await asyncio.wait_for(process.wait(), timeout=idle_timeout)
            await stderr_task
        except asyncio.TimeoutError as exc:
            raise PromptEnhanceError("提示词增强长时间无响应，已自动中止。") from exc
        finally:
            # Starlette disconnects cancel an AnyIO scope. Shield teardown so
            # the shared GPU lease cannot be released before the child exits.
            with anyio.CancelScope(shield=True):
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
                if not stderr_task.done():
                    stderr_task.cancel()
                    try:
                        await stderr_task
                    except asyncio.CancelledError:
                        pass

        if returncode != 0:
            summary = stderr_tail[-1] if stderr_tail else f"exit {returncode}"
            raise PromptEnhanceError(f"提示词增强失败：{summary}")

        if not final_text:
            raise PromptEnhanceError("模型未返回有效的增强提示词。")
        yield {"type": "done", "enhanced_prompt": final_text, "model": DEFAULT_MODEL_NAME}


async def enhance_prompt(
    prompt: str,
    *,
    recipe: str = "custom",
    style: str = "original",
    edit_mode: str = "global",
    idle_timeout: float = 45.0,
) -> dict[str, str]:
    """Enhance a user prompt using the same Qwen3-4B model bundled for FLUX.2 generation."""
    result: dict[str, str] | None = None
    async for event in enhance_prompt_stream(
        prompt, recipe=recipe, style=style, edit_mode=edit_mode, idle_timeout=idle_timeout,
    ):
        if event.get("type") == "done":
            result = {"enhanced_prompt": event["enhanced_prompt"], "model": event["model"]}
    if result is None:
        raise PromptEnhanceError("模型未返回有效的增强提示词。")
    return result
