"""Static contracts for board-only telemetry and ACL lifecycle helpers."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BoardDiagnosticsScriptTests(unittest.TestCase):
    def test_shell_diagnostics_are_not_formal_release_entries(self) -> None:
        shell_sources = list(ROOT.glob("*.sh"))
        shell_sources += list((ROOT / "palmprint_workbench").rglob("*.sh"))
        shell_sources += list((ROOT / "tools").rglob("*.sh"))
        self.assertEqual(shell_sources, [])
        self.assertIn("legacy_shell", (ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_python_lifecycle_probe_is_npu_only(self) -> None:
        probe = (ROOT / "tools" / "board" / "acl_lifecycle_probe.py").read_text(encoding="utf-8")
        self.assertIn('create_adapter(spec, "npu", "mixed_fp16", threads=args.threads)', probe)
        self.assertIn('contains_biometric_data": False', probe)
        self.assertIn("shutdown_acl_runtime", probe)
        self.assertIn("--shutdown-per-cycle", probe)
        self.assertIn("shared_runtime_until_probe_end", probe)
        self.assertIn("before_runtime_shutdown_final", probe)

    def test_python_trace_collector_is_available(self) -> None:
        source = (ROOT / "tools" / "board" / "collect_npu_trace.py").read_text(encoding="utf-8")
        self.assertIn("npu-smi", source)
        self.assertIn("reports/system", source)


if __name__ == "__main__":
    unittest.main()
