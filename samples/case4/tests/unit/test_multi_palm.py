from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

HAS_IMAGE_RUNTIME = all(importlib.util.find_spec(name) is not None for name in ("cv2", "numpy", "fastapi"))

if HAS_IMAGE_RUNTIME:
    import numpy as np

    from palmprint_workbench.api import server
    from palmprint_workbench.domain.preprocessing import PalmPreprocessor, RoiResult
    from palmprint_workbench.runtime.detector import (
        PalmBox,
        decode,
        decode_with_debug,
        detector_spec,
        load_detector_catalog,
        prepare_input,
        _nms,
    )
    from palmprint_workbench.services import workbench as service_layer


@unittest.skipUnless(HAS_IMAGE_RUNTIME, "image and API runtime dependencies are unavailable")
class MultiPalmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = np.random.default_rng(9).integers(40, 190, (480, 640, 3), dtype=np.uint8)
        self.boxes = [PalmBox((16, 48, 144, 208), 0.92, "left"), PalmBox((300, 52, 460, 230), 0.84, "right")]

    def test_decode_maps_every_box_to_original_image_and_sorts_left_to_right(self) -> None:
        raw = np.zeros((1, 2125, 34), dtype=np.float32)
        for index, (score, label) in ((225, (0.84, 1)), (205, (0.92, 0))):
            raw[0, index, label] = score
            raw[0, index, 2:] = -10
            raw[0, index, 2:].reshape(4, 8)[:, 4] = 10
        result = decode(raw, (480, 640))
        self.assertEqual(len(result), 2)
        self.assertLess(result[0].xyxy[0], result[1].xyxy[0])
        self.assertEqual([box.palm_side for box in result], ["left", "right"])
        self.assertAlmostEqual(result[0].xyxy[0], 16, delta=1)
        self.assertAlmostEqual(result[0].xyxy[1], 12, delta=1)

    def test_nms_identical_and_shifted_duplicates_keep_best_score(self) -> None:
        boxes = np.array([[0, 0, 100, 100], [0, 0, 100, 100], [5, 5, 105, 105]], dtype=np.float32)
        self.assertEqual(_nms(boxes, np.array([0.8, 0.95, 0.9]), 0.6), [1])

    def test_nms_nested_duplicate_below_iou_threshold(self) -> None:
        boxes = np.array([[0, 0, 100, 100], [20, 20, 80, 80], [150, 0, 250, 100]], dtype=np.float32)
        scores = np.array([0.95, 0.85, 0.90])
        self.assertEqual(_nms(boxes, scores, 0.6), [0, 2, 1])
        self.assertEqual(_nms(boxes, scores, 0.6, containment_threshold=0.9), [0, 2])

    def test_nms_preserves_neighbouring_and_three_distinct_palms(self) -> None:
        boxes = np.array([[0, 0, 100, 100], [35, 0, 135, 100], [180, 0, 280, 100]], dtype=np.float32)
        self.assertEqual(_nms(boxes, np.array([0.9, 0.8, 0.7]), 0.6, containment_threshold=0.9), [0, 1, 2])

    def test_nms_does_not_merge_offcentre_contained_palms(self) -> None:
        boxes = np.array([[0, 0, 100, 100], [0, 0, 60, 60]], dtype=np.float32)
        self.assertEqual(_nms(boxes, np.array([0.9, 0.8]), 0.6, containment_threshold=0.9), [0, 1])

    def test_nms_invalid_boxes_empty_and_tie_order(self) -> None:
        boxes = np.array([[0, 0, 0, 100], [float("nan"), 0, 100, 100], [10, 10, 30, 30], [60, 60, 80, 80]])
        self.assertEqual(_nms(boxes, np.array([0.99, 0.99, 0.8, 0.8]), 0.6), [2, 3])
        self.assertEqual(_nms(np.empty((0, 4)), np.empty(0), 0.6), [])

    def test_detector_input_uses_direct_stretch_mapping(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        spec = detector_spec("nanodet-plus-m-320-handed")
        tensor, transform = prepare_input(image, spec)
        self.assertEqual(tensor.shape, (1, 3, 320, 320))
        self.assertEqual(transform.mode, "direct_stretch")
        self.assertAlmostEqual(transform.scale_x, 0.5)
        self.assertAlmostEqual(transform.scale_y, 2 / 3)
        self.assertEqual(transform.pad_x, 0)
        self.assertEqual(transform.pad_y, 0)
        original = transform.to_source_boxes(
            np.asarray([[0, 0, 320, 320]], dtype=np.float32)
        )
        self.assertEqual(original.tolist(), [[0.0, 0.0, 640.0, 480.0]])

    def test_detector_catalog_contains_nanodet_and_two_picodet_models(self) -> None:
        default_id, specs = load_detector_catalog()
        self.assertEqual(default_id, "nanodet-plus-m-320-handed")
        self.assertEqual(set(specs), {
            "nanodet-plus-m-320-handed",
            "picodet-xs-320-seed-42",
            "picodet-xs-416-seed-42",
        })
        self.assertEqual(specs["nanodet-plus-m-320-handed"].output_shapes, ((1, 2125, 34),))
        self.assertEqual(len(specs["picodet-xs-320-seed-42"].output_shapes), 8)
        self.assertEqual(specs["picodet-xs-416-seed-42"].input_shape, (1, 3, 416, 416))

    def test_extract_box_stretches_entire_detected_region(self) -> None:
        result = PalmPreprocessor().extract_box(self.image, self.boxes[0].xyxy)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.roi.shape, (128, 128))
        self.assertEqual(result.geometry["bbox_xyxy"], [16, 48, 144, 208])
        self.assertEqual(result.geometry["roi_mode"], "detected_bbox")

    def test_registration_rejects_multiple_hands(self) -> None:
        with patch.object(service_layer, "detect_palms", return_value=self.boxes):
            result, error = service_layer._extract(self.image, False)
        self.assertFalse(result.ok)
        self.assertIn("多只手", error)

    def test_decode_keeps_class_local_boxes_and_rejects_wrong_class_contract(self) -> None:
        raw = np.zeros((1, 2125, 34), dtype=np.float32)
        raw[0, 205, 0] = 0.91
        raw[0, 205, 2:] = -10
        raw[0, 205, 2:].reshape(4, 8)[:, 4] = 10
        raw[0, 225, 1] = 0.88
        raw[0, 225, 2:] = -10
        raw[0, 225, 2:].reshape(4, 8)[:, 4] = 10
        result = decode(raw, (480, 640), model_id="nanodet-plus-m-320-handed")
        self.assertEqual({box.palm_side for box in result}, {"left", "right"})

    def test_debug_decode_records_per_class_score_boundaries(self) -> None:
        raw = np.zeros((1, 2125, 34), dtype=np.float32)
        raw[0, 205, 0] = 0.42
        raw[0, 205, 2:] = -10
        raw[0, 205, 2:].reshape(4, 8)[:, 4] = 10
        raw[0, 225, 1] = 0.81
        raw[0, 225, 2:] = -10
        raw[0, 225, 2:].reshape(4, 8)[:, 4] = 10
        detections, debug = decode_with_debug(
            raw, (480, 640), model_id="nanodet-plus-m-320-handed"
        )
        self.assertEqual({box.palm_side for box in detections}, {"left", "right"})
        classes = {item["palm_side"]: item for item in debug["classes"]}
        self.assertEqual(classes["left"]["candidates_at_threshold"], 1)
        self.assertEqual(classes["right"]["candidates_at_threshold"], 1)
        self.assertAlmostEqual(classes["left"]["max_score"], 0.42)
        self.assertAlmostEqual(classes["right"]["max_score"], 0.81)
        self.assertEqual(len(classes["left"]["top_candidates"]), 3)
        self.assertEqual(debug["detector_id"], "nanodet-plus-m-320-handed")

    def test_picodet_decoder_handles_four_class_and_four_dfl_outputs(self) -> None:
        spec = detector_spec("picodet-xs-320-seed-42")
        outputs = [np.zeros(shape, dtype=np.float32) for shape in spec.output_shapes]
        outputs[0][0, 0, 0] = 0.92
        outputs[4][0, 0] = -100
        outputs[4][0, 0, [1, 9, 17, 25]] = 100
        result = decode(outputs, (640, 480), model_id=spec.id)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].palm_side, "left")
        self.assertGreater(result[0].xyxy[2], result[0].xyxy[0])
        self.assertGreater(result[0].xyxy[3], result[0].xyxy[1])

    def test_enrollment_identity_requires_detector_side_match(self) -> None:
        state = server.EnrollmentState(
            "detector-match",
            server.EnrollmentOptions(palm_side="left"),
        )
        response = server._session_response(state)
        self.assertEqual(response["palm_side"], "left")
        self.assertEqual(response["palm_side_verification"], "detector_required_match")
        self.assertIsNone(server._enrollment_side_mismatch_reason("left", "left"))
        self.assertEqual(
            server._enrollment_side_mismatch_reason("left", "right"),
            "检测到右掌，与当前选择的左掌不一致",
        )
        self.assertEqual(
            server._enrollment_side_mismatch_reason("right", "left"),
            "检测到左掌，与当前选择的右掌不一致",
        )

    def test_detected_side_is_advisory_for_template_search(self) -> None:
        from types import SimpleNamespace

        roi = RoiResult(True, roi=np.full((128, 128), 100, dtype=np.uint8))
        search_result = {
            "accepted": False,
            "score": 0.4,
            "palm_id": "P-001:left",
            "person_id": "P-001",
            "display_name": "Alice",
            "user_id": "P-001:left",
            "user_name": "Alice",
            "palm_side": "left",
            "matches": [],
        }
        adapter = SimpleNamespace(encode=lambda value: SimpleNamespace(
            code=np.array([1.0], dtype=np.float32), preprocess_ms=0.0, inference_ms=0.0,
        ))
        with patch.object(server.WORKBENCH, "adapter", return_value=adapter), \
             patch.object(server.WORKBENCH.store, "search", return_value=search_result) as search, \
             patch.object(server.WORKBENCH.store, "summary", return_value={"palm_count": 1, "person_count": 1, "sample_count": 1}):
            payload = server._recognition_payload_hand(
                self.image,
                server.RecognitionOptions(),
                roi_result_override=roi,
                archive=False,
                validated=True,
                detected_palm_side="right",
            )
        self.assertEqual(payload["detected_palm_side"], "right")
        self.assertNotIn("palm_side", search.call_args.kwargs)

    def test_frame_retains_failed_hand_and_archives_once(self) -> None:
        good = RoiResult(True, roi=np.full((128, 128), 100, dtype=np.uint8))
        bad = RoiResult(False, reason="清晰度不足")
        with patch.object(server, "_validate_options"), \
             patch.object(server.service_layer, "detect_palms", return_value=self.boxes), \
             patch.object(server.WORKBENCH.preprocessor, "extract_box", side_effect=[good, bad]), \
             patch.object(server, "_recognition_payload_hand", side_effect=[
                 {"outcome": "candidates_only", "matches": [{"score": 0.8}]},
                 {"outcome": "roi_rejected", "matches": [], "status": "清晰度不足"},
             ]) as recognize, \
             patch.object(server.WORKBENCH.captures, "save_multi", return_value={"capture_id": "one"}) as save:
            payload = server._recognition_payload(self.image, server.RecognitionOptions())
        self.assertEqual(payload["detection_count"], 2)
        self.assertEqual([hand["index"] for hand in payload["hands"]], [1, 2])
        self.assertEqual(payload["hands"][1]["outcome"], "roi_rejected")
        self.assertEqual(payload["hands"][0]["detected_palm_side"], "left")
        self.assertEqual(payload["hands"][1]["detected_palm_side"], "right")
        self.assertEqual(recognize.call_args_list[0].kwargs["detected_palm_side"], "left")
        self.assertEqual(recognize.call_args_list[1].kwargs["detected_palm_side"], "right")
        self.assertEqual(recognize.call_count, 2)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(list(save.call_args.kwargs["rois"]), [1])

    def test_camera_detection_preview_returns_boxes_without_archiving(self) -> None:
        from fastapi.testclient import TestClient
        from types import SimpleNamespace

        with patch.object(server, "_camera_contract"), \
             patch.object(server.WORKBENCH, "capture_camera_frame", return_value=SimpleNamespace(rgb=self.image)), \
             patch.object(service_layer, "detect_palms", return_value=self.boxes), \
             patch.object(server.WORKBENCH.captures, "save_multi") as save:
            response = TestClient(server.app).post(
                "/api/cameras/%2Fdev%2Fvideo0/detections", json={"resolution": "640x480"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["detection_count"], 2)
        self.assertEqual(body["hands"][0]["bbox_xyxy"], list(self.boxes[0].xyxy))
        self.assertEqual(body["hands"][0]["detected_palm_side"], "left")
        self.assertEqual(body["hands"][1]["detected_palm_side"], "right")
        self.assertTrue(body["preview_url"].startswith("data:image/"))
        self.assertEqual(body["capture_resolution"], "640x480")
        self.assertEqual(body["preview_resolution"], "640x480")
        self.assertIn("debug_capture", body)
        self.assertFalse(body["debug_capture"]["saved"])
        save.assert_not_called()

    def test_empty_frame_and_detector_failure(self) -> None:
        with patch.object(server, "_validate_options"), \
             patch.object(server.service_layer, "detect_palms", return_value=[]), \
             patch.object(server.WORKBENCH.captures, "save_multi", return_value={"capture_id": "empty"}):
            payload = server._recognition_payload(self.image, server.RecognitionOptions())
        self.assertEqual(payload["detection_count"], 0)
        self.assertEqual(payload["hands"], [])
        with patch.object(server, "_validate_options"), \
             patch.object(server.service_layer, "detect_palms", side_effect=RuntimeError("ACL failure")):
            with self.assertRaisesRegex(RuntimeError, "ACL failure"):
                server._recognition_payload(self.image, server.RecognitionOptions())
