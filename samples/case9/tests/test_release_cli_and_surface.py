from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ascend_edge_chat import cli
from ascend_edge_chat.cli import main


class ReleaseCliAndSurfaceTests(unittest.TestCase):
    def test_version_command_reports_release_version(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)

    def test_acl_verify_command_uses_the_fixed_public_release_lock(self) -> None:
        verified = {
            "source_kind": "verified-local",
            "source_repository": "zhouxzh/ascend310b-llm-om-zoo",
            "source_revision": "a" * 40,
            "target": {"soc": "Ascend310B4", "tier": "8T"},
            "artifacts": [],
            "runtime_locks": {},
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            cli, "verify_static_release_artifacts", return_value=verified
        ) as verify, redirect_stdout(StringIO()) as output:
            status = main(["acl", "verify", "--data-dir", directory])
        self.assertEqual(status, 0)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs["lock_path"], cli.DEFAULT_STATIC_RELEASE_LOCK)
        self.assertIn('"soc": "Ascend310B4"', output.getvalue())

    def test_release_surface_has_no_legacy_project_identifier(self) -> None:
        root = Path(__file__).resolve().parents[1]
        files = [
            root / "README.md",
            root / "CHANGELOG.md",
            root / "pyproject.toml",
            *sorted((root / "ascend_edge_chat").rglob("*.py")),
            *sorted((root / "configs").glob("release-*")),
            *sorted((root / "scripts").rglob("*.sh")),
            *sorted((root / "docs").glob("*.md")),
        ]
        legacy_identifier = "case" + "9"
        for path in files:
            self.assertNotIn(legacy_identifier, path.read_text(encoding="utf-8").lower(), path)

    def test_release_acceptance_requires_provenance_and_bounded_cleanup(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "verify" / "run-release-acceptance.sh").read_text(encoding="utf-8")
        client = (root / "scripts" / "verify" / "release_acceptance.py").read_text(encoding="utf-8")
        self.assertIn("ASCEND_EDGE_CHAT_MODEL_SOURCE", script)
        self.assertIn("--model-source", script)
        self.assertIn("cleanup_timeout_seconds", script)
        self.assertIn("kill -KILL", script)
        self.assertIn("--model-source", client)
        self.assertIn("working_tree_clean", client)
        self.assertIn("offline-bundle", client)

    def test_static_acceptance_passes_token_and_model_source_with_equals(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "verify" / "run-release-8t-acceptance.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('--token="${ASCEND_EDGE_CHAT_GATEWAY_API_KEY}"', script)
        self.assertIn('--model-source="${ASCEND_EDGE_CHAT_MODEL_SOURCE}"', script)
