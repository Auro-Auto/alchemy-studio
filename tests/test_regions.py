from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from studio.regions import prepare_regions, finish_region, build_region_graph, validate_rectangles
from scripts.build_workflow import build_api_workflow


def test_protected_hole_and_inward_feather(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (100, 100), 'blue').save(source)
    result = prepare_regions(source, tmp_path / 'masks', [[.1, .1, .9, .9]], [[.4, .4, .6, .6]])
    mask = np.asarray(Image.open(result['mask_path']).convert('L'))
    assert not mask[:10].any() and not mask[:, :10].any()
    assert not mask[40:60, 40:60].any()
    assert mask[25, 25] == 255 and np.any((mask > 0) & (mask < 255))
    generated = tmp_path / 'generated.png'
    Image.new('RGB', (100, 100), 'red').save(generated)
    final = finish_region(generated, source, result['mask_path'], tmp_path / 'final')
    output = np.asarray(Image.open(final['image_path']))
    assert np.all(output[mask == 0] == [0, 0, 255])
    assert list(output[25, 25]) == [255, 0, 0]
    assert final['metrics']['raw_protected_mae_255'] > 0
    assert final['metrics']['changed_locked_background_pixels'] == 0


@pytest.mark.parametrize('rects', [[[0, 0, float('nan'), 1]], [[0, 0, 2, 1]], [[.5, 0, .1, 1]], [[False, 0, 1, 1]], [[0, 1, 1]]])
def test_bad_rectangles(rects):
    with pytest.raises(ValueError): validate_rectangles(rects)


def test_fully_protected_rejected(tmp_path):
    source = tmp_path / 'a.png'
    Image.new('RGB', (100, 100)).save(source)
    with pytest.raises(ValueError):
        prepare_regions(source, tmp_path, [[0, 0, 1, 1]], [[0, 0, 1, 1]])


def test_subject_intersection_preserves_rectangular_background(tmp_path):
    source, silhouette = tmp_path/'a.png', tmp_path/'subject.png'
    Image.new('RGB', (100, 100), 'white').save(source)
    pixels = np.zeros((100, 100), dtype=np.uint8)
    pixels[20:80, 30:70] = 255
    Image.fromarray(pixels).save(silhouette)
    result = prepare_regions(source,tmp_path/'masks',[[.1,.1,.9,.9]],[],subject_mask=silhouette)
    mask = np.asarray(Image.open(result['mask_path']).convert('L'))
    assert not mask[pixels==0].any()
    assert mask[50,50] == 255
    assert result['metadata']['subject_restriction'] != 'none_manual_rectangles_only'


def test_graph_preserves_reference_conditioning_and_uses_source_latent():
    original = build_api_workflow(prompt='test', seed=42, megapixels=.5)
    graph = build_region_graph(original)
    assert graph['104']['inputs']['latent_image'] == ['204', 0]
    assert graph['204']['inputs']['samples'] == ['116', 0]
    assert graph['203']['inputs']['channel'] == 'red'
    assert original['104']['inputs']['latent_image'] != ['204', 0]
    for key in original:
        if key not in {'104', '111'}: assert graph[key] == original[key]
    assert graph['111']['inputs']['resolution_steps'] == 16
    assert graph['202']['inputs']['upscale_method'] == 'nearest-exact'
    assert graph['201']['inputs']['image'] == 'edit_mask_latent.png'


def test_mismatched_canvas_refused(tmp_path):
    a, b = tmp_path / 'a.png', tmp_path / 'b.png'
    Image.new('RGB', (100, 100)).save(a)
    Image.new('RGB', (200, 100)).save(b)
    region = prepare_regions(a, tmp_path, [[.1, .1, .9, .9]], [])
    with pytest.raises(ValueError): finish_region(b, a, region['mask_path'], tmp_path)


@pytest.mark.parametrize('left', [620, 630])
def test_sub_latent_cell_edit_is_rejected_before_inference(tmp_path, left):
    """Previously valid 24px strips disappeared completely in the 45x45 grid."""
    source = tmp_path / 'source.png'
    Image.new('RGB', (2048, 2048), 'blue').save(source)
    with pytest.raises(ValueError, match='潜变量网格'):
        prepare_regions(source, tmp_path / 'masks', [[left/2048, .2, (left+24)/2048, .8]], [], megapixels=.5)
    assert not (tmp_path / 'masks').exists()


def test_higher_resolution_can_make_a_thin_edit_usable(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (2048, 2048), 'blue').save(source)
    edit = [[640/2048, .2, 680/2048, .8]]
    with pytest.raises(ValueError, match='潜变量网格'):
        prepare_regions(source, tmp_path / 'low', edit, [], megapixels=.5)
    result = prepare_regions(source, tmp_path / 'high', edit, [], megapixels=1.0)
    assert result['metadata']['inference_size'] == [1024, 1024]
    assert result['metadata']['latent_size'] == [64, 64]
    assert result['metadata']['active_latent_cells'] > 0


def test_thin_protected_strip_blocks_every_overlapping_latent_cell(tmp_path):
    """8px protection between old interpolation samples must not disappear."""
    source = tmp_path / 'source.png'
    Image.new('RGB', (2048, 2048), 'blue').save(source)
    result = prepare_regions(source, tmp_path / 'masks', [[.1, .1, .9, .9]],
                             [[630/2048, .1, 638/2048, .9]], megapixels=.5)
    with Image.open(result['latent_mask_path']) as image:
        assert image.mode == 'RGB'
        grid = np.asarray(image.convert('L'))
    assert grid.shape == (45, 45)
    assert result['metadata']['inference_size'] == [720, 720]
    # The stripe crosses source intervals of latent columns 13 and 14.
    assert not grid[8:37, 13:15].any()
    assert np.all(grid[8:37, 10] == 255)
    with Image.open(result['mask_path']) as image:
        final_mask = np.asarray(image.convert('L'))
    assert not final_mask[300:1700, 630:638].any()
    assert final_mask[1000, 1000] == 255


def test_grid_survives_actual_torch_nearest_up_bilinear_down():
    torch = pytest.importorskip('torch')
    functional = torch.nn.functional
    # Includes soft feather values and non-square grids, not just a binary case.
    generator = np.random.default_rng(42)
    grid = torch.from_numpy(generator.integers(0, 256, (1, 1, 39, 52)).astype(np.float32)) / 255
    upscaled = functional.interpolate(grid, size=(624, 832), mode='nearest-exact')
    sampled = functional.interpolate(upscaled, size=(39, 52), mode='bilinear')
    assert torch.equal(sampled, grid)


def test_inference_canvas_matches_comfy_binary_megapixels_and_rounding(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (3072, 2304), 'blue').save(source)
    result = prepare_regions(source, tmp_path / 'masks', [[.2, .2, .8, .8]], [], megapixels=.5)
    # sqrt(0.5*1024*1024/(3072*2304)), then Python round on each /16 axis.
    assert result['metadata']['inference_size'] == [832, 624]
    assert result['metadata']['latent_size'] == [52, 39]


def test_expected_canvas_accepts_legitimate_rounding_above_five_percent(tmp_path):
    source, generated = tmp_path / 'source.png', tmp_path / 'generated.png'
    Image.new('RGB', (20, 1000), 'blue').save(source)
    Image.new('RGB', (96, 5120), 'red').save(generated)
    result = prepare_regions(source, tmp_path / 'masks', [[0, .1, 1, .9]], [], megapixels=.5)
    assert result['metadata']['inference_size'] == [96, 5120]
    final = finish_region(generated, source, result['mask_path'], tmp_path / 'final',
                          expected_size=result['metadata']['inference_size'])
    assert final['metrics']['alignment']['aspect_ratio_rounding_error'] > .05
    assert final['metrics']['changed_locked_background_pixels'] == 0
    with Image.open(final['image_path']) as image:
        assert image.size == (20, 1000)
    with pytest.raises(ValueError, match='比例'):
        finish_region(generated, source, result['mask_path'], tmp_path / 'legacy')


def test_wrong_expected_canvas_rejected_even_when_aspect_matches(tmp_path):
    source, generated = tmp_path / 'source.png', tmp_path / 'generated.png'
    Image.new('RGB', (100, 100), 'blue').save(source)
    Image.new('RGB', (704, 704), 'red').save(generated)
    result = prepare_regions(source, tmp_path / 'masks', [[.1, .1, .9, .9]], [])
    with pytest.raises(ValueError, match='推理画布'):
        finish_region(generated, source, result['mask_path'], tmp_path / 'final',
                      expected_size=result['metadata']['inference_size'])


def test_region_coordinates_follow_exif_oriented_canvas(tmp_path):
    source = tmp_path / 'source.jpg'
    image = Image.new('RGB', (80, 120), 'blue')
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    result = prepare_regions(source, tmp_path / 'masks', [[.1, .1, .5, .9]], [])
    assert result['metadata']['size'] == [120, 80]
    with Image.open(result['hard_mask_path']) as image:
        mask = np.asarray(image)
    assert mask.shape == (80, 120)
    assert mask[40, 25] == 255 and mask[40, 90] == 0


@pytest.mark.parametrize('megapixels', [False, float('nan'), 0, 17])
def test_invalid_resolution_rejected(tmp_path, megapixels):
    source = tmp_path / 'source.png'
    Image.new('RGB', (100, 100)).save(source)
    with pytest.raises(ValueError):
        prepare_regions(source, tmp_path / 'masks', [[.1, .1, .9, .9]], [], megapixels=megapixels)
