from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import unittest


_MODULE_PATH = Path(__file__).parents[1] / "scripts" / "verify" / "output_budget_probe.py"
_SPEC = importlib.util.spec_from_file_location("output_budget_probe", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class OutputBudgetProbeTests(unittest.TestCase):
    def test_accepts_ascending_service_budgets(self) -> None:
        self.assertEqual(_MODULE._parse_budgets("32,128,192"), (32, 128, 192))

    def test_rejects_unordered_or_out_of_range_budgets(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            _MODULE._parse_budgets("128,64")
        with self.assertRaises(argparse.ArgumentTypeError):
            _MODULE._parse_budgets("257")

    def test_template_marker_list_detects_only_role_boundaries(self) -> None:
        self.assertTrue(any(marker in "回答<|assistant|>继续" for marker in _MODULE.CHAT_TEMPLATE_MARKERS))
        self.assertFalse(any(marker in "正常的中文回答" for marker in _MODULE.CHAT_TEMPLATE_MARKERS))


if __name__ == "__main__":
    unittest.main()
