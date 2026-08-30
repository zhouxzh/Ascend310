"""Pure-Python contract/runtime checks for the last-logits candidate."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np

from qwen25_acl_contract import (
    QWEN25_MODEL_ID,
    Qwen25Contract,
)
from qwen25_acl_runtime import Qwen25AclRuntime, RuntimeDescriptor, TensorDescriptor


VOCAB = 151936
EOS = 151645
ROOT = Path(__file__).resolve().parents[1]


class _LastLogitsBackend:
    def __init__(self, sequence: int = 8) -> None:
        self.sequence = sequence
        self.calls = 0

    def open(self, _path: Path) -> RuntimeDescriptor:
        inputs = tuple(
            TensorDescriptor(name, "int64", (1, self.sequence), 8 * self.sequence)
            for name in ("input_ids", "attention_mask", "position_ids")
        )
        output = TensorDescriptor("last_logits", "float16", (1, 1, VOCAB), 2 * VOCAB)
        return RuntimeDescriptor(inputs, (output,))

    def run(self, _inputs):
        self.calls += 1
        values = np.full((1, 1, VOCAB), -1000, dtype=np.float16)
        values[0, 0, EOS] = 1000
        return values

    def close(self) -> None:
        pass


class _Tokenizer:
    vocab_size = VOCAB
    eos_token_id = EOS
    pad_token_id = 151643
    im_start_id = 151644
    im_end_id = EOS
    bos_token_id = None

    def encode_messages(self, _messages):
        return [1, 2]

    def decode(self, _ids):
        return ""


def _contract(sequence: int = 8) -> dict:
    return {
        "schema_version": 1,
        "model": {
            "family": "qwen2.5",
            "model_id": QWEN25_MODEL_ID,
            "eos_token_id": EOS,
            "pad_token_id": 151643,
            "bos_token_id": None,
        },
        "acl_om": {
            "execution_mode": "last_logits_static",
            "static_sequence_length": sequence,
            "input_dtype": "int64",
            "logits_dtype": "float16",
            "precision": "float16",
            "input_order": ["input_ids", "attention_mask", "position_ids"],
            "input_order_verified": True,
            "inputs": [
                {"name": name, "dtype": "int64", "shape": [1, sequence], "byte_size": 8 * sequence, "role": "input"}
                for name in ("input_ids", "attention_mask", "position_ids")
            ],
            "outputs": [
                {"name": "last_logits", "dtype": "float16", "shape": [1, 1, VOCAB], "byte_size": 2 * VOCAB, "role": "logits"}
            ],
            "vocabulary_size": VOCAB,
            "operator_audit": {"opset": 17, "unsupported_operators": []},
        },
    }


class Qwen25LastLogitsContractTests(unittest.TestCase):
    def test_full_context_inspector_admits_optimized_tail(self):
        if importlib.util.find_spec("onnx") is None:
            self.skipTest("onnx is not installed")
        import onnx
        from onnx import helper, numpy_helper

        sequence = 4
        inputs = [
            helper.make_tensor_value_info(name, onnx.TensorProto.INT64, [1, sequence])
            for name in ("input_ids", "attention_mask", "position_ids")
        ]
        logits = numpy_helper.from_array(
            np.zeros((1, sequence, VOCAB), dtype=np.float16), name="full_logits"
        )
        one = numpy_helper.from_array(np.asarray(1, dtype=np.int64), name="one")
        axes = numpy_helper.from_array(np.asarray([1], dtype=np.int64), name="axes")
        nodes = [
            helper.make_node("Identity", ["full_logits"], ["case9_full_context_logits"]),
            helper.make_node("ReduceSum", ["attention_mask", "axes"], ["valid"], keepdims=0),
            helper.make_node("Sub", ["valid", "one"], ["index"]),
            helper.make_node("Gather", ["case9_full_context_logits", "index"], ["last_logits"], axis=1),
        ]
        output = helper.make_tensor_value_info("last_logits", onnx.TensorProto.FLOAT16, [1, 1, VOCAB])
        graph = helper.make_graph(nodes, "last-logits-fixture", inputs, [output], [logits, one, axes])
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
        metadata = {
            "case9.optimization.mode": "last_logits_gather",
            "case9.optimization.mask_rule": "attention_mask is a non-empty binary prefix; index=sum(mask)-1",
            "case9.export.precision": "fp16",
            "case9.export.dynamic_axes": "none",
        }
        for key, value in metadata.items():
            item = model.metadata_props.add()
            item.key, item.value = key, value
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "optimized.onnx"
            contract = root / "contract.json"
            report = root / "report.json"
            onnx.save(model, str(source))
            spec = importlib.util.spec_from_file_location(
                "qwen25_full_inspector", ROOT / "tools" / "inspect_qwen25_full_context_onnx.py"
            )
            self.assertIsNotNone(spec)
            assert spec is not None and spec.loader is not None
            inspector = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(inspector)
            result = inspector.inspect(source, "fixture", contract, report)
            self.assertTrue(result["static_onnx"]["supported"])
            self.assertEqual(result["acl_om"]["execution_mode"], "last_logits_static")
            self.assertEqual(
                [item["byte_size"] for item in result["acl_om"]["inputs"]],
                [8 * sequence] * 3,
            )
            self.assertEqual(result["acl_om"]["outputs"][0]["byte_size"], 2 * VOCAB)

    def test_contract_loads_and_round_trips_last_logits_mode(self):
        contract = Qwen25Contract.from_dict(_contract())
        self.assertEqual(contract.execution_mode, "last_logits_static")
        self.assertEqual(contract.logits_output.shape, (1, 1, VOCAB))
        encoded = contract.as_dict()
        self.assertEqual(encoded["acl_om"]["execution_mode"], "last_logits_static")
        self.assertEqual(encoded["acl_om"]["output_selection"], "attention_mask_sum_minus_one")

    def test_descriptor_infers_last_logits_mode(self):
        backend = _LastLogitsBackend()
        descriptor = backend.open(Path("fixture.om"))
        contract = Qwen25Contract.from_descriptor(descriptor.inputs, descriptor.outputs)
        self.assertEqual(contract.execution_mode, "last_logits_static")
        self.assertEqual(contract.logits_output.shape, (1, 1, VOCAB))

    def test_runtime_reads_single_output_row(self):
        backend = _LastLogitsBackend()
        tokenizer = _Tokenizer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            om = root / "candidate.om"
            tokenizer_path = root / "tokenizer.json"
            tokenizer_config_path = root / "tokenizer_config.json"
            contract_path = root / "contract.json"
            om.write_bytes(b"fixture")
            tokenizer_path.write_text("{}", encoding="utf-8")
            tokenizer_config_path.write_text("{}", encoding="utf-8")
            contract_path.write_text(json.dumps(_contract()), encoding="utf-8")
            runtime = Qwen25AclRuntime(
                om,
                tokenizer_path,
                contract_path=contract_path,
                tokenizer_config_path=tokenizer_config_path,
                backend=backend,
                tokenizer=tokenizer,
                max_tokens=1,
            )
            runtime.start()
            self.assertEqual(runtime.status()["execution_mode"], "last_logits_static")
            result = runtime.complete([{"role": "user", "content": "你好"}], 1)
            self.assertEqual(result.completion_tokens, 1)
            self.assertEqual(result.finish_reason, "stop")
            self.assertGreaterEqual(backend.calls, 1)
            runtime.close()

    def test_last_logits_contract_rejects_full_output_shape(self):
        raw = _contract()
        raw["acl_om"]["outputs"][0]["shape"] = [1, 8, VOCAB]
        raw["acl_om"]["outputs"][0]["byte_size"] = 2 * 8 * VOCAB
        with self.assertRaises(ValueError):
            Qwen25Contract.from_dict(raw)

    def test_descriptor_rejects_wrong_byte_size(self):
        contract = Qwen25Contract.from_dict(_contract())
        backend = _LastLogitsBackend()
        descriptor = backend.open(Path("fixture.om"))
        bad_output = TensorDescriptor("last_logits", "float16", (1, 1, VOCAB), 2 * VOCAB + 2)
        bad_descriptor = RuntimeDescriptor(descriptor.inputs, (bad_output,))
        with self.assertRaises(ValueError):
            contract.validate_descriptor(bad_descriptor.inputs, bad_descriptor.outputs)


if __name__ == "__main__":
    unittest.main()
