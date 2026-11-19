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

    async def read(self, count):
        return await self.readline()


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


def test_comfy_release_waits_for_cache_and_refuses_external_work(monkeypatch):
    import httpx

    real_client = httpx.AsyncClient
    calls = []
    reservations = iter([1024**3, 1024**3, 0])

    def responder(request):
        calls.append((request.method, request.url.path))
        if request.url.path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        if request.url.path == '/system_stats':
            return httpx.Response(200, json={
                'system': {'comfyui_version': '0.44.0'},
                'devices': [{
                    'type': 'cuda', 'torch_vram_total': next(reservations), 'torch_vram_free': 0,
                    'vram_total': 16 * 1024**3, 'vram_free': 15 * 1024**3,
                }],
            })
        assert request.url.path == '/free'
        assert json.loads(request.content) == {'unload_models': True, 'free_memory': True}
        return httpx.Response(200)

    monkeypatch.setattr(enhance.httpx, 'AsyncClient', lambda **kwargs: real_client(
        transport=httpx.MockTransport(responder), **kwargs))
    asyncio.run(enhance.release_comfy_models('http://comfy', timeout=1))
    assert calls.index(('POST', '/free')) > calls.index(('GET', '/system_stats'))
    assert calls.count(('GET', '/system_stats')) == 3

    calls.clear()
    def busy(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'queue_running': [], 'queue_pending': ['external']})
    monkeypatch.setattr(enhance.httpx, 'AsyncClient', lambda **kwargs: real_client(
        transport=httpx.MockTransport(busy), **kwargs))
    with pytest.raises(enhance.PromptEnhanceError, match='排队'):
        asyncio.run(enhance.release_comfy_models('http://comfy', timeout=1))
    assert calls == ['/queue']


def test_real_child_stderr_drain_and_stream_close_wait_for_exit(tmp_path, monkeypatch):
    model = tmp_path / 'qwen.safetensors'
    model.write_bytes(b'fixture')
    comfy = tmp_path / 'ComfyUI'
    comfy.mkdir()
    monkeypatch.setattr(enhance, 'COMFY_ROOT', comfy)
    monkeypatch.setattr(enhance, 'QWEN_ENCODER_PATH', model)
    monkeypatch.setattr(enhance, '_WORKER_SCRIPT', r'''
import json, os, sys, time
sys.stdin.read()
print(json.dumps({'type': 'stage', 'stage': str(os.getpid()), 'message': 'started'}), flush=True)
sys.stderr.write('x' * 524288)
sys.stderr.flush()
print(json.dumps({'type': 'delta', 'text': 'text'}), flush=True)
time.sleep(60)
''')
    real_exec = asyncio.create_subprocess_exec
    processes = []

    async def capture(*args, **kwargs):
        process = await real_exec(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)

    async def run():
        stream = enhance.enhance_prompt_stream('test', idle_timeout=3)
        assert (await anext(stream))['type'] == 'stage'
        assert (await anext(stream))['type'] == 'delta'
        await asyncio.wait_for(stream.aclose(), timeout=3)
        assert processes[0].returncode is not None

        stream = enhance.enhance_prompt_stream('test', idle_timeout=3)
        assert (await anext(stream))['type'] == 'stage'
        assert (await anext(stream))['type'] == 'delta'
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert processes[1].returncode is not None

    asyncio.run(run())


def test_comfy_release_does_not_treat_dynamic_vram_as_free(monkeypatch):
    import httpx

    real_client = httpx.AsyncClient
    calls = []
    def responder(request):
        calls.append(request.url.path)
        if request.url.path == '/queue':
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        if request.url.path == '/free':
            return httpx.Response(200)
        return httpx.Response(200, json={
            'system': {'comfyui_version': '0.44.0'},
            'devices': [{
                'type': 'cuda', 'torch_vram_total': 0, 'torch_vram_free': 0,
                'vram_total': 8 * 1024**3, 'vram_free': 3 * 1024**3,
            }],
        })
    monkeypatch.setattr(enhance.httpx, 'AsyncClient', lambda **kwargs: real_client(
        transport=httpx.MockTransport(responder), **kwargs))
    with pytest.raises(enhance.PromptEnhanceError, match='保守启动门槛'):
        asyncio.run(enhance.release_comfy_models('http://comfy', timeout=.01))
    assert '/free' in calls
