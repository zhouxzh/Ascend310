from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_mindspore_profile_artifacts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_mindspore_profile_artifacts_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load_module()


def profile_for(root: Path, *, status: str = "experimental_dirty_base", expected: bool = True):
    data = b"case9-model-artifact\n"
    digest = hashlib.sha256(data).hexdigest()
    artifact = SimpleNamespace(
        name="model",
        kind="weights",
        filename="model.bin",
        expected_bytes=len(data) if expected else None,
        sha256=digest if expected else None,
    )
    profile = SimpleNamespace(
        id="fixture-profile",
        model_id="fixture/model",
        revision="a" * 40,
        board_host="127.0.0.1",
        board_soc="Ascend310B4",
        board_tier="8T",
        status=status,
        cache_dir="artifacts/models/fixture",
        artifacts=(artifact,),
    )
    path = root / profile.cache_dir / artifact.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return profile, path


class MindSporeArtifactVerifierTests(unittest.TestCase):
    def test_verified_regular_artifact_reports_bytes_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, path = profile_for(root)
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["artifact_verified"])
            self.assertEqual(report["checked"], 1)
            self.assertEqual(report["verified"], 1)
            self.assertEqual(report["artifacts"][0]["actual_bytes"], path.stat().st_size)
            self.assertEqual(report["artifacts"][0]["actual_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_size_and_digest_mismatch_fails_without_mutating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, path = profile_for(root)
            profile.artifacts[0].expected_bytes += 1
            profile.artifacts[0].sha256 = "0" * 64
            original = path.read_bytes()
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["artifact_verified"])
            self.assertGreaterEqual(len(report["errors"]), 2)
            self.assertEqual(path.read_bytes(), original)

    def test_symlink_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, path = profile_for(root)
            outside = root / "outside.bin"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            try:
                path.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "failed")
            self.assertIn("symlink", " ".join(report["errors"]).lower())

    def test_symlinked_cache_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, path = profile_for(root)
            cache = path.parent
            real_cache = root / "real-cache"
            real_cache.mkdir()
            path.rename(real_cache / path.name)
            cache.rmdir()
            try:
                cache.symlink_to(real_cache, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "failed")
            self.assertIn("symlink", " ".join(report["errors"]).lower())

    def test_path_escape_and_non_regular_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, _ = profile_for(root)
            profile.cache_dir = "../outside"
            escaped = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(escaped["status"], "failed")
            self.assertIn("relative", " ".join(escaped["errors"]).lower())
            profile.cache_dir = "artifacts/models/fixture:ads"
            colon = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(colon["status"], "failed")
            self.assertIn("relative", " ".join(colon["errors"]).lower())
            file_root = root / "root-file"
            file_root.write_text("x", encoding="utf-8")
            with self.assertRaises(VERIFY.VerificationError):
                VERIFY.verify_profile_artifacts(profile_for(root)[0], file_root)

    def test_profile_without_artifacts_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, _ = profile_for(root)
            profile.artifacts = ()
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["artifact_verified"])

    def test_missing_lock_values_are_unverified_for_active_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, _ = profile_for(root, expected=False)
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["artifacts"][0]["status"], "unverified")
            self.assertEqual(report["checked"], 0)

    def test_blocked_profile_is_not_promoted_when_artifacts_are_unlocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, path = profile_for(root, status="blocked", expected=False)
            path.unlink()
            report = VERIFY.verify_profile_artifacts(profile, root)
            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["artifact_verified"])

    def test_output_report_is_written_atomically_and_cli_accepts_positional_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, path = profile_for(root)
            raw = {
                "schema_version": 1,
                "profiles": [{
                    "id": profile.id,
                    "display_name": "Fixture",
                    "model_id": profile.model_id,
                    "repository": "fixture/model",
                    "source": "local",
                    "revision": profile.revision,
                    "tokenizer_revision": profile.revision,
                    "revision_pinned": True,
                    "mirror": None,
                    "board": {"host": profile.board_host, "soc": profile.board_soc, "tier": profile.board_tier},
                    "runtime": {"provider": "mindspore", "context_length": 1024, "default_max_tokens": 32, "max_tokens": 80, "temperature": 0.0, "top_p": 1.0},
                    "cache_dir": profile.cache_dir,
                    "artifacts": [{"name": "model", "kind": "weights", "filename": "model.bin", "url": "https://example.invalid/model.bin", "expected_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
                    "status": "experimental_dirty_base",
                    "admission": {"eligible": False, "reason": "fixture"},
                    "notes": "fixture",
                }],
            }
            registry = root / "registry.json"
            registry.write_text(json.dumps(raw), encoding="utf-8")
            output = root / "reports" / "artifact-report.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = VERIFY.main([profile.id, "--registry", str(registry), "--root", str(root), "--output", str(output)])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "passed")
            self.assertTrue(output.is_file())
            stored = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(stored["profile"], profile.id)


if __name__ == "__main__":
    unittest.main()
