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

emit({"type": "stage", "stage": "loading", "message": "正在加载 Qwen3-4B 模型…"})

import comfy.sd
import torch

clip = comfy.sd.load_clip(
    ckpt_paths=[payload["model_path"]],
    embedding_directory=str(comfy_root / "models" / "embeddings"),
    clip_type=comfy.sd.CLIPType.FLUX2,
)

emit({"type": "stage", "stage": "generating", "message": "正在逐字生成结构化提示词…"})

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
    scope_note = "局部区域修改（仅改选区，保留功能结构与背景）" if edit_mode == "region" else "整图双参考融合"
    system = (
        "你是双参考图工作台提示词专家（图A提供结构，图B提供外观/表情）。"
        "你收到的是用户文字、用途和画风，请依据这些信息改写。"
        "优先遵守用户对A/B角色的明确说明；原文的保留项和禁止项必须保留。"
        "不要虚构未提供的图像细节，也不要从示例复制杯子、猫、茶水或把手到无关任务。"
        "梗图任务可以描述场景互动与表情反差，不强制变成一个器皿。"
        "严格按三段输出，禁用空洞套话：\n"
        "【融合目标】图A具体部位与图B特征如何物理融合。\n"
        "【材质与光影】结合画风写出具体材质、工艺与光影。\n"
        "【保留与约束】保留图A功能部件、背景及用户禁止项。"
    )
    example_user = "任务：陶瓷；画风：原样；局部修改\n原词：把猫的脸做到白瓷杯子上，保留杯口、茶水、右侧把手和桌面背景，不加文字"
    example_assistant = (
        "【融合目标】将图B猫咪五官与花纹以浅浮雕融入图A白瓷杯身正面，仅保留单张正脸。\n"
        "【材质与光影】杯身呈温润白釉陶瓷质感，五官带细腻釉下彩与柔和陶瓷高光。\n"
        "【保留与约束】严格保留图A杯口、茶水、右侧把手及桌面背景，无多余文字。"
    )
    user = f"任务：{recipe_label}；画风：{style_name}；{scope_note}\n原词：{prompt.strip()}"
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{example_user}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n{example_assistant}<|im_end|>\n"
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
        raise PromptEnhanceError("无法完成 ComfyUI 显存交接，请检查服务连接与队列状态。") from exc
    raise PromptEnhanceError(
        "提示词增强使用保守启动门槛（至少 75% 显存空闲且生图缓存已回落）；"
        "当前未满足，请稍后重试或关闭占用显存的程序。"
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
        raise PromptEnhanceError("未找到生图共用的 Qwen3-4B 模型文件（qwen_3_4b_fp4_flux2.safetensors）。")

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
                    yield {"type": "stalled", "message": "提示词增强响应较慢，仍在等待模型输出…"}
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
