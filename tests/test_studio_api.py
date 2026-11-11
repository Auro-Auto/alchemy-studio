"""API integration tests use in-process image processing and stubbed inference."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from studio.app import MAX_UPLOAD_BYTES, create_app
from studio.engine import ComfyRunner, InferenceFailure
from scripts.build_workflow import build_api_workflow
from studio.regions import build_region_graph


class StubVision:
    def __init__(self, available=True, fail_finish=False):
        self.available = available
        self.fail_finish = fail_finish
        self.prepared = []
        self.finished = 0
        self.finish_locks = []

    def segmentation_available(self):
        return self.available

    def prepare_reference(self, source, output_dir, role, isolate):
        self.prepared.append((role, isolate, output_dir.name))
        output_dir.mkdir(parents=True, exist_ok=True)
        image = Image.open(source).convert("RGB")
        destination = output_dir / "reference.png"
        image.save(destination)
        mask_path = None
        if isolate:
            mask_path = output_dir / "mask.png"
            mask = Image.new("L", image.size)
            ImageDraw.Draw(mask).rectangle((4, 4, image.width - 5, image.height - 5), fill=255)
            mask.save(mask_path)
        return {"image_path": destination, "mask_path": mask_path, "metadata": {"isolated": isolate}}

    def finish_candidate(self, generated, original_a, output_dir, lock_background, source_mask, *, appearance_reference=None, appearance_mask=None):
        assert appearance_reference is not None and appearance_reference.is_file()
        self.finish_locks.append(lock_background)
        if self.fail_finish:
            raise RuntimeError("mock composition failure")
        output_dir.mkdir(parents=True, exist_ok=True)
        image = Image.open(generated).convert("RGB")
        if lock_background:
            assert source_mask is not None
            original = Image.open(original_a).convert("RGB")
            image = Image.composite(image.resize(original.size), original, Image.open(source_mask))
        destination = output_dir / "final.png"
        image.save(destination)
        self.finished += 1
        return {"image_path": destination, "raw_path": generated, "score": self.finished / 10 if self.available else None, "metrics": {"scope": "shape only", "background_locked": lock_background}}


class StubRunner:
    def __init__(self, *, gate=None, fail_first=False):
        self.gate, self.fail_first = gate, fail_first
        self.calls = []
        self.active = 0
        self.peak_active = 0
        self.workflows = []
        self.masks = []

    async def __call__(self, workflow, directory, image_a, image_b, server, *, mask_path=None):
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        self.calls.append(workflow["106"]["inputs"]["noise_seed"])
        self.workflows.append(workflow)
        self.masks.append(mask_path)
        if mask_path is not None:
            assert mask_path.is_file()
            assert workflow["201"]["class_type"] == "LoadImage"
            assert workflow["104"]["inputs"]["latent_image"] == ["204", 0]
        try:
            while self.gate is not None and not self.gate.is_set():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.03)
            if self.fail_first and len(self.calls) == 1:
                raise InferenceFailure("mock CUDA inference failure", {"node_id": "104"})
            raw = directory / "raw.png"
            size = (64, 48)
            if mask_path is not None:
                record = json.loads((directory.parent / "job.json").read_text(encoding="utf-8"))
                size = tuple(record["region_control"]["metadata"]["inference_size"])
            Image.new("RGB", size, (200, 70, 50)).save(raw)
            return {"images": [raw], "metadata": {"status": "success", "workflow": workflow, "prompt_id": "stub"}}
        finally:
            self.active -= 1


async def online(server):
    return {"comfy_connected": True, "gpu": "stub GPU"}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "input").mkdir()
    for name in ("a.png", "b.png", "iced_drink_cup.png", "comfy_cat.png", "sneaker_black.png", "plain_mug.jpg", "real_cat.png"):
        Image.new("RGB", (64, 48), "blue").save(tmp_path / "input" / name)
    (tmp_path / "web" / "dist").mkdir(parents=True)
    (tmp_path / "web" / "dist" / "index.html").write_text("<p>local studio</p>")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "private.txt").write_text("private")
    return tmp_path


def config(**changes):
    return {"image_a": "input/a.png", "image_b": "input/b.png", "recipe": "ceramic", "mode": "structured", "prompt": "保留现有杯口，不加吸管", "isolate_subject": False, "lock_background": False, "candidates": 1, "seed": 42, "megapixels": 0.5, **changes}


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for stub job")


def finished(client, job_id):
    def check():
        job = client.get(f"/api/jobs/{job_id}").json()
        return job if job["status"] in {"success", "failed", "cancelled"} else None
    return wait_for(check)


def test_upload_normalizes_exif_alpha_and_rejects_invalid(project):
    app = create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)
    with TestClient(app) as client:
        image = Image.new("RGB", (20, 10), "red")
        exif = Image.Exif(); exif[274] = 6
        buffer = io.BytesIO(); image.save(buffer, format="JPEG", exif=exif)
        response = client.post("/api/uploads", files={"file": ("../../escape.jpg", buffer.getvalue(), "image/jpeg")})
        assert response.status_code == 200
        asset = response.json()
        assert (asset["width"], asset["height"]) == (10, 20)
        normalized = Image.open(project / asset["id"])
        assert normalized.mode == "RGB" and not normalized.getexif()
        assert client.get(asset["url"]).status_code == 200
        alpha = io.BytesIO(); Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(alpha, format="PNG")
        rgba_asset = client.post("/api/uploads", files={"file": ("alpha.png", alpha.getvalue(), "image/png")}).json()
        assert Image.open(project / rgba_asset["id"]).getpixel((0, 0)) == (255, 255, 255)
        assert client.post("/api/uploads", files={"file": ("fake.png", b"not an image", "image/png")}).status_code == 400
        assert client.post("/api/uploads", files={"file": ("too-large.png", b"x" * (MAX_UPLOAD_BYTES + 1), "image/png")}).status_code == 413


def test_safe_paths_and_same_origin_frontend(project):
    app = create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)
    with TestClient(app) as client:
        assert "local studio" in client.get("/").text
        assert client.get("/api/no-such-route").status_code == 404
        for path in ("scripts/private.txt", "data/%2e%2e/scripts/private.txt", "input/%2e%2e/%2e%2e/private.txt", "input%5c..%5cscripts%5cprivate.txt"):
            assert client.get("/api/files/" + path).status_code == 404
        for asset in ("../private.txt", "input/../scripts/private.txt", "C:/Windows/win.ini", "input\\a.png", "data/missing.png"):
            assert client.post("/api/jobs", json=config(image_a=asset)).status_code == 400
        assert client.post("/api/jobs", json=config(candidates=4)).status_code == 422
        assert client.post("/api/jobs", json=config(seed=True)).status_code == 422
        assert client.get("/api/files/input/a.png?download=true").headers["content-disposition"].startswith("attachment")


def test_unavailable_backend_and_segmentation_are_explicit(project):
    async def offline(server):
        raise OSError("mock disconnected")
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(False), backend_probe=offline)) as client:
        assert client.get("/api/status").json()["comfy_connected"] is False
        assert client.post("/api/jobs", json=config()).status_code == 503
        assert client.post("/api/jobs", json=config(isolate_subject=True)).status_code == 503
        assert client.get("/api/jobs").json() == []


def test_success_keeps_all_candidates_exact_prompts_and_artifacts(project):
    runner = StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(), backend_probe=online)) as client:
        assert len(client.get("/api/presets").json()) == 5
        response = client.post("/api/jobs", json=config(candidates=3))
        assert response.status_code == 202
        job = finished(client, response.json()["id"])
        assert job["status"] == "success" and job["progress"] == 100
        assert runner.calls == [42, 43, 44] and len(job["candidates"]) == 3
        assert job["config"]["edit_mode"] == "global"
        assert job["config"]["style"] == "original"
        assert runner.masks == [None, None, None]
        assert all(workflow["104"]["inputs"]["latent_image"] == ["113", 0] for workflow in runner.workflows)
        assert job["best_index"] == 2
        assert "保留现有杯口" in job["compiled_prompt"] and "exactly one face" in job["compiled_prompt"]
        assert all(candidate["raw_url"] != candidate["url"] for candidate in job["candidates"])
        assert all(client.get(candidate["url"]).status_code == 200 for candidate in job["candidates"])
        assert all("workflow" in attempt and attempt["inference"]["workflow"]["109"]["inputs"]["text"] == job["compiled_prompt"] for attempt in job["attempts"])
        exported = client.get(f"/api/jobs/{job['id']}/export")
        assert exported.headers["content-disposition"].startswith("attachment")
        assert exported.json()["config"]["candidates"] == 3
        assert (project / "data/jobs" / job["id"] / "job.json").is_file()


def test_meme_presets_replay_style_and_skip_invalid_entries(project):
    meme = dict(config(recipe="meme", style="clay", prompt="柴犬鞋飞奔"), id="meme_race", title="柴犬鞋")
    (project / "input/meme_presets.json").write_text(json.dumps([
        meme, {**meme, "id": "meme_missing", "image_b": "input/missing.png"},
        {**meme, "id": "meme_bad_style", "style": "unknown"},
    ]), encoding="utf-8")
    (project / "input/meme_sources.json").write_text("[]", encoding="utf-8")
    preview = project / "output/quality_presets/meme_race.jpg"
    preview.parent.mkdir(parents=True)
    Image.new("RGB", (64, 48)).save(preview)
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)) as client:
        items = [item for item in client.get("/api/presets").json() if item["category"] == "meme"]
        assert [item["id"] for item in items] == ["meme_race"]
        assert client.get(items[0]["reference_source_url"]).status_code == 200
        assert client.get(items[0]["preview_url"]).status_code == 200
        payload = config(recipe="meme", style="clay", prompt="柴犬鞋飞奔")
        response = client.post("/api/jobs", json=payload)
        assert response.status_code == 202
        job = finished(client, response.json()["id"])
        assert job["config"]["style"] == "clay"
        assert job["compiled_prompt"] == items[0]["preview_prompt"]
        assert "funny reaction picture" in job["compiled_prompt"]
        assert "matte polymer clay" in job["attempts"][0]["inference"]["workflow"]["109"]["inputs"]["text"]
        assert client.post("/api/jobs", json={**payload, "style": "unknown"}).status_code == 422


def test_creative_options_work_with_generation_and_report_existing_previews(project):
    previews = project / "output/style_previews"
    previews.mkdir(parents=True)
    Image.new("RGB", (64, 48)).save(previews / "felt.jpg")
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)) as client:
        catalog = client.get("/api/creative-options").json()
        styles = {entry["id"]: entry for entry in catalog["styles"]}
        assert {"original", "comic", "clay", "ink", "pixel", "poster", "felt", "popup"} <= styles.keys()
        assert "preview_url" not in styles["original"]
        assert client.get(styles["felt"]["preview_url"]).status_code == 200
        instructions = {entry["id"]: entry for entry in catalog["instructions"]}
        assert len(instructions) >= 15
        template = instructions["meme_cast"]
        response = client.post("/api/jobs", json=config(
            recipe=template["recipe"], style="felt", prompt=template["prompt"],
        ))
        assert response.status_code == 202
        job = finished(client, response.json()["id"])
        assert job["config"]["style"] == "felt"
        assert "needle-felted wool" in job["compiled_prompt"]
        assert template["prompt"] in job["compiled_prompt"]


def test_background_lock_preserves_scene_even_with_isolation_enabled(project):
    vision = StubVision()
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=vision, backend_probe=online)) as client:
        job_id = client.post("/api/jobs", json=config(isolate_subject=True, lock_background=True)).json()["id"]
        job = finished(client, job_id)
        assert job["status"] == "success"
        assert vision.prepared == [("structure", False, "reference_a"), ("structure", True, "mask_a_only"), ("appearance", True, "reference_b")]
        assert "original structure scene" in job["preprocessing_provenance"]["a"]["metadata"]["background_lock_strategy"]
        assert job["preprocessing"]["mask_a_url"]
        assert job["candidates"][0]["metrics"]["background_locked"] is True
        assert job["candidates"][0]["provenance"]["source_mask"]


def test_inference_failure_does_not_poison_next_job(project):
    runner = StubRunner(fail_first=True)
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(), backend_probe=online)) as client:
        first = client.post("/api/jobs", json=config()).json()["id"]
        second = client.post("/api/jobs", json=config(seed=100)).json()["id"]
        failed, success = finished(client, first), finished(client, second)
        assert failed["status"] == "failed" and "mock CUDA" in failed["error"]
        assert failed["attempts"][0]["status"] == "failed"
        assert success["status"] == "success" and runner.peak_active == 1
        assert "mock CUDA" in (project / "data/jobs" / first / "debug.log").read_text()


def test_cooperative_cancel_keeps_current_candidate_and_skips_queue(project):
    gate = threading.Event()
    runner = StubRunner(gate=gate)
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(), backend_probe=online)) as client:
        first = client.post("/api/jobs", json=config(candidates=3)).json()["id"]
        wait_for(lambda: len(runner.calls) == 1)
        second = client.post("/api/jobs", json=config(seed=200)).json()["id"]
        assert client.post(f"/api/jobs/{second}/cancel").json()["status"] == "cancelled"
        cancelling = client.post(f"/api/jobs/{first}/cancel").json()
        assert cancelling["status"] == "running" and cancelling["cancel_requested"] is True
        gate.set()
        cancelled = finished(client, first)
        assert cancelled["status"] == "cancelled" and len(cancelled["candidates"]) == 1
        assert runner.calls == [42] and runner.peak_active == 1


def test_two_concurrent_jobs_are_serialized(project):
    runner = StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(), backend_probe=online)) as client:
        first = client.post("/api/jobs", json=config(candidates=2)).json()["id"]
        second = client.post("/api/jobs", json=config(candidates=2, seed=90)).json()["id"]
        assert finished(client, first)["status"] == "success"
        assert finished(client, second)["status"] == "success"
        assert runner.calls == [42, 43, 90, 91] and runner.peak_active == 1


def test_restart_marks_previous_running_job_failed(project):
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)) as client:
        job_id = client.post("/api/jobs", json=config()).json()["id"]
        assert finished(client, job_id)["status"] == "success"
    path = project / "data/jobs" / job_id / "job.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(status="running", stage="candidate_1_inference")
    path.write_text(json.dumps(record), encoding="utf-8")
    runner = StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(), backend_probe=online)) as client:
        recovered = client.get(f"/api/jobs/{job_id}").json()
        assert recovered["status"] == "failed" and recovered["stage"] == "interrupted_restart"
        assert runner.calls == [] and len(recovered["candidates"]) == 1


def test_composition_failure_keeps_raw_generated_image(project):
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(fail_finish=True), backend_probe=online)) as client:
        job_id = client.post("/api/jobs", json=config()).json()["id"]
        job = finished(client, job_id)
        assert job["status"] == "failed" and len(job["candidates"]) == 1
        assert client.get(job["candidates"][0]["raw_url"]).status_code == 200
        assert "mock composition failure" in job["error"]


def test_process_lock_rejects_a_second_queue_owner(project):
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)):
        with pytest.raises(RuntimeError, match="one worker"):
            with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(), backend_probe=online)):
                pass


def test_region_request_rejects_invalid_coordinates_and_nonfinite_numbers(project):
    runner = StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(), backend_probe=online)) as client:
        invalid_edits = [[], [[0, 0, 1]], [[0, 0, 1, 1, 1]], [[0.5, 0, 0.5, 1]],
                         [[0, 0.8, 1, 0.2]], [[-0.1, 0, 1, 1]], [[0, 0, 1.1, 1]],
                         [[False, 0, 1, 1]], [["0", 0, 1, 1]], [[0, 0, 1, 1]] * 13, None]
        for regions in invalid_edits:
            response = client.post("/api/jobs", json=config(edit_mode="region", edit_regions=regions))
            assert response.status_code == 422, (regions, response.text)
        for value in (float("nan"), float("inf"), -float("inf")):
            payload = config(edit_mode="region", edit_regions=[[0, 0, value, 1]])
            response = client.post("/api/jobs", content=json.dumps(payload), headers={"Content-Type": "application/json"})
            assert response.status_code == 422
            assert response.json()["detail"]
        assert client.post("/api/jobs", json=config(edit_mode="region", edit_regions=[[0, 0, 1, 1]], protect_regions=[[0, True, 1, 1]])).status_code == 422
        assert runner.calls == [] and client.get("/api/jobs").json() == []


@pytest.mark.parametrize("lock_background", [False, True])
def test_region_without_segmentation_preserves_outside_and_skips_union(project, lock_background):
    vision, runner = StubVision(available=False), StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=vision, backend_probe=online)) as client:
        response = client.post("/api/jobs", json=config(edit_mode="region", edit_regions=[[0.15, 0.15, 0.85, 0.85]], lock_background=lock_background))
        assert response.status_code == 202
        job = finished(client, response.json()["id"])
        assert job["status"] == "success", job.get("error")
        assert vision.prepared == [("structure", False, "reference_a"), ("appearance", False, "reference_b")]
        assert vision.finish_locks == [False]
        assert job["region_control"]["subject_mask_applied"] is False
        assert job["region_control"]["union_background_lock_applied"] is False
        assert "segmentation unavailable" in job["region_control"]["foreground_restriction"]
        assert client.get(job["preprocessing"]["edit_mask_url"]).status_code == 200
        assert runner.workflows[0]["111"]["inputs"]["resolution_steps"] == 16
        region_preprocessing = job["preprocessing_provenance"]["regions"]
        assert runner.masks[0] == project / region_preprocessing["latent_mask_path"]
        assert runner.masks[0] != project / region_preprocessing["mask_path"]
        candidate = job["candidates"][0]
        assert candidate["score"] is None and candidate["metrics"]["region_locked"] is True
        assert candidate["metrics"]["changed_locked_background_pixels"] == 0
        assert candidate["metrics"]["generated_size"] == region_preprocessing["metadata"]["inference_size"]
        assert candidate["raw_url"] != candidate["url"]
        image = Image.open(io.BytesIO(client.get(candidate["url"]).content))
        assert image.getpixel((0, 0)) == (0, 0, 255)
        assert image.getpixel((32, 24)) == (200, 70, 50)


def test_local_detail_uses_crop_and_keeps_full_canvas(project):
    runner = StubRunner()
    app = create_app(project, inference_runner=runner, vision_module=StubVision(available=False), backend_probe=online)
    with TestClient(app) as client:
        assert client.post("/api/jobs", json=config(region_detail=True)).status_code == 422
        accepted = client.post("/api/jobs", json=config(
            edit_mode="region", region_detail=True, edit_regions=[[.36, .34, .62, .62]],
        ))
        assert accepted.status_code == 202
        job = finished(client, accepted.json()["id"])
        assert job["status"] == "success", job.get("error")
        assert job["region_control"]["mode"] == "detail"
        assert job["region_control"]["metadata"]["sampling_density_gain"] > 1
        assert runner.masks[0].name == "edit_mask_detail_latent.png"
        candidate = job["candidates"][0]
        assert candidate["metrics"]["changed_locked_background_pixels"] == 0
        assert candidate["raw_url"] != candidate["raw_window_url"]
        for key in ("url", "raw_url"):
            response = client.get(candidate[key])
            assert response.status_code == 200
            assert Image.open(io.BytesIO(response.content)).size == (64, 48)


def test_region_intersects_subject_preserves_a_scene_and_isolates_only_b(project):
    vision, runner = StubVision(), StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=vision, backend_probe=online)) as client:
        response = client.post("/api/jobs", json=config(edit_mode="region", edit_regions=[[0, 0, 1, 1]], protect_regions=[[0.4, 0.3, 0.6, 0.7]], isolate_subject=True, lock_background=True))
        job = finished(client, response.json()["id"])
        assert job["status"] == "success", job.get("error")
        assert vision.prepared == [("structure", False, "reference_a"), ("structure", True, "region_mask_a_only"), ("appearance", True, "reference_b")]
        assert vision.finish_locks == [False]
        assert job["region_control"]["subject_mask_applied"] is True
        mask = Image.open(project / job["preprocessing_provenance"]["regions"]["mask_path"]).convert("L")
        assert mask.getpixel((1, 1)) == 0  # Outside the extracted subject.
        assert mask.getpixel((32, 24)) == 0  # Explicit protected hole.
        assert mask.getpixel((15, 15)) > 128
        assert client.get(job["preprocessing"]["mask_a_url"]).status_code == 200
        final = Image.open(io.BytesIO(client.get(job["candidates"][0]["url"]).content))
        assert final.getpixel((32, 24)) == (0, 0, 255)
        assert final.getpixel((1, 1)) == (0, 0, 255)


def test_region_isolation_requires_model_but_empty_effective_mask_never_runs_gpu(project):
    runner = StubRunner()
    with TestClient(create_app(project, inference_runner=runner, vision_module=StubVision(False), backend_probe=online)) as client:
        base = config(edit_mode="region", edit_regions=[[0.2, 0.2, 0.8, 0.8]])
        assert client.post("/api/jobs", json={**base, "isolate_subject": True}).status_code == 503
        for updates in ({"protect_regions": [[0, 0, 1, 1]]}, {"edit_regions": [[0.001, 0.001, 0.002, 0.002]]}):
            response = client.post("/api/jobs", json={**base, **updates})
            assert response.status_code == 202
            job = finished(client, response.json()["id"])
            assert job["status"] == "failed" and "修改区" in job["error"]
            assert job["attempts"] == []
        assert runner.calls == []


def test_region_composition_failure_preserves_raw_candidate(project, monkeypatch):
    def failed_region(*args, **kwargs):
        raise ValueError("mock region alignment failure")
    monkeypatch.setattr("studio.engine.finish_region", failed_region)
    with TestClient(create_app(project, inference_runner=StubRunner(), vision_module=StubVision(False), backend_probe=online)) as client:
        response = client.post("/api/jobs", json=config(edit_mode="region", edit_regions=[[0.1, 0.1, 0.9, 0.9]]))
        job = finished(client, response.json()["id"])
        assert job["status"] == "failed" and "mock region alignment" in job["error"]
        assert len(job["candidates"]) == 1
        assert client.get(job["candidates"][0]["raw_url"]).status_code == 200


def test_comfy_runner_adds_mask_upload_without_replacing_reference_bindings(project, monkeypatch):
    directory = project / "data" / "runner"
    record_dir = directory / "runs" / "one"
    record_dir.mkdir(parents=True)
    mask_path = directory / "edit mask.png"
    output_path = record_dir / "result.png"
    Image.new("RGB", (64, 48), "white").save(mask_path)
    Image.new("RGB", (64, 48), "red").save(output_path)
    (record_dir / "run.json").write_text(json.dumps({"status": "success", "outputs": [{"local_path": str(output_path)}]}))
    captured = []
    class Process:
        returncode = 0
        async def wait(self):
            return 0
    async def fake_subprocess(*command, **kwargs):
        captured.extend(command)
        return Process()
    monkeypatch.setattr("studio.engine.asyncio.create_subprocess_exec", fake_subprocess)
    workflow = build_region_graph(build_api_workflow())
    result = asyncio.run(ComfyRunner(project)(workflow, directory, project / "input/a.png", project / "input/b.png", "http://localhost:8188", mask_path=mask_path))
    assert captured[captured.index("--upload") + 1] == f"201={mask_path.resolve()}"
    assert captured[captured.index("--image-a") + 1] == f"76={project / 'input/a.png'}"
    assert captured[captured.index("--image-b") + 1] == f"81={project / 'input/b.png'}"
    assert result["images"] == [output_path.resolve()]
