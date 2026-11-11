#!/usr/bin/env python3
"""Export local ComfyUI inference records to output/results.json and results.csv.

Run without arguments from any directory. Only run.json records containing
status, workflow, and history are included. Each output image gets one row;
runs with no output still get a row with an empty output_file. No execution
status or visual-quality filtering is performed: success means execution only.

Paths inside the project are project-relative with forward slashes. width and
height describe the actual PNG (or server image metadata if the PNG is missing).
megapixels is the configured reference-A pixel budget, not width*height / 1e6.
backend_seconds is execution_start to execution_success using ComfyUI's
millisecond timestamps; it remains null when either event is unavailable.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
import sys
from pathlib import Path


FIELDS = (
    "case", "run_file", "status", "client_seconds", "backend_seconds",
    "width", "height", "megapixels", "seed", "prompt", "output_file",
)


def relative_path(path, root):
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def finite_number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def backend_seconds(run):
    status = run["history"].get("status") or {}
    starts, finishes = [], []
    for message in status.get("messages", []):
        if not isinstance(message, (list, tuple)) or len(message) != 2 or not isinstance(message[1], dict):
            continue
        event, data = message
        if data.get("prompt_id") not in (None, run.get("prompt_id")):
            continue
        timestamp = finite_number(data.get("timestamp"))
        if timestamp is not None:
            if event == "execution_start":
                starts.append(timestamp)
            elif event == "execution_success":
                finishes.append(timestamp)
    if starts and finishes and max(finishes) >= min(starts):
        return round((max(finishes) - min(starts)) / 1000.0, 3)
    return None


def graph_value(graph, class_type, field, preferred_id):
    preferred = graph.get(preferred_id, {})
    if preferred.get("class_type") == class_type:
        return preferred.get("inputs", {}).get(field)
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return node.get("inputs", {}).get(field)
    return None


def case_name(run_file):
    container = run_file.parent.parent
    if container.name == "runs":
        # Batch layout: .../001_case_name/runs/<run-id>/run.json.
        return re.sub(r"^\d+_", "", container.parent.name)
    return container.name


def image_path(output, run_file, root):
    stored = output.get("local_path")
    if not isinstance(stored, str) or not stored:
        return None
    normalized = stored.replace("\\", "/")
    # run_comfy stores PNGs beside run.json. Prefer this after project relocation.
    nearby = run_file.parent / normalized.rsplit("/", 1)[-1]
    if nearby.is_file():
        return nearby
    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_file():
        return candidate
    return nearby


def dimensions(path, output):
    if path is not None and path.is_file():
        with path.open("rb") as image:
            header = image.read(24)
        if len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
            return struct.unpack(">II", header[16:24])
    metadata = output.get("server_image", {}).get("metadata", {})
    return metadata.get("width"), metadata.get("height")


def rows_for_run(run, run_file, root):
    graph = run["workflow"]
    seed = graph_value(graph, "RandomNoise", "noise_seed", "106")
    if seed is None:
        seed = graph_value(graph, "KSampler", "seed", "3")
    base = {
        "case": case_name(run_file), "run_file": relative_path(run_file, root),
        "status": run["status"], "client_seconds": finite_number(run.get("elapsed_seconds")),
        "backend_seconds": backend_seconds(run),
        "megapixels": graph_value(graph, "ImageScaleToTotalPixels", "megapixels", "111"),
        "seed": seed, "prompt": graph_value(graph, "CLIPTextEncode", "text", "109"),
    }
    for output in run.get("outputs", []) or [{}]:
        path = image_path(output, run_file, root)
        width, height = dimensions(path, output)
        row = {**base, "width": width, "height": height, "output_file": relative_path(path, root) if path else None}
        yield {field: row[field] for field in FIELDS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.project_root.expanduser().resolve()
    output_dir = root / "output"
    if not output_dir.is_dir():
        parser.error(f"Output directory does not exist: {output_dir}")
    rows, run_count, read_errors = [], 0, 0
    for run_file in sorted(output_dir.rglob("run.json")):
        try:
            run = json.loads(run_file.read_text(encoding="utf-8-sig"))
            if not isinstance(run, dict) or not isinstance(run.get("status"), str) or not isinstance(run.get("workflow"), dict) or not isinstance(run.get("history"), dict):
                continue
            current_rows = list(rows_for_run(run, run_file, root))
            rows.extend(current_rows)
            run_count += 1
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            read_errors += 1
            print(f"Cannot summarize {run_file}: {exc}", file=sys.stderr)
    json_path = output_dir / "results.json"
    csv_path = output_dir / "results.csv"
    json_temp = json_path.with_suffix(".json.tmp")
    json_temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json_temp.replace(json_path)
    csv_temp = csv_path.with_suffix(".csv.tmp")
    with csv_temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    csv_temp.replace(csv_path)
    print(f"Summarized {run_count} inference runs into {len(rows)} rows; status describes execution, not visual quality.")
    print(f"JSON: {json_path}\nCSV: {csv_path}")
    return 1 if read_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
