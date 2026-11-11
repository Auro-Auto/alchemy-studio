#!/usr/bin/env bash
# Run after the verified ComfyUI environment has been prepared.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
    echo 'Create the tested CUDA environment using setup_remote.sh first.' >&2
    exit 1
fi
.venv/bin/python -m pip install -r requirements-studio.txt -c constraints.txt
.venv/bin/python scripts/prepare_segmentation.py --download
.venv/bin/python -m pytest tests -q
echo 'Ready. Start with: .venv/bin/python scripts/start_studio.py'
