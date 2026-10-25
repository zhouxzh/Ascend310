from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from ascend_edge_chat.onnx_graph_surgery import (
    OnnxGraphSurgeryError,
    migrate_matmulnbits_to_fp16,
    normalize_transpose_perms,
    unpack_matmul_nbits_weight,
)


def _pack_four_bit(values: np.ndarray, block_size: int) -> np.ndarray:
    n, k = values.shape
    blocks = (k + block_size - 1) // block_size
    blob_size = block_size // 2
    packed = np.zeros((n, blocks, blob_size), dtype=np.uint8)
    for column in range(n):
        for block in range(blocks):
            start = block * block_size
            for chunk in range(block_size // 32):
                chunk_start = start + chunk * 32
                for index in range(16):
                    low = values[column, chunk_start + 2 * index]
                    high = values[column, chunk_start + 2 * index + 1]
                    packed[column, block, chunk * 16 + index] = (
                        int(low) | (int(high) << 4)
                    )
    return packed


class MatMulNBitsUnpackTests(unittest.TestCase):
    def test_mlas_nibble_order_and_transposed_weight_shape(self) -> None:
        k = 64
        n = 2
        block_size = 32
        quantized = np.arange(n * k, dtype=np.uint8).reshape(n, k) % 16
        packed = _pack_four_bit(quantized, block_size)
        scales = np.array([-0.25, 0.5, 0.125, -0.75], dtype=np.float16)

        unpacked = unpack_matmul_nbits_weight(
            packed,
            scales,
            k=k,
            n=n,
            bits=4,
            block_size=block_size,
        )

        self.assertEqual(unpacked.weight.shape, (k, n))
        self.assertEqual(unpacked.weight.dtype, np.float16)
        for column in range(n):
            for block in range(2):
                scale = scales[column * 2 + block]
                for position in range(block_size):
                    k_index = block * block_size + position
                    expected = (int(quantized[column, k_index]) - 8) * scale
                    self.assertEqual(unpacked.weight[k_index, column], expected)

    def test_non_scalar_zero_point_is_rejected(self) -> None:
        values = np.zeros((1, 32), dtype=np.uint8)
        with self.assertRaisesRegex(OnnxGraphSurgeryError, "scalar"):
            unpack_matmul_nbits_weight(
                _pack_four_bit(values, 32),
                np.ones(1, dtype=np.float16),
                k=32,
                n=1,
                bits=4,
                block_size=32,
                zero_points=np.ones(2, dtype=np.uint8),
            )


@unittest.skipUnless(onnx, "onnx is required")
class MatMulNBitsGraphMigrationTests(unittest.TestCase):
    def _write_graph(self, root: Path, include_other_custom: bool = False) -> Path:
        k = 64
        n = 2
        quantized = np.arange(n * k, dtype=np.uint8).reshape(n, k) % 16
        packed = _pack_four_bit(quantized, 32)
        scales = np.array([0.25, -0.5, 0.125, 0.75], dtype=np.float16)
        initializers = [
            numpy_helper.from_array(packed, name="packed"),
            numpy_helper.from_array(scales, name="scales"),
            numpy_helper.from_array(
                np.arange(8, dtype=np.float16).reshape(4, 2),
                name="embedding",
            ),
        ]
        nodes = [
            helper.make_node(
                "MatMulNBits",
                ["activation", "packed", "scales"],
                ["output"],
                name="quantized_matmul",
                domain="com.microsoft",
                K=k,
                N=n,
                bits=4,
                block_size=32,
            ),
            helper.make_node(
                "Transpose",
                ["embedding"],
                ["embedding_transposed"],
                name="implicit_transpose",
            ),
        ]
        outputs = ["output", "embedding_transposed"]
        if include_other_custom:
            nodes.append(
                helper.make_node(
                    "MatMulNBits",
                    ["activation", "packed", "scales"],
                    ["unused"],
                    name="other_quantized_matmul",
                    domain="vendor.example",
                    K=k,
                    N=n,
                    bits=4,
                    block_size=32,
                )
            )
            outputs.append("unused")
        graph = helper.make_graph(
            nodes,
            "matmulnbits_fixture",
            [
                helper.make_tensor_value_info(
                    "activation", TensorProto.FLOAT16, [1, k]
                )
            ],
            [
                helper.make_tensor_value_info(
                    name,
                    TensorProto.FLOAT16,
                    [1, n] if name in {"output", "unused"} else [2, 4],
                )
                for name in outputs
            ],
            initializer=initializers,
        )
        model = helper.make_model(
            graph,
            opset_imports=[
                helper.make_opsetid("", 14),
                helper.make_opsetid("com.microsoft", 1),
            ],
        )
        path = root / "source.onnx"
        onnx.save_model(model, path)
        return path

    def test_migration_removes_custom_domain_and_materializes_fp16_weights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write_graph(root)
            output = root / "migrated.onnx"
            report_path = root / "report.json"

            report = migrate_matmulnbits_to_fp16(source, output, report_path)
            migrated = onnx.load(output, load_external_data=False)
            recorded = json.loads(report_path.read_text(encoding="utf-8"))
            output_bytes = output.stat().st_size

        self.assertEqual(report["replaced_nodes"], 1)
        self.assertEqual(report["normalized_transposes"], 1)
        self.assertEqual(migrated.graph.node[0].op_type, "MatMul")
        self.assertEqual(migrated.graph.node[0].domain, "")
        transpose = next(
            node for node in migrated.graph.node if node.op_type == "Transpose"
        )
        transpose_perm = next(
            attribute for attribute in transpose.attribute if attribute.name == "perm"
        )
        self.assertEqual(list(transpose_perm.ints), [1, 0])
        self.assertNotIn(
            "com.microsoft",
            {item.domain for item in migrated.opset_import},
        )
        weights = [
            item
            for item in migrated.graph.initializer
            if item.name == "quantized_matmul_dequantized_weight"
        ]
        self.assertEqual(len(weights), 1)
        self.assertEqual(list(weights[0].dims), [64, 2])
        self.assertEqual(weights[0].data_type, TensorProto.FLOAT16)
        self.assertEqual(recorded["output"]["bytes"], output_bytes)

    def test_standalone_transpose_normalization_is_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write_graph(root)
            output = root / "normalized.onnx"
            report_path = root / "normalize.json"
            report = normalize_transpose_perms(source, output, report_path)
            migrated = onnx.load(output, load_external_data=False)
            recorded = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["normalized_transposes"], 1)
        self.assertEqual(recorded["normalized_transposes"], 1)
        transpose = next(
            node for node in migrated.graph.node if node.op_type == "Transpose"
        )
        transpose_perm = next(
            attribute for attribute in transpose.attribute if attribute.name == "perm"
        )
        self.assertEqual(list(transpose_perm.ints), [1, 0])

    def test_migration_rejects_a_remaining_custom_op(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write_graph(root, include_other_custom=True)
            with self.assertRaisesRegex(OnnxGraphSurgeryError, "vendor.example"):
                migrate_matmulnbits_to_fp16(source, root / "migrated.onnx")


if __name__ == "__main__":
    unittest.main()
