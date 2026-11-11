"""Start the GPU-backed Alchemy Studio on the remote Linux/WSL host."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PORT = 8890


def ready():
    with urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=3) as response:
        data = json.load(response)
    if not isinstance(data, dict) or "comfy_connected" not in data:
        raise ValueError("Unexpected service on Studio port")
    return data


def main():
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('Run this script on the configured Linux/WSL GPU host.')
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Missing frontend build. Run npm ci && npm run build in web/.')
    subprocess.run([str(python), str(ROOT / 'scripts/start_backend.py')], check=True)
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', PORT)) == 0:
            try:
                state = ready()
            except (OSError, ValueError) as exc:
                raise SystemExit(f'Existing port {PORT} service left unchanged: {exc}')
            print(json.dumps({'url': f'http://127.0.0.1:{PORT}', 'existing': True, **state}))
            return
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    env = dict(os.environ, COMFY_SERVER='http://127.0.0.1:8188', DO_NOT_TRACK='1')
    command = [str(python), '-m', 'uvicorn', 'studio.app:app', '--host', '127.0.0.1', '--port', str(PORT)]
    with (logs / 'studio.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (logs / 'studio.pid').write_text(str(process.pid), encoding='utf-8')
    for _ in range(45):
        if process.poll() is not None:
            raise SystemExit(f'Studio failed to start. See {logs / "studio.log"}')
        try:
            state = ready()
            print(json.dumps({'url': f'http://127.0.0.1:{PORT}', 'pid': process.pid, **state}))
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise SystemExit(f'Studio is still starting (PID {process.pid}); inspect logs/studio.log.')


if __name__ == '__main__':
    main()
