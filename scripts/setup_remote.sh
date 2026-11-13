#!/usr/bin/env bash
# Linux/WSL. Pass a Python interpreter with working Torch + CUDA installed.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${1:?Usage: bash scripts/setup_remote.sh /path/to/cuda/python}"
COMFY_COMMIT=bdb0816a292d221efeb6ec4ae41bcdff3f91fc86
cd "$ROOT"
mkdir -p backend logs
"$PYTHON_BIN" -c 'import torch; assert torch.cuda.is_available(), "CUDA required"; print(torch.__version__, torch.cuda.get_device_name(0))'
if [ ! -d backend/ComfyUI ]; then
    git init backend/ComfyUI
    git -C backend/ComfyUI remote add origin https://github.com/Comfy-Org/ComfyUI.git
fi
if [ ! -d backend/ComfyUI/.git ]; then
    echo 'Existing backend/ComfyUI is not the expected Git checkout.' >&2
    exit 1
fi
if ! git -C backend/ComfyUI rev-parse --verify HEAD >/dev/null 2>&1; then
    ORIGIN="$(git -C backend/ComfyUI remote get-url origin)"
    if [ "$ORIGIN" != 'https://github.com/Comfy-Org/ComfyUI.git' ]; then
        echo 'Incomplete checkout has an unexpected origin; left unchanged.' >&2
        exit 1
    fi
    git -C backend/ComfyUI fetch --depth 1 origin "$COMFY_COMMIT"
    git -C backend/ComfyUI checkout --detach FETCH_HEAD
fi
ACTUAL_COMMIT="$(git -C backend/ComfyUI rev-parse HEAD)"
if [ "$ACTUAL_COMMIT" != "$COMFY_COMMIT" ]; then
    echo "Existing backend has another commit; review it before changing it." >&2
    exit 1
fi
if [ ! -x .venv/bin/python ]; then
    "$PYTHON_BIN" -m venv --system-site-packages .venv
fi
# A venv created from another venv sees the base interpreter's packages, not
# its parent venv's packages. Provision Torch here when it was not inherited.
if ! .venv/bin/python -c 'import importlib.util; raise SystemExit(importlib.util.find_spec("torch") is None)'; then
    .venv/bin/python -m pip install 'torch==2.11.0' --index-url https://download.pytorch.org/whl/cu128 -c constraints.txt
fi
.venv/bin/python -c 'import torch; assert torch.__version__ == "2.11.0+cu128", "This reproduction requires tested Torch 2.11.0+cu128"; assert torch.cuda.is_available()'
.venv/bin/python -m pip install 'torchvision==0.26.0+cu128' --index-url https://download.pytorch.org/whl/cu128 -c constraints.txt
.venv/bin/python -c 'import torchvision; assert torchvision.__version__ == "0.26.0+cu128"'
.venv/bin/python -m pip install -r backend/ComfyUI/requirements.txt -c constraints.txt --report logs/pip-install-report.json
.venv/bin/python scripts/download_models.py
.venv/bin/python -m pip freeze > logs/requirements-frozen.txt
git -C backend/ComfyUI rev-parse HEAD > logs/comfyui-commit.txt
echo 'Ready. Start with: .venv/bin/python scripts/start_backend.py'
