#!/usr/bin/env python3
"""Export Qwen2.5-0.5B as a fixed-capacity StaticCache ONNX graph.

This script is an external, controller-side build step. It deliberately uses
only a local checkpoint and CPU PyTorch. The exported graph performs one
decode token per invocation and exposes the cache as 48 independent tensors.

The cache inputs use native Qwen layout ``[batch, kv_heads, time, dim]``.
``StaticCache`` receives those tensors without a packed or legacy tuple
adapter. Its update is functional ``index_copy`` so the ONNX graph contains
an explicit write at ``cache_position``; only the written token is exported
as each output. No ACL, CANN, ATC, ONNX Runtime, or board package is loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect as inspect_module
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


MODEL_FAMILY = "qwen2.5"
MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
EXECUTION_MODE = "static_kv_token_fp32"
BATCH_SIZE = 1
SEQUENCE_LENGTH = 1024
MASK_LENGTH = SEQUENCE_LENGTH
VOCABULARY_SIZE = 151936
NUM_LAYERS = 24
NUM_ATTENTION_HEADS = 14
NUM_KV_HEADS = 2
HEAD_DIM = 64
CACHE_SHAPE = (BATCH_SIZE, NUM_KV_HEADS, SEQUENCE_LENGTH, HEAD_DIM)
TOKEN_CACHE_SHAPE = (BATCH_SIZE, 1, NUM_KV_HEADS, HEAD_DIM)
NUM_CACHE_TENSORS = NUM_LAYERS * 2
DEFAULT_OPSET = 17


def _cache_input_name(layer: int, part: str) -> str:
    return f"past_key_values.{layer}.{part}"


def _cache_output_name(layer: int, part: str) -> str:
    return f"present.{layer}.{part}"


CACHE_INPUT_NAMES = tuple(
    _cache_input_name(layer, part)
    for layer in range(NUM_LAYERS)
    for part in ("key", "value")
)
CACHE_OUTPUT_NAMES = tuple(
    _cache_output_name(layer, part)
    for layer in range(NUM_LAYERS)
    for part in ("key", "value")
)
INPUT_NAMES = ("input_ids", "attention_mask", "position_ids") + CACHE_INPUT_NAMES
OUTPUT_NAMES = ("logits",) + CACHE_OUTPUT_NAMES


class ExportError(RuntimeError):
    """Raised when a local CPU export cannot be admitted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(256).startswith(b"version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def _validate_positive_int(value: int, name: str, *, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExportError(f"{name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ExportError(f"{name} must be <= {maximum}")
    return int(value)


def _validate_source_revision(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExportError("--source-revision must be a non-empty immutable revision")
    value = value.strip()
    if len(value) > 200 or any(char in value for char in "\r\n\x00"):
        raise ExportError("--source-revision contains invalid characters")
    return value


def _require_local_checkpoint(path_value: str) -> Path:
    if not isinstance(path_value, str) or not path_value.strip():
        raise ExportError("--model must be a local checkpoint directory")
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise ExportError(f"checkpoint directory does not exist: {path}; refusing Hub/network loading")
    if not (path / "config.json").is_file():
        raise ExportError(f"checkpoint is missing config.json: {path}")
    weight_files = tuple(
        item
        for item in path.iterdir()
        if item.is_file() and item.name.endswith((".bin", ".safetensors", ".pt", ".pth"))
    )
    index_files = (path / "model.safetensors.index.json", path / "pytorch_model.bin.index.json")
    if not weight_files and not any(item.is_file() for item in index_files):
        raise ExportError(f"checkpoint has no local model weights: {path}")
    if any(_is_lfs_pointer(item) for item in weight_files):
        raise ExportError("checkpoint contains a Git LFS pointer instead of model weights")
    return path


def _check_external_environment() -> None:
    """Reject packages that could turn this into a board/framework build."""

    forbidden = ("acl", "torch_npu", "mindspore", "mindtorch", "vllm", "mindie")
    present = [name for name in forbidden if importlib.util.find_spec(name) is not None]
    if present:
        raise ExportError(
            "external CPU export environment contains board/framework packages: "
            + ", ".join(sorted(present))
        )


def _load_dependencies() -> Tuple[Any, Any, Any]:
    _check_external_environment()
    try:
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM  # type: ignore
        from transformers.cache_utils import Cache, StaticCache  # type: ignore
    except ImportError as exc:
        raise ExportError(
            "CPU export dependencies are missing; install only "
            "requirements-qwen25-export-sci-agent.txt on the external builder"
        ) from exc
    return torch, AutoModelForCausalLM, (Cache, StaticCache)


def _validate_architecture(config: Any) -> None:
    expected = {
        "vocab_size": VOCABULARY_SIZE,
        "num_hidden_layers": NUM_LAYERS,
        "num_attention_heads": NUM_ATTENTION_HEADS,
        "num_key_value_heads": NUM_KV_HEADS,
    }
    for name, value in expected.items():
        actual = int(getattr(config, name, 0) or 0)
        if actual != value:
            raise ExportError(f"Qwen2.5 config {name} must be {value}, got {actual}")
    hidden_size = int(getattr(config, "hidden_size", 0) or 0)
    if hidden_size != NUM_ATTENTION_HEADS * HEAD_DIM:
        raise ExportError(f"Qwen2.5 hidden_size must be {NUM_ATTENTION_HEADS * HEAD_DIM}, got {hidden_size}")
    configured_head_dim = int(getattr(config, "head_dim", HEAD_DIM) or HEAD_DIM)
    if configured_head_dim != HEAD_DIM:
        raise ExportError(f"Qwen2.5 head_dim must be {HEAD_DIM}, got {configured_head_dim}")
    max_positions = int(getattr(config, "max_position_embeddings", 0) or 0)
    if max_positions < SEQUENCE_LENGTH:
        raise ExportError(f"checkpoint max_position_embeddings must be >= {SEQUENCE_LENGTH}")


def _load_model(model_dir: Path, source_revision: str, torch: Any, model_loader: Any) -> Any:
    kwargs: Dict[str, Any] = {"local_files_only": True, "trust_remote_code": False}
    try:
        model = model_loader.from_pretrained(
            str(model_dir),
            revision=source_revision,
            attn_implementation="eager",
            **kwargs,
        )
    except (TypeError, ValueError):
        try:
            model = model_loader.from_pretrained(str(model_dir), revision=source_revision, **kwargs)
        except Exception as exc:  # noqa: BLE001 - normalize loader failures
            raise ExportError(f"could not load the local checkpoint: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - normalize loader failures
        raise ExportError(f"could not load the local checkpoint: {exc}") from exc

    # Keep the 0.5B weight tensors FP16 so the single-file ONNX stays below
    # protobuf's 2 GiB limit. Public cache/logit interfaces remain FP32 and
    # are cast explicitly in the wrapper below.
    model = model.eval().to(torch.device("cpu")).half()
    for parameter in model.parameters():
        if parameter.device.type != "cpu" or parameter.dtype != torch.float16:
            raise ExportError("checkpoint parameters are not resident in CPU FP16")
    config = getattr(model, "config", None)
    if config is None:
        raise ExportError("checkpoint has no model config")
    config.use_cache = True
    if hasattr(config, "_attn_implementation"):
        config._attn_implementation = "eager"
    _validate_architecture(config)
    return model


def _make_input_cache(torch: Any, cache_base: Any, keys: Sequence[Any], values: Sequence[Any]) -> Any:
    """Create a StaticCache backed directly by graph inputs."""

    Cache, StaticCache = cache_base

    class InputStaticCache(StaticCache):
        def __init__(self, config: Any, key_values: Sequence[Any], value_values: Sequence[Any]) -> None:
            Cache.__init__(self)
            self.batch_size = BATCH_SIZE
            self.max_cache_len = SEQUENCE_LENGTH
            self.head_dim = HEAD_DIM
            self.num_key_value_heads = NUM_KV_HEADS
            self.dtype = torch.float16
            self.key_cache = [value.to(dtype=torch.float16) for value in key_values]
            self.value_cache = [value.to(dtype=torch.float16) for value in value_values]

        def update(
            self,
            key_states: Any,
            value_states: Any,
            layer_idx: int,
            cache_kwargs: Optional[Mapping[str, Any]] = None,
        ) -> Tuple[Any, Any]:
            if not isinstance(layer_idx, int) or not 0 <= layer_idx < NUM_LAYERS:
                raise ExportError("Qwen cache layer index is outside the fixed architecture")
            if not isinstance(cache_kwargs, Mapping) or "cache_position" not in cache_kwargs:
                raise ExportError("StaticCache update requires cache_position")
            cache_position = cache_kwargs["cache_position"]
            key_out = self.key_cache[layer_idx].index_copy(2, cache_position, key_states)
            value_out = self.value_cache[layer_idx].index_copy(2, cache_position, value_states)
            self.key_cache[layer_idx] = key_out
            self.value_cache[layer_idx] = value_out
            return key_out, value_out

    return InputStaticCache(cache_base, keys, values)


def _make_wrapper(torch: Any, model: Any, cache_base: Any) -> Any:
    class StaticQwenKV(torch.nn.Module):
        def __init__(self, inner: Any) -> None:
            super().__init__()
            self.inner = inner
            try:
                parameters = inspect_module.signature(inner.forward).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "cache_position" not in parameters or "num_logits_to_keep" not in parameters:
                raise ExportError("installed Transformers Qwen model lacks StaticCache export arguments")

        def forward(self, input_ids: Any, attention_mask: Any, position_ids: Any, *cache_inputs: Any) -> Tuple[Any, ...]:
            if len(cache_inputs) != NUM_CACHE_TENSORS:
                raise ExportError(f"expected {NUM_CACHE_TENSORS} split cache inputs")
            keys = [cache_inputs[layer * 2] for layer in range(NUM_LAYERS)]
            values = [cache_inputs[layer * 2 + 1] for layer in range(NUM_LAYERS)]
            cache = _make_input_cache(torch, cache_base, keys, values)
            output = self.inner(
                input_ids=input_ids,
                attention_mask=attention_mask,
                position_ids=position_ids,
                past_key_values=cache,
                cache_position=position_ids.reshape(-1),
                use_cache=True,
                return_dict=True,
                num_logits_to_keep=1,
            )
            logits = output.logits.float()
            if tuple(logits.shape) != (BATCH_SIZE, 1, VOCABULARY_SIZE):
                raise ExportError("Qwen forward returned logits with an unexpected token shape")
            result = [logits]
            position = position_ids.reshape(-1)
            for layer in range(NUM_LAYERS):
                key_token = torch.index_select(cache.key_cache[layer], 2, position)
                value_token = torch.index_select(cache.value_cache[layer], 2, position)
                result.extend(
                    [
                        key_token.transpose(1, 2).contiguous().float(),
                        value_token.transpose(1, 2).contiguous().float(),
                    ]
                )
            return tuple(result)

    return StaticQwenKV(model).eval().to(torch.device("cpu"))


def _dummy_inputs(torch: Any) -> Tuple[Any, ...]:
    base = (
        torch.zeros((BATCH_SIZE, 1), dtype=torch.int64, device="cpu"),
        torch.ones((BATCH_SIZE, MASK_LENGTH), dtype=torch.int64, device="cpu"),
        torch.zeros((BATCH_SIZE, 1), dtype=torch.int64, device="cpu"),
    )
    caches = tuple(
        torch.zeros(CACHE_SHAPE, dtype=torch.float32, device="cpu")
        for _ in range(NUM_CACHE_TENSORS)
    )
    return base + caches


def _export_kwargs(torch: Any, opset: int) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "input_names": list(INPUT_NAMES),
        "output_names": list(OUTPUT_NAMES),
        "opset_version": opset,
        "do_constant_folding": True,
        "export_params": True,
        "training": torch.onnx.TrainingMode.EVAL,
        "dynamic_axes": None,
    }
    try:
        parameters = inspect_module.signature(torch.onnx.export).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "dynamo" in parameters:
        kwargs["dynamo"] = False
    # Keep the model self-contained.  ATC/board transfer is intentionally
    # one atomic ONNX artifact; external initializer sidecars are rejected by
    # the inspector and are easy to lose during deployment.
    if "external_data" in parameters:
        kwargs["external_data"] = False
    return kwargs


def _metadata_values(*, source_revision: str, opset: int) -> Dict[str, str]:
    return {
        "case9.model_family": MODEL_FAMILY,
        "case9.model_id": MODEL_ID,
        "case9.execution_mode": EXECUTION_MODE,
        "case9.source_revision": source_revision,
        "case9.export.device": "cpu",
        "case9.export.precision": "fp32",
        "case9.export.weights_precision": "fp16",
        "case9.export.batch_size": str(BATCH_SIZE),
        "case9.export.static_sequence_length": str(SEQUENCE_LENGTH),
        "case9.export.mask_length": str(MASK_LENGTH),
        "case9.export.cache_layout": "split",
        "case9.export.cache_shape": json.dumps(list(CACHE_SHAPE), separators=(",", ":")),
        "case9.export.token_cache_shape": json.dumps(list(TOKEN_CACHE_SHAPE), separators=(",", ":")),
        "case9.export.cache_inputs": str(NUM_CACHE_TENSORS),
        "case9.export.dynamic_axes": "none",
        "case9.export.use_cache": "true",
        "case9.export.num_logits_to_keep": "1",
        "case9.export.opset": str(opset),
        "case9.export.vocab_size": str(VOCABULARY_SIZE),
    }


def _annotate_onnx(path: Path, metadata: Mapping[str, str]) -> Dict[str, Any]:
    try:
        import onnx  # type: ignore
    except ImportError as exc:
        raise ExportError("onnx is required to validate and annotate the export") from exc
    try:
        graph = onnx.load(str(path), load_external_data=False)
        onnx.checker.check_model(graph)
    except Exception as exc:  # noqa: BLE001 - normalize checker failures
        raise ExportError(f"exported ONNX failed checker validation: {exc}") from exc
    if any(item.data_location == onnx.TensorProto.EXTERNAL for item in graph.graph.initializer):
        raise ExportError("external ONNX initializers are not admitted")
    for key, value in metadata.items():
        for existing in list(graph.metadata_props):
            if existing.key == key:
                graph.metadata_props.remove(existing)
        item = graph.metadata_props.add()
        item.key = str(key)
        item.value = str(value)
    onnx.save(graph, str(path))
    return {
        "ir_version": int(graph.ir_version),
        "opsets": {(item.domain or "ai.onnx"): int(item.version) for item in graph.opset_import},
        "input_names": [item.name for item in graph.graph.input],
        "output_names": [item.name for item in graph.graph.output],
        "node_count": len(graph.graph.node),
        "initializer_count": len(graph.graph.initializer),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=True, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def export_model(
    model_dir: Path,
    output_path: Path,
    source_revision: str,
    *,
    opset: int = DEFAULT_OPSET,
    report_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Export a local checkpoint and return an immutable artifact report."""

    model_dir = _require_local_checkpoint(str(model_dir))
    source_revision = _validate_source_revision(source_revision)
    opset = _validate_positive_int(opset, "opset", maximum=20)
    torch, model_loader, cache_base = _load_dependencies()
    model = _load_model(model_dir, source_revision, torch, model_loader)
    wrapper = _make_wrapper(torch, model, cache_base)
    dummy = _dummy_inputs(torch)
    with torch.inference_mode():
        sample = wrapper(*dummy)
    if len(sample) != 1 + NUM_CACHE_TENSORS:
        raise ExportError("sample export returned an unexpected output count")
    if tuple(sample[0].shape) != (BATCH_SIZE, 1, VOCABULARY_SIZE) or sample[0].dtype != torch.float32:
        raise ExportError("model logits do not match FP32 [1,1,151936]")
    for value in sample[1:]:
        if tuple(value.shape) != TOKEN_CACHE_SHAPE or value.dtype != torch.float32:
            raise ExportError("model cache outputs do not match FP32 [1,1,2,64]")

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=output_path.name + ".", suffix=".part", dir=str(output_path.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        torch.onnx.export(wrapper, dummy, str(temporary), **_export_kwargs(torch, opset))
        graph_summary = _annotate_onnx(temporary, _metadata_values(source_revision=source_revision, opset=opset))
        if _is_lfs_pointer(temporary) or temporary.stat().st_size <= 0:
            raise ExportError("export produced an empty file or an LFS pointer")
        digest = _sha256(temporary)
        size = temporary.stat().st_size
        os.replace(temporary, output_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    report: Dict[str, Any] = {
        "schema_version": 2,
        "status": "exported",
        "model": {
            "family": MODEL_FAMILY,
            "model_id": MODEL_ID,
            "source_checkpoint": str(model_dir),
            "source_revision": source_revision,
        },
        "architecture": {
            "layers": NUM_LAYERS,
            "attention_heads": NUM_ATTENTION_HEADS,
            "kv_heads": NUM_KV_HEADS,
            "head_dim": HEAD_DIM,
            "vocabulary_size": VOCABULARY_SIZE,
        },
        "execution": {
            "mode": EXECUTION_MODE,
            "device": "cpu",
            "precision": "fp32",
            "weights_precision": "fp16",
            "batch_size": BATCH_SIZE,
            "static_sequence_length": SEQUENCE_LENGTH,
            "mask_length": MASK_LENGTH,
            "cache_layout": "split",
            "cache_input_shape": list(CACHE_SHAPE),
            "cache_output_shape": list(TOKEN_CACHE_SHAPE),
            "cache_tensor_count": NUM_CACHE_TENSORS,
            "input_names": list(INPUT_NAMES),
            "output_names": list(OUTPUT_NAMES),
            "dynamic_axes": None,
            "use_cache": True,
            "num_logits_to_keep": 1,
            "opset": opset,
        },
        "artifact": {"path": str(output_path), "bytes": size, "sha256": digest},
        "graph": graph_summary,
    }
    if report_path is not None:
        _write_json(report_path.expanduser().resolve(), report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="local Qwen2.5 checkpoint directory")
    parser.add_argument("--output", required=True, type=Path, help="output ONNX path")
    parser.add_argument("--source-revision", required=True, help="immutable checkpoint revision")
    parser.add_argument("--opset", type=int, default=DEFAULT_OPSET, help="ONNX opset (default: 17)")
    parser.add_argument("--report", type=Path, help="optional JSON export report")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = export_model(
            Path(args.model),
            args.output,
            args.source_revision,
            opset=args.opset,
            report_path=args.report,
        )
    except (ExportError, OSError) as exc:
        print(f"Qwen2.5 static-KV export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


export = export_model


if __name__ == "__main__":
    raise SystemExit(main())
