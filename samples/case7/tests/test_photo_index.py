import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from photo_index import AlbumIndex, AlbumIndexError


MODEL_A = "model_a__npu__mixed_fp16"
MODEL_B = "model_b__npu__mixed_fp16"


class FakeRegistry:
    def ids(self):
        return (MODEL_A, MODEL_B)


class FakeBackend:
    def __init__(self, model_id):
        self.model_id = model_id

    def encode_image(self, image):
        mean = float(np.asarray(image).mean()) / 255.0
        if self.model_id == MODEL_A:
            return np.array([1.0, mean, 0.1], np.float32)
        return np.array([0.1, 1.0 - mean, 1.0], np.float32)

    def encode_text(self, text):
        if self.model_id == MODEL_A:
            return np.array([1.0, 0.0, 0.1], np.float32)
        return np.array([0.1, 1.0, 1.0], np.float32)


class FakeManager:
    registry = FakeRegistry()

    def get(self, model_id):
        return FakeBackend(model_id)

    def encode_image(self, model_id, image):
        return self.get(model_id).encode_image(image)

    def encode_text(self, model_id, text):
        return self.get(model_id).encode_text(text)


class AlbumIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.photos = root / "photos"
        self.uploads = root / "uploads"
        self.photos.mkdir()
        self.uploads.mkdir()
        self.index = AlbumIndex(
            manager=FakeManager(),
            db_path=root / "album.sqlite3",
            index_dir=root / "indexes",
            import_dir=self.photos / "imports",
            photo_roots=[str(self.photos)],
            allow_numpy_fallback=True,
        )
        self.index._decode_bgr = lambda path: np.full((8, 8, 3), Image.open(path).getpixel((0, 0))[0], np.uint8)
        self.index._count_faces = lambda image: int(image.mean() > 100)

    def tearDown(self):
        self.index.close()
        self.temp.cleanup()

    def image(self, name, value):
        path = self.photos / name
        Image.new("RGB", (16, 16), (value, value, value)).save(path)
        return path

    def test_model_indexes_are_isolated_and_searchable(self):
        dark = self.image("dark.jpg", 10)
        bright = self.image("bright.jpg", 240)
        summary = self.index.index_paths([dark, bright])
        self.assertEqual(summary.indexed, 2)
        self.assertEqual(self.index.stats()["embeddings_by_model"], {MODEL_A: 2, MODEL_B: 2})
        result_a = self.index.search_text("anything", MODEL_A, 1)
        result_b = self.index.search_text("anything", MODEL_B, 1)
        self.assertEqual(result_a[0].model_id, MODEL_A)
        self.assertEqual(result_b[0].model_id, MODEL_B)

    def test_incremental_duplicate_and_clear_preserve_photos(self):
        original = self.image("one.jpg", 120)
        first = self.index.index_paths([original])
        second = self.index.index_paths([original])
        duplicate = self.photos / "copy.jpg"
        duplicate.write_bytes(original.read_bytes())
        third = self.index.index_paths([duplicate])
        self.assertEqual(first.indexed, 1)
        self.assertEqual(second.unchanged, 1)
        self.assertEqual(third.duplicates, 1)
        with self.assertRaises(AlbumIndexError):
            self.index.clear_embeddings(False)
        self.index.clear_embeddings(True)
        self.assertTrue(original.is_file())
        self.assertEqual(self.index.stats()["embeddings_by_model"], {})

    def test_unchanged_photo_skips_face_scan_but_fills_missing_embedding(self):
        """Incremental indexing must avoid the high-resolution Haar pass."""
        original = self.image("unchanged.jpg", 120)
        self.index.index_paths([original])

        decode_calls = []
        face_calls = []

        def unexpected_decode(path):
            decode_calls.append(path)
            raise AssertionError("unchanged validation should not decode the photo")

        def unexpected_face_scan(image):
            face_calls.append(image)
            raise AssertionError("unchanged validation should not run Haar detection")

        self.index._decode_bgr = unexpected_decode
        self.index._count_faces = unexpected_face_scan
        unchanged = self.index.index_paths([original])
        self.assertEqual(unchanged.unchanged, 1)
        self.assertEqual(decode_calls, [])
        self.assertEqual(face_calls, [])

        # Remove only one model vector.  The unchanged photo must be queued
        # for that model, while face detection remains skipped.
        self.index._connection.execute(
            "DELETE FROM embeddings WHERE photo_id=(SELECT id FROM photos WHERE filepath=?) AND model_id=?",
            (str(original.resolve()), MODEL_B),
        )
        self.index._connection.commit()
        self.index._indexes.clear()
        encode_calls = []
        original_decode = lambda path: np.full(
            (8, 8, 3), Image.open(path).getpixel((0, 0))[0], np.uint8
        )
        original_encode = self.index.manager.encode_image
        self.index._decode_bgr = lambda path: (decode_calls.append(path) or original_decode(path))
        self.index.manager.encode_image = lambda model_id, image: (
            encode_calls.append(model_id) or original_encode(model_id, image)
        )
        rebuilt = self.index.index_paths([original])
        self.assertEqual(rebuilt.indexed, 1)
        self.assertEqual(rebuilt.unchanged, 0)
        self.assertEqual(encode_calls, [MODEL_B])
        self.assertEqual(len(decode_calls), 1)
        self.assertEqual(face_calls, [])
        self.assertEqual(self.index.stats()["embeddings_by_model"], {MODEL_A: 1, MODEL_B: 1})

    def test_clear_one_model_preserves_other_vectors_and_photos(self):
        dark = self.image("dark.jpg", 10)
        bright = self.image("bright.jpg", 240)
        self.index.index_paths([dark, bright])
        with self.assertRaises(AlbumIndexError):
            self.index.clear_model_embeddings(MODEL_A)
        self.assertEqual(self.index.clear_model_embeddings(MODEL_A, confirmed=True), 2)
        self.assertEqual(self.index.stats()["embeddings_by_model"], {MODEL_B: 2})
        rebuilt = self.index.index_paths([dark, bright], model_ids=[MODEL_A])
        self.assertEqual(rebuilt.indexed, 2)
        self.assertTrue(dark.is_file())
        self.assertTrue(bright.is_file())
        self.assertEqual(self.index.stats()["embeddings_by_model"], {MODEL_A: 2, MODEL_B: 2})

    def test_upload_is_copied_and_deduplicated(self):
        outside = self.uploads / "upload.png"
        Image.new("RGB", (16, 16), (30, 30, 30)).save(outside)
        first = self.index.import_uploads([str(outside)])
        second = self.index.import_uploads([str(outside)])
        self.assertEqual(first.indexed, 1)
        self.assertEqual(second.duplicates, 1)
        self.assertEqual(len(list((self.photos / "imports").glob("*.png"))), 1)

    def test_upload_deduplicates_repeated_parts_in_one_batch(self):
        outside = self.uploads / "upload.png"
        Image.new("RGB", (16, 16), (45, 45, 45)).save(outside)
        summary = self.index.import_uploads([str(outside), str(outside)])
        self.assertEqual(summary.indexed, 1)
        self.assertEqual(summary.duplicates, 1)
        self.assertEqual(len(self.index.list_photos()), 1)

    def test_progress_reporter_tracks_validation_and_model_encoding(self):
        dark = self.image("dark.jpg", 10)
        bright = self.image("bright.jpg", 240)
        events = []
        summary = self.index.index_paths([dark, bright], progress_reporter=events.append)
        self.assertEqual(summary.indexed, 2)
        validating = [event for event in events if event["phase"] == "validating"]
        self.assertEqual([event["files_completed"] for event in validating], [0, 1, 2])
        encoding = [event for event in events if event["phase"] == "embedding"]
        self.assertEqual([event["embedding_completed"] for event in encoding], [0, 1, 2, 3, 4])
        self.assertTrue(all(event["embedding_total"] == 4 for event in encoding))
        self.assertEqual(
            [event["current_model"] for event in encoding[1:]],
            [MODEL_A, MODEL_A, MODEL_B, MODEL_B],
        )
        self.assertEqual(events[-1]["phase"], "finalizing")

    def test_upload_progress_reporter_tracks_imports_before_indexing(self):
        outside = self.uploads / "upload.png"
        Image.new("RGB", (16, 16), (55, 55, 55)).save(outside)
        events = []
        summary = self.index.import_uploads([str(outside)], progress_reporter=events.append)
        self.assertEqual(summary.indexed, 1)
        importing = [event for event in events if event["phase"] == "importing"]
        self.assertEqual([event["files_completed"] for event in importing], [0, 1])
        self.assertEqual(importing[-1]["accepted"], 1)
        self.assertTrue(any(event["phase"] == "embedding" for event in events))

    def test_new_upload_has_no_manual_tags_and_preserves_legacy_metadata(self):
        outside = self.uploads / "upload.png"
        Image.new("RGB", (20, 10), (30, 30, 30)).save(outside)
        self.index.import_uploads([str(outside)])
        row = self.index.list_photos()[0]
        self.assertEqual(row["width"], 20)
        self.assertEqual(row["capture_time_source"], "upload_time")
        self.assertEqual(row["tags"], "")
        # Existing databases may already contain tags from an older release.
        # They remain readable as compatibility metadata, but import_uploads
        # never asks callers to provide manual semantic labels.
        self.index.update_photo_metadata(row["id"], {"tags": "家庭 晴天"})
        self.assertEqual(self.index.get_photo(row["id"])["tags"], "家庭 晴天")
        with self.assertRaises(AlbumIndexError):
            self.index.delete_photo(row["id"], confirmed=False)
        self.assertTrue(self.index.delete_photo(row["id"], confirmed=True)["deleted"])
        self.assertEqual(self.index.stats()["available_photos"], 0)

    def test_schema_v2_backfills_existing_photo_metadata(self):
        photo = self.image("legacy.jpg", 90)
        self.index.index_paths([photo])
        self.index._connection.execute(
            "UPDATE photos SET width=NULL, height=NULL, mime_type=NULL WHERE filepath=?", (str(photo.resolve()),)
        )
        self.index._connection.commit()
        self.index.close()
        self.index = AlbumIndex(
            manager=FakeManager(),
            db_path=Path(self.temp.name) / "album.sqlite3",
            index_dir=Path(self.temp.name) / "indexes",
            import_dir=self.photos / "imports",
            photo_roots=[str(self.photos)],
            allow_numpy_fallback=True,
        )
        row = self.index.list_photos()[0]
        self.assertEqual((row["width"], row["height"]), (16, 16))
        self.assertEqual(row["mime_type"], "image/jpeg")

    def test_directory_escape_and_missing_photo_are_handled(self):
        outside = self.uploads / "outside.jpg"
        Image.new("RGB", (16, 16), (10, 10, 10)).save(outside)
        with self.assertRaises(AlbumIndexError):
            self.index.discover(str(self.uploads))
        photo = self.image("gone.jpg", 80)
        self.index.index_paths([photo])
        photo.unlink()
        self.assertEqual(self.index.mark_unavailable(), 1)
        self.assertEqual(self.index.stats()["unavailable_photos"], 1)


if __name__ == "__main__":
    unittest.main()
