#!/usr/bin/env python3
"""Export Qwen2.5-0.5B as a fixed-shape CPU ONNX graph (external builder).

The board never runs this script.  It uses local files only, CPU Torch, and
FP16 weights/logits by default so the resulting artifact remains a single
file.  No dynamic axes or KV-cache tensors are exported.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional, Sequence

MODEL_ID = "qwen2.5-0.5b-instruct-static-fp16"
VOCAB_SIZE = 151936
DEFAULT_SEQUENCE_LENGTH = 128


class ExportError(RuntimeError):
    pass


def _validate_source_revision(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\x00"):
        raise ExportError("source revision must be a non-empty immutable value")
    return value.strip()


def _validate_positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExportError(f"{name} must be a positive integer")
    return int(value)


def _require_local_checkpoint(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or not (path / "config.json").is_file():
        raise ExportError("checkpoint must be a local directory with config.json")
    if not any(path.glob("*.safetensors")) and not any(path.glob("*.bin")):
        raise ExportError("checkpoint has no local model weights")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_environment() -> None:
    forbidden = ("acl", "torch_npu", "mindspore", "mindtorch", "vllm", "mindie")
    present = [name for name in forbidden if importlib.util.find_spec(name) is not None]
    if present:
        raise ExportError("external builder contains forbidden board packages: " + ", ".join(present))


def _load(model_dir: Path, revision: str, precision: str):
    _check_environment()
    try:
        import torch
        from transformers import AutoModelForCausalLM
    except ImportError as exc:
        raise ExportError("install the external sci-agent export requirements first") from exc
    model_dir = _require_local_checkpoint(str(model_dir))
    weights = tuple(model_dir.glob("*.safetensors")) + tuple(model_dir.glob("*.bin"))
    if not weights:
        raise ExportError("local checkpoint has no model weights")
    try:
        model = AutoModelForCausalLM.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=False,
            revision=revision, attn_implementation="eager",
        )
    except (TypeError, ValueError):
        model = AutoModelForCausalLM.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=False, revision=revision,
        )
    model = model.eval().to(torch.device("cpu"))
    model = model.half() if precision == "fp16" else model.float()
    expected = torch.float16 if precision == "fp16" else torch.float32
    if any(p.device.type != "cpu" or p.dtype != expected for p in model.parameters()):
        raise ExportError("checkpoint parameters are not resident in requested CPU precision")
    model.config.use_cache = False
    return torch, model


def export_model(model_dir: Path, output: Path, report: Optional[Path], revision: str, sequence_length: int, precision: str) -> Dict[str, Any]:
    sequence_length = _validate_positive_int(sequence_length, "sequence_length")
    if sequence_length > 4096:
        raise ExportError("sequence_length must be between 1 and 4096")
    if precision not in {"fp16", "fp32"}:
        raise ExportError("precision must be fp16 or fp32")
    revision = _validate_source_revision(revision)
    torch, model = _load(model_dir, revision, precision)
    vocab = int(getattr(model.config, "vocab_size", 0))
    if vocab != VOCAB_SIZE:
        raise ExportError(f"unexpected Qwen2.5 vocabulary: {vocab}")

    class Wrapper(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner
        def forward(self, input_ids, attention_mask, position_ids):
            result = self.inner(input_ids=input_ids, attention_mask=attention_mask,
                                position_ids=position_ids, use_cache=False, return_dict=False)
            logits = result[0] if isinstance(result, (tuple, list)) else result.logits
            return logits.half() if precision == "fp16" else logits.float()

    wrapper = Wrapper(model).eval()
    shape = (1, sequence_length)
    ids = torch.zeros(shape, dtype=torch.int64)
    mask = torch.ones(shape, dtype=torch.int64)
    positions = torch.arange(sequence_length, dtype=torch.int64).reshape(shape)
    with torch.inference_mode():
        sample = wrapper(ids, mask, positions)
    expected_dtype = torch.float16 if precision == "fp16" else torch.float32
    if tuple(sample.shape) != (1, sequence_length, VOCAB_SIZE) or sample.dtype != expected_dtype:
        raise ExportError("model output does not match the fixed logits contract")

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".part", dir=str(output.parent))
    os.close(fd)
    temporary = Path(tmp_name)
    try:
        kwargs: Dict[str, Any] = {
            "input_names": ["input_ids", "attention_mask", "position_ids"],
            "output_names": ["logits"], "opset_version": 17,
            "do_constant_folding": True, "export_params": True,
            "training": torch.onnx.TrainingMode.EVAL, "dynamic_axes": None,
        }
        if "dynamo" in inspect.signature(torch.onnx.export).parameters:
            kwargs["dynamo"] = False
        torch.onnx.export(wrapper, (ids, mask, positions), str(temporary), **kwargs)
        import onnx
        graph = onnx.load(str(temporary), load_external_data=False)
        onnx.checker.check_model(graph)
        metadata = {
            "case9.model_id": MODEL_ID, "case9.model_family": "qwen2.5",
            "case9.source_revision": revision, "case9.export.device": "cpu",
            "case9.export.precision": precision, "case9.export.sequence_length": str(sequence_length),
            "case9.export.vocab_size": str(VOCAB_SIZE), "case9.export.dynamic_axes": "none",
            "case9.export.use_cache": "false",
        }
        for item in list(graph.metadata_props):
            if item.key in metadata:
                graph.metadata_props.remove(item)
        for key, value in metadata.items():
            item = graph.metadata_props.add(); item.key = key; item.value = value
        onnx.save(graph, str(temporary))
        if any(init.data_location == onnx.TensorProto.EXTERNAL for init in graph.graph.initializer):
            raise ExportError("external initializers are not admitted")
        os.replace(temporary, output)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass
    result = {
        "schema_version": 1, "status": "exported", "model_id": MODEL_ID,
        "source_revision": revision,
        "execution": {"device": "cpu", "precision": precision, "batch_size": 1,
                       "sequence_length": sequence_length, "dynamic_axes": None, "use_cache": False},
        "artifact": {"path": str(output), "bytes": output.stat().st_size, "sha256": _sha256(output)},
    }
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--sequence-length", type=int, default=DEFAULT_SEQUENCE_LENGTH)
    parser.add_argument("--precision", choices=("fp16", "fp32"), default="fp16")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export_model(args.model, args.output, args.report, args.source_revision, args.sequence_length, args.precision), indent=2))
        return 0
    except (ExportError, OSError) as exc:
        print(f"export failed: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
