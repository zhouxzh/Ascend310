from __future__ import annotations

import hashlib
from http import HTTPStatus
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify_case9_completion.py"
RUN_SCRIPT = ROOT / "scripts" / "run_case9_gap_acceptance.sh"
SYNC_SCRIPT = ROOT / "scripts" / "sync_case9_gap_bundle.sh"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = load_module("case9_gap_verifier_test", VERIFIER_PATH)


def bash_command(command: str) -> list[str]:
    """Return a bash invocation that works from Windows through WSL."""

    if shutil.which("wsl.exe"):
        return ["wsl.exe", "-e", "bash", "-lc", command]
    return ["bash", "-lc", command]


def bash_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    if len(value) > 1 and value[1] == ":":
        return "/mnt/" + value[0].lower() + value[2:]
    return value


def bash_available() -> bool:
    return subprocess.call(
        bash_command("command -v bash >/dev/null 2>&1"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


class FixtureTests(unittest.TestCase):
    def test_probe_fixture_has_ten_chinese_and_five_english_entries(self) -> None:
        payload = json.loads(
            (ROOT / "tests" / "fixtures" / "case9_dual_board_probe.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload.get("schema_version"), 1)
        probes = payload.get("probes")
        self.assertIsInstance(probes, list)
        self.assertEqual(len(probes), 15)
        self.assertEqual(sum(item.get("language") == "zh" for item in probes), 10)
        self.assertEqual(sum(item.get("language") == "en" for item in probes), 5)
        self.assertEqual(len({item.get("id") for item in probes}), 15)
        self.assertTrue(all(isinstance(item.get("prompt"), str) and item["prompt"] for item in probes))

    def _write_valid_bundle(self, root: Path) -> None:
        reports = root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        required = []
        for index, (board, model) in enumerate(VERIFIER.EXPECTED_MATRIX):
            report = reports / ("%02d.json" % index)
            report.write_text(json.dumps({"board": board, "model": model, "status": "passed"}) + "\n", encoding="utf-8")
            relative = report.relative_to(root).as_posix()
            data = report.read_bytes()
            required.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        document = {
            "schema_version": 1,
            "bundle": "case9-dual-board-gap",
            "boards": {
                key: {**value, "status": "selected"} for key, value in VERIFIER.EXPECTED_BOARDS.items()
            },
            "matrix": [
                {"board": board, "model": model, "status": "passed", "report": "reports/%02d.json" % index}
                for index, (board, model) in enumerate(VERIFIER.EXPECTED_MATRIX)
            ],
            "required_files": required,
        }
        (root / "bundle-manifest.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        (root / "SHA256SUMS.txt").write_text(
            "".join("%s  %s\n" % (item["sha256"], item["path"]) for item in required), encoding="ascii"
        )

    def test_verifier_accepts_complete_matrix_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_bundle(root)
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["matrix"]["observed"], 8)
            self.assertEqual(result["checked"], 8)

    def test_verifier_rejects_missing_combination_and_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_bundle(root)
            manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
            manifest["matrix"].pop()
            (root / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("missing matrix combination" in item for item in result["failures"]))
            self._write_valid_bundle(root)
            target = root / "reports" / "00.json"
            target.write_text("changed\n", encoding="utf-8")
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("mismatch" in item for item in result["failures"]))

    def test_verifier_requires_reason_for_blocked_combination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_bundle(root)
            manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
            item = manifest["matrix"][0]
            item["status"] = "blocked"
            item.pop("report", None)
            item.pop("reason", None)
            (root / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("needs a concrete reason" in value for value in result["failures"]))

    def test_verifier_requires_hashed_report_for_completed_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_valid_bundle(root)
            manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
            manifest["matrix"][0]["status"] = "passed"
            manifest["matrix"][0]["report"] = "reports/unhashed.json"
            (root / "reports" / "unhashed.json").write_text("unhashed\n", encoding="utf-8")
            (root / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("not hashed" in value for value in result["failures"]))

    def test_safe_relative_rejects_platform_escape(self) -> None:
        for value in ("../outside", "reports/../outside", "C:/outside", "reports\\x", ""):
            with self.assertRaises(ValueError):
                VERIFIER.safe_relative(value)


@unittest.skipUnless(bash_available(), "bash unavailable")
class ShellContractTests(unittest.TestCase):
    def test_shell_scripts_parse_and_use_current_board_addresses(self) -> None:
        for script in (RUN_SCRIPT, SYNC_SCRIPT):
            result = subprocess.run(
                bash_command("bash -n %s" % shlex.quote(bash_path(script))),
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        run_text = RUN_SCRIPT.read_text(encoding="utf-8")
        sync_text = SYNC_SCRIPT.read_text(encoding="utf-8")
        for text in (run_text, sync_text):
            self.assertIn("192.168.1.90", text)
            self.assertIn("192.168.1.95", text)
            self.assertNotIn("192.168.8.210", text)
            self.assertIn(".part", text)
            self.assertIn("sha256", text.lower())
        self.assertIn("--partial --append-verify", sync_text)
        self.assertNotRegex(sync_text, r"(?m)^\s*(?:rsync|scp|ssh)[^\n]*--delete")
        self.assertIn("mv -f -- \"$part\"", sync_text)
        self.assertIn("qwen25-static-kv-1024-b1.om", run_text)
        self.assertIn("qwen25-static-kv-1024-b1.om", sync_text)
        self.assertIn("--long-budgets 8,16,24,32,48,64,80", run_text)
        self.assertNotIn("PYTHONNOUSERSITE=0", run_text)
        self.assertIn("PYTHONNOUSERSITE=1", run_text)
        self.assertIn('om_env="case9-acl-om"', run_text)
        self.assertIn('om_env="base"', run_text)
        self.assertIn('boards/$board/', sync_text)
        self.assertIn('reports/$board/', sync_text)
        self.assertIn('--report-run-id', sync_text)
        self.assertIn('--report BOARD MODEL REL', sync_text)
        self.assertIn('--report-om BOARD MODEL REL', sync_text)
        self.assertIn('--local-report BOARD MODEL PATH', sync_text)
        self.assertIn('--local-artifact DEST_REL PATH', sync_text)
        self.assertIn('REPORT_PROFILES=(', sync_text)

    def test_acceptance_dry_run_is_non_mutating_and_lists_both_boards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reports"
            command = "bash %s --dry-run --board both --kind all --profiles qwen1.5-0.5b-mindspore --output %s --run-id gap-test" % (
                shlex.quote(bash_path(RUN_SCRIPT)), shlex.quote(bash_path(output))
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("board8t", result.stdout)
            self.assertIn("board20t", result.stdout)
            self.assertIn("192.168.1.95", result.stdout)
            self.assertIn("no remote process management", result.stdout)
            self.assertFalse(output.exists())

    def test_acceptance_wrapper_uses_directory_output_for_mindspore_helper(self) -> None:
        text = RUN_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('remote_report="$remote_run/acceptance.json"', text)
        self.assertIn('--output %q --run-id', text)
        self.assertIn('"$remote_run" "$RUN_ID"', text)
        self.assertNotIn('"$remote_report" "$RUN_ID" "$remote_probe"', text)

    def test_sync_dry_run_does_not_create_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            command = "bash %s --dry-run --board 8t --no-models --no-reports --bundle %s --sync-run-id gap-test" % (
                shlex.quote(bash_path(SYNC_SCRIPT)), shlex.quote(bash_path(output))
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("dry-run complete", result.stdout)
            self.assertFalse(output.exists())

    def test_sync_default_report_dry_run_does_not_probe_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            command = "bash %s --dry-run --board 8t --no-source --no-models --run-id report-test --bundle %s" % (
                shlex.quote(bash_path(SYNC_SCRIPT)), shlex.quote(bash_path(output))
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("DRY-RUN default report board8t/qwen1.5-0.5b-mindspore", result.stdout)
            self.assertIn("reports/mindspore-chat/case9-gap/report-test", result.stdout)
            self.assertIn("no SSH", result.stdout)
            self.assertFalse(output.exists())

    def test_sync_dry_run_uses_separate_om_report_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            command = "bash %s --dry-run --board 20t --no-source --no-models --no-reports --bundle %s --run-id om-report --report-om board20t qwen25-onnx-om run/case9-gap/om-report/qwen25-onnx-om/acceptance.json" % (
                shlex.quote(bash_path(SYNC_SCRIPT)), shlex.quote(bash_path(output))
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("DRY-RUN explicit OM report board20t/qwen25-onnx-om", result.stdout)
            self.assertIn("case9-qwen25-kv1024-20t", result.stdout)
            self.assertFalse(output.exists())

    def test_sync_local_report_dry_run_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            command = "bash %s --dry-run --board 8t --no-source --no-models --no-reports --bundle %s --run-id local-report --local-report board8t deepseek-r1-qwen-1.5b-mindspore %s" % (
                shlex.quote(bash_path(SYNC_SCRIPT)),
                shlex.quote(bash_path(output)),
                shlex.quote(bash_path(Path(directory) / "acceptance.json")),
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("DRY-RUN local report board8t/deepseek-r1-qwen-1.5b-mindspore", result.stdout)
            self.assertFalse(output.exists())

    def test_sync_skip_board20_also_skips_explicit_remote_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            command = "bash %s --dry-run --board both --skip-board20 --no-source --no-models --no-reports --bundle %s --run-id skip-test --report board20t qwen1.5-0.5b-mindspore reports/x/acceptance.json" % (
                shlex.quote(bash_path(SYNC_SCRIPT)),
                shlex.quote(bash_path(output)),
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("skip explicit report outside board selection", result.stdout)
            self.assertNotIn("DRY-RUN explicit report board20t", result.stdout)
            self.assertFalse(output.exists())

    def test_sync_local_artifact_execute_is_atomic_and_checksummed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.onnx"
            source.write_bytes(b"synthetic-model-artifact\x00\x01\n")
            bundle = root / "bundle"
            command = "bash %s --execute --board 8t --no-source --no-models --no-reports --bundle %s --run-id artifact-test --local-artifact artifacts/common/model.onnx %s" % (
                shlex.quote(bash_path(SYNC_SCRIPT)),
                shlex.quote(bash_path(bundle)),
                shlex.quote(bash_path(source)),
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            copied = bundle / "artifacts" / "common" / "model.onnx"
            self.assertEqual(copied.read_bytes(), source.read_bytes())
            self.assertFalse((copied.with_name(copied.name + ".part")).exists())
            manifest = json.loads((bundle / "bundle-manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in manifest["required_files"] if item["path"] == "artifacts/common/model.onnx")
            self.assertEqual(entry["bytes"], source.stat().st_size)
            self.assertEqual(entry["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_sync_local_artifact_rejects_reserved_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.bin"
            source.write_bytes(b"x")
            command = "bash %s --dry-run --board 8t --no-source --no-models --no-reports --bundle %s --run-id artifact-test --local-artifact artifacts/bundle-manifest.json %s" % (
                shlex.quote(bash_path(SYNC_SCRIPT)),
                shlex.quote(bash_path(root / "bundle")),
                shlex.quote(bash_path(source)),
            )
            result = subprocess.run(
                bash_command(command), cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reserved local artifact destination", result.stdout)


if __name__ == "__main__":
    unittest.main()
