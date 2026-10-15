from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qwen25_acl_contract import QWEN25_SUPPORTED_EXECUTION_MODE
from qwen25_legacy_compat_service import (
    ArtifactDigest,
    ArtifactPaths,
    CompatibilityError,
    CompatibilityProfile,
    SOURCE_SOC,
    TARGET_SOC,
)


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_qwen25_legacy_compat_pin_manifest.py"
SPEC = importlib.util.spec_from_file_location("create_qwen25_legacy_compat_pin_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _profile(paths: ArtifactPaths) -> CompatibilityProfile:
    artifacts = {}
    for name, path in paths.items().items():
        data = path.read_bytes()
        artifacts[name] = ArtifactDigest(len(data), hashlib.sha256(data).hexdigest())
    return CompatibilityProfile(
        key="fixture-full",
        source_soc=SOURCE_SOC,
        target_soc=TARGET_SOC,
        execution_mode=QWEN25_SUPPORTED_EXECUTION_MODE,
        artifacts=artifacts,
    )


class CreateLegacyPinManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.paths = ArtifactPaths(
            root / "model.om",
            root / "contract.json",
            root / "tokenizer.json",
            root / "tokenizer_config.json",
        )
        for index, path in enumerate(self.paths.items().values()):
            path.write_bytes(("fixture-%d" % index).encode("ascii"))
        self.profile = _profile(self.paths)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_builds_and_atomically_writes_verified_manifest(self):
        output = self.paths.om.parent / "pins.json"
        with patch.object(MODULE, "profile_for", return_value=self.profile):
            document = MODULE.build_manifest(self.profile.key, self.paths)
            digest = MODULE.write_manifest(document, output)
        self.assertTrue(output.is_file())
        self.assertFalse(output.with_name(output.name + ".part").exists())
        self.assertEqual(digest, hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(document["artifacts"]["om"]["sha256"], self.profile.artifacts["om"].sha256)

    def test_rejects_substituted_artifact_and_bad_expected_output_digest(self):
        output = self.paths.om.parent / "pins.json"
        self.paths.om.write_bytes(b"substituted")
        with patch.object(MODULE, "profile_for", return_value=self.profile):
            with self.assertRaisesRegex(CompatibilityError, "fixed historical identity"):
                MODULE.build_manifest(self.profile.key, self.paths)

        # Restore the profile to the new fixture identity solely to exercise
        # the output digest gate independently from source identity checks.
        restored = _profile(self.paths)
        with patch.object(MODULE, "profile_for", return_value=restored):
            document = MODULE.build_manifest(restored.key, self.paths)
            with self.assertRaisesRegex(CompatibilityError, "expected-output-sha256"):
                MODULE.write_manifest(document, output, "0" * 64)
        self.assertFalse(output.exists())

    def test_refuses_to_overwrite_existing_manifest(self):
        output = self.paths.om.parent / "pins.json"
        output.write_text("existing evidence", encoding="utf-8")
        with patch.object(MODULE, "profile_for", return_value=self.profile):
            document = MODULE.build_manifest(self.profile.key, self.paths)
            with self.assertRaisesRegex(CompatibilityError, "refusing to overwrite"):
                MODULE.write_manifest(document, output)


if __name__ == "__main__":
    unittest.main()
