from __future__ import annotations

import unittest

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
import onnxruntime as ort

from tools.export_midi_ddsp_onnx import (
    align_one_hot_types_for_atc,
    decompose_batch_normalization_for_atc,
    prune_unused_opset_imports,
)


def make_model(nodes, inputs, outputs, initializers=()):
    graph = helper.make_graph(
        nodes,
        "test_graph",
        inputs,
        outputs,
        initializer=list(initializers),
    )
    model = helper.make_model(
        graph,
        opset_imports=[
            helper.make_opsetid("", 13),
            helper.make_opsetid("ai.onnx.ml", 2),
        ],
    )
    model.ir_version = min(model.ir_version, 9)
    return model


def run_model(model, feeds):
    session = ort.InferenceSession(
        model.SerializeToString(), providers=["CPUExecutionProvider"]
    )
    return session.run(None, feeds)


class MidiDdspOnnxCompatibilityTest(unittest.TestCase):
    def test_prunes_unused_opset_domain(self) -> None:
        model = make_model(
            [helper.make_node("Identity", ["x"], ["y"])],
            [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
            [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])],
        )

        removed = prune_unused_opset_imports(model)

        self.assertEqual(removed, [("ai.onnx.ml", 2)])
        self.assertEqual([(item.domain, item.version) for item in model.opset_import], [("", 13)])
        onnx.checker.check_model(model)

    def test_decomposes_batch_normalization_without_changing_output(self) -> None:
        x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 2, 3, 1])
        y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 2, 3, 1])
        initializers = [
            numpy_helper.from_array(np.array([1.2, 0.8], np.float32), "scale"),
            numpy_helper.from_array(np.array([0.1, -0.2], np.float32), "bias"),
            numpy_helper.from_array(np.array([0.4, -0.5], np.float32), "mean"),
            numpy_helper.from_array(np.array([0.5, 1.5], np.float32), "variance"),
        ]
        model = make_model(
            [
                helper.make_node(
                    "BatchNormalization",
                    ["x", "scale", "bias", "mean", "variance"],
                    ["y"],
                    name="bn",
                    epsilon=1e-3,
                )
            ],
            [x],
            [y],
            initializers,
        )
        value = np.arange(6, dtype=np.float32).reshape(1, 2, 3, 1) / 4
        expected = run_model(model, {"x": value})[0]

        count = decompose_batch_normalization_for_atc(model)
        actual = run_model(model, {"x": value})[0]

        self.assertEqual(count, 1)
        self.assertNotIn("BatchNormalization", {node.op_type for node in model.graph.node})
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
        onnx.checker.check_model(model)

    def test_aligns_one_hot_indices_with_depth_type(self) -> None:
        indices = helper.make_tensor_value_info("indices", TensorProto.INT64, [3])
        output = helper.make_tensor_value_info("output", TensorProto.FLOAT, [3, 4])
        depth = numpy_helper.from_array(np.asarray(4, dtype=np.int32), "depth")
        values = numpy_helper.from_array(np.asarray([0.0, 1.0], dtype=np.float32), "values")
        model = make_model(
            [helper.make_node("OneHot", ["indices", "depth", "values"], ["output"], name="one_hot")],
            [indices],
            [output],
            [depth, values],
        )
        feed = {"indices": np.asarray([0, 2, 3], dtype=np.int64)}
        expected = run_model(model, feed)[0]

        count = align_one_hot_types_for_atc(model)
        actual = run_model(model, feed)[0]

        self.assertEqual(count, 1)
        cast = next(node for node in model.graph.node if node.op_type == "Cast")
        self.assertEqual(helper.get_attribute_value(cast.attribute[0]), TensorProto.INT32)
        np.testing.assert_array_equal(actual, expected)
        onnx.checker.check_model(model)


if __name__ == "__main__":
    unittest.main()
