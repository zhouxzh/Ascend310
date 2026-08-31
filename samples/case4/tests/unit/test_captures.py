from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
import importlib.util

import numpy as np

if importlib.util.find_spec("cv2") is not None:
    from palmprint_workbench.domain.captures import CaptureStore
else:  # pragma: no cover - local controller without OpenCV
    CaptureStore = None  # type: ignore[assignment,misc]


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

    def test_template_deletion_only_marks_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(Path(directory))
            record = store.save(
                original=self._image(),
                roi=self._image(),
                metadata={"model_id": "ccnet", "user_id": "alice"},
            )
            self.assertEqual(store.mark_template_deleted(model_id="ccnet", user_id="alice"), 1)
            updated = store.get(record["capture_id"])
            self.assertTrue(updated["template_deleted"])
            self.assertTrue(store.path_for(record["capture_id"], "original").is_file())
            self.assertTrue(store.path_for(record["capture_id"], "roi").is_file())


if __name__ == "__main__":
    unittest.main()
