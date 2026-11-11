"""Deterministic pixel/geometry tests; no network, model weights or GPU required."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from studio import vision


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, name, image):
        path = self.root / name
        image.save(path)
        return path

    def read(self, path):
        with Image.open(path) as image:
            return image.copy()

    def subject(self, size=(100, 80), box=(30, 25, 69, 64), color=(20, 80, 230, 255)):
        image = Image.new("RGBA", size, (17, 201, 23, 0))
        ImageDraw.Draw(image).rectangle(box, fill=color)
        return image

    def source_and_mask(self):
        y, x = np.mgrid[:80, :100]
        array = np.stack([(x * 2) % 256, (y * 3) % 256, (x + y) % 256], axis=-1).astype(np.uint8)
        image = Image.fromarray(array)
        ImageDraw.Draw(image).rectangle((30, 25, 69, 64), fill=(200, 20, 20))
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rectangle((30, 25, 69, 64), fill=255)
        return self.save("original.png", image), self.save("source_mask.png", mask)

    def test_structure_preserves_canvas_and_true_subject_pixels(self):
        source = self.save("subject.png", self.subject())
        with patch.object(vision, "_session", side_effect=AssertionError("alpha requires no model")):
            result = vision.prepare_reference(source, self.root / "prep", "structure", True)
        image = self.read(result["image_path"])
        self.assertEqual(image.size, (100, 80))
        self.assertEqual(image.getpixel((50, 40)), (20, 80, 230))
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(result["metadata"]["method"], "source_alpha")
        self.assertEqual(self.read(result["mask_path"]).size, (100, 80))

    def test_alpha_is_applied_once_not_squared(self):
        image = self.subject()
        image.putpixel((30, 30), (0, 0, 0, 128))
        result = vision.prepare_reference(self.save("alpha.png", image), self.root / "p", "a", True)
        self.assertEqual(self.read(result["image_path"]).getpixel((30, 30)), (127, 127, 127))

    def test_appearance_crops_with_padding_and_original_geometry_mask(self):
        source = self.save("subject.png", self.subject())
        result = vision.prepare_reference(source, self.root / "prep", "appearance", True)
        image = self.read(result["image_path"])
        self.assertEqual(image.size, (48, 48))
        self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(result["metadata"]["crop_box"], [26, 21, 74, 69])
        self.assertEqual(self.read(result["mask_path"]).size, (100, 80))

    def test_no_isolation_does_not_load_model(self):
        source = self.save("plain.png", Image.new("RGB", (40, 20), (8, 19, 71)))
        with patch.object(vision, "_session", side_effect=AssertionError("no inference requested")):
            result = vision.prepare_reference(source, self.root / "prep", "b", False)
        self.assertIsNone(result["mask_path"])
        self.assertEqual(self.read(result["image_path"]).getpixel((2, 2)), (8, 19, 71))

    def test_background_lock_preserves_background_and_new_ears(self):
        source, mask = self.source_and_mask()
        generated = self.subject()
        ImageDraw.Draw(generated).polygon([(32, 26), (35, 10), (45, 26)], fill=(20, 80, 230, 255))
        candidate = self.save("candidate.png", generated)
        result = vision.finish_candidate(candidate, source, self.root / "result", True, mask)
        output = np.array(self.read(result["image_path"]))
        original = np.array(self.read(source))
        alpha = np.array(self.read(result["mask_path"]))
        self.assertTrue(np.array_equal(output[alpha == 0], original[alpha == 0]))
        self.assertGreater(int((alpha == 0).sum()), 3000)
        self.assertEqual(tuple(output[17, 36]), (20, 80, 230))
        self.assertEqual(tuple(output[40, 50]), (20, 80, 230))
        self.assertTrue(np.array_equal(np.array(self.read(result["raw_path"])), np.array(generated)))
        self.assertEqual(result["metrics"]["changed_locked_background_pixels"], 0)
        self.assertGreater(result["metrics"]["expanded_edit_fraction"], 0)
        self.assertTrue(np.any((alpha > 0) & (alpha < 255)))

    def test_original_silhouette_is_editable_to_avoid_ghost_remnants(self):
        source, mask = self.source_and_mask()
        generated = self.subject(box=(37, 29, 63, 61))
        # Generated image's RGB outside the new object is the replacement backdrop.
        result = vision.finish_candidate(self.save("small.png", generated), source, self.root / "r", True, mask)
        output = self.read(result["image_path"])
        self.assertEqual(output.getpixel((32, 40)), (255, 255, 255))
        self.assertEqual(output.getpixel((50, 40)), (20, 80, 230))

    def test_no_foreground_and_almost_full_foreground_rejected(self):
        for alpha in (0, 254):
            with self.subTest(alpha=alpha):
                image = Image.new("RGBA", (32, 32), (40, 40, 40, alpha))
                source = self.save(f"bad{alpha}.png", image)
                with self.assertRaises(vision.SegmentationError):
                    vision.prepare_reference(source, self.root / str(alpha), "a", True)

    def test_bad_source_mask_size_rejected(self):
        source, _ = self.source_and_mask()
        mask = self.save("badmask.png", Image.new("L", (20, 20), 100))
        with self.assertRaisesRegex(vision.SegmentationError, "size"):
            vision.finish_candidate(self.save("c.png", self.subject()), source, self.root / "r", True, mask)

    def test_displaced_subject_refuses_false_background_lock(self):
        source, mask = self.source_and_mask()
        image = self.subject(box=(1, 1, 14, 14))
        with self.assertRaisesRegex(vision.SegmentationError, "displaced"):
            vision.finish_candidate(self.save("c.png", image), source, self.root / "r", True, mask)

    def test_aspect_mismatch_does_not_stretch_or_silently_crop(self):
        source, mask = self.source_and_mask()
        candidate = self.subject(size=(100, 100), box=(25, 32, 74, 79))
        result = vision.finish_candidate(self.save("square.png", candidate), source, self.root / "r", True, mask)
        self.assertEqual(self.read(result["image_path"]).size, (100, 80))
        self.assertEqual(result["metrics"]["alignment"]["fitted_size"], [80, 80])
        self.assertEqual(result["metrics"]["alignment"]["offset_xy"], [10, 0])

    def test_aspect_mismatch_cannot_erase_source_outside_generated_canvas(self):
        source, mask = self.source_and_mask()
        candidate = self.subject(size=(20, 100), box=(3, 20, 16, 79))
        with self.assertRaisesRegex(vision.SegmentationError, "aspect ratio"):
            vision.finish_candidate(self.save("narrow.png", candidate), source, self.root / "r", True, mask)

    def test_unavailable_segmentation_keeps_raw_but_does_not_claim_score(self):
        source = self.save("plain.png", Image.new("RGB", (40, 20), (8, 19, 71)))
        with patch.object(vision, "_session", side_effect=vision.SegmentationUnavailable("missing local model")):
            result = vision.finish_candidate(source, source, self.root / "r", False, None)
            with self.assertRaises(vision.SegmentationUnavailable):
                vision.finish_candidate(source, source, self.root / "r2", True, None)
        self.assertIsNone(result["score"])
        self.assertFalse(result["metrics"]["background_locked"])
        self.assertFalse(result["metrics"]["score_available"])

    def test_contiguous_shape_scores_above_equal_area_fragments(self):
        continuous = Image.new("L", (100, 100), 0)
        fragments = Image.new("L", (100, 100), 0)
        ImageDraw.Draw(continuous).rectangle((30, 30, 69, 69), fill=255)
        draw = ImageDraw.Draw(fragments)
        for x, y in ((20, 20), (60, 20), (20, 60), (60, 60)):
            draw.rectangle((x, y, x + 19, y + 19), fill=255)
        good, good_metrics = vision._shape_score(continuous)
        bad, bad_metrics = vision._shape_score(fragments)
        self.assertGreater(good, bad)
        self.assertEqual(good_metrics["component_count"], 1)
        self.assertEqual(bad_metrics["component_count"], 4)
        self.assertIn("duplicate faces", good_metrics["score_scope"])

    def test_cropped_shape_scores_lower(self):
        centered = Image.new("L", (100, 100), 0)
        clipped = Image.new("L", (100, 100), 0)
        ImageDraw.Draw(centered).rectangle((30, 30, 69, 69), fill=255)
        ImageDraw.Draw(clipped).rectangle((0, 30, 39, 69), fill=255)
        self.assertGreater(vision._shape_score(centered)[0], vision._shape_score(clipped)[0])

    def test_preprocess_black_image_is_finite_and_model_shape(self):
        tensor = vision._preprocess(Image.new("RGB", (50, 30)))
        self.assertEqual(tensor.shape, (1, 3, 320, 320))
        self.assertEqual(tensor.dtype, np.float32)
        self.assertTrue(np.isfinite(tensor).all())
        self.assertAlmostEqual(float(tensor[0, 0, 0, 0]), -0.485 / 0.229, places=5)

    def test_flat_or_nan_model_prediction_is_not_fake_foreground(self):
        for value in (0.0, 1.0, float("nan")):
            with self.subTest(value=value), self.assertRaises(vision.SegmentationError):
                vision._prediction_mask(np.full((1, 1, 20, 20), value), (100, 80))

    def test_missing_model_status_has_no_download_side_effect(self):
        with patch.object(vision, "MODEL_PATH", self.root / "absent.onnx"):
            self.assertFalse(vision.segmentation_available())
        self.assertFalse((self.root / "absent.onnx").exists())

    def test_reference_palette_matching_candidate_ranks_higher(self):
        source, mask = self.source_and_mask()
        appearance = self.save("appearance.png", self.subject())
        matched = self.save("matched.png", self.subject())
        mismatched = self.save("mismatched.png", self.subject(color=(230, 30, 20, 255)))
        good = vision.finish_candidate(matched, source, self.root / "good", False, mask, appearance_reference=appearance)
        bad = vision.finish_candidate(mismatched, source, self.root / "bad", False, mask, appearance_reference=appearance)
        self.assertGreater(good["score"], bad["score"])
        self.assertEqual(good["metrics"]["shape_score"], bad["metrics"]["shape_score"])
        self.assertEqual(good["metrics"]["score_kind"], "reference_heuristic_v2")
        self.assertEqual(good["metrics"]["structure_similarity"], 1.0)
        self.assertEqual(good["metrics"]["palette_similarity"], 1.0)
        self.assertEqual(bad["metrics"]["palette_similarity"], 0.0)
        self.assertAlmostEqual(good["score"] - bad["score"], 30.0)
        self.assertIn("duplicate faces", good["metrics"]["score_scope"])

    def test_reference_background_color_does_not_affect_palette_or_score(self):
        source, mask = self.source_and_mask()
        candidate = self.save("candidate.png", self.subject())
        references = []
        for name, background in (("red", (255, 0, 0)), ("green", (0, 255, 0))):
            image = Image.new("RGB", (100, 80), background)
            ImageDraw.Draw(image).rectangle((30, 25, 69, 64), fill=(20, 80, 230))
            references.append(self.save(f"{name}.png", image))
        scores = [vision.finish_candidate(candidate, source, self.root / str(i), False, mask,
                  appearance_reference=path, appearance_mask=mask) for i, path in enumerate(references)]
        self.assertEqual(scores[0]["score"], scores[1]["score"])
        self.assertEqual(scores[0]["metrics"]["palette_similarity"], 1.0)
        self.assertEqual(scores[1]["metrics"]["palette_similarity"], 1.0)

    def test_normalized_outline_preserves_shape_under_translation_and_scale(self):
        first = Image.new("L", (100, 100), 0)
        second = Image.new("L", (240, 180), 0)
        different = Image.new("L", (100, 100), 0)
        ImageDraw.Draw(first).rectangle((10, 20, 29, 59), fill=255)
        ImageDraw.Draw(second).rectangle((130, 70, 169, 149), fill=255)
        ImageDraw.Draw(different).rectangle((10, 20, 49, 59), fill=255)
        a, b, c = [vision._normalized_silhouette(mask) for mask in (first, second, different)]
        iou = lambda left, right: (left & right).sum() / (left | right).sum()
        self.assertGreaterEqual(iou(a, b), 0.99)
        self.assertLess(iou(a, c), 0.75)

    def test_palette_explicitly_represents_black_gray_and_white(self):
        image = self.subject(size=(100, 80), box=(20, 20, 79, 59), color=(0, 0, 0, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 20, 59, 59), fill=(128, 128, 128, 255))
        draw.rectangle((60, 20, 79, 59), fill=(255, 255, 255, 255))
        palette = vision._foreground_palette(image, image.getchannel("A"))
        self.assertTrue(np.all(palette[:3] > 0.25))
        self.assertAlmostEqual(float(palette[:3].sum()), 1.0)
        self.assertEqual(float(palette[3:].sum()), 0.0)

    def test_reference_mask_failure_falls_back_without_discarding_locked_output(self):
        source, mask = self.source_and_mask()
        image = self.save("candidate.png", self.subject())
        appearance = self.save("appearance.png", self.subject())
        result = vision.finish_candidate(image, source, self.root / "result", True, mask,
                 appearance_reference=appearance, appearance_mask=self.root / "missing_mask.png")
        self.assertTrue(result["image_path"].is_file())
        self.assertTrue(result["metrics"]["background_locked"])
        self.assertEqual(result["metrics"]["changed_locked_background_pixels"], 0)
        self.assertEqual(result["score"], result["metrics"]["shape_score"])
        self.assertEqual(result["metrics"]["score_kind"], "mask_geometry_v1")
        self.assertFalse(result["metrics"]["reference_score_available"])
        self.assertIn("FileNotFoundError", result["metrics"]["reference_score_fallback_reason"])

    def test_reference_a_mask_failure_is_nonfatal_only_for_optional_scoring(self):
        source, _ = self.source_and_mask()
        candidate = self.save("candidate.png", self.subject())
        appearance = self.save("appearance.png", self.subject())
        with patch.object(vision, "_session", side_effect=vision.SegmentationUnavailable("missing model")):
            result = vision.finish_candidate(candidate, source, self.root / "result", False, None,
                                             appearance_reference=appearance)
            with self.assertRaises(vision.SegmentationUnavailable):
                vision.finish_candidate(candidate, source, self.root / "locked", True, None,
                                        appearance_reference=appearance)
        self.assertIsNotNone(result["score"])
        self.assertFalse(result["metrics"]["reference_score_available"])
        self.assertIn("missing model", result["metrics"]["reference_score_fallback_reason"])

    def test_repeated_candidates_reuse_reference_descriptors(self):
        source, mask = self.source_and_mask()
        appearance = self.save("appearance.png", self.subject())
        candidate = self.save("candidate.png", self.subject())
        with patch.object(vision, "_extract_mask", wraps=vision._extract_mask) as extraction:
            for i in range(2):
                vision.finish_candidate(candidate, source, self.root / str(i), False, mask,
                                        appearance_reference=appearance)
        # Two candidate extractions, one B extraction; A uses its provided mask.
        self.assertEqual(extraction.call_count, 3)


if __name__ == "__main__":
    unittest.main()
