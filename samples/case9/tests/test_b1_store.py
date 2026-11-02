from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ascend_edge_chat.static_kv.b1_store import B1DownloadError, load_b1_download_lock


class B1StoreTests(unittest.TestCase):
    def _document(self, *, revision: str = "a" * 40) -> dict[str, object]:
        entries = []
        names = (
            "onnx", "tokenizer", "tokenizer-config", "license", "controller-contract",
            "b1-om", "b1-descriptor-contract", "tokenizer-lock", "b1-om-upstream-lock",
        )
        for index, name in enumerate(names):
            entries.append(
                {
                    "name": name,
                    "source_path": "models/test/%s.bin" % name,
                    "destination": "candidate/%s.bin" % name,
                    "bytes": index + 1,
                    "sha256": ("%x" % index) * 64,
                }
            )
        return {
            "schema_version": 1,
            "candidate_id": "qwen2.5-0.5b-instruct-static-kv-1024-b1-offline",
            "release_status": "experimental-machine",
            "source": {
                "repository": "zhouxzh/ascend310b-llm-om-zoo",
                "revision": revision,
                "mirror": "https://hf-mirror.com",
            },
            "base_model": {
                "repository": "Qwen/Qwen2.5-0.5B-Instruct",
                "revision": "7ae557604adf67be50417f59c2c2f167def9a775",
            },
            "target": {"soc": "Ascend310B1", "tier": "20T", "cann": "8.0.0", "python": "3.9.25"},
            "artifacts": entries,
        }

    def test_source_lock_requires_a_full_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b1.json"
            path.write_text(json.dumps(self._document(revision="main")), encoding="utf-8")
            with self.assertRaises(B1DownloadError):
                load_b1_download_lock(path)

    def test_source_lock_accepts_exact_revision_and_all_nine_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b1.json"
            path.write_text(json.dumps(self._document()), encoding="utf-8")
            lock = load_b1_download_lock(path)
        self.assertEqual(lock.revision, "a" * 40)
        self.assertEqual(len(lock.artifacts), 9)
        self.assertEqual({artifact.name for artifact in lock.artifacts}, {
            "onnx", "tokenizer", "tokenizer-config", "license", "controller-contract",
            "b1-om", "b1-descriptor-contract", "tokenizer-lock", "b1-om-upstream-lock",
        })


if __name__ == "__main__":
    unittest.main()
