from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
import importlib.util

import numpy as np

if importlib.util.find_spec("cv2") is not None:
    from palmprint_workbench.domain.captures import CaptureStore, DetectorDebugCaptureStore
else:  # pragma: no cover - local controller without OpenCV
    CaptureStore = None  # type: ignore[assignment,misc]
    DetectorDebugCaptureStore = None  # type: ignore[assignment,misc]


@unittest.skipUnless(CaptureStore is not None, "OpenCV is required for image archive tests")
class CaptureStoreTests(unittest.TestCase):
    @staticmethod
    def _image(value: int = 80) -> np.ndarray:
        return np.full((32, 32, 3), value, dtype=np.uint8)

    def test_save_writes_original_roi_metadata_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory) / "data" / "captures")
            record = store.save(
                original=self._image(),
                roi=self._image(120),
                metadata={"purpose": "enrollment", "source": "upload", "model_id": "ccnet"},
            )
            capture_dir = Path(directory) / "data" / "captures" / record["capture_id"]
            self.assertTrue((capture_dir / "original.jpg").is_file())
            self.assertTrue((capture_dir / "roi.png").is_file())
            self.assertTrue((capture_dir / "metadata.json").is_file())
            self.assertEqual(store.get(record["capture_id"])["model_id"], "ccnet")
            self.assertTrue((Path(directory) / "data" / "captures" / "index.json").is_file())

    def test_roi_failure_keeps_original_and_has_no_roi_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory))
            record = store.save(
                original=self._image(),
                roi=None,
                metadata={"roi_ok": False, "status": "recognition_failed"},
            )
            self.assertTrue(store.path_for(record["capture_id"], "original").is_file())
            with self.assertRaises(FileNotFoundError):
                store.path_for(record["capture_id"], "roi")

    def test_multi_hand_capture_writes_one_original_and_indexed_rois(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory))
            record = store.save_multi(original=self._image(), rois={1: self._image(80), 3: self._image(120)}, metadata={"purpose": "recognition"})
            folder = Path(directory) / record["capture_id"]
            self.assertTrue((folder / "original.jpg").is_file())
            self.assertTrue((folder / "roi_001.png").is_file())
            self.assertTrue((folder / "roi_003.png").is_file())
            self.assertEqual(len(list(folder.glob("original*.jpg"))), 1)
            self.assertEqual(set(store.get(record["capture_id"])["roi_paths"]), {"1", "3"})

    def test_detector_debug_archive_is_disabled_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reports" / "debug" / "detector"
            archive = DetectorDebugCaptureStore(
                root, enabled=False, max_frames=2, min_interval_seconds=0.0
            )
            result = archive.maybe_save(
                image_rgb=self._image(),
                device="/dev/video2",
                requested_resolution="640x480",
                detections=[],
                detection_ms=20.0,
            )
            self.assertFalse(result["saved"])
            self.assertEqual(result["reason"], "disabled")
            self.assertFalse(root.exists())

    def test_detector_debug_archive_writes_bounded_raw_overlay_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reports" / "debug" / "detector"
            archive = DetectorDebugCaptureStore(
                root, enabled=True, max_frames=1, min_interval_seconds=0.0
            )
            result = archive.maybe_save(
                image_rgb=self._image(),
                device="/dev/video2",
                requested_resolution="640x480",
                detections=[{
                    "index": 1,
                    "bbox_xyxy": [2.0, 3.0, 20.0, 24.0],
                    "detection_score": 0.87,
                    "detected_palm_side": "left",
                }],
                detection_ms=20.0,
                detector_debug={"classes": [{"palm_side": "left", "max_score": 0.87}]},
            )
            self.assertTrue(result["saved"])
            self.assertEqual(result["stored_frames"], 1)
            run_dir = root / result["run_id"]
            self.assertTrue((run_dir / "frame-0001.jpg").is_file())
            self.assertTrue((run_dir / "frame-0001-overlay.jpg").is_file())
            metadata = json.loads((run_dir / "frame-0001.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["device"], "/dev/video2")
            self.assertEqual(metadata["detections"][0]["detected_palm_side"], "left")
            self.assertEqual(metadata["detector_debug"]["classes"][0]["max_score"], 0.87)
            limited = archive.maybe_save(
                image_rgb=self._image(90),
                device="/dev/video2",
                requested_resolution="640x480",
                detections=[],
                detection_ms=21.0,
            )
            self.assertFalse(limited["saved"])
            self.assertEqual(limited["reason"], "frame_limit_reached")

    def test_concurrent_saves_do_not_overwrite_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory))

            def save(index: int) -> str:
                return store.save(
                    original=self._image(index),
                    roi=self._image(index + 1),
                    metadata={"status": str(index)},
                )["capture_id"]

            with ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(save, range(24)))
            self.assertEqual(len(set(ids)), 24)
            self.assertEqual(len(store.list(limit=100)), 24)

    def test_paths_are_confined_and_cleanup_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory))
            record = store.save(original=self._image(), roi=self._image(), metadata={})
            with self.assertRaises(ValueError):
                store.path_for("../escape", "original")
            self.assertFalse(store.cleanup("missing"))
            self.assertTrue(store.cleanup(record["capture_id"]))
            self.assertEqual(store.list(limit=10), [])

    def test_template_deletion_removes_metadata_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory))
            record = store.save(
                original=self._image(),
                roi=self._image(),
                metadata={"model_id": "ccnet", "palm_id": "P-001:left"},
            )
            self.assertEqual(store.purge_template(model_id="ccnet", palm_id="P-001:left"), 1)
            with self.assertRaises(KeyError):
                store.get(record["capture_id"])
            self.assertFalse((Path(directory) / record["capture_id"]).exists())


if __name__ == "__main__":
    unittest.main()
