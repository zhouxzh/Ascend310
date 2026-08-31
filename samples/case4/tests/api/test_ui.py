from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.board.verify_frontend_assets import validate_dist


ROOT = Path(__file__).resolve().parents[2]


class FrontendAssetTests(unittest.TestCase):
    def _write_bundle(self, root: Path, script_reference: str = "/assets/app.js") -> Path:
        dist = root / "dist"
        assets = dist / "assets"
        assets.mkdir(parents=True)
        (assets / "app.js").write_text("console.log('palmprint');\n", encoding="utf-8")
        (assets / "app.css").write_text("body { color: #111827; }\n", encoding="utf-8")
        (dist / "index.html").write_text(
            "<!doctype html><html><head>"
            '<link rel="stylesheet" href="/assets/app.css">'
            f'</head><body><script type="module" src="{script_reference}"></script></body></html>',
            encoding="utf-8",
        )
        return dist

    def test_valid_prebuilt_bundle_requires_no_node_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = validate_dist(self._write_bundle(Path(temporary)), strict=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["script_count"], 1)
        self.assertFalse(result["node_runtime_required"])

    def test_missing_referenced_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = self._write_bundle(Path(temporary), "/assets/missing.js")
            with self.assertRaisesRegex(FileNotFoundError, "missing"):
                validate_dist(dist, strict=True)

    def test_asset_path_cannot_escape_dist(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = self._write_bundle(Path(temporary), "/../outside.js")
            with self.assertRaisesRegex(ValueError, "escapes dist"):
                validate_dist(dist, strict=True)

    def test_encoded_asset_path_cannot_escape_dist(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = self._write_bundle(Path(temporary), "/assets/%2e%2e/outside.js")
            with self.assertRaisesRegex(ValueError, "escapes dist"):
                validate_dist(dist, strict=True)

    def test_strict_bundle_rejects_external_bootstrap_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = self._write_bundle(Path(temporary))
            index = dist / "index.html"
            index.write_text(
                index.read_text(encoding="utf-8").replace(
                    'src="/assets/app.js"',
                    'src="https://cdn.invalid/palmprint.js"',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "external"):
                validate_dist(dist, strict=True)


class DeploymentContractTests(unittest.TestCase):
    def test_production_entry_is_python_only_and_has_no_wildcard_import(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("import *", source)
        self.assertIn("def main", source)
        self.assertIn("palmprint_workbench.api", source)
        self.assertTrue((ROOT / "palmprint_workbench" / "api" / "__main__.py").is_file())

    def test_shell_wrappers_are_outside_the_source_release_tree(self):
        self.assertFalse((ROOT / "setup.sh").exists())
        shell_sources = list(ROOT.glob("*.sh"))
        shell_sources += list((ROOT / "palmprint_workbench").rglob("*.sh"))
        shell_sources += list((ROOT / "tools").rglob("*.sh"))
        self.assertEqual(shell_sources, [])
        self.assertFalse((ROOT / ".gitignore").read_text(encoding="utf-8").find("legacy_shell") < 0)

    def test_requirements_are_api_runtime_only(self):
        board = (ROOT / "requirements" / "board.lock").read_text(encoding="utf-8")
        dev = (ROOT / "requirements" / "dev.lock").read_text(encoding="utf-8")
        export = (ROOT / "requirements" / "export.lock").read_text(encoding="utf-8")
        for package in ("fastapi==", "uvicorn==", "python-multipart=="):
            self.assertIn(package, board)
        self.assertIn("pytest==", dev)
        self.assertIn("onnx==", export)
        self.assertIn("torch==", export)
        self.assertNotIn("torch==", board)
        self.assertNotIn("pytest==", board)
        self.assertFalse((ROOT / "requirements.txt").exists())

    def test_manual_asset_verifier_module_is_available(self):
        source = (ROOT / "palmprint_workbench" / "tools" / "verify_assets.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("verify_runtime_assets", source)

    def test_http_smoke_uses_the_public_image_upload_field(self):
        source = (ROOT / "tools" / "board" / "ui_http_smoke_test.py").read_text(encoding="utf-8")
        self.assertIn('_multipart_file("image",', source)
        self.assertNotIn('_multipart_file("file",', source)


if __name__ == "__main__":
    unittest.main()
