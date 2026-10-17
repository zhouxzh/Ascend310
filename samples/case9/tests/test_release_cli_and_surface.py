from __future__ import annotations

from pathlib import Path
import unittest

from ascend_edge_chat.cli import main


class ReleaseCliAndSurfaceTests(unittest.TestCase):
    def test_version_command_reports_release_version(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)

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
