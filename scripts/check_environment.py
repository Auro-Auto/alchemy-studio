"""Check a classmate's Linux/WSL machine before installation or before starting."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def command_output(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Command failed")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", action="store_true", help="Also check installed dependencies, assets and model sizes.")
    args = parser.parse_args()
    failures = []

    def check(name, operation):
        try:
            detail = operation()
            print(f"[OK] {name}: {detail}", flush=True)
        except Exception as error:
            failures.append(name)
            print(f"[FAIL] {name}: {error}", flush=True)

    def require(condition, message, result):
        if not condition:
            raise RuntimeError(message)
        return result

    check("Operating system", lambda: require(
        platform.system() == "Linux", "Use Ubuntu 22.04 or WSL2. See docs/CLASSMATE_SETUP.md.", platform.platform(),
    ))
    check("Python", lambda: require(
        sys.version_info[:2] == (3, 10), "Use Python 3.10 for this tested setup.", sys.version.split()[0],
    ))
    minimum_free = 5 if args.ready else 25
    check("Free disk", lambda: require(
        shutil.disk_usage(ROOT).free >= minimum_free * 1024**3,
        f"Free at least {minimum_free} GiB on the project drive.", f">= {minimum_free} GiB",
    ))
    for tool in ("git", "curl", "npm"):
        check(tool, lambda tool=tool: require(bool(shutil.which(tool)), f"Install {tool} inside Linux/WSL.", shutil.which(tool)))

    def node_version():
        version = command_output(["node", "--version"]).lstrip("v")
        major, minor, *_ = map(int, version.split("."))
        return require(
            (major == 20 and minor >= 19) or (major == 22 and minor >= 12) or major > 22,
            "Install Linux Node.js 22.12+ or Node.js 20.19+. A Windows npm path is insufficient.", version,
        )

    check("Node.js", node_version)
    check("NVIDIA driver", lambda: command_output([
        "nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader",
    ]))
    if args.ready:
        def torch_cuda():
            import torch
            import torchvision
            require(torch.__version__ == "2.11.0+cu128", "Expected torch 2.11.0+cu128.", "")
            require(torchvision.__version__ == "0.26.0+cu128", "Expected matching torchvision 0.26.0+cu128.", "")
            require(torch.cuda.is_available(), "CUDA is unavailable in this Python environment.", "")
            torch.zeros(1, device="cuda").add_(1)
            return f"{torch.__version__}, {torch.cuda.get_device_name(0)}"

        check("PyTorch CUDA allocation", torch_cuda)

        def dependencies():
            import fastapi
            import httpx
            import numpy
            import onnxruntime
            import PIL
            import uvicorn
            return ", ".join(
                f"{name} {importlib.metadata.version(name)}"
                for name in ("fastapi", "httpx", "numpy", "onnxruntime", "Pillow", "uvicorn")
            )

        check("Application imports", dependencies)
        for model in json.loads((ROOT / "models.json").read_text(encoding="utf-8")):
            path = ROOT / "backend/ComfyUI/models" / model["folder"] / model["name"]
            check(model["name"], lambda path=path, model=model: require(
                path.is_file() and path.stat().st_size == model["size"],
                "Missing or wrong size. Run scripts/download_models.py to download and verify SHA256.", "Expected size",
            ))
        check("ComfyUI commit", lambda: require(
            command_output(["git", "-C", str(ROOT / "backend/ComfyUI"), "rev-parse", "HEAD"])
            == "bdb0816a292d221efeb6ec4ae41bcdff3f91fc86",
            "Run scripts/setup_remote.sh with the tested CUDA Python.", "Pinned commit",
        ))
        check("Frontend build", lambda: require(
            (ROOT / "web/dist/index.html").is_file(), "Run npm ci && npm run build in web/.", "Present",
        ))
        check("Segmentation model", lambda: command_output([
            sys.executable, str(ROOT / "scripts/prepare_segmentation.py"), "--check",
        ]))
    print(f"\n{len(failures)} blocking check(s). 8GB GPU inference still requires validation on that device.")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
