"""Controller-only tests for the Qwen2.5 last-logits graph rewrite."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "optimize_qwen25_last_logits_onnx.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("qwen25_last_logits_optimizer", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load optimizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LastLogitsOptimizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("onnx") is None:
            raise unittest.SkipTest("onnx is not installed")
        cls.onnx = __import__("onnx")
        cls.optimizer = _load_module()

    def _graph(self, *, dynamic: bool = False) -> Path:
        import numpy as np
        from onnx import helper, numpy_helper

        sequence, vocab = 4, 7
        shape = [1, "sequence"] if dynamic else [1, sequence]
        inputs = [
            helper.make_tensor_value_info(name, self.onnx.TensorProto.INT64, shape)
            for name in ("input_ids", "attention_mask", "position_ids")
        ]
        constant = numpy_helper.from_array(
            np.arange(sequence * vocab, dtype=np.float32).reshape(1, sequence, vocab),
            name="constant_logits",
        )
        node = helper.make_node("Identity", ["constant_logits"], ["logits"])
        output = helper.make_tensor_value_info(
            "logits", self.onnx.TensorProto.FLOAT, [1, sequence, vocab]
        )
        graph = helper.make_graph([node], "optimizer-fixture", inputs, [output], [constant])
        model = self.onnx.helper.make_model(
            graph, opset_imports=[helper.make_opsetid("", 17)], producer_name="case9-test"
        )
        handle = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
        handle.close()
        path = Path(handle.name)
        self.onnx.save(model, str(path))
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_rewrite_has_fixed_last_logits_output(self):
        source = self._graph()
        output = source.with_name(source.stem + "-optimized.onnx")
        self.addCleanup(output.unlink, missing_ok=True)
        result = self.optimizer.optimize_model(source, output, source_revision="fixture")
        self.assertEqual(result["optimization"]["output_shape"], [1, 1, 7])
        model = self.onnx.load(str(output), load_external_data=False)
        self.onnx.checker.check_model(model)
        self.assertEqual(model.graph.output[0].name, "last_logits")
        self.assertEqual(
            [node.op_type for node in model.graph.node[-3:]],
            ["ReduceSum", "Sub", "Gather"],
        )
        shape = model.graph.output[0].type.tensor_type.shape.dim
        self.assertEqual([item.dim_value for item in shape], [1, 1, 7])

    def test_rewrite_matches_original_last_row_with_onnxruntime(self):
        if importlib.util.find_spec("onnxruntime") is None:
            self.skipTest("onnxruntime is not installed")
        import numpy as np
        import onnxruntime as ort

        source = self._graph()
        output = source.with_name(source.stem + "-optimized.onnx")
        self.addCleanup(output.unlink, missing_ok=True)
        self.optimizer.optimize_model(source, output)
        old = ort.InferenceSession(str(source), providers=["CPUExecutionProvider"])
        new = ort.InferenceSession(str(output), providers=["CPUExecutionProvider"])
        values = {
            "input_ids": np.zeros((1, 4), dtype=np.int64),
            "attention_mask": np.asarray([[1, 1, 1, 0]], dtype=np.int64),
            "position_ids": np.arange(4, dtype=np.int64).reshape(1, 4),
        }
        original = old.run(None, values)[0][0, 2]
        optimized = new.run(None, values)[0][0, 0]
        np.testing.assert_array_equal(original, optimized)

    def test_dynamic_graph_is_rejected(self):
        source = self._graph(dynamic=True)
        output = source.with_name(source.stem + "-optimized.onnx")
        self.addCleanup(output.unlink, missing_ok=True)
        with self.assertRaises(self.optimizer.OptimizationError):
            self.optimizer.optimize_model(source, output)

    def test_source_and_output_must_differ(self):
        source = self._graph()
        with self.assertRaises(self.optimizer.OptimizationError):
            self.optimizer.optimize_model(source, source)


if __name__ == "__main__":
    unittest.main()
