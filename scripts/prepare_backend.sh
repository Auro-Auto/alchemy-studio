#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/setup_remote.sh" "${1:?Usage: bash scripts/prepare_backend.sh /path/to/cuda/python}"
