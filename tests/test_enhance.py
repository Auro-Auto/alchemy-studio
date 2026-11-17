"""Unit tests for studio.enhance (streaming, <think> stripping, idle timeout, cancellation)."""
import asyncio
import json
from unittest.mock import patch

import pytest

from studio import enhance


class FakeStream:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        item = self._lines.pop(0)
        if isinstance(item, float):
            await asyncio.sleep(item)
            return await self.readline()
        return item


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, chunk):
        self.data.extend(chunk)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stdout_lines, stderr_lines=(), returncode=0):
        self.stdin = FakeStdin()
        self.stdout = FakeStream(stdout_lines)
        self.stderr = FakeStream(stderr_lines)
        self.returncode = None
        self._final_rc = returncode
        self.killed = False

    async def wait(self):
        if self.returncode is None:
            self.returncode = -9 if self.killed else self._final_rc
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_clean_response_strips_closed_and_unclosed_think():
    assert enhance._clean_response("<think>internal</think>【融合目标】内容") == "【融合目标】内容"
    assert enhance._clean_response("<think>still thinking...") == ""
    assert enhance._clean_response("【融合目标】内容<think>trailing") == "【融合目标】内容"
    assert enhance._clean_response('“【融合目标】内容”') == "【融合目标】内容"


def test_build_chat_prompt_includes_recipe_style_and_scope():
    text = enhance._build_chat_prompt("小猫杯", recipe="ceramic", style="felt", edit_mode="region")
    assert "陶瓷器皿融合" in text
    assert "羊毛毡" in text
    assert "局部区域修改" in text
    assert "小猫杯" in text


def test_enhance_prompt_stream_happy_path_and_unclosed_think(tmp_path):
    model = tmp_path / "qwen.safetensors"
    model.write_bytes(b"ok")
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()

    proc = FakeProcess([
        json.dumps({"type": "stage", "stage": "loading", "message": "loading"}).encode() + b"\n",
        json.dumps({"type": "delta", "text": "<think>secret"}).encode() + b"\n",
        json.dumps({"type": "delta", "text": "<think>secret</think>【融合目标】"}).encode() + b"\n",
        json.dumps({"type": "done", "text": "<think>secret</think>【融合目标】完成"}).encode() + b"\n",
    ])

    async def fake_exec(*args, **kwargs):
        return proc

    async def run():
        with patch.object(enhance, "COMFY_ROOT", comfy), patch.object(enhance, "QWEN_ENCODER_PATH", model), \
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            return [event async for event in enhance.enhance_prompt_stream("测试", idle_timeout=2.0)]

    events = asyncio.run(run())
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas == ["【融合目标】"]
    assert events[-1] == {"type": "done", "enhanced_prompt": "【融合目标】完成", "model": enhance.DEFAULT_MODEL_NAME}


def test_enhance_prompt_stream_stalled_then_timeout_and_nonzero_exit(tmp_path):
    model = tmp_path / "qwen.safetensors"
    model.write_bytes(b"ok")
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()

    stalled_proc = FakeProcess([0.2, 0.2])

    async def fake_exec_stall(*args, **kwargs):
        return stalled_proc

    async def run_stall():
        events = []
        with patch.object(enhance, "COMFY_ROOT", comfy), patch.object(enhance, "QWEN_ENCODER_PATH", model), \
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec_stall):
            with pytest.raises(enhance.PromptEnhanceError, match="无响应"):
                async for event in enhance.enhance_prompt_stream("测试", idle_timeout=0.05, max_idle_warnings=1):
                    events.append(event)
        return events

    events = asyncio.run(run_stall())
    assert [e["type"] for e in events] == ["stalled"]
    assert stalled_proc.killed is True

    fail_proc = FakeProcess([], stderr_lines=[b"traceback line\n", b"RuntimeError: boom\n"], returncode=1)

    async def fake_exec_fail(*args, **kwargs):
        return fail_proc

    async def run_fail():
        with patch.object(enhance, "COMFY_ROOT", comfy), patch.object(enhance, "QWEN_ENCODER_PATH", model), \
                patch("asyncio.create_subprocess_exec", side_effect=fake_exec_fail):
            await enhance.enhance_prompt("测试", idle_timeout=1.0)

    with pytest.raises(enhance.PromptEnhanceError, match="RuntimeError: boom"):
        asyncio.run(run_fail())
