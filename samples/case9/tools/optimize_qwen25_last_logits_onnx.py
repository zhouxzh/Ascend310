#!/usr/bin/env python3
"""Reduce a static Qwen2.5 ONNX output to the last valid token.

This is a controller-side graph rewrite.  It does not change model weights or
the transformer computation: the original ``[1, S, V]`` logits remain an
internal value and a small ``ReduceSum -> Sub -> Gather`` tail exposes only
``[1, 1, V]``.  The ACL service can therefore avoid copying the full logits
tensor back to the host after every token.

The rewrite is deliberately fail-closed.  It accepts only a three-input,
fixed-shape full-context graph with a binary prefix attention mask.  The
caller must still inspect the rewritten graph and run ATC/ACL validation on
the target CANN release; this script is not an Ascend conversion tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
OUTPUT_NAME = "last_logits"
OPTIMIZATION_MODE = "last_logits_gather"


class OptimizationError(RuntimeError):
    """Raised when a graph does not meet the rewrite contract."""


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


def _shape(value: Any) -> Tuple[int, ...]:
    tensor_shape = value.type.tensor_type.shape
    dimensions = []
    for index, dimension in enumerate(tensor_shape.dim):
        if dimension.dim_param or not dimension.HasField("dim_value") or dimension.dim_value <= 0:
            raise OptimizationError(
                f"{value.name} has a dynamic/non-positive dimension at index {index}"
            )
        dimensions.append(int(dimension.dim_value))
    return tuple(dimensions)


def _dtype(value: Any, tensor_proto: Any) -> int:
    return int(value.type.tensor_type.elem_type)


def _metadata(graph: Any, values: Mapping[str, str]) -> None:
    for key in values:
        for existing in list(graph.metadata_props):
            if existing.key == key:
                graph.metadata_props.remove(existing)
    for key, value in values.items():
        item = graph.metadata_props.add()
        item.key = str(key)
        item.value = str(value)


def _unique_name(existing: set[str], base: str) -> str:
    candidate = base
    suffix = 0
    while candidate in existing:
        suffix += 1
        candidate = f"{base}_{suffix}"
    existing.add(candidate)
    return candidate


def _validate_source(path: Path, onnx: Any) -> Tuple[Any, Any, Tuple[int, ...], int]:
    if not path.is_file():
        raise OptimizationError(f"source ONNX does not exist: {path}")
    if _is_lfs_pointer(path):
        raise OptimizationError("source is a Git LFS pointer, not an ONNX model")
    try:
        model = onnx.load(str(path), load_external_data=False)
        onnx.checker.check_model(model)
    except Exception as exc:  # noqa: BLE001 - normalize protobuf/checker errors
        raise OptimizationError(f"cannot load/check source ONNX: {exc}") from exc

    graph = model.graph
    if any(item.data_location == onnx.TensorProto.EXTERNAL for item in graph.initializer):
        raise OptimizationError("external initializers are not admitted")
    input_values = [item for item in graph.input if item.name not in {x.name for x in graph.initializer}]
    if tuple(item.name for item in input_values) != INPUT_ORDER:
        raise OptimizationError("graph inputs must be input_ids, attention_mask, position_ids in order")
    input_shapes = tuple(_shape(item) for item in input_values)
    if len(input_shapes) != 3 or any(shape != input_shapes[0] for shape in input_shapes):
        raise OptimizationError("all inputs must share one static shape [1,S]")
    if input_shapes[0][0] != 1 or len(input_shapes[0]) != 2:
        raise OptimizationError("inputs must have static shape [1,S]")
    if any(_dtype(item, onnx.TensorProto) != onnx.TensorProto.INT64 for item in input_values):
        raise OptimizationError("all three inputs must be int64")
    if len(graph.output) != 1:
        raise OptimizationError("source graph must expose exactly one output")
    output = graph.output[0]
    output_shape = _shape(output)
    if len(output_shape) != 3 or output_shape[:2] != input_shapes[0] or output_shape[2] <= 0:
        raise OptimizationError("source output must have static shape [1,S,V]")
    if _dtype(output, onnx.TensorProto) not in {
        onnx.TensorProto.FLOAT16,
        onnx.TensorProto.FLOAT,
    }:
        raise OptimizationError("source logits must be float16 or float32")
    if not output.name:
        raise OptimizationError("source output has no name")
    return model, output, output_shape, int(input_shapes[0][1])


def optimize_model(
    source: Path,
    output: Path,
    *,
    source_revision: Optional[str] = None,
    report: Optional[Path] = None,
) -> Dict[str, Any]:
    """Rewrite ``source`` and return an immutable artifact report."""

    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if source == output:
        raise OptimizationError("source and output must be different files")
    try:
        import numpy as np  # type: ignore
        import onnx  # type: ignore
        from onnx import helper, numpy_helper
    except ImportError as exc:
        raise OptimizationError("numpy and onnx are required on the external builder") from exc

    model, source_output, source_shape, sequence_length = _validate_source(source, onnx)
    graph = model.graph
    old_output_name = source_output.name

    # Collect every value name before adding the rewrite tail.  Renaming the
    # original graph output keeps the public output name unambiguous.
    value_names: set[str] = set()
    for value in list(graph.input) + list(graph.output) + list(graph.value_info):
        value_names.add(value.name)
    for initializer in graph.initializer:
        value_names.add(initializer.name)
    for node in graph.node:
        value_names.update(node.input)
        value_names.update(node.output)
    if OUTPUT_NAME in value_names:
        raise OptimizationError(
            f"source already uses reserved optimization value name: {OUTPUT_NAME}"
        )
    producers = [node for node in graph.node if old_output_name in node.output]
    if len(producers) != 1:
        raise OptimizationError("source output must have exactly one graph producer")

    full_logits_name = _unique_name(value_names, "case9_full_context_logits")
    # The old output is normally produced by the final Cast node.  Update all
    # references so it becomes an internal value, then expose a new `logits`
    # equivalent under the optimizer's stable `last_logits` name.
    for node in graph.node:
        for index, name in enumerate(node.output):
            if name == old_output_name:
                node.output[index] = full_logits_name
        for index, name in enumerate(node.input):
            if name == old_output_name:
                node.input[index] = full_logits_name
    for value in graph.value_info:
        if value.name == old_output_name:
            value.name = full_logits_name

    one_name = _unique_name(value_names, "case9_last_logits_one")
    axes_name = _unique_name(value_names, "case9_attention_mask_axes")
    valid_length_name = _unique_name(value_names, "case9_valid_length")
    last_index_name = _unique_name(value_names, "case9_last_index")
    axes = numpy_helper.from_array(np.asarray([1], dtype=np.int64), name=axes_name)
    one = numpy_helper.from_array(np.asarray(1, dtype=np.int64), name=one_name)
    graph.initializer.extend([axes, one])

    nodes = [
        helper.make_node(
            "ReduceSum",
            ["attention_mask", axes_name],
            [valid_length_name],
            keepdims=0,
            name="case9_reduce_attention_mask_length",
        ),
        helper.make_node(
            "Sub",
            [valid_length_name, one_name],
            [last_index_name],
            name="case9_compute_last_logits_index",
        ),
        helper.make_node(
            "Gather",
            [full_logits_name, last_index_name],
            [OUTPUT_NAME],
            axis=1,
            name="case9_gather_last_logits",
        ),
    ]
    graph.node.extend(nodes)
    graph.output.remove(source_output)
    graph.output.append(
        helper.make_tensor_value_info(
            OUTPUT_NAME,
            int(source_output.type.tensor_type.elem_type),
            [1, 1, source_shape[2]],
        )
    )
    metadata = {
        "case9.optimization.mode": OPTIMIZATION_MODE,
        "case9.optimization.source_output": old_output_name,
        "case9.optimization.output_shape": json.dumps([1, 1, source_shape[2]]),
        "case9.optimization.sequence_length": str(sequence_length),
        "case9.optimization.mask_rule": "attention_mask is a non-empty binary prefix; index=sum(mask)-1",
        "case9.optimization.source_bytes": str(source.stat().st_size),
        "case9.optimization.source_sha256": _sha256(source),
    }
    if source_revision is not None:
        if not isinstance(source_revision, str) or not source_revision.strip() or any(
            c in source_revision for c in "\r\n\x00"
        ):
            raise OptimizationError("source_revision must be a single non-empty line")
        metadata["case9.source_revision"] = source_revision.strip()
    # ONNX metadata lives on ModelProto, not GraphProto.
    _metadata(model, metadata)

    try:
        onnx.checker.check_model(model)
    except Exception as exc:  # noqa: BLE001 - normalize graph validation errors
        raise OptimizationError(f"optimized graph failed ONNX checker: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".part", dir=str(output.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        onnx.save(model, str(temporary))
        if _is_lfs_pointer(temporary) or temporary.stat().st_size <= 0:
            raise OptimizationError("optimized output is empty or an LFS pointer")
        os.replace(temporary, output)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    result: Dict[str, Any] = {
        "schema_version": 1,
        "status": "optimized",
        "optimization": {
            "mode": OPTIMIZATION_MODE,
            "source_output": old_output_name,
            "source_shape": list(source_shape),
            "output_shape": [1, 1, source_shape[2]],
            "sequence_length": sequence_length,
            "mask_rule": "non-empty binary prefix; index=sum(attention_mask)-1",
            "nodes": [node.op_type for node in nodes],
        },
        "source_artifact": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": _sha256(source),
        },
        "artifact": {
            "path": str(output),
            "bytes": output.stat().st_size,
            "sha256": _sha256(output),
        },
    }
    if source_revision is not None:
        result["source_revision"] = source_revision.strip()
    if report is not None:
        report = report.expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = report.with_name(report.name + ".tmp")
        temporary_report.write_text(
            json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary_report, report)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = optimize_model(
            args.source,
            args.output,
            source_revision=args.source_revision,
            report=args.report,
        )
    except (OptimizationError, OSError) as exc:
        print(f"Qwen2.5 last-logits optimization failed: {exc}", file=__import__("sys").stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
