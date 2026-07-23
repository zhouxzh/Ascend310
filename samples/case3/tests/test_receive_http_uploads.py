from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.receive_http_uploads import resolve_upload_target


class UploadPathTest(unittest.TestCase):
    def test_resolves_nested_path_below_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            target = resolve_upload_target(root, "om/fp16/Violin.om")

            self.assertEqual(target, root / "om" / "fp16" / "Violin.om")

    def test_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                resolve_upload_target(Path(directory), "om/%2e%2e/secret")

    def test_rejects_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                resolve_upload_target(Path(directory), "/tmp/model.om")


if __name__ == "__main__":
    unittest.main()
