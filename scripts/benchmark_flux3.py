"""Run either model on the same protected-edit cases and retain images and GPU samples."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_workflow import build_api_workflow, write_json
from studio.engine import safe_asset
from studio.prompts import compile_prompt
from studio.regions import build_region_graph, finish_region, prepare_regions
from studio.vision import prepare_reference


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def get_queue(server):
    with urlopen(server + "/queue", timeout=5) as response:
        state = json.load(response)
    if state["queue_running"] or state["queue_pending"]:
        raise RuntimeError("ComfyUI queue is busy. Run the comparison when the GPU is idle.")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_flux3_weights(destination):
    model_root = ROOT / "backend" / "ComfyUI" / "models"
    manifest = read_json(destination / "official-weights.json")
    expected = {
        "diffusion_models/flux3-image-klein-int8-convrot.safetensors",
        "text_encoders/qwen3vl_4b_int8_convrot.safetensors",
    }
    if {entry["path"] for entry in manifest["files"]} != expected:
        raise ValueError("The manifest must describe the two official FLUX 3 INT8 files.")
    for entry in manifest["files"]:
        weight = model_root / entry["path"]
        if not weight.is_file():
            raise FileNotFoundError(f"Missing authorized model download: {weight}")
        if weight.stat().st_size != entry["bytes"] or sha256(weight) != entry["sha256"]:
            raise ValueError(f"Official file size or SHA-256 mismatch: {weight}")
        print("Verified", entry["path"], flush=True)


def build_sheet(destination, cases):
    labels = ("Source A", "Reference B", "FLUX.2 4B FP8", "FLUX 3 INT8")
    width, height = 310, 293
    sheet = Image.new("RGB", (width * len(labels), 34 + height * len(cases)), "#f4f0e8")
    draw = ImageDraw.Draw(sheet)
    for index, label in enumerate(labels):
        draw.text((index * width + 10, 10), label, fill="#252a24")
    for row, case in enumerate(cases):
        name = case["name"].removesuffix("_current_region")
        paths = [ROOT / case["image_a"], ROOT / case["image_b"]]
        paths.extend(destination / model / name / "finished" / "region_composite.png" for model in ("flux2", "flux3"))
        for col, path in enumerate(paths):
            top = 32 + row * height
            draw.text((col * width + 10, top), name.replace("_", " / seed "), fill="#252a24")
            if not path.is_file():
                draw.text((col * width + 10, top + 30), "Pending", fill="#6c6c5f")
                continue
            with Image.open(path) as source:
                image = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGB"), (width - 16, height - 29))
            sheet.paste(image, (col * width + (width - image.width) // 2, top + 18))
    sheet.save(destination / "comparison.jpg", quality=92)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("flux2", "flux3"), required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-pid", type=int)
    parser.add_argument("--unload-first", action="store_true", help="Evict cached models on an idle server before the first case.")
    args = parser.parse_args()
    server = args.server.rstrip("/")
    destination = args.output.resolve()
    output = destination / args.model
    cases = [item for item in read_json(args.cases) if item["name"].endswith("_current_region")]
    if not cases or any(item.get("edit_mode") != "region" for item in cases):
        parser.error("Provide protected-edit cases ending with _current_region.")
    if any(not item["name"].replace("_", "").isalnum() for item in cases):
        parser.error("Case names must contain only letters, numbers and underscores.")
    protocol = {
        "cases": cases,
        "prompt_upsampling": False,
        "controls": "Identical prepared A/B images, instruction content, seed, pixel budget, protection masks and final compositing. Each model uses its own reference tokens and distilled sampler configuration.",
        "gpu_scope": "run_comfy client interval including upload and download. First case after explicit unload is cold; later cases retain model cache.",
        "review": ["character features and palette", "material and depth", "mask-edge seams", "functional structures"],
    }
    protocol_path = destination / "protocol.json"
    if protocol_path.is_file() and read_json(protocol_path) != protocol:
        raise RuntimeError("The existing comparison uses different inputs or settings.")
    other = destination / ("flux3" if args.model == "flux2" else "flux2") / "results.json"
    paired_inputs = {row["case"]: row["input_sha256"] for row in read_json(other)} if other.is_file() else {}
    if args.model == "flux3":
        verify_flux3_weights(destination)
    output.mkdir(parents=True, exist_ok=False)
    write_json(protocol_path, protocol)
    get_queue(server)
    if args.unload_first:
        payload = json.dumps({"unload_models": True, "free_memory": True}).encode()
        with urlopen(Request(server + "/free", data=payload, headers={"Content-Type": "application/json"}), timeout=5):
            pass
        time.sleep(4)

    rows = []
    for index, case in enumerate(cases):
        get_queue(server)
        name = case["name"].removesuffix("_current_region")
        directory = output / name
        a = safe_asset(ROOT, case["image_a"])
        b = safe_asset(ROOT, case["image_b"])
        a_ref = prepare_reference(a, directory / "source", "structure", False)["image_path"]
        b_ref = prepare_reference(b, directory / "reference", "appearance", False)["image_path"]
        subject = prepare_reference(a, directory / "subject", "structure", True)["mask_path"]
        region = prepare_regions(a, directory / "mask", case["edit_regions"], case["protect_regions"],
                                 subject_mask=subject, megapixels=case["megapixels"])
        fingerprints = {
            "a": sha256(a_ref), "b": sha256(b_ref),
            "latent_mask": sha256(region["latent_mask_path"]), "final_mask": sha256(region["mask_path"]),
        }
        if name in paired_inputs and paired_inputs[name] != fingerprints:
            raise RuntimeError(f"Prepared references or masks changed for {name}; the cases are no longer paired.")
        prompt = compile_prompt(case["recipe"], case["mode"], case["prompt"], case.get("lock_background", False))
        if args.model == "flux2":
            graph = build_region_graph(build_api_workflow(prompt=prompt, seed=case["seed"], megapixels=case["megapixels"]))
        else:
            from scripts.build_flux3_benchmark import build_flux3_benchmark_graph
            graph = build_flux3_benchmark_graph(prompt, case["seed"], case["megapixels"], masked=True)
        workflow = directory / "workflow.json"
        write_json(workflow, graph)
        gpu_path = directory / "gpu.json"
        command = [sys.executable, str(ROOT / "scripts/measure_gpu.py"), "--output", str(gpu_path), "--interval", ".10"]
        if args.comfy_pid:
            command += ["--pid", str(args.comfy_pid)]
        command += ["--", sys.executable, str(ROOT / "scripts/run_comfy.py"), "--server", server,
                    "--workflow", str(workflow), "--output-dir", str(directory / "runs"), "--poll-interval", ".3",
                    "--image-a", f"76={a_ref}", "--image-b", f"81={b_ref}", "--upload", f"201={region['latent_mask_path']}"]
        started = time.monotonic()
        with (directory / "run.log").open("wb") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        row = {
            "case": name, "model": args.model, "seed": case["seed"],
            "expected_size": region["metadata"]["inference_size"],
            "input_sha256": fingerprints,
            "seconds_with_client": round(time.monotonic() - started, 3),
            "cold_start_requested": bool(args.unload_first and index == 0),
            "gpu": str(gpu_path.relative_to(destination)),
            "status": "failed" if result.returncode else "inference_complete",
        }
        if gpu_path.is_file():
            gpu = read_json(gpu_path)
            row.update(device=gpu["device"], process=gpu["process"], sampling_source=gpu["sampling_source"])
        rows.append(row)
        write_json(output / "results.json", rows)
        if result.returncode:
            raise RuntimeError(f"Benchmark failed for {name}. See {directory / 'run.log'}.")
        metadata = read_json(next((directory / "runs").glob("*/run.json")))
        raw = Path(next(item["local_path"] for item in metadata["outputs"] if str(item["node_id"]) == "94"))
        final = finish_region(raw, a, region["mask_path"], directory / "finished",
                              expected_size=region["metadata"]["inference_size"])
        row.update(status="success", raw=str(raw.relative_to(destination)),
                   final=str(final["image_path"].relative_to(destination)), protection=final["metrics"])
        write_json(output / "results.json", rows)
        build_sheet(destination, cases)
        print(name, row["seconds_with_client"], "seconds", row.get("device"), flush=True)


if __name__ == "__main__":
    main()
