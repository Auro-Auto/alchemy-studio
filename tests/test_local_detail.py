from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from studio.local_detail import finish_detail, prepare_detail
from studio.regions import prepare_regions


def _fixture(tmp_path):
    y, x = np.indices((480, 640))
    pixels = np.stack((x % 256, y % 256, (x + y) % 256), axis=-1).astype(np.uint8)
    source = tmp_path / "source.png"
    Image.fromarray(pixels).save(source)
    foreground = np.zeros((480, 640), dtype=np.uint8)
    foreground[105:430, 205:475] = 255
    subject = tmp_path / "subject.png"
    Image.fromarray(foreground).save(subject)
    region = prepare_regions(
        source, tmp_path / "region",
        [[.39, .44, .67, .81]], [[330 / 640, .4, 335 / 640, .85]],
        subject_mask=subject,
    )
    detail = prepare_detail(source, {"image_path": source, "mask_path": subject},
                            region, tmp_path / "detail", .5)
    return source, pixels, region, detail


def test_local_crop_keeps_subject_context_and_thin_protection(tmp_path):
    source, original, region, detail = _fixture(tmp_path)
    metadata = detail["metadata"]
    left, top, right, bottom = metadata["crop_box"]
    assert left <= 205 and top <= 105 and right >= 475 and bottom >= 430
    assert (right - left) * (bottom - top) < 640 * 480
    assert metadata["sampling_density_gain"] > 1
    with Image.open(detail["image_path"]) as image:
        np.testing.assert_array_equal(np.asarray(image), original[top:bottom, left:right])
    with Image.open(detail["latent_mask_path"]) as image:
        grid = np.asarray(image.convert("L"))
    width, height = metadata["inference_size"]
    assert grid.shape == (height // 16, width // 16)
    assert np.count_nonzero(grid > 128) > region["metadata"]["active_latent_cells"]
    # Even a strip narrower than a model cell must block every overlapping cell.
    columns = np.arange(grid.shape[1])
    starts = left + columns * (right - left) // grid.shape[1]
    ends = left + ((columns + 1) * (right - left) + grid.shape[1] - 1) // grid.shape[1]
    assert np.any((starts < 335) & (ends > 330))
    assert not grid[:, (starts < 335) & (ends > 330)].any()


def test_local_finish_restores_full_canvas_and_separates_raw_scope(tmp_path):
    source, original, region, detail = _fixture(tmp_path)
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", tuple(detail["metadata"]["inference_size"]), (248, 231, 178)).save(candidate)
    result = finish_detail(candidate, source, region["mask_path"], detail, tmp_path / "finished")
    left, top, right, bottom = detail["metadata"]["crop_box"]
    with Image.open(region["mask_path"]) as image:
        alpha = np.asarray(image.convert("L"))
    with Image.open(result["image_path"]) as image:
        final = np.asarray(image)
    with Image.open(result["raw_canvas_path"]) as image:
        raw_canvas = np.asarray(image)
    outside_crop = np.ones(alpha.shape, dtype=bool)
    outside_crop[top:bottom, left:right] = False
    np.testing.assert_array_equal(raw_canvas[outside_crop], original[outside_crop])
    np.testing.assert_array_equal(final[alpha == 0], original[alpha == 0])
    assert np.all(final[alpha == 255] == [248, 231, 178])
    metrics = result["metrics"]
    assert metrics["changed_locked_background_pixels"] == 0
    assert metrics["locked_background_pixels"] == np.count_nonzero(alpha == 0)
    assert metrics["raw_protected_scope"] == "crop_only"
    assert metrics["raw_protected_measured_pixels"] == np.count_nonzero(alpha[top:bottom, left:right] == 0)
    assert metrics["raw_protected_mae_255"] > metrics["raw_canvas_protected_mae_255"] > 0


def test_local_finish_rejects_wrong_model_canvas_and_edit_outside_crop(tmp_path):
    source, _, region, detail = _fixture(tmp_path)
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (16, 16)).save(candidate)
    with pytest.raises(ValueError, match="推理画布"):
        finish_detail(candidate, source, region["mask_path"], detail, tmp_path / "failed")
    Image.new("RGB", tuple(detail["metadata"]["inference_size"])).save(candidate)
    bad_detail = deepcopy(detail)
    bad_detail["metadata"]["crop_box"] = [0, 0, 5, 5]
    with pytest.raises(ValueError, match="未覆盖"):
        finish_detail(candidate, source, region["mask_path"], bad_detail, tmp_path / "failed")
