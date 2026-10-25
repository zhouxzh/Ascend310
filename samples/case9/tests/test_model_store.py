from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from ascend_edge_chat.artifacts import model_store


class ModelStoreTests(unittest.TestCase):
    def _manifest(self, profile: object) -> dict[str, object]:
        artifacts = [
            {
                "name": item.name,
                "filename": item.filename,
                "bytes": item.expected_bytes,
                "sha256": item.sha256,
            }
            for item in profile.artifacts
        ]
        return {
            "schema_version": 1,
            "source_kind": "offline-bundle",
            "repository": profile.repository,
            "revision": profile.revision,
            "license": profile.license,
            "artifacts": artifacts,
        }

    def test_offline_bundle_requires_matching_provenance_manifest(self) -> None:
        profile = model_store.release_profile()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with self.assertRaisesRegex(model_store.ArtifactError, "manifest"):
                model_store._verify_offline_bundle_manifest(source, profile)
            source.joinpath("offline-bundle.manifest.json").write_text(
                json.dumps(self._manifest(profile)), encoding="utf-8"
            )
            model_store._verify_offline_bundle_manifest(source, profile)

    def test_offline_bundle_rejects_another_revision(self) -> None:
        profile = model_store.release_profile()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = self._manifest(profile)
            manifest["revision"] = "0" * 40
            source.joinpath("offline-bundle.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(model_store.ArtifactError, "revision"):
                model_store._verify_offline_bundle_manifest(source, profile)

    def test_tutorial_lock_rejects_replaced_cache_file(self) -> None:
        payload = b"expected lesson artifact"
        digest = hashlib.sha256(payload).hexdigest()
        profile = SimpleNamespace(
            id="lesson",
            repository="example/lesson",
            revision="a" * 40,
            cache_dir="models/lesson",
        )
        lock = {
            "schema_version": 1,
            "profiles": {
                "lesson": {
                    "repository": "example/lesson",
                    "revision": "a" * 40,
                    "artifacts": [
                        {
                            "name": "model",
                            "kind": "weights",
                            "filename": "model.bin",
                            "bytes": len(payload),
                            "sha256": digest,
                        }
                    ],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "models" / "lesson"
            cache.mkdir(parents=True)
            cache.joinpath("model.bin").write_bytes(payload)
            lock_path = root / "tutorial-lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            verified = model_store.verify_tutorial_profile_artifacts(
                root, profile, lock_path=lock_path
            )
            self.assertEqual(len(verified), 1)
            cache.joinpath("model.bin").write_bytes(b"replaced")
            with self.assertRaisesRegex(model_store.ArtifactError, "size mismatch"):
                model_store.verify_tutorial_profile_artifacts(
                    root, profile, lock_path=lock_path
                )


if __name__ == "__main__":
    unittest.main()
