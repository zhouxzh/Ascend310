"""Controller-side checks for the explicit 20T OM campaign transfer allowlist."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "sync_case9_om20t.sh"
OVERLAY_SCRIPT = ROOT / "scripts" / "provision_case9_om20t_overlay.sh"
WHEEL = ROOT / (
    "repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/"
    "artifacts/wheels/tokenizers-0.19.1-cp39-cp39-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
)

TOKENIZERS_WHEEL_BYTES = 3_570_575
TOKENIZERS_WHEEL_SHA256 = "c85cf76561fbd01e0d9ea2d1cbe711a65400092bc52b5242b16cfd22e51f0c58"

RUNTIME_ALLOWLIST = (
    "qwen25_kv_acl_contract.py",
    "qwen25_kv_acl_runtime.py",
    "qwen25_kv_acl_service.py",
    "qwen25_kv_tokenizer.py",
    "qwen25_acl_contract.py",
    "qwen25_acl_runtime.py",
    "qwen25_acl_service.py",
    "qwen25_tokenizer.py",
    "qwen25_legacy_compat_service.py",
    "acl_om_contract.py",
    "acl_om_runtime.py",
    "acl_om_service.py",
    "acl_om_tokenizer.py",
    "tinyllama_acl_contract.py",
    "tinyllama_acl_runtime.py",
    "tinyllama_acl_service.py",
    "tinyllama_tokenizer.py",
    "local_model_manifest.json",
    "scripts/serve_qwen25_kv_acl.py",
    "scripts/serve_qwen25_acl.py",
    "scripts/probe_acl_om.py",
    "scripts/probe_openai_compat_api.py",
    "scripts/qwen25_acceptance.py",
    "scripts/create_qwen25_b1_campaign_lock.py",
    "scripts/run_case9_om20t_campaign.sh",
    "scripts/run_qwen25_b1_20t_acceptance.sh",
    "scripts/serve_qwen25_legacy_compat.py",
    "scripts/create_qwen25_legacy_compat_pin_manifest.py",
    "scripts/provision_case9_om20t_overlay.sh",
    "tests/fixtures/qwen25_chinese_probe.json",
)


class Case9Om20TSyncTests(unittest.TestCase):
    def test_runtime_allowlist_covers_each_local_acl_family(self) -> None:
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        for relative in RUNTIME_ALLOWLIST:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)
                self.assertIn(relative, source)
        self.assertIn('remote="src/$item"', source)
        self.assertNotIn("rsync -a --delete", source)

    def test_optional_rsync_bandwidth_limit_is_validated_and_opt_in(self) -> None:
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('RSYNC_BWLIMIT="${CASE9_OM20T_RSYNC_BWLIMIT:-0}"', source)
        self.assertIn(
            '[[ "$RSYNC_BWLIMIT" =~ ^(0|[1-9][0-9]*)$ ]]',
            source,
        )
        self.assertIn(
            'RSYNC_TRANSFER_ARGS=(-a --partial --append-verify --info=progress2)',
            source,
        )
        self.assertIn('RSYNC_TRANSFER_ARGS+=(--bwlimit="$RSYNC_BWLIMIT")', source)
        self.assertIn('rsync "${RSYNC_TRANSFER_ARGS[@]}"', source)

        numeric_cap = re.compile(r"^(0|[1-9][0-9]*)$")
        for value in ("0", "1", "256", "1048576"):
            with self.subTest(value=value):
                self.assertIsNotNone(numeric_cap.fullmatch(value))
        for value in ("", "01", "-1", "12MiB", "1.5"):
            with self.subTest(value=value):
                self.assertIsNone(numeric_cap.fullmatch(value))

    def test_complete_b1_acceptance_support_is_explicit_and_self_contained(self) -> None:
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        acceptance = ROOT / "scripts" / "qwen25_acceptance.py"
        probes = ROOT / "tests" / "fixtures" / "qwen25_chinese_probe.json"
        self.assertTrue(acceptance.is_file())
        self.assertTrue(probes.is_file())
        self.assertIn("scripts/qwen25_acceptance.py", source)
        self.assertIn("tests/fixtures/qwen25_chinese_probe.json", source)
        probe_data = json.loads(probes.read_text(encoding="utf-8"))
        self.assertIsInstance(probe_data, list)
        self.assertEqual(len(probe_data), 10)
        self.assertTrue(all(isinstance(item.get("prompt"), str) for item in probe_data))
        # It is deliberately stdlib-only and reads its optional probe file from
        # the command line, so no third-party package or unlisted Python module
        # needs to be copied for a board-side B1 acceptance run.
        acceptance_source = acceptance.read_text(encoding="utf-8")
        for disallowed_import in ("import numpy", "import torch", "import acl"):
            self.assertNotIn(disallowed_import, acceptance_source)

    def test_native_b1_acceptance_launcher_is_a_board_runtime_input(self) -> None:
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        launcher = ROOT / "scripts" / "run_qwen25_b1_20t_acceptance.sh"
        self.assertTrue(launcher.is_file())
        self.assertIn("scripts/run_qwen25_b1_20t_acceptance.sh", source)
        launcher_source = launcher.read_text(encoding="utf-8")
        # Its only Case9 executable inputs are already explicitly present in
        # the runtime allowlist.  Its unit test remains controller-only.
        for dependency in (
            "scripts/serve_qwen25_kv_acl.py",
            "scripts/qwen25_acceptance.py",
            "scripts/provision_case9_om20t_overlay.sh",
            "tests/fixtures/qwen25_chinese_probe.json",
        ):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, RUNTIME_ALLOWLIST)
                self.assertIn(Path(dependency).name, launcher_source)
        self.assertNotIn("tests/test_run_qwen25_b1_20t_acceptance.py", source)

    def test_legacy_compatibility_runner_has_only_allowlisted_local_dependencies(self) -> None:
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        required = (
            "qwen25_legacy_compat_service.py",
            "scripts/serve_qwen25_legacy_compat.py",
            "scripts/create_qwen25_legacy_compat_pin_manifest.py",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())
                self.assertIn(relative, source)
        # The compatibility service imports only the existing, separately
        # allowlisted ACL helpers.  Its pin manifest is generated from the
        # selected board artifacts, so a checked-in test fixture is neither a
        # runtime input nor an allowed sync dependency.
        compatibility_source = (ROOT / "qwen25_legacy_compat_service.py").read_text(encoding="utf-8")
        for dependency in (
            "acl_om_service",
            "qwen25_acl_contract",
            "qwen25_acl_runtime",
            "qwen25_acl_service",
        ):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency + ".py", RUNTIME_ALLOWLIST)
                self.assertIn("from " + dependency + " import", compatibility_source)

    def test_pinned_aarch64_tokenizers_wheel_matches_the_sync_contract(self) -> None:
        source = SYNC_SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(WHEEL.is_file())
        self.assertEqual(WHEEL.stat().st_size, TOKENIZERS_WHEEL_BYTES)
        self.assertEqual(hashlib.sha256(WHEEL.read_bytes()).hexdigest(), TOKENIZERS_WHEEL_SHA256)
        self.assertIn("TOKENIZERS_WHEEL_SOURCE=", source)
        self.assertIn("TOKENIZERS_WHEEL_REMOTE=", source)
        self.assertIn(str(TOKENIZERS_WHEEL_BYTES), source)
        self.assertIn(TOKENIZERS_WHEEL_SHA256, source)
        self.assertIn('sync_one "$TOKENIZERS_WHEEL_SOURCE" "$TOKENIZERS_WHEEL_REMOTE"', source)

    def test_overlay_provisioner_is_local_only_and_hash_bound(self) -> None:
        source = OVERLAY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--no-index --no-deps --only-binary=:all:", source)
        self.assertIn('"$ROOT/overlay"', source)
        self.assertIn('"$ROOT/wheels/$WHEEL_NAME"', source)
        self.assertIn(str(TOKENIZERS_WHEEL_BYTES), source)
        self.assertIn(TOKENIZERS_WHEEL_SHA256, source)
        self.assertIn("refusing to replace", source)
        for forbidden in ("--user", "--upgrade", "apt-get", "curl "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertNotRegex(
            source,
            re.compile(r"(?m)^\s*(?:if\s+!\s+)?(?:python\s+-m\s+)?pip\s+uninstall\b"),
        )

    def test_shell_syntax_when_bash_is_available(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is unavailable on this controller")
        for script in (SYNC_SCRIPT, OVERLAY_SCRIPT):
            with self.subTest(script=script.name):
                completed = subprocess.run(
                    [bash, "-n"],
                    check=False,
                    capture_output=True,
                    input=script.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"),
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode("utf-8", errors="replace"),
                )


if __name__ == "__main__":
    unittest.main()
