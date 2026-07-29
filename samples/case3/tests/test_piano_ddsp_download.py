from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from tools.download_piano_ddsp_onnx import (
    download_ace2,
    download_http,
    parse_sha256s,
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


class PianoDownloadTest(unittest.TestCase):
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

    def test_explicit_ace2_transfer_records_actual_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "artifact.bin"

            def fake_run(command: list[str], check: bool) -> SimpleNamespace:
                del check
                Path(command[-1]).write_bytes(b"artifact")
                return SimpleNamespace(returncode=0)

            with mock.patch("tools.download_piano_ddsp_onnx.subprocess.run", side_effect=fake_run):
                source = download_ace2("ace2", "/fixed/release", "artifact.bin", target)
            self.assertEqual(source, "/fixed/release/artifact.bin")
            self.assertEqual(target.read_bytes(), b"artifact")

    def test_checksum_parser_rejects_malformed_digest(self) -> None:
        with self.assertRaises(ValueError):
            parse_sha256s("not-a-hash  model.onnx")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.onnx"
            path.write_bytes(b"bad")
            self.assertFalse(verified(path, "0" * 64))


if __name__ == "__main__":
    unittest.main()
