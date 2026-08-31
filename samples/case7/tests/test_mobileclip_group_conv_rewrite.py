import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import onnx
    from onnx import TensorProto, checker, helper, numpy_helper
except ImportError:  # pragma: no cover - model tooling is optional locally.
    onnx = None


@unittest.skipUnless(onnx is not None, "onnx model tooling is unavailable")
class GroupedConvRewriteTests(unittest.TestCase):
    def _model(self):
        input_value = helper.make_tensor_value_info("image", TensorProto.FLOAT, [1, 2, 3, 3])
        output_value = helper.make_tensor_value_info("embedding", TensorProto.FLOAT, [1, 4, 3, 3])
        # Each group has two outputs.  Distinct coefficients make an incorrect
        # [slot, group] flattening visibly differ from ONNX's [group, slot].
        weight = numpy_helper.from_array(
            np.asarray([[[[1]]], [[[2]]], [[[3]]], [[[4]]]], dtype="float32"),
            name="weight",
        )
        bias = numpy_helper.from_array(
            np.asarray([0, 10, 20, 30], dtype="float32"), name="bias"
        )
        conv = helper.make_node(
            "Conv",
            ["image", "weight", "bias"],
            ["embedding"],
            name="/grouped",
            group=2,
            kernel_shape=[1, 1],
        )
        graph = helper.make_graph([conv], "grouped-conv", [input_value], [output_value], [weight, bias])
        return helper.make_model(
            graph,
            opset_imports=[helper.make_opsetid("", 17)],
            ir_version=8,
        )

    def test_preserves_group_slot_channel_order(self):
        from scripts import rewrite_mobileclip_group_conv as rewrite

        source = self._model()
        rewritten, records = rewrite.rewrite_model(
            onnx.load_from_string(source.SerializeToString()), node_names=("/grouped",)
        )
        checker.check_model(rewritten)
        self.assertEqual(len(rewritten.graph.node), 6)
        self.assertEqual(records[0]["interleave_formula"], "output_channel = group_index * slots + slot")
        self.assertEqual(records[0]["interleave_example"], ["g0s0", "g0s1", "g1s0", "g1s1"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.onnx"
            rewritten_path = root / "rewritten.onnx"
            onnx.save(source, source_path)
            onnx.save(rewritten, rewritten_path)
            result = rewrite.verify_equivalence(source_path, rewritten_path, seeds=(17, 18))

        self.assertTrue(result["passed"])
        for check in result["checks"]:
            self.assertEqual(check["outputs"][0]["max_abs"], 0.0)

    def test_fixture_directory_records_full_equivalence_summary(self):
        from scripts import rewrite_mobileclip_group_conv as rewrite

        source = self._model()
        rewritten, _records = rewrite.rewrite_model(
            onnx.load_from_string(source.SerializeToString()), node_names=("/grouped",)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "source.onnx"
            rewritten_path = root / "rewritten.onnx"
            fixture_dir = root / "fixtures"
            fixture_dir.mkdir()
            onnx.save(source, source_path)
            onnx.save(rewritten, rewritten_path)
            for index in range(2):
                value = np.full((1, 2, 3, 3), index + 1, dtype=np.float32)
                np.savez_compressed(fixture_dir / f"fixture-{index}.npz", input=value)
            result = rewrite.verify_fixture_directory(
                source_path,
                rewritten_path,
                fixture_dir,
                min_cosine=0.999999,
                max_abs=1e-6,
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["fixture_count"], 2)
        self.assertEqual(result["passed_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["min_cosine"], 1.0)
        self.assertEqual(result["max_abs"], 0.0)
        self.assertEqual(result["thresholds"]["min_cosine"], 0.999999)
        self.assertTrue(all(item["fixture_sha256"] for item in result["fixtures"]))


if __name__ == "__main__":
    unittest.main()
