from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from tools.download_model_release import (
    DEFAULT_PIANO_COMMIT,
    DEFAULT_PIANO_REVISION,
    REPOSITORY,
    download_http,
    default_release_files,
    hf_url,
    parse_sha256s,
    validate_pinned_revision,
    validate_requested_revision,
    validate_release,
    verified,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status: int) -> None:
        super().__init__(payload)
        self.status = status

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.ranges: list[str | None] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        del timeout
        self.ranges.append(request.get_header("Range"))
        return self.responses.pop(0)


class ModelReleaseDownloadTest(unittest.TestCase):
    def test_http_range_resumes_part_file_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "model.onnx"
            target.with_name("model.onnx.part").write_bytes(b"abc")
            opener = FakeOpener([FakeResponse(b"def", 206)])
            download_http(opener, "https://example.invalid/model", target, None, 2.0)
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertEqual(opener.ranges, ["bytes=3-"])
            self.assertFalse(target.with_name("model.onnx.part").exists())

    def test_http_restarts_when_server_ignores_range(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "model.onnx"
            target.with_name("model.onnx.part").write_bytes(b"stale")
            opener = FakeOpener([FakeResponse(b"ignored", 200), FakeResponse(b"complete", 200)])
            download_http(opener, "https://example.invalid/model", target, None, 2.0)
            self.assertEqual(target.read_bytes(), b"complete")
            self.assertEqual(opener.ranges, ["bytes=5-", None])

    def test_default_release_rejects_a_moved_tag(self) -> None:
        validate_pinned_revision(REPOSITORY, DEFAULT_PIANO_REVISION, DEFAULT_PIANO_COMMIT)
        with self.assertRaisesRegex(RuntimeError, "expected"):
            validate_pinned_revision(REPOSITORY, DEFAULT_PIANO_REVISION, "0" * 40)

    def test_download_rejects_moving_branch_names(self) -> None:
        for revision in ("main", "master", "HEAD"):
            with self.assertRaisesRegex(ValueError, "fixed release"):
                validate_requested_revision(revision)

    def test_checksum_parser_rejects_malformed_or_unsafe_entries(self) -> None:
        with self.assertRaises(ValueError):
            parse_sha256s("not-a-hash  model.onnx")
        with self.assertRaises(ValueError):
            parse_sha256s(f"{'0' * 64}  ../model.onnx")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.onnx"
            path.write_bytes(b"bad")
            self.assertFalse(verified(path, "0" * 64))

    def test_release_validation_rejects_a_sha256_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "model.onnx").write_bytes(b"model")
            hashes = {"model.onnx": hashlib.sha256(b"other").hexdigest()}
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                validate_release(root, hashes, ("model.onnx",))

    def test_hf_url_uses_the_resolved_commit_and_release_directory(self) -> None:
        url = hf_url(REPOSITORY, DEFAULT_PIANO_COMMIT, "midi-ddsp/v2/model.onnx")
        self.assertIn(DEFAULT_PIANO_COMMIT, url)
        self.assertTrue(url.endswith("midi-ddsp/v2/model.onnx"))

    def test_default_selection_excludes_source_only_model_formats(self) -> None:
        files = default_release_files(
            {"model.onnx": "0" * 64, "model.om": "1" * 64, "weights.pt": "2" * 64}
        )
        self.assertEqual(files, ("model.onnx", "model.om"))


if __name__ == "__main__":
    unittest.main()
