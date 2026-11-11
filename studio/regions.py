"""Explicit edit/protect regions shared by latent sampling and pixel restoration.

Coordinates belong to EXIF-oriented image A. White permits change. The final
restoration guarantee is deliberately separate from raw-model preservation.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from scripts.build_workflow import node


RESOLUTION_STEPS = 16
# Exact algorithm verified in the deployed ComfyUI commit (not decimal MP):
# https://github.com/Comfy-Org/ComfyUI/blob/bdb0816a292d221efeb6ec4ae41bcdff3f91fc86/comfy_extras/nodes_post_processing.py
COMFY_RESIZE_SOURCE = (
    "https://github.com/Comfy-Org/ComfyUI/blob/"
    "bdb0816a292d221efeb6ec4ae41bcdff3f91fc86/comfy_extras/nodes_post_processing.py"
)


def _inference_size(size, megapixels):
    if isinstance(megapixels, bool) or not isinstance(megapixels, (int, float)) or not math.isfinite(megapixels) or not .01 <= megapixels <= 16:
        raise ValueError("生成像素预算必须是 0.01 到 16 的有限数值。")
    width, height = size
    scale = math.sqrt(megapixels * 1024 * 1024 / (width * height))
    canvas = (
        round(width * scale / RESOLUTION_STEPS) * RESOLUTION_STEPS,
        round(height * scale / RESOLUTION_STEPS) * RESOLUTION_STEPS,
    )
    if min(canvas) < RESOLUTION_STEPS:
        raise ValueError("原图长宽比过于极端，模型画布有一边不足 16 像素。")
    return canvas


def _rectangle_sums(values, x0, y0, x1, y1):
    """Summed-area table; one int64 source-sized buffer per call, no torch/GPU."""
    # int64 avoids overflow for 20 MP masks whose 255-valued sum exceeds uint32.
    integral = np.zeros((values.shape[0] + 1, values.shape[1] + 1), dtype=np.int64)
    interior = integral[1:, 1:]
    interior[:] = values
    np.cumsum(interior, axis=0, out=interior)
    np.cumsum(interior, axis=1, out=interior)
    return (
        integral[y1[:, None], x1] - integral[y0[:, None], x1]
        - integral[y1[:, None], x0] + integral[y0[:, None], x0]
    )


def _latent_mask(hard, soft, inference_size, *, allow_empty=False):
    """A latent cell may change only if every overlapping source pixel may change.

    The floor/ceil rectangle includes every source pixel with nonzero area overlap
    with the cell. This deliberately sacrifices a boundary cell to preserve thin
    protected strips. It does not claim that a VAE has a one-cell receptive field;
    exact output-pixel protection still comes from finish_region.
    """
    height, width = hard.shape
    latent_width, latent_height = (side // RESOLUTION_STEPS for side in inference_size)
    columns = np.arange(latent_width, dtype=np.int64)
    rows = np.arange(latent_height, dtype=np.int64)
    x0, y0 = columns * width // latent_width, rows * height // latent_height
    x1 = ((columns + 1) * width + latent_width - 1) // latent_width
    y1 = ((rows + 1) * height + latent_height - 1) // latent_height
    protected_count = _rectangle_sums(hard == 0, x0, y0, x1, y1)
    soft_sum = _rectangle_sums(soft, x0, y0, x1, y1)
    area = (y1 - y0)[:, None] * (x1 - x0)
    grid = np.clip(np.rint(soft_sum / area), 0, 255).astype(np.uint8)
    grid[protected_count > 0] = 0
    if not allow_empty and not np.any(grid > 128):
        raise ValueError("修改区在当前生成分辨率的潜变量网格中过窄或被保护区覆盖，请扩大修改区或提高生成分辨率。")
    return grid


def validate_rectangles(rectangles):
    if not isinstance(rectangles, (list, tuple)) or len(rectangles) > 12:
        raise ValueError("最多绘制 12 个区域。")
    result = []
    for rect in rectangles:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise ValueError("区域需要四个归一化坐标。")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1 for x in rect):
            raise ValueError("区域坐标必须是 0 到 1 的有限数值。")
        x0, y0, x1, y1 = map(float, rect)
        if x0 >= x1 or y0 >= y1:
            raise ValueError("区域必须具有正宽度和高度。")
        result.append([x0, y0, x1, y1])
    return result


def _rgb(path):
    with Image.open(path) as source:
        oriented = ImageOps.exif_transpose(source).convert("RGBA")
        base = Image.new("RGBA", oriented.size, "white")
        return Image.alpha_composite(base, oriented).convert("RGB")


def prepare_regions(
    original_a: Path, output_dir: Path, edit_regions, protect_regions, *,
    subject_mask: Path | None = None, megapixels: float = .5, allow_empty_latent: bool = False,
):
    edits, protections = validate_rectangles(edit_regions), validate_rectangles(protect_regions)
    if not edits:
        raise ValueError("请先绘制一个修改区域。")
    source = _rgb(original_a)
    width, height = source.size
    inference_size = _inference_size(source.size, megapixels)
    hard = np.zeros((height, width), dtype=np.uint8)
    for rectangles, value in ((edits, 255), (protections, 0)):
        for x0, y0, x1, y1 in rectangles:
            # Pixel-center inclusion is reproducible and symmetric at boundaries.
            left, top = math.ceil(x0 * width - .5), math.ceil(y0 * height - .5)
            right, bottom = math.ceil(x1 * width - .5), math.ceil(y1 * height - .5)
            hard[max(0, top):min(height, bottom), max(0, left):min(width, right)] = value
    if subject_mask is not None:
        with Image.open(subject_mask) as image:
            if image.size != source.size:
                raise ValueError("主体蒙版与原图尺寸不一致。")
            silhouette = np.asarray(image.convert('L')) >= 128
        hard[~silhouette] = 0
    if np.count_nonzero(hard) < 64:
        raise ValueError("修改区扣除保护区后过小，请重新绘制。")
    radius = max(1.0, min(8.0, min(source.size) * .006))
    blurred = np.asarray(Image.fromarray(hard).filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    # Feather only inward; neither blur nor protected holes can enlarge the ROI.
    soft = np.clip(2 * blurred - 255, 0, 255).astype(np.uint8)
    soft[hard == 0] = 0
    if not np.any(soft > 128):
        raise ValueError("修改区过窄，无法稳定生成。")
    coarse = _latent_mask(hard, soft, inference_size, allow_empty=allow_empty_latent)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mask_path, hard_path = output_dir / "edit_mask.png", output_dir / "edit_mask_hard.png"
    latent_path = output_dir / "edit_mask_latent.png"
    Image.fromarray(soft).convert("RGB").save(mask_path)
    Image.fromarray(hard).save(hard_path)
    Image.fromarray(coarse).convert("RGB").save(latent_path)
    return {"mask_path": mask_path, "hard_mask_path": hard_path, "latent_mask_path": latent_path, "metadata": {
        "policy": "union_edit_rectangles_minus_protect_rectangles_inward_feather",
        "coordinate_system": "normalized_exif_oriented_source_A",
        "subject_restriction": "foreground_probability_at_least_0.5" if subject_mask is not None else "none_manual_rectangles_only",
        "edit_regions": edits, "protect_regions": protections,
        "size": list(source.size), "feather_radius_px": radius,
        "megapixels": float(megapixels), "inference_size": list(inference_size),
        "resolution_steps": RESOLUTION_STEPS, "resize_source": COMFY_RESIZE_SOURCE,
        "latent_size": [coarse.shape[1], coarse.shape[0]],
        "latent_mask_policy": "any_overlapping_protected_source_pixel_blocks_cell_otherwise_mean_soft_mask",
        "active_latent_cells": int(np.count_nonzero(coarse > 128)),
        "latent_editable_fraction": round(float((coarse > 0).mean()), 6),
        "editable_fraction": round(float((soft > 0).mean()), 6),
    }}


def build_region_graph(workflow: dict) -> dict:
    graph = copy.deepcopy(workflow)
    # Avoid VAE's implicit center crop: source and mask share the exact canvas.
    graph["111"]["inputs"]["resolution_steps"] = RESOLUTION_STEPS
    graph["201"] = node("LoadImage", "Conservative latent edit mask", image="edit_mask_latent.png")
    # Each 1/16-resolution mask cell becomes a constant 16x16 block. Comfy's
    # subsequent bilinear reduction to latent size samples that same constant.
    graph["202"] = node("ImageScale", "Align edit mask", image=["201", 0], upscale_method="nearest-exact", width=["114", 0], height=["114", 1], crop="disabled")
    graph["203"] = node("ImageToMask", "White permits editing", image=["202", 0], channel="red")
    graph["204"] = node("SetLatentNoiseMask", "Protect source latent", samples=["116", 0], mask=["203", 0])
    graph["104"]["inputs"]["latent_image"] = ["204", 0]
    return graph


def finish_region(
    generated: Path, original_a: Path, mask_path: Path, output_dir: Path, *,
    expected_size=None,
):
    source, candidate = _rgb(original_a), _rgb(generated)
    with Image.open(mask_path) as image:
        mask = image.convert("L")
    if mask.size != source.size:
        raise ValueError("修改区与原图尺寸不一致。")
    ratio_error = abs((candidate.width / candidate.height) / (source.width / source.height) - 1)
    if expected_size is not None:
        if (not isinstance(expected_size, (list, tuple)) or len(expected_size) != 2
                or any(isinstance(side, bool) or not isinstance(side, int) or side <= 0 for side in expected_size)):
            raise ValueError("预期推理尺寸必须是两个正整数。")
        if candidate.size != tuple(expected_size):
            raise ValueError("生成图尺寸与记录的推理画布不一致，无法对齐保护区。")
    elif ratio_error > .05:
        raise ValueError("生成图比例偏离原图过多，无法对齐保护区。")
    # Comfy rounds the inference canvas to model-compatible dimensions. Map that
    # canvas back to source coordinates; do not reposition individual subjects.
    fitted = candidate.resize(source.size, Image.Resampling.LANCZOS)
    result = Image.composite(fitted, source, mask)
    original_array = np.asarray(source, dtype=np.int16)
    raw_array = np.asarray(fitted, dtype=np.int16)
    final_array = np.asarray(result, dtype=np.int16)
    mask_array = np.asarray(mask)
    protected = mask_array == 0
    editable = mask_array > 128
    changed = int(np.count_nonzero(np.any(final_array != original_array, axis=2) & protected))
    if changed:
        raise RuntimeError("Protected region restoration failed.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path, saved_mask = output_dir / "region_composite.png", output_dir / "region_mask.png"
    result.save(result_path)
    mask.save(saved_mask)
    def mae(values, selection):
        return round(float(np.abs(values - original_array)[selection].mean()), 6) if selection.any() else None
    return {"image_path": result_path, "mask_path": saved_mask, "metrics": {
        "region_locked": True, "background_locked": True,
        "composite_policy": "explicit_edit_mask_source_pixel_restoration",
        "composite_mask_path": str(saved_mask),
        "raw_protected_mae_255": mae(raw_array, protected),
        "final_protected_mae_255": mae(final_array, protected),
        "raw_edit_mae_255": mae(raw_array, editable),
        "locked_background_pixels": int(protected.sum()),
        "changed_locked_background_pixels": changed,
        "editable_fraction": round(float((mask_array > 0).mean()), 6),
        "region_metric_scope": "Pixel change, not aesthetic quality or semantic preservation. Final zero is from restoration, not the model.",
        "alignment": {"method": "resize_inference_canvas_to_source_coordinates", "aspect_ratio_rounding_error": ratio_error,
                      "expected_inference_size": list(expected_size) if expected_size is not None else None},
        "original_size": list(source.size), "generated_size": list(candidate.size),
    }}
