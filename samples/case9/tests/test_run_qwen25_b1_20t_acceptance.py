"""Static safety checks for the isolated native B1/20T acceptance launcher."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_qwen25_b1_20t_acceptance.sh"


class Qwen25B120TAcceptanceLauncherTests(unittest.TestCase):
    @staticmethod
    def _bash_path(path: Path) -> str:
        value = str(path.resolve()).replace("\\", "/")
        if len(value) >= 2 and value[1] == ":":
            return "/mnt/" + value[0].lower() + value[2:]
        return value

    def test_launcher_requires_native_campaign_admission_boundaries(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for expected in (
            "--campaign-lock is required",
            "--allow-dirty-base is required",
            "/home/HwHiAiUser/case9-om-20t-*",
            'TARGET_SOC="Ascend310B1"',
            'MODEL_ID="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"',
            'PORT=18101',
            "provision_case9_om20t_overlay.sh",
            "qwen25_acceptance.py",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)
        self.assertIn("--soc-version \"$TARGET_SOC\"", source)
        self.assertNotIn("--compatibility-experiment", source)
        self.assertNotIn("--allow-unlocked-artifacts", source)

    def test_launcher_owns_only_a_verified_session_process_group(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("setsid bash -c", source)
        self.assertIn('"$SERVICE_PGID" == "$SERVICE_PID"', source)
        self.assertIn('kill -TERM -- "-$SERVICE_PGID"', source)
        self.assertIn('kill -KILL -- "-$SERVICE_PGID"', source)
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)

    def test_launcher_keeps_conversion_and_package_mutation_out_of_scope(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8").lower()
        self.assertNotIn("atc ", source)
        self.assertNotIn("pip install", source)
        self.assertNotIn("pip uninstall", source)
        self.assertNotIn("apt-get", source)

    def test_shell_syntax_and_help_when_bash_is_available(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is unavailable")
        script = self._bash_path(SCRIPT)
        syntax = subprocess.run(
            [bash, "-n", script],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            [bash, script, "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--campaign-lock", help_result.stdout)
        self.assertIn("--allow-dirty-base", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
