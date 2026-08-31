import os
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import config
from photo_index import AlbumIndex, AlbumIndexError


class PhotoStoragePathTests(unittest.TestCase):
    @staticmethod
    def _load_config_with_environment(values):
        spec = importlib.util.spec_from_file_location("case7_storage_config", Path(config.__file__))
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, values, clear=False):
            spec.loader.exec_module(module)
        return module

    def test_default_library_is_a_managed_pictures_child(self):
        expected = Path.home() / "Pictures" / "ai-album"
        # Environment overrides are useful for board mounts and tests.  When
        # no override is active, ordinary uploads must be outside the release
        # tree and the broad Pictures directory must not itself be indexed.
        if not os.environ.get("SMART_ALBUM_PHOTO_DIR"):
            self.assertEqual(Path(config.PHOTO_LIBRARY_DIR), expected.resolve())
        self.assertEqual(Path(config.IMPORT_DIR), Path(config.PHOTO_LIBRARY_DIR) / "imports")
        self.assertIn(Path(config.PHOTO_LIBRARY_DIR), {Path(root) for root in config.PHOTO_ROOTS})
        self.assertNotIn(Path.home() / "Pictures", {Path(root) for root in config.PHOTO_ROOTS})
        self.assertNotEqual(Path(config.IMPORT_DIR).parent, Path(config.PHOTO_DIR))

    def test_upload_staging_honors_isolated_test_data_dir(self):
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary) / "data"
            with mock.patch.object(app, "DATA_DIR", str(isolated)):
                self.assertEqual(app._upload_staging_dir(), isolated / "upload-tmp")

    def test_photo_library_override_stays_outside_release_and_contains_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "managed-library"
            configured = self._load_config_with_environment(
                {
                    "SMART_ALBUM_PHOTO_DIR": str(library),
                    "SMART_ALBUM_IMPORT_DIR": str(library / "imports"),
                    "SMART_ALBUM_UPLOAD_TMP_DIR": str(library / ".upload-tmp"),
                }
            )
            self.assertEqual(Path(configured.PHOTO_LIBRARY_DIR), library.resolve())
            self.assertEqual(Path(configured.IMPORT_DIR), library / "imports")
            self.assertEqual(Path(configured.UPLOAD_TMP_DIR), library / ".upload-tmp")

    def test_photo_library_override_rejects_release_tree(self):
        with self.assertRaises(RuntimeError):
            self._load_config_with_environment(
                {
                    "SMART_ALBUM_PHOTO_DIR": str(Path(config.BASE_DIR) / "managed-photos"),
                    "SMART_ALBUM_IMPORT_DIR": str(Path(config.BASE_DIR) / "managed-photos" / "imports"),
                    "SMART_ALBUM_UPLOAD_TMP_DIR": str(Path(config.BASE_DIR) / "managed-photos" / ".upload-tmp"),
                }
            )

    def test_import_destination_must_be_under_an_explicit_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "photos"
            root.mkdir()
            outside = Path(temporary) / "outside" / "imports"
            with self.assertRaises(AlbumIndexError):
                AlbumIndex(
                    db_path=Path(temporary) / "album.sqlite3",
                    index_dir=Path(temporary) / "indexes",
                    import_dir=outside,
                    photo_roots=[str(root)],
                    allow_numpy_fallback=True,
                )

    def test_import_destination_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "photos"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            link = root / "imports"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable on this host")
            with self.assertRaises(AlbumIndexError):
                AlbumIndex(
                    db_path=base / "album.sqlite3",
                    index_dir=base / "indexes",
                    import_dir=link,
                    photo_roots=[str(root)],
                    allow_numpy_fallback=True,
                )

    def test_recursive_discovery_skips_transient_upload_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "photos"
            imports = root / "imports"
            staging = root / ".upload-tmp"
            imports.mkdir(parents=True)
            staging.mkdir()
            # The fixture contents need not be decoded here; discover filters
            # by path and extension before validation/indexing.
            (imports / "kept.jpg").write_bytes(b"photo")
            (staging / "in-flight.jpg").write_bytes(b"partial")
            index = AlbumIndex(
                db_path=base / "album.sqlite3",
                index_dir=base / "indexes",
                import_dir=imports,
                photo_roots=[str(root)],
                allow_numpy_fallback=True,
                upload_tmp_dir=staging,
            )
            try:
                self.assertEqual(index.discover(str(root)), [imports / "kept.jpg"])
            finally:
                index.close()


if __name__ == "__main__":
    unittest.main()
