from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FETCH = load_module("mindspore_profile_fetch", "scripts/fetch_mindspore_profile_artifacts.py")
REGISTRY = load_module("mindspore_acceptance_registry", "scripts/create_mindspore_acceptance_registry.py")


class MindSpore20TCampaignToolsTests(unittest.TestCase):
    def test_huggingface_endpoint_mirrors_direct_urls(self):
        previous = os.environ.get("HF_ENDPOINT")
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com/"
        try:
            self.assertEqual(
                FETCH._pinned_url(
                    "https://huggingface.co/Qwen/demo/resolve/main/config.json?download=true",
                    "a" * 40,
                ),
                "https://hf-mirror.com/Qwen/demo/resolve/" + "a" * 40 + "/config.json?download=true",
            )
            self.assertEqual(
                FETCH._pinned_url("https://example.invalid/model.bin", "a" * 40),
                "https://example.invalid/model.bin",
            )
        finally:
            if previous is None:
                os.environ.pop("HF_ENDPOINT", None)
            else:
                os.environ["HF_ENDPOINT"] = previous

    def test_modelers_mirror_builds_native_modelers_url(self):
        url = FETCH._pinned_url(
            "https://huggingface.co/MindSpore-Lab/MiniCPM3-4B-FP16/resolve/main/config.json?download=true",
            "b" * 40,
            mirror="modelers",
            repository="MindSpore-Lab/MiniCPM3-4B-FP16",
        )
        self.assertEqual(
            url,
            "https://modelers.cn/coderepo/web/v1/file/MindSpore-Lab/MiniCPM3-4B-FP16/"
            + "b" * 40
            + "/media/config.json",
        )

    def test_modelers_main_revision_is_not_resolved_through_hf_api(self):
        profile = {
            "id": "modelers-fixture",
            "repository": "MindSpore-Lab/fixture",
            "revision": "main",
            "mirror": "modelers",
            "cache_dir": "artifacts/models/fixture",
            "artifacts": [{
                "name": "config", "kind": "config", "filename": "config.json",
                "url": "https://huggingface.co/MindSpore-Lab/fixture/resolve/main/config.json",
                "expected_bytes": None, "sha256": None,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FETCH.FetchError) as error:
                FETCH.fetch(profile, Path(directory), resolve_main=True)
        self.assertIn("immutable Modelers revision", str(error.exception))

    def test_existing_artifact_is_locked_without_network(self):
        payload = b"case9-model-artifact\n"
        digest = hashlib.sha256(payload).hexdigest()
        profile = {
            "id": "fixture-model",
            "repository": "example/fixture",
            "revision": "a" * 40,
            "cache_dir": "artifacts/models/fixture",
            "artifacts": [{
                "name": "model", "kind": "weights", "filename": "model.bin",
                "url": "https://example.invalid/model.bin",
                "expected_bytes": len(payload), "sha256": digest,
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / profile["cache_dir"] / "model.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            locked = FETCH.fetch(profile, root, resolve_main=False)
        self.assertEqual(locked["resolved_revision"], "a" * 40)
        self.assertEqual(locked["artifacts"][0]["action"], "reused")
        self.assertEqual(locked["artifacts"][0]["sha256"], digest)

    def test_ephemeral_registry_only_admits_selected_20t_profile(self):
        digest = "b" * 64
        source = {
            "schema_version": 2,
            "profiles": [{
                "id": "fixture-model", "candidate_kind": "native_mindspore",
                "revision": "a" * 40, "tokenizer_revision": "a" * 40,
                "cache_dir": "artifacts/models/fixture", "status": "blocked",
                "admission": {"eligible": False, "reason": "fixture"},
                "artifacts": [{"name": "model", "filename": "model.bin", "url": "https://example.invalid/model.bin"}],
                "validation": {"Ascend310B1": {"status": "not-run"}},
            }],
        }
        lock = {
            "profile": "fixture-model", "resolved_revision": "c" * 40,
            "artifacts": [{"name": "model", "bytes": 17, "sha256": digest, "url": "https://example.invalid/pinned.bin"}],
        }
        built = REGISTRY.build(source, lock, "fixture-model", "fixture-run", cache_dir_override="model")
        profile = built["profiles"][0]
        self.assertEqual(profile["status"], "blocked")
        self.assertEqual(profile["cache_dir"], "model")
        self.assertEqual(profile["revision"], "c" * 40)
        self.assertEqual(profile["validation"]["Ascend310B1"]["status"], "experimental_dirty_base")
        self.assertFalse(profile["admission"]["eligible"])


if __name__ == "__main__":
    unittest.main()
