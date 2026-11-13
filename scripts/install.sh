#!/usr/bin/env bash
# Run inside Ubuntu 22.04 / WSL2 after installing Linux Python, Node.js and NVIDIA drivers.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
command -v "$PYTHON_BIN" >/dev/null || { echo 'Install Python 3.10 and python3.10-venv first.' >&2; exit 1; }
"$PYTHON_BIN" scripts/check_environment.py
mkdir -p logs
trap 'echo "Installation stopped. Fix the error above, then rerun bash scripts/install.sh." >&2' ERR
if [ ! -x .venv/bin/python ]; then
    "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install 'torch==2.11.0' 'torchvision==0.26.0' --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -c 'import torch, torchvision; assert torch.__version__ == "2.11.0+cu128"; assert torchvision.__version__ == "0.26.0+cu128"; assert torch.cuda.is_available(), "CUDA unavailable"; torch.zeros(1, device="cuda"); print(torch.cuda.get_device_name(0))'
bash scripts/setup_remote.sh "$ROOT/.venv/bin/python"
bash scripts/setup_studio.sh
(cd web && npm ci && npm run build)
.venv/bin/python scripts/check_environment.py --ready
echo 'Installed. Run .venv/bin/python scripts/start_studio.py and open http://127.0.0.1:8890'
