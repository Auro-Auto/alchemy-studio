"""CPU subject extraction and explicitly masked compositing for Alchemy Studio.

U2Netp preprocessing follows the documented rembg session algorithm:
https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2netp.py
https://github.com/danielgatis/rembg/blob/main/rembg/sessions/base.py
The implementation below is independent; no rembg package is required. U2Net is
Apache-2.0 (https://github.com/xuebinqin/U-2-Net), rembg is MIT. Model provisioning
and its provenance are handled only by scripts/prepare_segmentation.py.

Scores compare mask geometry and foreground color distributions, not semantic
quality, identity, aesthetics, or duplicate faces.
"""
from __future__ import annotations

from collections import deque
from functools import lru_cache
import hashlib
import importlib.util
from pathlib import Path
import threading

import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / "models" / "segmentation" / "u2netp.onnx"
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
MODEL_MD5 = "8e83ca70e441ab06c318d82300c84806"
MODEL_BYTES = 4_574_861
MAX_IMAGE_PIXELS = 20_000_000
NEUTRAL = (255, 255, 255)
SCORE_SCOPE = (
    "Mask-geometry heuristic only: continuity, border contact and foreground area. "
    "Does not assess semantic fusion, identity, aesthetics or duplicate faces."
)
REFERENCE_SCORE_SCOPE = (
    "Reference heuristic only: mask geometry, normalized A silhouette overlap and "
    "B foreground HSV palette overlap. Does not assess semantic fusion, identity, "
    "aesthetics or duplicate faces; material and lighting changes can lower palette similarity."
)
REFERENCE_WEIGHTS = {"shape": 0.45, "structure": 0.25, "palette": 0.30}
_RUN_LOCK = threading.Lock()


class SegmentationUnavailable(RuntimeError):
    """A local model/dependency is unavailable; no network fallback is attempted."""


class SegmentationError(ValueError):
    """An unreliable mask must not be presented as successful subject extraction."""


@lru_cache(maxsize=4)
def _verified_model(path: str, size: int, mtime_ns: int) -> bool:
    del mtime_ns
    if size != MODEL_BYTES:
        return False
    return hashlib.md5(Path(path).read_bytes()).hexdigest() == MODEL_MD5


def segmentation_available() -> bool:
    """Check installed CPU runtime and verified model, without loading/downloading it."""
    try:
        stat = MODEL_PATH.stat()
        return (
            importlib.util.find_spec("onnxruntime") is not None
            and _verified_model(str(MODEL_PATH), stat.st_size, stat.st_mtime_ns)
        )
    except (OSError, ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def _session():
    if not segmentation_available():
        raise SegmentationUnavailable(
            "CPU segmentation is unavailable. Install onnxruntime and run "
            "python scripts/prepare_segmentation.py --download before starting jobs."
        )
    try:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        return ort.InferenceSession(
            str(MODEL_PATH), sess_options=options, providers=["CPUExecutionProvider"]
        )
    except (ImportError, OSError, RuntimeError) as exc:
        raise SegmentationUnavailable(f"Unable to load local U2Netp: {exc}") from exc


def _open_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds the 20 megapixel CPU processing limit.")
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        return ImageOps.exif_transpose(image).convert("RGBA" if has_alpha else "RGB")


def _rgb(image: Image.Image) -> Image.Image:
    if "A" in image.getbands():
        background = Image.new("RGB", image.size, NEUTRAL)
        background.paste(image.convert("RGB"), mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def _preprocess(image: Image.Image) -> np.ndarray:
    array = np.asarray(_rgb(image).resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
    array /= max(float(array.max()), 1e-6)
    array -= np.array((0.485, 0.456, 0.406), dtype=np.float32)
    array /= np.array((0.229, 0.224, 0.225), dtype=np.float32)
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None])


def _prediction_mask(prediction: np.ndarray, size: tuple[int, int]) -> Image.Image:
    pred = np.asarray(prediction, dtype=np.float32).squeeze()
    if pred.ndim != 2 or not np.isfinite(pred).all():
        raise SegmentationError("Segmentation returned an invalid or non-finite mask.")
    low, high = float(pred.min()), float(pred.max())
    if high - low < 1e-6:
        raise SegmentationError("Segmentation has no reliable foreground/background contrast.")
    pred = np.clip((pred - low) / (high - low), 0.0, 1.0)
    mask = Image.fromarray(np.rint(pred * 255).astype(np.uint8))
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    # Suppress residual background probabilities; retain a smooth transition.
    return mask.point([round(np.clip((v / 255 - 0.05) / 0.90, 0, 1) * 255) for v in range(256)])


def _mask_summary(mask: Image.Image) -> dict:
    sample = mask.copy()
    sample.thumbnail((256, 256), Image.Resampling.BOX)
    values = np.asarray(sample)
    foreground = values >= 128
    area = int(foreground.sum())
    ys, xs = np.where(foreground)
    bbox = None if not area else [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    return {"foreground_fraction": float(area / foreground.size), "sample_bbox": bbox}


def _validate_mask(mask: Image.Image) -> dict:
    summary = _mask_summary(mask)
    area = summary["foreground_fraction"]
    if area < 0.003:
        raise SegmentationError("No usable foreground was found (less than 0.3% of the image).")
    if area > 0.97:
        raise SegmentationError("Foreground covers more than 97%; background cannot be separated reliably.")
    return summary


def _extract_mask(image: Image.Image) -> tuple[Image.Image, dict]:
    if "A" in image.getbands() and image.getchannel("A").getextrema()[0] < 255:
        mask = image.getchannel("A").copy()
        return mask, {"method": "source_alpha", **_validate_mask(mask)}
    # The lock bounds simultaneous activation memory on machines shared with FLUX.
    with _RUN_LOCK:
        session = _session()
        output_name = session.get_outputs()[0].name
        output = session.run([output_name], {session.get_inputs()[0].name: _preprocess(image)})[0]
    mask = _prediction_mask(output, image.size)
    return mask, {
        "method": "u2netp_onnx_cpu", "model_md5": MODEL_MD5,
        "model_path": str(MODEL_PATH), "inference_size": [320, 320],
        **_validate_mask(mask),
    }


def _role_name(role: str) -> str:
    value = role.lower()
    if value in {"a", "structure", "image_a", "structure_a"}:
        return "a"
    if value in {"b", "appearance", "image_b", "appearance_b"}:
        return "b"
    raise ValueError("role must identify structure A or appearance B")


def prepare_reference(source: Path, output_dir: Path, role: str, isolate: bool) -> dict:
    """Preserve A's canvas, optionally crop B, and supply a neutral RGB reference."""
    source, output_dir = Path(source), Path(output_dir)
    name = _role_name(role)
    image = _open_image(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"reference_{name}.png"
    metadata = {
        "role": name, "source_size": list(image.size), "isolation_applied": bool(isolate),
        "neutral_rgb": list(NEUTRAL), "crop_box": None,
    }
    mask_path = None
    reference = _rgb(image)
    if isolate:
        mask, segmentation = _extract_mask(image)
        metadata.update(segmentation)
        mask_path = output_dir / f"mask_{name}.png"
        # Masks always retain source geometry, including B, for traceability.
        mask.save(mask_path)
        reference = Image.composite(image.convert("RGB"), Image.new("RGB", image.size, NEUTRAL), mask)
        if name == "b":
            bbox = mask.point(lambda v: 255 if v >= 128 else 0).getbbox()
            if bbox is None:
                raise SegmentationError("The appearance reference has no usable foreground.")
            x0, y0, x1, y1 = bbox
            padding = max(4, round(max(x1 - x0, y1 - y0) * 0.10))
            crop = (max(0, x0 - padding), max(0, y0 - padding), min(image.width, x1 + padding), min(image.height, y1 + padding))
            reference = reference.crop(crop)
            metadata.update(crop_box=list(crop), crop_padding_fraction=0.10)
    reference.save(image_path)
    metadata["output_size"] = list(reference.size)
    return {"image_path": image_path, "mask_path": mask_path, "metadata": metadata}


def _component_sizes(binary: np.ndarray) -> list[int]:
    """Eight-connected components on a bounded (<=256 px) scoring mask."""
    remaining = binary.copy()
    height, width = remaining.shape
    sizes = []
    for y, x in zip(*np.where(remaining)):
        if not remaining[y, x]:
            continue
        remaining[y, x] = False
        queue = deque([(int(y), int(x))])
        size = 0
        while queue:
            cy, cx = queue.popleft()
            size += 1
            for ny in range(max(0, cy - 1), min(height, cy + 2)):
                for nx in range(max(0, cx - 1), min(width, cx + 2)):
                    if remaining[ny, nx]:
                        remaining[ny, nx] = False
                        queue.append((ny, nx))
        sizes.append(size)
    return sorted(sizes, reverse=True)


def _shape_score(mask: Image.Image) -> tuple[float, dict]:
    sample = mask.copy()
    sample.thumbnail((256, 256), Image.Resampling.BOX)
    binary = np.asarray(sample) >= 128
    sizes = _component_sizes(binary)
    pixels = int(binary.sum())
    fraction = pixels / binary.size
    continuity = sizes[0] / pixels if pixels else 0.0
    # Ignore single-pixel dust in reported component count, not in continuity.
    components = sum(s >= max(2, pixels * 0.005) for s in sizes)
    edge = np.zeros_like(binary)
    band = max(1, round(min(binary.shape) * 0.02))
    edge[:band] = edge[-band:] = True
    edge[:, :band] = edge[:, -band:] = True
    border_fraction = float((binary & edge).sum() / max(1, pixels))
    crop_safety = max(0.0, 1.0 - 5.0 * border_fraction)
    # A broad working range is intentional; this is not an aesthetic score.
    area_reasonableness = min(1.0, fraction / 0.10, max(0.0, (0.97 - fraction) / 0.12))
    score = 100 * (0.45 * continuity + 0.30 * crop_safety + 0.25 * area_reasonableness)
    if not pixels:
        score = 0.0
    return round(float(np.clip(score, 0, 100)), 2), {
        "score_kind": "mask_geometry_v1", "score_scope": SCORE_SCOPE,
        "continuity": round(continuity, 6), "component_count": components,
        "foreground_fraction": round(fraction, 6),
        "border_contact_fraction": round(border_fraction, 6),
        "crop_safety": round(crop_safety, 6),
        "area_reasonableness": round(area_reasonableness, 6),
        "score_weights": {"continuity": 0.45, "crop_safety": 0.30, "area_reasonableness": 0.25},
    }


def _normalized_silhouette(mask: Image.Image) -> np.ndarray:
    """128-square, centered, uniformly scaled foreground; translation/scale free."""
    binary = mask.point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if bbox is None:
        raise SegmentationError("No foreground is available for silhouette comparison.")
    cropped = binary.crop(bbox)
    scale = 128 / max(cropped.size)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    normalized = Image.new("L", (128, 128), 0)
    normalized.paste(
        cropped.resize(size, Image.Resampling.NEAREST),
        ((128 - size[0]) // 2, (128 - size[1]) // 2),
    )
    return np.asarray(normalized) >= 128


def _foreground_palette(image: Image.Image, mask: Image.Image) -> np.ndarray:
    """Bounded HSV histogram with separate black/gray/white bins.

    Nearest-neighbor sampling prevents RGB background colors bleeding into the
    palette during resizing. A one-pixel erosion discards uncertain boundaries;
    very thin subjects fall back to the un-eroded binary foreground.
    """
    if mask.size != image.size:
        raise SegmentationError("Palette mask size does not match the reference image.")
    binary = mask.point(lambda value: 255 if value >= 128 else 0)
    bbox = binary.getbbox()
    if bbox is None:
        raise SegmentationError("No foreground is available for palette comparison.")
    pixels = image.convert("RGB").crop(bbox)
    sample_mask = binary.crop(bbox)
    pixels.thumbnail((256, 256), Image.Resampling.NEAREST)
    sample_mask = sample_mask.resize(pixels.size, Image.Resampling.NEAREST)
    eroded = np.asarray(sample_mask.filter(ImageFilter.MinFilter(3))) >= 128
    selected = eroded if eroded.sum() >= 8 else np.asarray(sample_mask) >= 128
    hsv = np.asarray(pixels.convert("HSV"), dtype=np.float32)[selected] / 255.0
    if len(hsv) == 0:
        raise SegmentationError("No usable foreground colors are available.")
    hue, saturation, value = hsv.T
    # Near-black colors are grouped by value even if their numerical hue is noisy.
    neutral = (saturation < 0.15) | (value < 0.15)
    indices = np.empty(len(hsv), dtype=np.int32)
    indices[neutral] = np.where(value[neutral] < 0.22, 0, np.where(value[neutral] < 0.75, 1, 2))
    chromatic = ~neutral
    hbin = np.minimum((hue[chromatic] * 12).astype(np.int32), 11)
    sbin = (saturation[chromatic] >= 0.60).astype(np.int32)
    vbin = np.minimum((value[chromatic] * 3).astype(np.int32), 2)
    indices[chromatic] = 3 + hbin * 6 + sbin * 3 + vbin
    histogram = np.bincount(indices, minlength=75).astype(np.float64)
    return histogram / histogram.sum()


def _reference_mask(image: Image.Image, mask_path: Path | None) -> tuple[Image.Image, dict]:
    if mask_path is None:
        return _extract_mask(image)
    with Image.open(mask_path) as opened:
        mask = opened.convert("L")
    if mask.size != image.size:
        raise SegmentationError("Source mask size does not match the EXIF-oriented original image canvas.")
    return mask, {"method": "provided_mask", **_validate_mask(mask)}


def _file_signature(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return str(resolved), stat.st_size, stat.st_mtime_ns


@lru_cache(maxsize=8)
def _cached_reference_features(image_signature: tuple, mask_signature: tuple | None):
    """Cache only tiny descriptors, never full image arrays or model activations."""
    image = _open_image(Path(image_signature[0]))
    mask_path = None if mask_signature is None else Path(mask_signature[0])
    mask, metadata = _reference_mask(image, mask_path)
    return _normalized_silhouette(mask), _foreground_palette(image, mask), metadata


def _reference_features(image_path: Path, mask_path: Path | None):
    return _cached_reference_features(
        _file_signature(Path(image_path)),
        None if mask_path is None else _file_signature(Path(mask_path)),
    )


def _recommendation_score(
    candidate: Image.Image, candidate_mask: Image.Image, shape_score: float,
    original_a: Path, source_mask: Path | None, appearance_reference: Path | None,
    appearance_mask: Path | None, *, prepared_source_mask: Image.Image | None = None,
) -> tuple[float, dict]:
    """Optional recommendation stage: any reference failure falls back to shape."""
    metrics = {
        "shape_score": shape_score, "reference_score_available": False,
        "structure_similarity": None, "palette_similarity": None,
    }
    if appearance_reference is None:
        metrics["reference_score_fallback_reason"] = "Appearance reference B was not provided; retaining the shape-only score."
        return shape_score, metrics
    try:
        if prepared_source_mask is None:
            source_silhouette, _, source_metadata = _reference_features(original_a, source_mask)
        else:
            source_silhouette = _normalized_silhouette(prepared_source_mask)
            source_metadata = {"method": "reused_background_lock_mask"}
        candidate_silhouette = _normalized_silhouette(candidate_mask)
        union = int(np.count_nonzero(source_silhouette | candidate_silhouette))
        similarity = float(np.count_nonzero(source_silhouette & candidate_silhouette) / max(1, union))
        metrics["structure_similarity"] = round(similarity, 6)
        _, reference_palette, appearance_metadata = _reference_features(appearance_reference, appearance_mask)
        candidate_palette = _foreground_palette(candidate, candidate_mask)
        palette_similarity = float(np.minimum(reference_palette, candidate_palette).sum())
        components = {"shape": shape_score, "structure": similarity * 100, "palette": palette_similarity * 100}
        score = sum(REFERENCE_WEIGHTS[key] * value for key, value in components.items())
        metrics.update(
            score_kind="reference_heuristic_v2", score_scope=REFERENCE_SCORE_SCOPE,
            reference_score_available=True, palette_similarity=round(palette_similarity, 6),
            score_components={key: round(value, 4) for key, value in components.items()},
            score_weights=dict(REFERENCE_WEIGHTS),
            structure_comparison="foreground_bbox_uniform_fit_centered_128px_binary_iou",
            palette_comparison="foreground_hsv_75bin_histogram_intersection",
            palette_bins={"achromatic": ["black", "gray", "white"], "hue": 12, "saturation": 2, "value": 3},
            palette_sample_max_edge=256, reference_a_segmentation=source_metadata,
            reference_b_segmentation=appearance_metadata,
        )
        return round(float(np.clip(score, 0, 100)), 2), metrics
    except Exception as exc:
        # A completed image must survive missing/corrupt reference masks, model
        # unavailability and optional descriptor failures. Lock errors live outside.
        metrics["reference_score_fallback_reason"] = f"{type(exc).__name__}: {exc}"
        return shape_score, metrics


def _fit_to_canvas(image: Image.Image, mask: Image.Image, size: tuple[int, int]):
    """Uniform resize plus letterboxing; never stretch/crop a generated subject."""
    scale = min(size[0] / image.width, size[1] / image.height)
    target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    offset = ((size[0] - target[0]) // 2, (size[1] - target[1]) // 2)
    fitted_image = Image.new("RGB", size, NEUTRAL)
    fitted_mask = Image.new("L", size, 0)
    pixels = image.convert("RGB")
    if "A" in image.getbands():
        # Discard undefined RGB values under fully transparent pixels. Keep
        # unassociated edge colors so the final soft mask applies alpha once.
        visible = image.getchannel("A").point(lambda value: 255 if value else 0)
        pixels = Image.composite(pixels, Image.new("RGB", image.size, NEUTRAL), visible)
    fitted_image.paste(pixels.resize(target, Image.Resampling.LANCZOS), offset)
    fitted_mask.paste(mask.resize(target, Image.Resampling.LANCZOS), offset)
    coverage = Image.new("L", size, 0)
    coverage.paste(255, (*offset, offset[0] + target[0], offset[1] + target[1]))
    return fitted_image, fitted_mask, coverage, {"scale": scale, "offset_xy": list(offset), "fitted_size": list(target)}


def finish_candidate(
    generated: Path, original_a: Path, output_dir: Path, lock_background: bool,
    source_mask: Path | None, *, appearance_reference: Path | None = None,
    appearance_mask: Path | None = None,
) -> dict:
    """Save raw output and optionally composite over A using auditable soft masks.

    The editable region is the UNION of original and generated foregrounds.
    Including the original silhouette removes remnants of the old object; the
    generated silhouette preserves additions such as ears. The original photo
    is copied exactly outside the saved composite mask. Generation outside A's
    original subject is an explicit, recorded expansion of the editable region.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate = _open_image(Path(generated))
    raw_path = output_dir / "raw.png"
    candidate.save(raw_path)
    metrics = {"background_locked": False, "raw_path": str(raw_path), "score_scope": SCORE_SCOPE}
    try:
        candidate_mask, extraction = _extract_mask(candidate)
    except (SegmentationUnavailable, SegmentationError) as exc:
        if lock_background:
            raise
        metrics.update(score_available=False, segmentation_error=str(exc))
        return {"image_path": raw_path, "raw_path": raw_path, "score": None, "metrics": metrics}
    score, shape = _shape_score(candidate_mask)
    metrics.update(shape, segmentation=extraction, score_available=True)
    candidate_mask_path = output_dir / "generated_mask.png"
    candidate_mask.save(candidate_mask_path)
    metrics["generated_mask_path"] = str(candidate_mask_path)
    if not lock_background:
        score, recommendation = _recommendation_score(
            candidate, candidate_mask, score, original_a, source_mask,
            appearance_reference, appearance_mask,
        )
        metrics.update(recommendation)
        return {"image_path": raw_path, "raw_path": raw_path, "mask_path": candidate_mask_path, "score": score, "metrics": metrics}

    original = _open_image(Path(original_a))
    background = _rgb(original)
    original_mask, source_extraction = _reference_mask(original, source_mask)
    fitted, foreground, coverage, alignment = _fit_to_canvas(candidate, candidate_mask, original.size)
    # The source object cannot be erased in letterbox pixels with no generated content.
    uncovered = ImageChops.subtract(original_mask, coverage)
    if uncovered.getextrema()[1] > 32:
        raise SegmentationError("Generated aspect ratio leaves part of the original subject outside its canvas; background lock refused.")
    a = np.asarray(original_mask.resize((256, 256), Image.Resampling.BOX)) >= 128
    b = np.asarray(foreground.resize((256, 256), Image.Resampling.BOX)) >= 128
    overlap = float((a & b).sum() / max(1, min(a.sum(), b.sum())))
    if overlap < 0.10:
        raise SegmentationError("Generated subject is displaced from the original (less than 10% overlap); background lock refused.")
    union = ImageChops.lighter(original_mask, foreground)
    feather_radius = max(0.5, min(3.0, min(original.size) / 512))
    # Max preserves opaque subject pixels; Gaussian adds only a narrow soft edge.
    composite_mask = ImageChops.lighter(union, union.filter(ImageFilter.GaussianBlur(feather_radius)))
    composite_mask = ImageChops.darker(composite_mask, coverage)
    if float((np.asarray(composite_mask) > 0).mean()) > 0.97:
        raise SegmentationError("Combined edit region covers more than 97%; meaningful background lock is unavailable.")
    result = Image.composite(fitted, background, composite_mask)
    composite_path = output_dir / "composite.png"
    composite_mask_path = output_dir / "composite_mask.png"
    result.save(composite_path)
    composite_mask.save(composite_mask_path)
    # Validate the claim from the actual saved-pixel computation, not just the formula.
    difference = ImageChops.difference(result, background)
    changed = np.asarray(difference).any(axis=2)
    locked = np.asarray(composite_mask) == 0
    background_changed = int(np.count_nonzero(changed & locked))
    if background_changed:
        raise RuntimeError("Internal compositing error: locked background pixels changed.")
    mask_array = np.asarray(composite_mask)
    original_array = np.asarray(original_mask)
    metrics.update(
        background_locked=True, original_size=list(original.size), generated_size=list(candidate.size),
        alignment=alignment, source_segmentation=source_extraction,
        composite_path=str(composite_path), composite_mask_path=str(composite_mask_path),
        composite_policy="union_original_and_generated_foreground_with_soft_edge",
        feather_radius_px=feather_radius, subject_overlap=round(overlap, 6),
        editable_fraction=round(float((mask_array > 0).mean()), 6),
        expanded_edit_fraction=round(float(((mask_array > 0) & (original_array == 0)).mean()), 6),
        locked_background_pixels=int(locked.sum()), changed_locked_background_pixels=background_changed,
        transparency_policy="original alpha composited on white before RGB background lock",
    )
    score, recommendation = _recommendation_score(
        candidate, candidate_mask, score, original_a, source_mask,
        appearance_reference, appearance_mask, prepared_source_mask=original_mask,
    )
    metrics.update(recommendation)
    return {"image_path": composite_path, "raw_path": raw_path, "mask_path": composite_mask_path, "score": score, "metrics": metrics}
