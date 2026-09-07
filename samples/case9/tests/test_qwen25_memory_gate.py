from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_gate():
    path = ROOT / "scripts" / "qwen25_15_memory_gate.py"
    spec = importlib.util.spec_from_file_location("qwen25_memory_gate_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load memory-gate helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load_gate()


class MemoryGateHelperTests(unittest.TestCase):
    def test_memory_snapshot_exposes_current_and_peak_rss(self) -> None:
        snapshot = GATE._memory_snapshot()
        self.assertIn("rss_bytes", snapshot)
        self.assertIn("current_rss_bytes", snapshot)
        self.assertIn("max_rss_bytes", snapshot)
        self.assertEqual(snapshot["rss_bytes"], snapshot["current_rss_bytes"])

    def test_cann_probe_has_explicit_module_status(self) -> None:
        snapshot = GATE._cann_module_probe()
        self.assertIn("status", snapshot)
        for name in ("acl", "te", "hccl"):
            self.assertIn(name, snapshot)
            self.assertIn("available", snapshot[name])

    def test_after_npu_snapshot_must_be_healthy_and_same_soc(self) -> None:
        report = {
            "status": "diagnostic_passed",
            "errors": [],
            "measurements": {
                "expected_soc": "Ascend310B4",
                "npu_after": {"returncode": 0, "output": "310B4"},
                "npu_after_soc": "Ascend310B4",
            },
        }
        GATE._validate_after_npu(report)
        self.assertEqual(report["after_npu_gate"], "passed")
        self.assertEqual(report["status"], "diagnostic_passed")
        self.assertEqual(report["errors"], [])

        failed = {
            "status": "diagnostic_passed",
            "errors": [],
            "measurements": {
                "expected_soc": "Ascend310B4",
                "npu_after": {"returncode": 1, "output": ""},
                "npu_after_soc": "Ascend310B1",
            },
        }
        GATE._validate_after_npu(failed)
        self.assertEqual(failed["after_npu_gate"], "failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(failed["errors"]), 2)

    def test_diagnostic_status_has_distinct_nonzero_exit_code(self) -> None:
        self.assertEqual(GATE._exit_code_for_status("passed"), 0)
        self.assertEqual(GATE._exit_code_for_status("diagnostic_passed"), 3)
        self.assertEqual(GATE._exit_code_for_status("failed"), 1)

    def test_placement_gate_is_strict_unless_context_only_is_explicit(self) -> None:
        self.assertEqual(GATE._placement_gate_value(False), "1")
        self.assertEqual(GATE._placement_gate_value(True), "0")
