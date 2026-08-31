"""Pure parsing contracts for board-only NPU telemetry."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "collect_npu_trace", ROOT / "tools" / "board" / "collect_npu_trace.py"
)
assert SPEC is not None and SPEC.loader is not None
TRACE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRACE)


class NpuSmiParserTests(unittest.TestCase):
    def test_310b4_table_fields_are_labeled_correctly(self) -> None:
        payload = """+---+
| 0       310B4                 | Alarm           | 0.0          80                15    / 15            |
| 0       0                     | NA              | 2            14541/ 15610                            |
"""

        parsed = TRACE._parse_npu_smi(payload)

        self.assertEqual(parsed["device"], 0)
        self.assertEqual(parsed["npu_model"], "310B4")
        self.assertEqual(parsed["health"], "Alarm")
        self.assertEqual(parsed["power_w"], 0.0)
        self.assertEqual(parsed["temperature_c"], 80.0)
        self.assertEqual(parsed["hugepages_used"], 15)
        self.assertEqual(parsed["hugepages_total"], 15)
        self.assertEqual(parsed["memory_mb_used"], 14541)
        self.assertEqual(parsed["memory_mb_total"], 15610)


class FaultDeltaTests(unittest.TestCase):
    @staticmethod
    def _snapshot(dmesg: list[str], history: list[str]) -> dict[str, object]:
        return {
            "dmesg_tail": {"stdout": "\n".join(dmesg)},
            "lpm_history_tail": {"lines": history},
        }

    def test_new_lpm_and_release_errors_block_a_clean_command(self) -> None:
        old = "[11:55] ordinary prior line"
        before = self._snapshot([old], ["[11:55] prior history"])
        after = self._snapshot(
            [old, "[12:01] sched_wait_for_publish_event err_ret=-512", "[12:01] Kthread_create not up to expectations"],
            ["[11:55] prior history", "[12:00] system exception 0xA6193215 ModuleName LPM"],
        )

        delta = TRACE._fault_delta(before, after)

        self.assertTrue(delta["has_blocking_faults"])
        self.assertEqual(
            delta["blocking_categories"],
            ["driver_release_failure", "err_ret_minus_512", "lpm"],
        )

    def test_preexisting_faults_do_not_block_without_a_new_occurrence(self) -> None:
        lpm = "[11:55] system exception 0xA6193215 ModuleName LPM"
        before = self._snapshot(["old err_ret=-512"], [lpm])
        after = self._snapshot(["old err_ret=-512", "new normal process release"], [lpm])

        delta = TRACE._fault_delta(before, after)

        self.assertFalse(delta["has_blocking_faults"])
        self.assertEqual(delta["blocking_events"], [])

    def test_repeated_identical_fault_line_is_counted_as_new(self) -> None:
        lpm = "system exception 0xA6193215 ModuleName LPM"
        before = self._snapshot([], [lpm])
        after = self._snapshot([], [lpm, lpm])

        delta = TRACE._fault_delta(before, after)

        self.assertTrue(delta["has_blocking_faults"])
        self.assertEqual(len(delta["blocking_events"]), 1)


if __name__ == "__main__":
    unittest.main()
