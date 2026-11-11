"""Persistent single-consumer job engine; ComfyUI transport stays in run_comfy.py."""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import logging
import math
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from scripts.build_workflow import (
    DEFAULT_TEXT_ENCODER, MODEL_NAME, VAE_NAME, build_api_workflow, provenance,
)
from .prompts import compile_prompt
from .local_detail import finish_detail, prepare_detail
from .regions import build_region_graph, finish_region, prepare_regions

TERMINAL = {"success", "failed", "cancelled"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_asset(root: Path, asset: str) -> Path:
    if not isinstance(asset, str) or not asset or "\\" in asset or ":" in asset or "\x00" in asset:
        raise ValueError("Invalid asset path")
    parts = PurePosixPath(asset).parts
    if not parts or parts[0] not in {"input", "output", "data"} or ".." in parts or asset.startswith("/"):
        raise ValueError("Asset path must stay inside input, output or data")
    result = (root / Path(*parts)).resolve()
    allowed_root = (root / parts[0]).resolve()
    if not allowed_root.is_relative_to(root) or not result.is_relative_to(allowed_root) or not result.is_file():
        raise ValueError("Asset is missing or outside its allowed directory")
    return result


def asset_id(root, path):
    path = Path(path).resolve()
    relative = path.relative_to(root).as_posix()
    safe_asset(root, relative)
    return relative


def asset_url(root, path):
    return "/api/files/" + quote(asset_id(root, path), safe="/")


class InferenceFailure(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


async def probe_backend(server):
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.get(server.rstrip("/") + "/system_stats")
        response.raise_for_status()
        info = response.json()
    devices = info.get("devices", [])
    return {"comfy_connected": True, "gpu": ", ".join(str(device.get("name", "unknown")) for device in devices) or "unknown"}


class ComfyRunner:
    def __init__(self, project_root, timeout=900):
        self.root = project_root
        self.timeout = timeout

    async def __call__(self, workflow, directory, image_a, image_b, server, *, mask_path=None):
        workflow_path = directory / "workflow_api.json"
        atomic_json(workflow_path, workflow)
        command = [
            sys.executable, str(self.root / "scripts" / "run_comfy.py"),
            "--server", server, "--workflow", str(workflow_path),
            "--output-dir", str(directory / "runs"), "--timeout", str(self.timeout),
            "--image-a", f"76={image_a}", "--image-b", f"81={image_b}",
        ]
        if mask_path is not None:
            if workflow.get("201", {}).get("class_type") != "LoadImage":
                raise ValueError("Region mask upload requires LoadImage node 201.")
            mask_path = Path(mask_path).resolve()
            if not mask_path.is_file():
                raise ValueError("Region mask upload is missing.")
            command.extend(["--upload", f"201={mask_path}"])
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(*command, cwd=self.root, env=env, stdout=stdout, stderr=stderr)
            try:
                returncode = await process.wait()
            except asyncio.CancelledError:
                # Stop only our HTTP client process, never ComfyUI's global interrupt.
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                raise
        records = list((directory / "runs").glob("*/run.json"))
        if len(records) != 1:
            raise InferenceFailure("推理客户端未产生完整记录，请检查候选的 stderr.log。", {"exit_code": returncode})
        record = json.loads(records[0].read_text(encoding="utf-8"))
        if returncode != 0 or record.get("status") != "success":
            raise InferenceFailure(record.get("error") or f"推理客户端退出：{returncode}", {"run_file": str(records[0]), "record": record})
        images = []
        for item in record.get("outputs", []):
            path = Path(item["local_path"]).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise InferenceFailure("推理记录包含无效的输出路径。")
            images.append(path)
        if not images:
            raise InferenceFailure("推理完成，但未生成图片。")
        return {"images": images, "metadata": record, "metadata_path": records[0]}


class JobEngine:
    def __init__(self, root, server, *, runner=None, vision=None, probe=None):
        self.root = Path(root).resolve()
        self.server = server.rstrip("/")
        self.jobs_dir = self.root / "data" / "jobs"
        self.runner = runner or ComfyRunner(self.root)
        self.vision = vision
        self.probe = probe or probe_backend
        self.jobs = {}
        self.queue = asyncio.Queue()
        self.worker = None
        self.lock_file = None
        self.started = {}
        self.logger = logging.getLogger(f"alchemy.studio.{hash(str(self.root))}")
        self.log_handler = None

    def vision_api(self):
        if self.vision is None:
            self.vision = importlib.import_module("studio.vision")
        return self.vision

    def segmentation_available(self):
        try:
            return bool(self.vision_api().segmentation_available())
        except (ImportError, OSError, RuntimeError):
            self.logger.warning("Segmentation availability check failed", exc_info=True)
            return False

    async def backend_status(self):
        try:
            result = await self.probe(self.server)
            return {"comfy_connected": bool(result.get("comfy_connected")), "gpu": str(result.get("gpu", "unknown"))}
        except Exception:
            self.logger.warning("ComfyUI status check failed", exc_info=True)
            return {"comfy_connected": False, "gpu": "Unavailable"}

    def serialize(self, value):
        if isinstance(value, Path):
            try:
                return value.resolve().relative_to(self.root).as_posix()
            except ValueError:
                return str(value)
        if isinstance(value, dict):
            return {str(key): self.serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.serialize(item) for item in value]
        if hasattr(value, "item"):
            return self.serialize(value.item())
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    def save(self, job):
        atomic_json(self.jobs_dir / job["id"] / "job.json", self.serialize(job))

    def snapshot(self, job):
        result = copy.deepcopy(self.serialize(job))
        if job["id"] in self.started and job["status"] == "running":
            result["elapsed_seconds"] = round(time.monotonic() - self.started[job["id"]], 3)
        return result

    async def start(self):
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.log_handler = logging.FileHandler(self.root / "data" / "studio.log", encoding="utf-8")
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(self.log_handler)
        self.logger.setLevel(logging.INFO)
        self.lock_file = (self.root / "data" / "studio.lock").open("a+")
        try:
            self.lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                if self.lock_file.read(1) == "":
                    self.lock_file.write(" ")
                    self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock_file.close()
            self.lock_file = None
            self.logger.removeHandler(self.log_handler)
            self.log_handler.close()
            raise RuntimeError("Alchemy Studio must run with one worker; another process owns the job queue.") from exc
        for path in sorted(self.jobs_dir.glob("*/job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not re.fullmatch(r"[0-9a-f]{32}", job.get("id", "")) or path.parent.name != job["id"]:
                    raise ValueError("Invalid persisted job ID")
                if job["status"] == "running":
                    job.update(status="failed", stage="interrupted_restart", error="服务在上次任务运行时重启；旧 ComfyUI 任务可能仍在运行，未自动重提。", finished_at=utc_now())
                    self.save(job)
                self.jobs[job["id"]] = job
            except Exception:
                self.logger.exception("Could not recover job %s", path)
        for job in sorted(self.jobs.values(), key=lambda item: item["created_at"]):
            if job["status"] == "queued":
                self.queue.put_nowait(job["id"])
        self.worker = asyncio.create_task(self.work(), name="alchemy-single-worker")

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None
        if self.log_handler:
            self.logger.removeHandler(self.log_handler)
            self.log_handler.close()

    def create(self, config):
        config = dict(config)
        config.setdefault("style", "original")
        config.setdefault("edit_mode", "global")
        config.setdefault("region_detail", False)
        config.setdefault("edit_regions", [])
        config.setdefault("protect_regions", [])
        job_id = uuid.uuid4().hex
        prompt = compile_prompt(
            config["recipe"], config["mode"], config["prompt"], config["lock_background"], style=config["style"],
        )
        job = {
            "id": job_id, "status": "queued", "stage": "queued", "progress": 0,
            "config": config, "created_at": utc_now(), "elapsed_seconds": 0.0,
            "candidates": [], "attempts": [], "best_index": 0, "preprocessing": {},
            "cancel_requested": False, "compiled_prompt": prompt,
            "progress_semantics": "Completed processing stages, not diffusion sampling percentage.",
            "score_semantics": "Heuristic reference silhouette, palette and foreground integrity; cannot judge semantics, identity, aesthetics or duplicate faces.",
            "models": {"diffusion_model": MODEL_NAME, "text_encoder": DEFAULT_TEXT_ENCODER, "vae": VAE_NAME, "steps": 4, "sampler": "euler", "cfg": 1.0},
            "workflow_provenance": provenance(),
        }
        self.jobs[job_id] = job
        self.save(job)
        self.queue.put_nowait(job_id)
        return self.snapshot(job)

    def cancel(self, job):
        if job["status"] in TERMINAL:
            return self.snapshot(job)
        job["cancel_requested"] = True
        if job["status"] == "queued":
            job.update(status="cancelled", stage="cancelled", finished_at=utc_now())
        else:
            job["stage"] = "cancel_requested_after_current_stage"
        self.save(job)
        return self.snapshot(job)

    def cancelled(self, job):
        if not job.get("cancel_requested"):
            return False
        job.update(status="cancelled", stage="cancelled", finished_at=utc_now())
        self.save(job)
        return True

    def stage(self, job, stage, completed, total):
        job["stage"] = "cancel_requested_after_current_stage" if job.get("cancel_requested") else stage
        job["progress"] = round(100 * completed / total, 1)
        self.save(job)

    async def work(self):
        while True:
            job_id = await self.queue.get()
            job = self.jobs[job_id]
            try:
                if job["status"] != "queued":
                    continue
                self.started[job_id] = time.monotonic()
                job.update(status="running", started_at=utc_now())
                self.save(job)
                await self.execute(job)
            except asyncio.CancelledError:
                if job["status"] == "running":
                    job.update(status="failed", stage="interrupted_shutdown", error="服务停止；仅停止本任务客户端，未向 ComfyUI 发送全局中断。", finished_at=utc_now())
                raise
            except Exception as exc:
                self.logger.exception("Job %s failed at %s", job_id, job.get("stage"))
                with (self.jobs_dir / job_id / "debug.log").open("a", encoding="utf-8") as handle:
                    handle.write(f"{utc_now()} {job.get('stage')}\n{traceback.format_exc()}\n")
                job.update(status="failed", stage="failed", error=str(exc)[:8000], finished_at=utc_now())
                if isinstance(exc, InferenceFailure):
                    job["failure_details"] = self.serialize(exc.details)
                if job["attempts"] and job["attempts"][-1]["status"] == "running":
                    job["attempts"][-1].update(status="failed", error=str(exc), finished_at=utc_now())
            finally:
                if job_id in self.started:
                    job["elapsed_seconds"] = round(time.monotonic() - self.started.pop(job_id), 3)
                self.save(job)
                self.queue.task_done()

    async def execute(self, job):
        config = job["config"]
        region_mode = config.get("edit_mode", "global") == "region"
        segmentation_ready = self.segmentation_available()
        union_background_lock = config["lock_background"] and not region_mode
        if (config["isolate_subject"] or union_background_lock) and not segmentation_ready:
            raise RuntimeError("本地分割模型不可用，无法完成请求的主体隔离或背景锁定。")
        backend = await self.backend_status()
        if not backend["comfy_connected"]:
            raise RuntimeError("无法连接 ComfyUI，请确认推理服务已启动。")
        if self.cancelled(job):
            return
        vision = self.vision_api()
        directory = self.jobs_dir / job["id"]
        original_a = safe_asset(self.root, config["image_a"])
        original_b = safe_asset(self.root, config["image_b"])
        region_offset = int(region_mode)
        total = 3 + region_offset + 2 * config["candidates"]
        self.stage(job, "preparing_structure", 0, total)
        isolate_structure = config["isolate_subject"] and not config["lock_background"] and not region_mode
        prepared_a = await asyncio.to_thread(vision.prepare_reference, original_a, directory / "reference_a", "structure", isolate_structure)
        prepared_a.setdefault("metadata", {})["background_lock_strategy"] = (
            "Keep the original structure scene as model input; extract a separate mask for compositing."
            if union_background_lock else "Explicit region sampling and source-pixel restoration; keep the full structure scene."
            if region_mode else "No background lock requested."
        )
        if (union_background_lock or (region_mode and segmentation_ready)) and prepared_a.get("mask_path") is None:
            if self.cancelled(job):
                return
            mask_directory = "region_mask_a_only" if region_mode else "mask_a_only"
            mask_reference = await asyncio.to_thread(vision.prepare_reference, original_a, directory / mask_directory, "structure", True)
            prepared_a["mask_path"] = mask_reference["mask_path"]
            prepared_a.setdefault("metadata", {})["background_mask_preparation"] = mask_reference["metadata"]
        if union_background_lock and prepared_a.get("mask_path") is None:
            raise RuntimeError("主体掩膜缺失，不能声称已锁定背景。")
        job["preprocessing"]["a_url"] = asset_url(self.root, prepared_a["image_path"])
        if prepared_a.get("mask_path"):
            job["preprocessing"]["mask_a_url"] = asset_url(self.root, prepared_a["mask_path"])
        job["preprocessing_provenance"] = {"a": self.serialize(prepared_a)}
        region_info = None
        detail_info = None
        inference_a = Path(prepared_a["image_path"])
        sampling_mask = None
        if region_mode:
            if self.cancelled(job):
                return
            self.stage(job, "preparing_regions", 1, total)
            region_info = await asyncio.to_thread(
                prepare_regions, original_a, directory / "regions",
                config.get("edit_regions", []), config.get("protect_regions", []),
                subject_mask=prepared_a.get("mask_path"),
                megapixels=config["megapixels"],
                allow_empty_latent=config["region_detail"],
            )
            sampling_mask = Path(region_info["latent_mask_path"])
            if config["region_detail"]:
                detail_info = await asyncio.to_thread(
                    prepare_detail, original_a, prepared_a, region_info,
                    directory / "detail", config["megapixels"],
                )
                inference_a = Path(detail_info["image_path"])
                sampling_mask = Path(detail_info["latent_mask_path"])
                job["preprocessing"]["detail_a_url"] = asset_url(self.root, inference_a)
                job["preprocessing_provenance"]["detail"] = self.serialize(detail_info)
            job["preprocessing"]["edit_mask_url"] = asset_url(self.root, region_info["mask_path"])
            job["preprocessing_provenance"]["regions"] = self.serialize(region_info)
            job["region_control"] = {
                "mode": "detail" if detail_info else "region", "sampling": "source_A_latent_with_edit_noise_mask",
                "restoration": "explicit_edit_mask_source_pixel_restoration",
                "subject_mask_applied": prepared_a.get("mask_path") is not None,
                "foreground_restriction": "source_A_subject_intersection" if prepared_a.get("mask_path") else "none; segmentation unavailable",
                "union_background_lock_applied": False,
                "mask": self.serialize(region_info["mask_path"]),
                "sampling_mask": self.serialize(sampling_mask),
                "metadata": self.serialize((detail_info or region_info)["metadata"]),
            }
        self.stage(job, "preparing_appearance", 1 + region_offset, total)
        if self.cancelled(job):
            return
        prepared_b = await asyncio.to_thread(vision.prepare_reference, original_b, directory / "reference_b", "appearance", config["isolate_subject"])
        job["preprocessing"]["b_url"] = asset_url(self.root, prepared_b["image_path"])
        if prepared_b.get("mask_path"):
            job["preprocessing"]["mask_b_url"] = asset_url(self.root, prepared_b["mask_path"])
        job["preprocessing_provenance"]["b"] = self.serialize(prepared_b)
        self.save(job)
        for index in range(config["candidates"]):
            if self.cancelled(job):
                return
            seed = (config["seed"] + index) % (2**64)
            candidate_dir = directory / f"candidate_{index + 1}"
            candidate_dir.mkdir(exist_ok=True)
            workflow = build_api_workflow(prompt=job["compiled_prompt"], seed=seed, megapixels=config["megapixels"], image_a=inference_a.name, image_b=Path(prepared_b["image_path"]).name)
            if region_mode:
                workflow = build_region_graph(workflow)
            workflow_path = candidate_dir / "workflow_api.json"
            atomic_json(workflow_path, workflow)
            attempt = {"index": index, "seed": seed, "status": "running", "started_at": utc_now(), "workflow": self.serialize(workflow_path), "prompt": job["compiled_prompt"]}
            job["attempts"].append(attempt)
            self.stage(job, f"candidate_{index + 1}_inference", 2 + region_offset + 2 * index, total)
            runner_options = {"mask_path": sampling_mask} if region_mode else {}
            generated = await self.runner(workflow, candidate_dir, inference_a, Path(prepared_b["image_path"]), self.server, **runner_options)
            attempt["inference"] = self.serialize(generated.get("metadata", {}))
            if generated.get("metadata_path"):
                attempt["run_file"] = self.serialize(Path(generated["metadata_path"]))
            self.stage(job, f"candidate_{index + 1}_finishing", 3 + region_offset + 2 * index, total)
            for output_index, raw_path in enumerate(generated["images"]):
                raw_path = Path(raw_path)
                # Retain the raw image before optional composition, including visual failures.
                raw_url = asset_url(self.root, raw_path)
                candidate = {
                    "id": f"{index + 1}_{output_index}", "url": raw_url, "download_url": raw_url + "?download=true",
                    "raw_url": raw_url, "raw_download_url": raw_url + "?download=true",
                    "seed": seed, "score": None, "metrics": {}, "processing_status": "finishing",
                    "provenance": {"raw_image": self.serialize(raw_path), "workflow": self.serialize(workflow_path), "background_lock_requested": config["lock_background"]},
                }
                job["candidates"].append(candidate)
                self.save(job)
                if detail_info is not None:
                    finished = await asyncio.to_thread(
                        finish_detail, raw_path, original_a, Path(region_info["mask_path"]),
                        detail_info, candidate_dir / f"detail_{output_index}",
                    )
                    candidate["raw_window_url"] = raw_url
                    projected = asset_url(self.root, finished["raw_canvas_path"])
                    candidate.update(raw_url=projected, raw_download_url=projected + "?download=true")
                else:
                    finished = await asyncio.to_thread(
                        vision.finish_candidate, raw_path, original_a,
                        candidate_dir / f"finished_{output_index}", union_background_lock,
                        prepared_a.get("mask_path"), appearance_reference=original_b,
                        appearance_mask=prepared_b.get("mask_path"),
                    )
                if region_mode and detail_info is None:
                    regional = await asyncio.to_thread(
                        finish_region, raw_path, original_a, Path(region_info["mask_path"]),
                        candidate_dir / f"region_{output_index}",
                        expected_size=region_info["metadata"]["inference_size"],
                    )
                    finished["region_processing"] = regional
                    finished["image_path"] = regional["image_path"]
                    finished["mask_path"] = regional["mask_path"]
                    finished["metrics"] = {**finished.get("metrics", {}), **regional.get("metrics", {})}
                final_path = Path(finished["image_path"])
                final_url = asset_url(self.root, final_path)
                score = finished.get("score")
                score = float(score) if score is not None and math.isfinite(float(score)) else None
                candidate.update(url=final_url, download_url=final_url + "?download=true", score=score, metrics=self.serialize(finished.get("metrics", {})), processing_status="complete")
                candidate["provenance"].update(final_image=self.serialize(final_path), source_structure=config["image_a"], source_mask=self.serialize(prepared_a.get("mask_path")), finishing=self.serialize(finished))
                self.save(job)
            attempt.update(status="success", finished_at=utc_now())
            self.stage(job, "candidate_completed", 4 + region_offset + 2 * index, total)
        if self.cancelled(job):
            return
        scored = [(i, candidate["score"]) for i, candidate in enumerate(job["candidates"]) if candidate["score"] is not None]
        job["best_index"] = max(scored, key=lambda item: item[1])[0] if scored else 0
        job.update(status="success", stage="complete", progress=100, finished_at=utc_now())
        self.save(job)
