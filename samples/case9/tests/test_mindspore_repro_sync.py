from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_mindspore_chat_repro_bundle.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "mindspore_repro_sync_fixture.json"


class MindSporeReproSyncFixtureTests(unittest.TestCase):
    def test_fixture_declares_canonical_report_provenance(self) -> None:
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 2)
        profile = document["profiles"][0]
        self.assertEqual(profile["candidate_kind"], "native_mindspore")
        report = document["test_expectations"]["report"]
        self.assertEqual(
            report["destination"],
            "board8t/profile-reports/fixture-native/Ascend310B4/run-1/acceptance.json",
        )
        self.assertEqual(
            report["registry_path"],
            profile["validation"]["Ascend310B4"]["report_paths"][0],
        )
        self.assertGreaterEqual(len(report["remote_candidates"]), 2)

    def test_sync_script_contains_report_provenance_fields_and_collision_guard(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "canonical_report_destination",
            "profile-reports",
            '"registry_path": registry_path_value',
            '"remote_candidates"',
            '"destination": dest_rel',
            "colliding report destination",
        ):
            self.assertIn(required, source)
        self.assertNotIn("rsync -a --delete", source)


@unittest.skipUnless(
    os.name != "nt" and shutil.which("bash"),
    "the fake-board integration test requires a POSIX bash runtime",
)
class MindSporeReproSyncIntegrationTests(unittest.TestCase):
    @staticmethod
    def _make_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @staticmethod
    def _run_sync(
        *,
        registry: Path,
        allowlist: Path,
        bundle: Path,
        fake_bin: Path,
        env: dict[str, str],
        dry_run: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "bash",
            str(SCRIPT),
            "--board",
            "8t",
            "--board8-host",
            "fixture",
            "--board20-host",
            "fixture",
            "--remote-root",
            "/home/test/case9",
            "--registry",
            str(registry),
            "--source-allowlist",
            str(allowlist),
            "--bundle",
            str(bundle),
            "--sync-run-id",
            "fixture-run",
        ]
        if dry_run:
            command.append("--dry-run")
        merged_env = dict(env)
        merged_env["PATH"] = str(fake_bin) + os.pathsep + os.environ.get("PATH", "")
        return subprocess.run(
            command,
            cwd=ROOT,
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=120,
        )

    def _write_fixture_inputs(self, root: Path) -> tuple[Path, Path, Path, Path, dict]:
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        registry = root / "registry.json"
        # Keep assertions/expected paths in the fixture file, but write only
        # the canonical schema-v2 object to the registry.  The production
        # validator intentionally rejects extension keys at the registry root.
        registry_document = {
            key: value for key, value in document.items() if key != "test_expectations"
        }
        registry.write_text(json.dumps(registry_document, indent=2) + "\n", encoding="utf-8")
        allowlist = root / "allowlist.txt"
        # README.md is a small, tracked controller file and exercises the
        # explicit local-source path without copying the checkout recursively.
        allowlist.write_text("README.md\n", encoding="utf-8")
        payload = root / "payload.bin"
        payload.write_bytes(document["test_expectations"]["payload"].encode("utf-8"))
        fake_bin = root / "bin"
        fake_bin.mkdir()
        return registry, allowlist, payload, fake_bin, document

    def test_execute_records_canonical_report_and_missing_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, allowlist, payload, fake_bin, document = self._write_fixture_inputs(root)
            payload_bytes = payload.stat().st_size
            payload_sha = hashlib.sha256(payload.read_bytes()).hexdigest()
            self.assertEqual(payload_bytes, 22)
            self.assertEqual(
                payload_sha,
                document["profiles"][0]["artifacts"][0]["sha256"],
            )

            self._make_executable(
                fake_bin / "ssh",
                r'''#!/usr/bin/env bash
set -eu
cmd="${!#}"
case "$cmd" in
  *"test -f"*"stat"*"/artifacts/models/fixture/model.bin"*|*"test -f"*"stat"*"/reports/board8t/run-1/acceptance.json"*)
    printf '%s\n' "$FIXTURE_SIZE" ;;
  *"test -f"*"/artifacts/models/fixture/model.bin"*) exit 0 ;;
  *"test -f"*"/reports/board8t/run-1/acceptance.json"*) exit 0 ;;
  *"test -f"*) exit 1 ;;
  *"stat"*"/artifacts/models/fixture/model.bin"*|*"stat"*"/reports/board8t/run-1/acceptance.json"*)
    printf '%s\n' "$FIXTURE_SIZE" ;;
  *"sha256sum"*"/artifacts/models/fixture/model.bin"*|*"sha256sum"*"/reports/board8t/run-1/acceptance.json"*)
    printf '%s\n' "$FIXTURE_SHA" ;;
  *) exit 1 ;;
esac
''',
            )
            self._make_executable(
                fake_bin / "rsync",
                r'''#!/usr/bin/env bash
set -eu
destination="${!#}"
mkdir -p "$(dirname "$destination")"
cp -- "$FIXTURE_PAYLOAD" "$destination"
''',
            )
            env = dict(os.environ)
            env.update({
                "FIXTURE_PAYLOAD": str(payload),
                "FIXTURE_SIZE": str(payload_bytes),
                "FIXTURE_SHA": payload_sha,
            })
            bundle = root / "bundle"
            result = self._run_sync(
                registry=registry,
                allowlist=allowlist,
                bundle=bundle,
                fake_bin=fake_bin,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stdout)

            manifest = json.loads((bundle / "bundle-manifest.json").read_text(encoding="utf-8"))
            reports = manifest["profile_plan"]["reports"]
            report_expectation = document["test_expectations"]["report"]
            report = next(item for item in reports if item["registry_path"] == report_expectation["registry_path"])
            self.assertEqual(report["remote_candidates"], report_expectation["remote_candidates"])
            self.assertEqual(report["destination"], report_expectation["destination"])

            missing_report = next(
                item
                for item in manifest["missing"]
                if item.get("kind") == "report"
                and item.get("registry_path") == document["test_expectations"]["missing_report"]["registry_path"]
            )
            self.assertEqual(
                missing_report["destination"],
                document["test_expectations"]["missing_report"]["destination"],
            )
            self.assertEqual(
                missing_report["remote_candidates"],
                document["test_expectations"]["missing_report"]["remote_candidates"],
            )

            paths = [entry["path"] for entry in manifest["files"]]
            self.assertIn(report_expectation["destination"], paths)
            self.assertIn("board8t/artifacts/models/fixture/model.bin", paths)
            self.assertTrue(all(not path.startswith("repro/") for path in paths))
            self.assertFalse(any(path.endswith(".part") for path in paths))
            self.assertEqual(manifest["sources"]["board8t"]["status"], "partial")
            self.assertGreater(manifest["scope"]["missing_optional_entries"], 0)

    def test_normalized_report_destination_collision_fails_before_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry, allowlist, _payload, fake_bin, _document = self._write_fixture_inputs(root)
            document = json.loads(registry.read_text(encoding="utf-8"))
            document["profiles"][0]["validation"]["Ascend310B4"]["report_paths"].append(
                "reports/board8t/run-1/acceptance.json"
            )
            registry.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            result = self._run_sync(
                registry=registry,
                allowlist=allowlist,
                bundle=root / "bundle",
                fake_bin=fake_bin,
                env=dict(os.environ),
                dry_run=True,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("colliding report destination", result.stdout)
            self.assertFalse((root / "bundle").exists())


if __name__ == "__main__":
    unittest.main()
