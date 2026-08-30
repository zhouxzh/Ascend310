from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "compare_qwen25_static_kv.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("qwen25_static_compare", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TOOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompareHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tool = _load_tool()

    def test_cosine_handles_zero_vectors_without_nan(self) -> None:
        self.assertEqual(self.tool._cosine([0.0, 0.0], [0.0, 0.0]), 1.0)
        self.assertEqual(self.tool._cosine([0.0, 0.0], [1.0, 0.0]), 0.0)

    def test_topk_rejects_nonfinite_logits(self) -> None:
        with self.assertRaises(self.tool.ComparisonError):
            self.tool._topk([1.0, float("nan")])

    def test_compare_arguments_are_fail_closed(self) -> None:
        with self.assertRaises(self.tool.ComparisonError):
            self.tool.compare(Path("."), Path("missing"), Path("missing"), Path("missing"), Path("missing"), ["x"], max_steps=0)
        with self.assertRaises(self.tool.ComparisonError):
            self.tool.compare(Path("."), Path("missing"), Path("missing"), Path("missing"), Path("missing"), [], max_steps=1)


if __name__ == "__main__":
    unittest.main()
