"""Explicitly provision the small CPU model; API requests never invoke this script.

Usage: python scripts/prepare_segmentation.py --download
       python scripts/prepare_segmentation.py --check

Only Python's standard library is needed to download/verify the model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "segmentation" / "u2netp.onnx"
URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
MD5 = "8e83ca70e441ab06c318d82300c84806"
SIZE = 4_574_861
SOURCE = "https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2netp.py"


def inspect(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "available": False, "reason": "model missing"}
    content = path.read_bytes()
    digest = hashlib.md5(content).hexdigest()
    return {
        "path": str(path), "available": len(content) == SIZE and digest == MD5,
        "bytes": len(content), "md5": digest, "sha256": hashlib.sha256(content).hexdigest(),
    }


def provision() -> dict:
    existing = inspect(MODEL)
    if existing["available"]:
        return existing
    if MODEL.exists():
        raise RuntimeError("Existing model fails checksum; move it aside before explicit reinstallation.")
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        request = urllib.request.Request(URL, headers={"User-Agent": "AlchemyStudio/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=MODEL.parent, prefix="u2netp-", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                count = 0
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > SIZE:
                        raise RuntimeError("Model response exceeds the expected official artifact size.")
                    output.write(chunk)
        result = inspect(temporary)
        if not result["available"]:
            raise RuntimeError(f"Downloaded model failed the official MD5/size verification: {result}")
        temporary.replace(MODEL)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    result = inspect(MODEL)
    provenance = {
        **result, "download_url": URL, "official_checksum_source": SOURCE,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "upstream_architecture": "https://github.com/xuebinqin/U-2-Net",
        "architecture_repository_license": "Apache-2.0",
        "architecture_license_url": "https://github.com/xuebinqin/U-2-Net/blob/master/LICENSE",
        "onnx_distributor": "https://github.com/danielgatis/rembg",
        "distributor_repository_license": "MIT",
        "distributor_license_url": "https://github.com/danielgatis/rembg/blob/main/LICENSE.txt",
        "license_note": "Repository licenses are recorded as declared upstream; the ONNX release has no separate asset-specific license text.",
        "preprocessing_source": "https://github.com/danielgatis/rembg/blob/main/rembg/sessions/base.py",
        "dependencies": ["Pillow", "numpy", "onnxruntime (CPUExecutionProvider)"],
    }
    MODEL.with_suffix(".sources.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--download", action="store_true", help="explicitly download and verify the official 4.57 MB artifact")
    group.add_argument("--check", action="store_true", help="verify the local artifact without network access (default)")
    args = parser.parse_args()
    result = provision() if args.download else inspect(MODEL)
    print(json.dumps(result, indent=2))
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
