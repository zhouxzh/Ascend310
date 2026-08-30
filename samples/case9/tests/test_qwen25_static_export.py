"""Controller-side tests for the external Qwen2.5 static ONNX workflow.

These tests are intentionally CPU/controller-only.  They do not import ACL,
run ATC, download a checkpoint, invoke Transformers, or execute ONNX Runtime.
The optional graph fixtures use the ONNX protobuf package only when it is
already installed in the test environment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
EXPORTER_PATH = TOOLS / "export_qwen25_full_context_onnx.py"
INSPECTOR_PATH = TOOLS / "inspect_qwen25_full_context_onnx.py"
REQUIREMENTS_PATH = ROOT / "requirements-qwen25-export-sci-agent.txt"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Qwen25StaticSourceTests(unittest.TestCase):
    def test_expected_files_exist(self) -> None:
        self.assertTrue(EXPORTER_PATH.is_file())
        self.assertTrue(INSPECTOR_PATH.is_file())
        self.assertTrue(REQUIREMENTS_PATH.is_file())

    def test_exporter_is_local_cpu_only_and_static(self) -> None:
        source = EXPORTER_PATH.read_text(encoding="utf-8")
        for required in (
            "local_files_only",
            "torch.device(\"cpu\")",
            ".float()",
            "use_cache=False",
            '"dynamic_axes": None',
            "input_names",
            "output_names",
            "source_revision",
            "onnx.checker.check_model",
        ):
            self.assertIn(required, source)
        for forbidden in ("import acl", "import torch_npu", "import mindspore", "atc "):
            self.assertNotIn(forbidden, source)

    def test_inspector_is_static_and_never_executes_graph(self) -> None:
        source = INSPECTOR_PATH.read_text(encoding="utf-8")
        for required in (
            "load_external_data=False",
            "onnx.checker.check_model",
            "dynamic or symbolic dimensions",
            "external initializers are not supported",
            "KV-cache tensor names are not admitted",
            "source_artifact",
            "source_revision",
            "SUPPORTED_OPERATOR_TYPES",
        ):
            self.assertIn(required, source)
        for forbidden in ("import acl", "import torch", "onnxruntime", "subprocess", "atc "):
            self.assertNotIn(forbidden, source)

    def test_external_requirements_have_no_board_runtime(self) -> None:
        lines = [
            line.strip().lower()
            for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        joined = "\n".join(lines)
        self.assertIn("torch==", joined)
        self.assertIn("onnx==", joined)
        self.assertIn("onnxruntime==", joined)
        for forbidden in (
            "torch_npu",
            "torchaudio",
            "mindtorch",
            "mindspore",
            "vllm",
            "mindie",
            "acl",
            "cann",
            "atc",
        ):
            self.assertNotIn(forbidden, joined)
        self.assertNotIn("onnxruntime-gpu", joined)

    def test_exporter_argument_contract_is_fail_closed(self) -> None:
        exporter = _load_module("qwen25_exporter_for_test", EXPORTER_PATH)
        with self.assertRaises(exporter.ExportError):
            exporter._require_local_checkpoint("Qwen/Qwen2.5-0.5B-Instruct")
        with self.assertRaises(exporter.ExportError):
            exporter._validate_source_revision("")
        with self.assertRaises(exporter.ExportError):
            exporter._validate_positive_int(0, "sequence_length")


class Qwen25StaticInspectorFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if importlib.util.find_spec("onnx") is None:
            raise unittest.SkipTest("onnx is not installed; graph fixture checks are optional")
        cls.inspector = _load_module("qwen25_inspector_for_test", INSPECTOR_PATH)
        import onnx  # type: ignore

        cls.onnx = onnx

    def _write_graph(self, *, dynamic: bool = False) -> Path:
        onnx = self.onnx
        sequence_length = 4
        vocab_size = 151936
        input_shape = [1, "sequence"] if dynamic else [1, sequence_length]
        inputs = [
            onnx.helper.make_tensor_value_info(name, onnx.TensorProto.INT64, input_shape)
            for name in ("input_ids", "attention_mask", "position_ids")
        ]
        logits_tensor = onnx.helper.make_tensor(
            "constant_logits",
            onnx.TensorProto.FLOAT16,
            [1, sequence_length, vocab_size],
            [0.0] * (sequence_length * vocab_size),
        )
        node = onnx.helper.make_node("Constant", [], ["logits"], value=logits_tensor)
        output = onnx.helper.make_tensor_value_info(
            "logits", onnx.TensorProto.FLOAT16, [1, sequence_length, vocab_size]
        )
        graph = onnx.helper.make_graph([node], "qwen25-test", inputs, [output])
        model = onnx.helper.make_model(
            graph,
            opset_imports=[onnx.helper.make_opsetid("", 17)],
            producer_name="case9-test",
        )
        metadata = {
            "case9.source_revision": "test-revision",
            "case9.export.device": "cpu",
            "case9.export.precision": "fp16",
            "case9.export.dynamic_axes": "none",
            "case9.export.vocab_size": str(vocab_size),
            "case9.model_id": "qwen2.5-0.5b-instruct-static-fp16",
        }
        for key, value in metadata.items():
            item = model.metadata_props.add()
            item.key = key
            item.value = value
        handle = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
        handle.close()
        path = Path(handle.name)
        onnx.save(model, str(path))
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_fixed_graph_is_admitted(self) -> None:
        path = self._write_graph()
        contract = self.inspector.inspect(
            path, "test-revision", output=path.with_suffix(".json"),
            report=path.with_name(path.stem + "-report.json"),
        )
        self.assertTrue(contract["static_onnx"]["supported"])
        self.assertEqual(contract["source_artifact"]["bytes"], path.stat().st_size)

    def test_dynamic_input_is_blocked(self) -> None:
        path = self._write_graph(dynamic=True)
        contract = self.inspector.inspect(
            path, "test-revision", output=path.with_suffix(".json"),
            report=path.with_name(path.stem + "-report.json"),
        )
        self.assertFalse(contract["static_onnx"]["supported"])
        self.assertTrue(any("dynamic" in reason for reason in [contract["static_onnx"]["support_reason"]]))


if __name__ == "__main__":
    unittest.main()
