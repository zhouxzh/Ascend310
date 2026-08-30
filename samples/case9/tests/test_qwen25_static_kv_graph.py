"""Controller-only tests for the 1024-token split StaticCache graph."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from qwen25_kv_acl_contract import (
    ContractError,
    QWEN25_CACHE_LENGTH,
    QWEN25_CACHE_INPUT_COUNT,
    QWEN25_MODEL_ID,
    QWEN25_SPLIT_KV_SHAPE,
    QWEN25_TOKEN_SPLIT_KV_SHAPE,
    Qwen25Contract,
)


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "tools" / "export_qwen25_static_onnx.py"
INSPECTOR_PATH = ROOT / "tools" / "inspect_qwen25_static_onnx.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaticGraphSourceTests(unittest.TestCase):
    def test_exporter_declares_split_static_cache(self) -> None:
        source = EXPORTER_PATH.read_text(encoding="utf-8")
        for required in (
            "StaticCache",
            "index_copy",
            "num_logits_to_keep=1",
            "CACHE_SHAPE = (BATCH_SIZE, NUM_KV_HEADS, SEQUENCE_LENGTH, HEAD_DIM)",
            "SEQUENCE_LENGTH = 1024",
            "MASK_LENGTH = SEQUENCE_LENGTH",
            "dynamic_axes",
            "local_files_only",
        ):
            self.assertIn(required, source)
        for forbidden in ("past_key_values)\",", "_unpack_packed", "PACKED_KV_SHAPE"):
            self.assertNotIn(forbidden, source)

    def test_exporter_input_and_output_names_are_layer_key_value_ordered(self) -> None:
        exporter = _load_module("qwen25_static_exporter", EXPORTER_PATH)
        self.assertEqual(len(exporter.CACHE_INPUT_NAMES), 48)
        self.assertEqual(exporter.CACHE_INPUT_NAMES[:4], (
            "past_key_values.0.key",
            "past_key_values.0.value",
            "past_key_values.1.key",
            "past_key_values.1.value",
        ))
        self.assertEqual(exporter.CACHE_OUTPUT_NAMES[-2:], ("present.23.key", "present.23.value"))
        self.assertEqual(exporter.CACHE_SHAPE, (1, 2, 1024, 64))
        self.assertEqual(exporter.TOKEN_CACHE_SHAPE, (1, 1, 2, 64))

    def test_exporter_forces_a_single_onnx_artifact(self) -> None:
        source = EXPORTER_PATH.read_text(encoding="utf-8")
        self.assertIn('kwargs["external_data"] = False', source)


@unittest.skipUnless(importlib.util.find_spec("onnx") is not None, "onnx is not installed")
class StaticGraphInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inspector = _load_module("qwen25_static_inspector", INSPECTOR_PATH)
        import onnx  # type: ignore

        cls.onnx = onnx

    def _write_fixture(self, *, bad_name: bool = False, bad_mask: bool = False) -> Path:
        onnx = self.onnx
        helper = onnx.helper
        inputs = [
            helper.make_tensor_value_info("input_ids", onnx.TensorProto.INT64, [1, 1]),
            helper.make_tensor_value_info(
                "attention_mask", onnx.TensorProto.INT64, [1, 1023 if bad_mask else 1024]
            ),
            helper.make_tensor_value_info("position_ids", onnx.TensorProto.INT64, [1, 1]),
        ]
        cache_names = []
        for layer in range(24):
            for part in ("key", "value"):
                name = f"past_key_values.{layer}.{part}"
                cache_names.append(name)
                inputs.append(helper.make_tensor_value_info(name, onnx.TensorProto.FLOAT, [1, 2, 1024, 64]))
        if bad_name:
            inputs[3].name = "past_key_values.0.bad"
        outputs = [helper.make_tensor_value_info("logits", onnx.TensorProto.FLOAT, [1, 1, 151936])]
        nodes = [
            helper.make_node(
                "Constant",
                [],
                ["logits"],
                value=onnx.numpy_helper.from_array(np.zeros((1, 1, 151936), dtype=np.float32)),
            )
        ]
        for layer in range(24):
            for part in ("key", "value"):
                name = f"present.{layer}.{part}"
                outputs.append(helper.make_tensor_value_info(name, onnx.TensorProto.FLOAT, [1, 1, 2, 64]))
                nodes.append(
                    helper.make_node(
                        "Constant",
                        [],
                        [name],
                        value=onnx.numpy_helper.from_array(np.zeros((1, 1, 2, 64), dtype=np.float32)),
                    )
                )
        graph = helper.make_graph(nodes, "qwen25-static-kv-fixture", inputs, outputs)
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
        metadata = {
            "case9.model_id": self.inspector.MODEL_ID,
            "case9.execution_mode": self.inspector.EXECUTION_MODE,
            "case9.source_revision": "fixture-revision",
            "case9.export.device": "cpu",
            "case9.export.precision": "fp32",
            "case9.export.static_sequence_length": "1024",
            "case9.export.mask_length": "1024",
            "case9.export.cache_layout": "split",
            "case9.export.dynamic_axes": "none",
            "case9.export.num_logits_to_keep": "1",
        }
        for key, value in metadata.items():
            item = model.metadata_props.add()
            item.key, item.value = key, value
        handle = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
        handle.close()
        path = Path(handle.name)
        onnx.save(model, str(path))
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_split_fixture_is_admitted_with_exact_byte_sizes(self) -> None:
        path = self._write_fixture()
        contract, report = self.inspector.inspect(path, "fixture-revision")
        self.assertTrue(contract["acl_om"]["supported_static_qwen25_layout"])
        self.assertEqual(len(contract["acl_om"]["inputs"]), 3 + QWEN25_CACHE_INPUT_COUNT)
        self.assertEqual(contract["acl_om"]["inputs"][3]["shape"], list(QWEN25_SPLIT_KV_SHAPE))
        self.assertEqual(contract["acl_om"]["inputs"][3]["byte_size"], 524288)
        self.assertEqual(contract["acl_om"]["outputs"][1]["shape"], list(QWEN25_TOKEN_SPLIT_KV_SHAPE))
        self.assertEqual(contract["acl_om"]["outputs"][1]["byte_size"], 512)
        self.assertEqual(report["status"], "admitted")
        self.assertEqual(Qwen25Contract.from_dict(contract).model_id, QWEN25_MODEL_ID)

    def test_wrong_cache_name_or_mask_is_blocked(self) -> None:
        for kwargs, expected in (({"bad_name": True}, "cache input order"), ({"bad_mask": True}, "attention_mask")):
            path = self._write_fixture(**kwargs)
            contract, _ = self.inspector.inspect(path, "fixture-revision")
            self.assertFalse(contract["acl_om"]["supported_static_qwen25_layout"])
            self.assertIn(expected, contract["acl_om"]["support_reason"])

    def test_packed_layout_is_not_admitted(self) -> None:
        path = self._write_fixture()
        with self.assertRaises(self.inspector.InspectionError):
            self.inspector.inspect(path, "fixture-revision", cache_layout="packed")


class ContractShapeTests(unittest.TestCase):
    def test_contract_rejects_legacy_packed_layout(self) -> None:
        raw = {
            "schema_version": 1,
            "model": {
                "family": "qwen2.5",
                "model_id": QWEN25_MODEL_ID,
                "eos_token_id": 151645,
                "pad_token_id": 151643,
                "bos_token_id": None,
            },
            "acl_om": {
                "execution_mode": "static_kv_token_fp32",
                "static_sequence_length": 1024,
                "mask_length": 1024,
                "cache_layout": "packed",
                "input_order": ["input_ids", "attention_mask", "position_ids"],
                "inputs": [],
                "outputs": [],
                "input_order_verified": True,
            },
        }
        with self.assertRaises(ContractError):
            Qwen25Contract.from_dict(raw)

    def test_om_descriptor_can_use_atc_rewritten_tensor_names(self) -> None:
        from types import SimpleNamespace

        inputs = [
            SimpleNamespace(name="input_ids", dtype="int64", shape=(1, 1), byte_size=8),
            SimpleNamespace(name="attention_mask", dtype="int64", shape=(1, 1024), byte_size=8192),
            SimpleNamespace(name="position_ids", dtype="int64", shape=(1, 1), byte_size=8),
        ]
        for index in range(48):
            inputs.append(
                SimpleNamespace(
                    name=f"Model_input_{index}", dtype="float32", shape=(1, 2, 1024, 64), byte_size=524288
                )
            )
        outputs = [SimpleNamespace(name="PartitionedCall_logits", dtype="float32", shape=(1, 1, 151936), byte_size=607744)]
        outputs.extend(
            SimpleNamespace(name=f"PartitionedCall_cache_{index}", dtype="float32", shape=(1, 1, 2, 64), byte_size=512)
            for index in range(48)
        )
        contract = Qwen25Contract.from_descriptor(inputs, outputs, source_revision="atc-revision")
        self.assertEqual(contract.cache_layout, "split")
        self.assertEqual(contract.cache_inputs[0].name, "Model_input_0")
        self.assertEqual(contract.logits_output.name, "PartitionedCall_logits")

    def test_contract_rejects_reordered_cache_indices(self) -> None:
        from types import SimpleNamespace

        inputs = [
            SimpleNamespace(name="input_ids", dtype="int64", shape=(1, 1), byte_size=8),
            SimpleNamespace(name="attention_mask", dtype="int64", shape=(1, 1024), byte_size=8192),
            SimpleNamespace(name="position_ids", dtype="int64", shape=(1, 1), byte_size=8),
        ]
        inputs.extend(
            SimpleNamespace(name=f"in_{index}", dtype="float32", shape=(1, 2, 1024, 64), byte_size=524288)
            for index in range(48)
        )
        outputs = [SimpleNamespace(name="logits", dtype="float32", shape=(1, 1, 151936), byte_size=607744)]
        outputs.extend(
            SimpleNamespace(name=f"out_{index}", dtype="float32", shape=(1, 1, 2, 64), byte_size=512)
            for index in range(48)
        )
        contract = Qwen25Contract.from_descriptor(inputs, outputs)
        bad_inputs = list(contract.inputs)
        bad_inputs[3] = replace(bad_inputs[3], cache_index=1, cache_part="value")
        bad_inputs[4] = replace(bad_inputs[4], cache_index=0, cache_part="key")
        bad = replace(contract, inputs=tuple(bad_inputs))
        with self.assertRaises(ContractError):
            bad.validate_static_expectations()

    def test_contract_rejects_duplicate_output_names(self) -> None:
        from types import SimpleNamespace

        inputs = [
            SimpleNamespace(name="input_ids", dtype="int64", shape=(1, 1), byte_size=8),
            SimpleNamespace(name="attention_mask", dtype="int64", shape=(1, 1024), byte_size=8192),
            SimpleNamespace(name="position_ids", dtype="int64", shape=(1, 1), byte_size=8),
        ]
        inputs.extend(
            SimpleNamespace(name=f"in_{index}", dtype="float32", shape=(1, 2, 1024, 64), byte_size=524288)
            for index in range(48)
        )
        outputs = [SimpleNamespace(name="logits", dtype="float32", shape=(1, 1, 151936), byte_size=607744)]
        outputs.extend(
            SimpleNamespace(name=f"out_{index}", dtype="float32", shape=(1, 1, 2, 64), byte_size=512)
            for index in range(48)
        )
        contract = Qwen25Contract.from_descriptor(inputs, outputs)
        bad_outputs = list(contract.outputs)
        bad_outputs[2] = replace(bad_outputs[2], name=bad_outputs[1].name)
        bad = replace(contract, outputs=tuple(bad_outputs))
        with self.assertRaises(ContractError):
            bad.validate_static_expectations()

    def test_contract_rejects_missing_or_wrong_byte_size(self) -> None:
        from types import SimpleNamespace

        inputs = [
            SimpleNamespace(name="input_ids", dtype="int64", shape=(1, 1), byte_size=8),
            SimpleNamespace(name="attention_mask", dtype="int64", shape=(1, 1024), byte_size=8192),
            SimpleNamespace(name="position_ids", dtype="int64", shape=(1, 1), byte_size=8),
        ]
        inputs.extend(
            SimpleNamespace(name=f"in_{index}", dtype="float32", shape=(1, 2, 1024, 64), byte_size=524288)
            for index in range(48)
        )
        outputs = [SimpleNamespace(name="logits", dtype="float32", shape=(1, 1, 151936), byte_size=607744)]
        outputs.extend(
            SimpleNamespace(name=f"out_{index}", dtype="float32", shape=(1, 1, 2, 64), byte_size=512)
            for index in range(48)
        )
        inputs[3].byte_size = None
        with self.assertRaises(ContractError):
            Qwen25Contract.from_descriptor(inputs, outputs)

    def test_contract_rejects_null_required_special_id(self) -> None:
        raw = {
            "schema_version": 1,
            "model": {
                "family": "qwen2.5",
                "model_id": QWEN25_MODEL_ID,
                "eos_token_id": None,
                "pad_token_id": 151643,
                "bos_token_id": None,
            },
            "acl_om": {
                "execution_mode": "static_kv_token_fp32",
                "supported_static_qwen25_layout": True,
                "static_sequence_length": 1024,
                "mask_length": 1024,
                "cache_layout": "split",
                "cache_shape": [1, 2, 1024, 64],
                "input_order": ["input_ids", "attention_mask", "position_ids"],
                "inputs": [],
                "outputs": [],
                "input_order_verified": True,
            },
        }
        with self.assertRaises(ContractError):
            Qwen25Contract.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
