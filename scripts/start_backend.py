"""Start the isolated ComfyUI server on Linux/WSL, bound to loopback only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def server_info():
    with urlopen("http://127.0.0.1:8188/system_stats", timeout=2) as response:
        info = json.load(response)
    version = info.get("system", {}).get("comfyui_version")
    if version != "0.44.0":
        raise ValueError(f"Expected ComfyUI 0.44.0, received {version!r}")
    return info


def main():
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8188)) == 0:
            try:
                server_info()
            except (OSError, ValueError, AttributeError) as exc:
                raise SystemExit(f"Port 8188 is occupied by an unverified service; left unchanged: {exc}")
            print("ComfyUI 0.44.0 is already responding at http://127.0.0.1:8188; left running.")
            return
    python = ROOT / ".venv/bin/python"
    backend = ROOT / "backend/ComfyUI"
    if not python.is_file() or not (backend / "main.py").is_file():
        raise SystemExit("Run setup_remote.sh on the Linux/WSL host first.")
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    command = [str(python), "main.py", "--listen", "127.0.0.1", "--port", "8188",
               "--disable-auto-launch", "--reserve-vram", "1.0", "--disable-api-nodes"]
    env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    with (logs / "comfyui.log").open("ab") as log:
        process = subprocess.Popen(command, cwd=backend, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (logs / "comfyui.pid").write_text(str(process.pid), encoding="utf-8")
    for _ in range(60):
        if process.poll() is not None:
            raise SystemExit(f"Backend exited: see {logs / 'comfyui.log'}")
        try:
            info = server_info()
            print(json.dumps({"pid": process.pid, "url": "http://127.0.0.1:8188",
                              "version": info.get("system", {}).get("comfyui_version")}, indent=2))
            return
        except (OSError, ValueError, AttributeError):
            time.sleep(1)
    raise SystemExit(f"Backend is still starting (PID {process.pid}); inspect {logs / 'comfyui.log'}")


if __name__ == "__main__":
    main()
