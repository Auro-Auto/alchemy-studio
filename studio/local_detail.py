"""Spend the existing inference pixel budget on a window around image A's edits."""
from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from PIL import Image

from .regions import _inference_size, _latent_mask, _rgb, finish_region


def _fit_axis(start, end, minimum, limit):
    """Fit a centered interval while preserving available context at image edges."""
    length = min(limit, max(end - start, minimum))
    left = max(0, min(limit - length, (start + end - length) // 2))
    return left, left + length


def _crop_window(edit_box, subject_box, size):
    left, top, right, bottom = edit_box
    if subject_box:
        sx0, sy0, sx1, sy1 = subject_box
        left, top = min(left, sx0), min(top, sy0)
        right, bottom = max(right, sx1), max(bottom, sy1)
    # An available silhouette supplies context such as a handle or cup lid.
    # Without one, keep a wider border around the annotated editing area.
    padding = .08 if subject_box else .35
    pad_x, pad_y = math.ceil((right - left) * padding), math.ceil((bottom - top) * padding)
    width, height = size
    left, right = _fit_axis(left - pad_x, right + pad_x, math.ceil(width * .25), width)
    top, bottom = _fit_axis(top - pad_y, bottom + pad_y, math.ceil(height * .25), height)
    # Add context along the short dimension of a thin window when the image allows.
    left, right = _fit_axis(left, right, math.ceil((bottom - top) / 3), width)
    top, bottom = _fit_axis(top, bottom, math.ceil((right - left) / 3), height)
    return left, top, right, bottom


def _read_mask(path, size):
    with Image.open(path) as image:
        if image.size != size:
            raise ValueError("局部高清蒙版尺寸与原图不一致。")
        return image.convert("L")


def _contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def prepare_detail(original_a, prepared_a, region_info, output_dir, megapixels):
    """Crop prepared A and rebuild its conservative latent mask in local coordinates."""
    with _rgb(Path(original_a)) as source:
        source_size = source.size
    if isinstance(prepared_a, Mapping):
        image_path = prepared_a["image_path"]
        subject_path = prepared_a.get("mask_path")
    else:
        image_path, subject_path = prepared_a, None

    hard = _read_mask(region_info["hard_mask_path"], source_size)
    soft = _read_mask(region_info["mask_path"], source_size)
    edit_box, soft_box = hard.getbbox(), soft.getbbox()
    if not edit_box or not soft_box:
        raise ValueError("局部高清需要有效的修改区域。")
    subject_box = None
    if subject_path:
        with _read_mask(subject_path, source_size) as subject:
            subject_box = subject.point([0] * 128 + [255] * 128).getbbox()
    crop_box = _crop_window(edit_box, subject_box, source_size)
    if not _contains(crop_box, soft_box):
        raise ValueError("局部窗口未覆盖完整的修改区域。")

    with _rgb(Path(image_path)) as prepared:
        if prepared.size != source_size:
            raise ValueError("局部高清要求结构参考图与原图使用同一画布。")
        cropped = prepared.crop(crop_box)
    hard_crop = np.asarray(hard.crop(crop_box))
    soft_crop = np.asarray(soft.crop(crop_box))
    if np.any((hard_crop == 0) & (soft_crop > 0)):
        raise ValueError("局部高清软蒙版越出了可编辑区域。")
    inference_size = _inference_size(cropped.size, megapixels)
    latent = _latent_mask(hard_crop, soft_crop, inference_size)
    full_inference_size = _inference_size(source_size, megapixels)
    density = math.prod(inference_size) / math.prod(cropped.size)
    full_density = math.prod(full_inference_size) / math.prod(source_size)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    crop_path = output_dir / "reference_a_detail.png"
    latent_path = output_dir / "edit_mask_detail_latent.png"
    cropped.save(crop_path)
    Image.fromarray(latent).convert("RGB").save(latent_path)
    return {
        "image_path": crop_path,
        "latent_mask_path": latent_path,
        "metadata": {
            "coordinate_system": "exif_oriented_source_A_pixels_xyxy",
            "crop_box": list(crop_box),
            "source_size": list(source_size),
            "crop_size": list(cropped.size),
            "inference_size": list(inference_size),
            "full_inference_size": list(full_inference_size),
            "latent_size": [latent.shape[1], latent.shape[0]],
            "active_latent_cells": int(np.count_nonzero(latent > 128)),
            "full_active_latent_cells": region_info.get("metadata", {}).get("active_latent_cells"),
            "sampling_density_gain": round(density / full_density, 6),
            "crop_area_fraction": round(math.prod(cropped.size) / math.prod(source_size), 6),
            "context_policy": "include_subject_bounds" if subject_box else "pad_edit_bounds",
            "subject_box": list(subject_box) if subject_box else None,
            "latent_mask_policy": "any_overlapping_protected_source_pixel_blocks_cell_otherwise_mean_soft_mask",
        },
    }


def finish_detail(generated, original_a, mask_path, detail_info, output_dir):
    """Map a raw crop to image A's canvas, then use the existing pixel restoration."""
    metadata = detail_info["metadata"]
    box = metadata["crop_box"]
    expected_size = tuple(metadata["inference_size"])
    output_dir = Path(output_dir)
    raw_canvas_path = output_dir / "raw_detail_canvas.png"
    with _rgb(Path(original_a)) as source, _rgb(Path(generated)) as candidate:
        source_size = source.size
        if list(source_size) != metadata["source_size"]:
            raise ValueError("局部高清的原图尺寸与裁剪记录不一致。")
        if (len(box) != 4 or any(type(value) is not int for value in box)
                or not (0 <= box[0] < box[2] <= source.width and 0 <= box[1] < box[3] <= source.height)):
            raise ValueError("局部高清的裁剪坐标无效。")
        if candidate.size != expected_size:
            raise ValueError("生成图尺寸与记录的局部推理画布不一致，无法对齐保护区。")
        mask = _read_mask(mask_path, source_size)
        mask_box = mask.getbbox()
        if not mask_box or not _contains(box, mask_box):
            raise ValueError("局部窗口未覆盖完整的修改区域。")
        size = (box[2] - box[0], box[3] - box[1])
        aligned = candidate.resize(size, Image.Resampling.LANCZOS)
        protected_crop = np.asarray(mask.crop(box)) == 0
        protected_count = int(protected_crop.sum())
        crop_mae = None
        if protected_count:
            delta = np.abs(np.asarray(aligned, dtype=np.int16) - np.asarray(source.crop(box), dtype=np.int16))
            crop_mae = round(float(delta[protected_crop].mean()), 6)
        # The raw canvas is for a spatially aligned before/after view. Its
        # ungenerated surroundings come from A and cannot count as model output.
        source.paste(aligned, (box[0], box[1]))
        output_dir.mkdir(parents=True, exist_ok=True)
        source.save(raw_canvas_path)

    result = finish_region(raw_canvas_path, Path(original_a), Path(mask_path), output_dir,
                           expected_size=source_size)
    result["raw_canvas_path"] = raw_canvas_path
    metrics = result["metrics"]
    metrics.update({
        "local_detail": True,
        "local_detail_crop_box": list(box),
        "local_detail_sampling_density_gain": metadata["sampling_density_gain"],
        "local_detail_active_latent_cells": metadata["active_latent_cells"],
        "local_detail_full_active_latent_cells": metadata["full_active_latent_cells"],
        "composite_policy": "local_detail_edit_mask_source_pixel_restoration",
        "raw_canvas_protected_mae_255": metrics["raw_protected_mae_255"],
        "raw_protected_mae_255": crop_mae,
        "raw_protected_scope": "crop_only",
        "raw_protected_measured_pixels": protected_count,
        "region_metric_scope": "Raw error is measured in the generated window. Final zero is from restoration, not the model.",
        "alignment": {
            "method": "resize_inference_window_and_paste_at_source_crop",
            "crop_box": list(box),
            "aspect_ratio_rounding_error": abs((expected_size[0] / expected_size[1]) / (size[0] / size[1]) - 1),
            "expected_inference_size": list(expected_size),
        },
        "generated_size": list(expected_size),
    })
    return result
