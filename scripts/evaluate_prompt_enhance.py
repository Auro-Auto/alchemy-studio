"""Record real prompt-enhancement SSE output for manual semantic acceptance.

Requires a running installed Studio and its original Qwen encoder. This script
does not download models, replace the model or claim image-quality improvements.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("【融合目标】", "【材质与光影】", "【保留与约束】")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8890")
    parser.add_argument("--cases", type=Path, default=ROOT / "experiments/prompt_enhance.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/prompt_enhance")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    output = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    records = []
    failed = False

    def save():
        target = output / "results.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({
            "semantic_review_required": True,
            "image_quality_tested": False,
            "records": records,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)

    for item in cases:
        payload = dict(item)
        name = payload.pop("name")
        record = {"name": name, "request": payload, "events": []}
        records.append(record)
        started = time.monotonic()
        request = Request(
            args.server.rstrip("/") + "/api/prompts/enhance/stream",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        print(f"{name}...", flush=True)
        try:
            with urlopen(request, timeout=args.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    record["events"].append(event)
                    save()
                    if event.get("type") == "error":
                        raise RuntimeError(event.get("detail") or "Enhancement failed")
                    if event.get("type") == "done":
                        text = event["enhanced_prompt"]
                        record.update(
                            enhanced_prompt=text, model=event.get("model"),
                            section_checks={section: section in text for section in SECTIONS},
                        )
            if not record.get("enhanced_prompt"):
                raise RuntimeError("Stream ended without a completed enhancement")
        except HTTPError as error:
            record["error"] = error.read().decode("utf-8", errors="replace")
            failed = True
        except (OSError, ValueError, RuntimeError) as error:
            record["error"] = str(error)
            failed = True
        record["seconds"] = round(time.monotonic() - started, 3)
        save()
        print(f"  {'FAILED' if record.get('error') else 'completed'} in {record['seconds']}s", flush=True)
        if record.get("error"):
            # Stop after a failure so an unavailable model is not retried per case.
            break
    print(output / "results.json", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
