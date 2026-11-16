from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "offline" / "handed_nanodet_fulltest.py"
SPEC = importlib.util.spec_from_file_location("handed_nanodet_fulltest", MODULE_PATH)
assert SPEC and SPEC.loader
EVAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVAL
SPEC.loader.exec_module(EVAL)


class HandedNanoDetFullTestUnitTests(unittest.TestCase):
    def _raw(self) -> np.ndarray:
        raw = np.zeros(EVAL.OUTPUT_SHAPE, dtype=np.float32)
        raw[0, 0, 0] = 0.90
        raw[0, 0, 1] = 0.80
        raw[0, 0, 2:] = -10.0
        raw[0, 0, 2:].reshape(4, 8)[:, 4] = 10.0
        return raw

    def test_two_class_decoder_retains_overlapping_left_and_right(self) -> None:
        detections = EVAL.decode_output(self._raw(), image_width=320, image_height=320, score_threshold=0.05)
        self.assertEqual([item.label for item in detections], [0, 1])
        self.assertEqual([round(item.score, 2) for item in detections], [0.90, 0.80])
        self.assertTrue(all(item.xyxy[2] > item.xyxy[0] and item.xyxy[3] > item.xyxy[1] for item in detections))

    def test_decoder_rejects_legacy_single_class_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected output"):
            EVAL.decode_output(np.zeros((1, 2125, 33), dtype=np.float32), image_width=320, image_height=320)

    def test_task_metrics_counts_wrong_side_duplicate_and_false_positive(self) -> None:
        annotations = {
            "categories": [{"id": 1, "name": "left_palm"}, {"id": 2, "name": "right_palm"}],
            "images": [{"id": 1, "file_name": "test/a.jpg", "width": 100, "height": 100}],
            "annotations": [
                {"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 20, 20]},
                {"id": 2, "image_id": 1, "category_id": 2, "bbox": [40, 0, 20, 20]},
            ],
        }
        predictions = [
            {"image_id": 1, "category_id": 1, "bbox": [0, 0, 20, 20], "score": 0.95},
            {"image_id": 1, "category_id": 1, "bbox": [40, 0, 20, 20], "score": 0.90},
            {"image_id": 1, "category_id": 1, "bbox": [0, 0, 20, 20], "score": 0.80},
            {"image_id": 1, "category_id": 2, "bbox": [70, 70, 10, 10], "score": 0.70},
        ]
        result = EVAL.task_metrics(annotations, predictions, score_threshold=0.35, iou_threshold=0.5)["groups"]["all"]
        self.assertEqual(result["ground_truth"], 2)
        self.assertEqual(result["predictions"], 4)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["correct_handedness"], 1)
        self.assertEqual(result["wrong_handedness"], 1)
        self.assertEqual(result["missed"], 0)
        self.assertEqual(result["duplicate"], 1)
        self.assertEqual(result["false_positive"], 1)

    def test_prediction_validation_rejects_unknown_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown image_id"):
            EVAL.validate_coco_predictions(
                [{"image_id": 3, "category_id": 1, "bbox": [0, 0, 1, 1], "score": 0.5}], {1}
            )

    def test_raw_agreement_separates_class_and_dfl_error(self) -> None:
        expected = self._raw()
        actual = expected.copy()
        actual[0, 0, 1] += 0.25
        actual[0, 0, 2] += 1.5
        agreement = EVAL.RawAgreement(atol=0.02, rtol=0.02)
        per_image = agreement.compare(expected, actual)
        self.assertAlmostEqual(per_image["class_max_abs_error"], 0.25, places=6)
        self.assertAlmostEqual(per_image["dfl_max_abs_error"], 1.5, places=6)
        summary = agreement.summary()
        self.assertEqual(summary["images"], 1)
        self.assertEqual(summary["allclose_images"], 0)
        self.assertAlmostEqual(summary["raw_max_abs_error"], 1.5)

    def test_reference_pack_round_trips_exact_tensors(self) -> None:
        record = EVAL.ImageRecord(7, "test/a.jpg", 320, 240, "a" * 64, "subject", "session")
        tensor = np.zeros(EVAL.INPUT_SHAPE, dtype=np.float32)
        output = self._raw()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = EVAL.ReferencePackWriter(root, source={"onnx_sha256": "b" * 64}, shard_size=1)
            writer.append(record, tensor, output)
            writer.finalize(annotations_sha256="c" * 64, annotations_count=1)
            samples = list(EVAL.iter_reference_samples(root, expected_onnx_sha256="b" * 64, expected_annotations_sha256="c" * 64))
        self.assertEqual(len(samples), 1)
        metadata, loaded_tensor, loaded_output = samples[0]
        self.assertEqual(metadata["image_id"], 7)
        np.testing.assert_array_equal(loaded_tensor, tensor)
        np.testing.assert_array_equal(loaded_output, output)


if __name__ == "__main__":
    unittest.main()
