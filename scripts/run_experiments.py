#!/usr/bin/env python3
"""Build and run a JSON list of Alchemy Lab cases sequentially.

Example:
  python scripts/run_experiments.py --cases experiments/example.json
  python scripts/run_experiments.py --cases experiments/example.json --dry-run

Each case needs name, prompt, image_a, and image_b. Optional fields are seed,
megapixels, and text_encoder (the defaults come from build_workflow.py).
Image paths are relative to the case JSON file, not the working directory.
The runner uploads them to the workflow's LoadImage nodes 76 and 81.

A new batch directory contains each generated API workflow, stdout/stderr logs,
run_comfy results, and an incrementally saved summary.json. A known execution
failure does not stop later cases unless --stop-on-error is set. Timeout,
interruption, or an unknown submission outcome always stops the batch: the
server may still be executing that job. No automatic resubmission is performed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from build_workflow import (
    DEFAULT_SEED,
    DEFAULT_TEXT_ENCODER,
    PROJECT_ROOT,
    build_api_workflow,
    load_official_template,
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def positive_seconds(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return value


def read_cases(path):
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Cases JSON must be a nonempty list of objects.")
    allowed = {"name", "prompt", "image_a", "image_b", "seed", "megapixels", "text_encoder"}
    cases, names = [], set()
    for index, case in enumerate(raw, 1):
        if not isinstance(case, dict) or set(case) - allowed:
            raise ValueError(f"Case {index}: expected an object with only {sorted(allowed)}.")
        current = {"seed": DEFAULT_SEED, "megapixels": 0.25, "text_encoder": DEFAULT_TEXT_ENCODER, **case}
        for field in ("name", "prompt", "image_a", "image_b", "text_encoder"):
            if not isinstance(current.get(field), str) or not current[field].strip():
                raise ValueError(f"Case {index}: {field} must be a nonempty string.")
        if current["name"] in names:
            raise ValueError(f"Duplicate case name: {current['name']!r}")
        names.add(current["name"])
        if type(current["seed"]) is not int or not 0 <= current["seed"] <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError(f"Case {index}: seed must be an unsigned 64-bit integer.")
        mp = current["megapixels"]
        if type(mp) not in (int, float) or not math.isfinite(mp) or not 0.01 <= mp <= 16:
            raise ValueError(f"Case {index}: megapixels must be between 0.01 and 16.")
        for field in ("image_a", "image_b"):
            image = Path(current[field]).expanduser()
            image = (path.parent / image).resolve() if not image.is_absolute() else image.resolve()
            if not image.is_file():
                raise ValueError(f"Case {index}: missing {field}: {image}")
            current[field] = str(image)
        cases.append(current)
    return cases


def run_case(command, case_dir):
    """The subprocess owns HTTP handling, timeouts, uploads and PNG downloads."""
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with (case_dir / "stdout.log").open("w", encoding="utf-8") as stdout, (case_dir / "stderr.log").open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, env=environment, check=False)
    result = {"exit_code": completed.returncode, "status": "failed", "prompt_id": None, "outputs": []}
    records = list((case_dir / "runs").glob("*/run.json"))
    if len(records) != 1:
        result["error"] = f"Expected one run.json, found {len(records)}; inspect stderr.log."
        result["needs_server_check"] = True
        return result
    result["run_metadata"] = str(records[0])
    try:
        run = json.loads(records[0].read_text(encoding="utf-8"))
        result.update({
            "status": run["status"], "prompt_id": run.get("prompt_id"),
            "runner_elapsed_seconds": run.get("elapsed_seconds"),
            "outputs": [item["local_path"] for item in run.get("outputs", [])],
            "error": run.get("error"), "submission_outcome": run.get("submission_outcome"),
        })
        if completed.returncode != 0 and result["status"] == "success":
            result.update(status="failed", error="Runner exited nonzero despite success metadata; inspect stderr.log.")
        if completed.returncode == 0 and result["status"] != "success":
            result.update(status="failed", error="Runner returned zero without success metadata.")
        execution_status = run.get("history", {}).get("status", {})
        execution_finished = execution_status.get("completed") is True or execution_status.get("status_str") == "error"
        result["needs_server_check"] = (
            run["status"] in {"timeout", "interrupted", "submitting", "waiting", "downloading"}
            or completed.returncode in (124, 130)
            or str(run.get("submission_outcome", "")).startswith("unknown:")
            or bool(run.get("prompt_id") and not execution_finished)
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(status="failed", error=f"Cannot read runner metadata: {exc}", needs_server_check=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", required=True, type=Path, help="JSON list of experiment cases")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "output" / "experiments", help="parent directory for this batch")
    parser.add_argument("--timeout", type=positive_seconds, default=900.0, help="run_comfy timeout per case, in seconds")
    parser.add_argument("--poll-interval", type=positive_seconds, default=1.0)
    parser.add_argument("--stop-on-error", action="store_true", help="stop after any failed case")
    parser.add_argument("--dry-run", action="store_true", help="validate cases and write API workflows/summary without contacting a server")
    args = parser.parse_args(argv)
    cases_path = args.cases.expanduser().resolve()
    try:
        cases = read_cases(cases_path)
        load_official_template()  # Preserve the builder's source/hash validation.
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    batch_dir = args.output_dir.expanduser().resolve() / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    summary_path = batch_dir / "summary.json"
    summary = {
        "cases_file": str(cases_path), "server": args.server, "dry_run": args.dry_run,
        "started_at": now(), "finished_at": None, "status": "running",
        "results": [{"case": case, "status": "pending"} for case in cases],
    }
    write_json(summary_path, summary)
    print(f"Batch directory: {batch_dir}", flush=True)
    exit_code = 0
    active = None
    try:
        for index, active in enumerate(summary["results"], 1):
            case = active["case"]
            slug = re.sub(r"[^\w.-]", "_", case["name"]).strip(".")[:80] or "case"
            case_dir = batch_dir / f"{index:03d}_{slug}"
            case_dir.mkdir()
            workflow_path = case_dir / "workflow_api.json"
            options = {key: value for key, value in case.items() if key != "name"}
            options["image_a"] = Path(case["image_a"]).name
            options["image_b"] = Path(case["image_b"]).name
            write_json(workflow_path, build_api_workflow(**options))
            command = [
                sys.executable, str(Path(__file__).with_name("run_comfy.py")),
                "--server", args.server, "--workflow", str(workflow_path),
                "--output-dir", str(case_dir / "runs"), "--timeout", str(args.timeout),
                "--poll-interval", str(args.poll_interval),
                "--image-a", f"76={case['image_a']}", "--image-b", f"81={case['image_b']}",
            ]
            active.update(status="prepared" if args.dry_run else "running", started_at=now(), workflow=str(workflow_path), command=command)
            write_json(summary_path, summary)
            print(f"[{index}/{len(cases)}] {case['name']}", flush=True)
            started = time.monotonic()
            if not args.dry_run:
                active.update(run_case(command, case_dir))
            active.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
            write_json(summary_path, summary)
            print(f"  {active['status']} ({active['elapsed_seconds']} s)", flush=True)
            if active["status"] not in ("success", "prepared"):
                exit_code = 1
                if args.stop_on_error or active.get("needs_server_check"):
                    summary["stop_reason"] = "Check the previous server job before resubmitting." if active.get("needs_server_check") else "Stopped by --stop-on-error."
                    break
        summary["status"] = "prepared" if args.dry_run else ("success" if exit_code == 0 else "failed")
    except KeyboardInterrupt:
        exit_code = 130
        summary.update(status="interrupted", stop_reason="Client interrupted; check the previous server job before resubmitting.")
        if active is not None:
            active.update(status="interrupted", finished_at=now())
    except (OSError, ValueError, TypeError) as exc:
        exit_code = 1
        summary.update(status="failed", stop_reason=str(exc))
        if active is not None:
            active.update(status="failed", error=str(exc), finished_at=now())
        print(f"Error: {exc}", file=sys.stderr)
    finally:
        for result in summary["results"]:
            if result["status"] == "pending":
                result.update(status="skipped", error=summary.get("stop_reason", "Batch stopped."))
        summary["finished_at"] = now()
        summary["counts"] = {status: sum(item["status"] == status for item in summary["results"]) for status in sorted({item["status"] for item in summary["results"]})}
        write_json(summary_path, summary)
        print(f"Summary: {summary_path}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
