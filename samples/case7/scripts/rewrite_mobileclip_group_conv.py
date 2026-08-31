#!/usr/bin/env python3
"""Rewrite selected grouped convolutions into serial branches.

The MobileCLIP image graph contains a grouped convolution with two output
channels per input group.  Some Ascend compiler releases are sensitive to
that form.  This tool creates an isolated ONNX candidate by running one
grouped Conv for each output slot and interleaving the slots back into the
original channel order with Unsqueeze/Concat/Reshape.

The source graph and production artifacts are never modified.  The rewrite is
deliberately conservative: only Conv nodes explicitly named on the command
line (or all eligible nodes with ``--all-eligible``) are changed.  A grouped
Conv whose output count is not an integer multiple of its group count is
rejected rather than guessed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import onnx
from onnx import checker, helper, numpy_helper, shape_inference


DEFAULT_NODE = (
    "/image_encoder/model/network.5/proj/proj.0/"
    "lkb_reparam/Conv"
)
DEFAULT_INPUT_SHAPE = (1, 3, 256, 256)
SCHEMA_VERSION = 1


class RewriteError(RuntimeError):
    """Raised when a graph cannot be rewritten without changing semantics."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _attribute_value(node: onnx.NodeProto, name: str, default):
    for attribute in node.attribute:
        if attribute.name == name:
            return helper.get_attribute_value(attribute)
    return default


def _initializer_map(graph: onnx.GraphProto) -> dict[str, onnx.TensorProto]:
    return {value.name: value for value in graph.initializer}


def _tensor_shape(value: onnx.ValueInfoProto) -> tuple[int | None, ...] | None:
    tensor = value.type.tensor_type
    if not tensor.HasField("shape"):
        return None
    result: list[int | None] = []
    for dimension in tensor.shape.dim:
        if dimension.HasField("dim_value"):
            result.append(int(dimension.dim_value))
        else:
            result.append(None)
    return tuple(result)


def _value_shapes(model: onnx.ModelProto) -> dict[str, tuple[int | None, ...]]:
    """Infer value shapes, falling back to the source graph's annotations."""

    shapes: dict[str, tuple[int | None, ...]] = {}
    for value in (*model.graph.input, *model.graph.value_info, *model.graph.output):
        shape = _tensor_shape(value)
        if shape is not None:
            shapes[value.name] = shape
    try:
        inferred = shape_inference.infer_shapes(model)
    except Exception:
        inferred = None
    if inferred is not None:
        for value in (*inferred.graph.input, *inferred.graph.value_info, *inferred.graph.output):
            shape = _tensor_shape(value)
            if shape is not None:
                shapes[value.name] = shape
    return shapes


def _new_name(used: set[str], proposed: str) -> str:
    value = proposed
    counter = 1
    while value in used:
        value = f"{proposed}_{counter}"
        counter += 1
    used.add(value)
    return value


def _copy_conv(node: onnx.NodeProto, *, name: str, weight: str, bias: str | None, output: str) -> onnx.NodeProto:
    """Copy a Conv node while replacing only its parameter/output names."""

    result = copy.deepcopy(node)
    result.name = name
    del result.input[:]
    result.input.extend([node.input[0], weight])
    if bias is not None:
        result.input.append(bias)
    del result.output[:]
    result.output.append(output)
    return result


def _shape_initializer(
    graph: onnx.GraphProto,
    used: set[str],
    base: str,
    shape: Sequence[int],
) -> str:
    name = _new_name(used, base)
    graph.initializer.append(
        numpy_helper.from_array(np.asarray(tuple(shape), dtype=np.int64), name=name)
    )
    return name


def _rewrite_one(
    node: onnx.NodeProto,
    graph: onnx.GraphProto,
    initializers: Mapping[str, onnx.TensorProto],
    shapes: Mapping[str, tuple[int | None, ...]],
    used_names: set[str],
) -> tuple[list[onnx.NodeProto], dict[str, object], set[str]]:
    if node.op_type != "Conv":
        raise RewriteError(f"target node is not Conv: {node.name}")
    if len(node.input) not in (2, 3):
        raise RewriteError(f"unsupported Conv input count for {node.name}: {len(node.input)}")
    group = int(_attribute_value(node, "group", 1))
    if group <= 1:
        raise RewriteError(f"target Conv is not grouped: {node.name}")
    weight_proto = initializers.get(node.input[1])
    if weight_proto is None:
        raise RewriteError(f"grouped Conv weight is not an initializer: {node.name}")
    weight = numpy_helper.to_array(weight_proto)
    if weight.ndim != 4:
        raise RewriteError(f"expected rank-4 Conv weights for {node.name}, got {weight.shape}")
    output_channels, input_channels_per_group = int(weight.shape[0]), int(weight.shape[1])
    if output_channels % group:
        raise RewriteError(
            f"output channels {output_channels} are not divisible by group {group}: {node.name}"
        )
    output_multiplier = output_channels // group
    if output_multiplier <= 1:
        raise RewriteError(
            f"grouped Conv has one output per group and does not need channel interleaving: {node.name}"
        )
    if input_channels_per_group * group <= 0:
        raise RewriteError(f"invalid grouped Conv input shape: {node.name}")

    bias_name: str | None = None
    bias: np.ndarray | None = None
    if len(node.input) == 3 and node.input[2]:
        bias_name = node.input[2]
        bias_proto = initializers.get(bias_name)
        if bias_proto is None:
            raise RewriteError(f"grouped Conv bias is not an initializer: {node.name}")
        bias = numpy_helper.to_array(bias_proto)
        if bias.ndim != 1 or len(bias) != output_channels:
            raise RewriteError(f"unexpected grouped Conv bias shape for {node.name}: {bias.shape}")

    source_output = node.output[0]
    output_shape = shapes.get(source_output)
    if output_shape is not None and len(output_shape) != 4:
        raise RewriteError(f"expected rank-4 Conv output for {node.name}: {output_shape}")

    prefix = f"{node.name}/group_split"
    replacement: list[onnx.NodeProto] = []
    branch_outputs: list[str] = []
    added_initializers: list[str] = []

    # Each branch keeps the original group count.  Selecting slot::M from the
    # original [group*M, channels/group, kh, kw] tensor gives one output per
    # group.  Stacking branches on a new axis and flattening (group, slot)
    # restores ONNX's original [g0s0, g0s1, g1s0, ...] channel order.
    for slot in range(output_multiplier):
        branch_weight_name = _new_name(
            used_names, f"{prefix}/weight_{slot}"
        )
        graph.initializer.append(
            numpy_helper.from_array(weight[slot::output_multiplier].copy(), name=branch_weight_name)
        )
        added_initializers.append(branch_weight_name)
        branch_bias_name: str | None = None
        if bias is not None:
            branch_bias_name = _new_name(used_names, f"{prefix}/bias_{slot}")
            graph.initializer.append(
                numpy_helper.from_array(bias[slot::output_multiplier].copy(), name=branch_bias_name)
            )
            added_initializers.append(branch_bias_name)
        branch_output = _new_name(used_names, f"{prefix}/branch_{slot}")
        branch_outputs.append(branch_output)
        replacement.append(
            _copy_conv(
                node,
                name=_new_name(used_names, f"{prefix}/Conv_{slot}"),
                weight=branch_weight_name,
                bias=branch_bias_name,
                output=branch_output,
            )
        )

    axes_name = _shape_initializer(graph, used_names, f"{prefix}/unsqueeze_axes", [2])
    added_initializers.append(axes_name)
    unsqueezed: list[str] = []
    for slot, branch_output in enumerate(branch_outputs):
        value = _new_name(used_names, f"{prefix}/branch_{slot}_5d")
        unsqueezed.append(value)
        replacement.append(
            helper.make_node(
                "Unsqueeze",
                [branch_output, axes_name],
                [value],
                name=_new_name(used_names, f"{prefix}/Unsqueeze_{slot}"),
            )
        )

    stacked = _new_name(used_names, f"{prefix}/stacked")
    replacement.append(
        helper.make_node(
            "Concat",
            unsqueezed,
            [stacked],
            name=_new_name(used_names, f"{prefix}/Concat_slots"),
            axis=2,
        )
    )

    # A static output shape is preferable for ATC when shape inference knows
    # it.  For dynamic graphs [0,-1,0,0] copies N/H/W and infers G*M.
    if output_shape is not None and all(value is not None for value in output_shape):
        reshape_shape = tuple(int(value) for value in output_shape if value is not None)
    else:
        reshape_shape = (0, -1, 0, 0)
    reshape_name = _shape_initializer(
        graph, used_names, f"{prefix}/reshape_shape", reshape_shape
    )
    added_initializers.append(reshape_name)
    replacement.append(
        helper.make_node(
            "Reshape",
            [stacked, reshape_name],
            [source_output],
            name=_new_name(used_names, f"{prefix}/Reshape_channels"),
        )
    )

    record = {
        "source_node": node.name,
        "source_output": source_output,
        "group": group,
        "source_weight_shape": list(weight.shape),
        "output_multiplier": output_multiplier,
        "branch_weight_shape": list(weight[::output_multiplier].shape),
        "interleave_layout": "[N, group, slots, H, W] -> [N, group*slots, H, W]",
        "interleave_formula": "output_channel = group_index * slots + slot",
        "interleave_example": ["g0s0", "g0s1", "g1s0", "g1s1"],
        "replacement_nodes": [value.name for value in replacement],
        "added_initializers": added_initializers,
        "output_shape": list(reshape_shape),
    }
    removable = {node.input[1]}
    if bias_name is not None:
        removable.add(bias_name)
    return replacement, record, removable


def rewrite_model(
    model: onnx.ModelProto,
    *,
    node_names: Iterable[str] = (),
    all_eligible: bool = False,
) -> tuple[onnx.ModelProto, list[dict[str, object]]]:
    """Implementation split out so graph node order is preserved safely."""

    requested = {str(value) for value in node_names if str(value)}
    if not requested and not all_eligible:
        requested = {DEFAULT_NODE}
    source_nodes = list(model.graph.node)
    shapes = _value_shapes(model)
    initializers = _initializer_map(model.graph)
    used_names = {node.name for node in source_nodes if node.name}
    used_names.update(tensor.name for tensor in model.graph.initializer)
    used_names.update(value.name for value in model.graph.input)
    used_names.update(value.name for value in model.graph.output)
    for node in source_nodes:
        used_names.update(value for value in (*node.input, *node.output) if value)
    selected: list[onnx.NodeProto] = []
    def is_eligible(node: onnx.NodeProto) -> bool:
        if node.op_type != "Conv" or int(_attribute_value(node, "group", 1)) <= 1:
            return False
        if len(node.input) < 2:
            return False
        tensor = initializers.get(node.input[1])
        if tensor is None:
            return False
        shape = numpy_helper.to_array(tensor).shape
        group = int(_attribute_value(node, "group", 1))
        return len(shape) == 4 and shape[0] % group == 0 and shape[0] // group > 1

    for node in source_nodes:
        eligible = is_eligible(node)
        if all_eligible:
            if eligible:
                selected.append(node)
        elif node.name in requested:
            selected.append(node)
    if requested and not all_eligible:
        found = {node.name for node in selected}
        missing = sorted(requested - found)
        if missing:
            raise RewriteError(f"requested grouped Conv node(s) not found: {', '.join(missing)}")
    if not selected:
        raise RewriteError("no eligible grouped Conv nodes selected")

    replacements: dict[int, list[onnx.NodeProto]] = {}
    records: list[dict[str, object]] = []
    removable: set[str] = set()
    for node in selected:
        replacement, record, old_initializers = _rewrite_one(
            node, model.graph, initializers, shapes, used_names
        )
        replacements[id(node)] = replacement
        records.append(record)
        removable.update(old_initializers)

    del model.graph.node[:]
    for original in source_nodes:
        if id(original) in replacements:
            model.graph.node.extend(replacements[id(original)])
        else:
            model.graph.node.append(original)

    # Remove only parameter tensors that no remaining node references.  This
    # keeps shared initializers safe while avoiding stale copies in candidates.
    referenced = {
        input_name
        for node in model.graph.node
        for input_name in node.input
        if input_name
    }
    if removable:
        kept = [tensor for tensor in model.graph.initializer if tensor.name not in removable or tensor.name in referenced]
        del model.graph.initializer[:]
        model.graph.initializer.extend(kept)

    # Make the transformation auditable without changing runtime semantics.
    metadata = {prop.key: prop.value for prop in model.metadata_props}
    metadata["case7_group_conv_rewrite"] = "slot_split_interleave_v1"
    metadata["case7_group_conv_rewrite_nodes"] = ",".join(record["source_node"] for record in records)
    del model.metadata_props[:]
    for key in sorted(metadata):
        prop = model.metadata_props.add()
        prop.key = key
        prop.value = metadata[key]

    checker.check_model(model)
    return model, records


def _model_input(model: onnx.ModelProto) -> onnx.ValueInfoProto:
    initializer_names = {value.name for value in model.graph.initializer}
    for value in model.graph.input:
        if value.name not in initializer_names:
            return value
    raise RewriteError("model has no non-initializer input")


def _input_shape(model: onnx.ModelProto) -> tuple[int, ...]:
    value = _model_input(model)
    shape = _tensor_shape(value)
    if shape is None:
        return DEFAULT_INPUT_SHAPE
    result = []
    for index, dimension in enumerate(shape):
        if dimension is None or dimension <= 0:
            result.append(DEFAULT_INPUT_SHAPE[index] if index < len(DEFAULT_INPUT_SHAPE) else 1)
        else:
            result.append(int(dimension))
    return tuple(result)


def _ort_type(model: onnx.ModelProto) -> np.dtype:
    value = _model_input(model)
    elem_type = value.type.tensor_type.elem_type
    if elem_type == onnx.TensorProto.FLOAT16:
        return np.dtype("float16")
    if elem_type == onnx.TensorProto.FLOAT:
        return np.dtype("float32")
    if elem_type == onnx.TensorProto.DOUBLE:
        return np.dtype("float64")
    raise RewriteError(f"unsupported ONNX input dtype for equivalence check: {elem_type}")


def _equivalence_sessions(source_path: Path, target_path: Path):
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RewriteError("equivalence checks require onnxruntime") from exc
    source_model = onnx.load(str(source_path), load_external_data=True)
    target_model = onnx.load(str(target_path), load_external_data=True)
    source_session = ort.InferenceSession(str(source_path), providers=["CPUExecutionProvider"])
    target_session = ort.InferenceSession(str(target_path), providers=["CPUExecutionProvider"])
    input_name = source_session.get_inputs()[0].name
    if input_name != target_session.get_inputs()[0].name:
        raise RewriteError("source and target input names differ")
    return source_model, target_model, source_session, target_session, input_name


def _compare_outputs(
    source_outputs: Sequence[np.ndarray],
    target_outputs: Sequence[np.ndarray],
    *,
    rtol: float,
    atol: float,
) -> list[dict[str, object]]:
    if len(source_outputs) != len(target_outputs):
        raise RewriteError("source and target output counts differ")
    output_checks: list[dict[str, object]] = []
    for index, (left, right) in enumerate(zip(source_outputs, target_outputs)):
        left_array = np.asarray(left)
        right_array = np.asarray(right)
        if left_array.shape != right_array.shape:
            raise RewriteError(
                f"output shape differs for output {index}: {left_array.shape} != {right_array.shape}"
            )
        finite = bool(np.isfinite(left_array).all() and np.isfinite(right_array).all())
        left_float = left_array.astype(np.float64, copy=False).reshape(-1)
        right_float = right_array.astype(np.float64, copy=False).reshape(-1)
        difference = np.abs(left_float - right_float)
        left_norm = float(np.linalg.norm(left_float))
        right_norm = float(np.linalg.norm(right_float))
        cosine = (
            float(np.dot(left_float, right_float) / (left_norm * right_norm))
            if left_norm and right_norm
            else (1.0 if not left_norm and not right_norm else 0.0)
        )
        output_checks.append(
            {
                "index": index,
                "shape": list(left_array.shape),
                "dtype_source": str(left_array.dtype),
                "dtype_target": str(right_array.dtype),
                "finite": finite,
                "max_abs": float(np.max(difference)) if difference.size else 0.0,
                "mean_abs": float(np.mean(difference)) if difference.size else 0.0,
                "cosine": cosine,
                "allclose": bool(np.allclose(left_array, right_array, rtol=rtol, atol=atol)),
            }
        )
    return output_checks


def verify_equivalence(
    source_path: Path,
    target_path: Path,
    *,
    seeds: Sequence[int] = (310, 311),
) -> dict[str, object]:
    """Compare source/rewritten outputs with deterministic random inputs."""

    source_model, _target_model, source_session, target_session, input_name = _equivalence_sessions(
        source_path, target_path
    )
    shape = _input_shape(source_model)
    dtype = _ort_type(source_model)
    checks: list[dict[str, object]] = []
    for seed in seeds:
        rng = np.random.default_rng(int(seed))
        value = rng.standard_normal(shape).astype(dtype)
        source_outputs = source_session.run(None, {input_name: value})
        target_outputs = target_session.run(None, {input_name: value})
        output_checks = _compare_outputs(
            source_outputs, target_outputs, rtol=1e-5, atol=1e-6
        )
        checks.append({"seed": int(seed), "outputs": output_checks})
    passed = all(
        item["allclose"]
        for check in checks
        for item in check["outputs"]
    )
    return {
        "backend": "onnxruntime",
        "input_name": input_name,
        "input_shape": list(shape),
        "input_dtype": str(dtype),
        "seeds": [int(seed) for seed in seeds],
        "checks": checks,
        "passed": bool(passed),
    }


def verify_fixture_directory(
    source_path: Path,
    target_path: Path,
    fixture_dir: Path,
    *,
    input_key: str = "input",
    min_cosine: float = 0.999999,
    max_abs: float = 1e-5,
    rtol: float = 1e-5,
    atol: float = 1e-6,
) -> dict[str, object]:
    """Compare source/target ONNX output for every fixed NPZ fixture.

    Fixtures are intentionally read-only and must each carry an array under
    ``input_key`` (the Case7 numerical reference generator uses ``input``).
    The helper compares the two ONNX graphs, not an OM model, so it provides a
    graph-rewrite equivalence gate before any board-only ATC work begins.
    """

    fixture_dir = Path(fixture_dir).resolve()
    if not fixture_dir.is_dir():
        raise RewriteError(f"fixture directory does not exist: {fixture_dir}")
    fixtures = sorted(path for path in fixture_dir.iterdir() if path.is_file() and path.suffix.lower() == ".npz")
    if not fixtures:
        raise RewriteError(f"fixture directory contains no NPZ files: {fixture_dir}")
    if not -1.0 <= min_cosine <= 1.0:
        raise RewriteError(f"min_cosine must be in [-1, 1], got {min_cosine}")
    if max_abs < 0 or rtol < 0 or atol < 0:
        raise RewriteError("max_abs, rtol, and atol must be non-negative")

    source_model, _target_model, source_session, target_session, input_name = _equivalence_sessions(
        source_path, target_path
    )
    expected_dtype = _ort_type(source_model)
    checks: list[dict[str, object]] = []
    for fixture_path in fixtures:
        try:
            with np.load(fixture_path, allow_pickle=False) as fixture:
                if input_key not in fixture.files:
                    raise RewriteError(
                        f"fixture does not contain {input_key!r}: {fixture_path.name}"
                    )
                value = np.asarray(fixture[input_key])
        except (OSError, ValueError) as exc:
            raise RewriteError(f"failed to read fixture {fixture_path}: {exc}") from exc
        if value.dtype != expected_dtype:
            raise RewriteError(
                f"fixture input dtype differs from source ONNX for {fixture_path.name}: "
                f"{value.dtype} != {expected_dtype}"
            )
        source_outputs = source_session.run(None, {input_name: value})
        target_outputs = target_session.run(None, {input_name: value})
        outputs = _compare_outputs(source_outputs, target_outputs, rtol=rtol, atol=atol)
        passed = all(
            item["finite"]
            and item["allclose"]
            and float(item["cosine"]) >= min_cosine
            and float(item["max_abs"]) <= max_abs
            for item in outputs
        )
        checks.append(
            {
                "fixture": fixture_path.name,
                "fixture_sha256": sha256_file(fixture_path),
                "input_shape": list(value.shape),
                "input_dtype": str(value.dtype),
                "outputs": outputs,
                "passed": passed,
            }
        )

    output_records = [output for check in checks for output in check["outputs"]]
    min_observed_cosine = min(float(item["cosine"]) for item in output_records)
    max_observed_abs = max(float(item["max_abs"]) for item in output_records)
    passed_count = sum(1 for check in checks if check["passed"])
    return {
        "backend": "onnxruntime",
        "fixture_dir": str(fixture_dir),
        "input_key": input_key,
        "input_name": input_name,
        "fixture_count": len(checks),
        "passed_count": passed_count,
        "failed_count": len(checks) - passed_count,
        "thresholds": {
            "min_cosine": min_cosine,
            "max_abs": max_abs,
            "rtol": rtol,
            "atol": atol,
        },
        "min_cosine": min_observed_cosine,
        "max_abs": max_observed_abs,
        "fixtures": checks,
        "passed": passed_count == len(checks),
    }


def rewrite_file(
    source_path: Path,
    target_path: Path,
    *,
    node_names: Iterable[str] = (),
    all_eligible: bool = False,
    report_path: Path | None = None,
    verify_seeds: Sequence[int] = (),
    fixture_dir: Path | None = None,
    fixture_input_key: str = "input",
    fixture_min_cosine: float = 0.999999,
    fixture_max_abs: float = 1e-5,
    fixture_rtol: float = 1e-5,
    fixture_atol: float = 1e-6,
    force: bool = False,
) -> dict[str, object]:
    source_path = source_path.resolve()
    target_path = target_path.resolve()
    if source_path == target_path:
        raise RewriteError("source and target paths must differ")
    if target_path.exists() and not force:
        raise RewriteError(f"refusing to overwrite existing target: {target_path}")
    if not source_path.is_file():
        raise RewriteError(f"missing source ONNX: {source_path}")
    model = onnx.load(str(source_path), load_external_data=True)
    source_node_count = len(model.graph.node)
    rewritten, records = rewrite_model(model, node_names=node_names, all_eligible=all_eligible)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(rewritten, str(target_path))
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "rewritten",
        "source": str(source_path),
        "source_sha256": sha256_file(source_path),
        "target": str(target_path),
        "target_sha256": sha256_file(target_path),
        "source_nodes": source_node_count,
        "target_nodes": len(rewritten.graph.node),
        "node_delta": len(rewritten.graph.node) - source_node_count,
        "replacements": records,
        "checker": "passed",
    }
    equivalence_passed = True
    if verify_seeds:
        verification = verify_equivalence(source_path, target_path, seeds=verify_seeds)
        report["equivalence"] = verification
        equivalence_passed = equivalence_passed and bool(verification["passed"])
    if fixture_dir is not None:
        fixture_verification = verify_fixture_directory(
            source_path,
            target_path,
            fixture_dir,
            input_key=fixture_input_key,
            min_cosine=fixture_min_cosine,
            max_abs=fixture_max_abs,
            rtol=fixture_rtol,
            atol=fixture_atol,
        )
        report["fixture_equivalence"] = fixture_verification
        equivalence_passed = equivalence_passed and bool(fixture_verification["passed"])
    if verify_seeds or fixture_dir is not None:
        report["status"] = "reference_equivalent" if equivalence_passed else "equivalence_failed"
    if report_path is not None:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not equivalence_passed:
        # Persist a failure report first: an ONNX candidate without a retained
        # equivalence record must not be handed to the board ATC pipeline.
        raise RewriteError("rewritten ONNX failed an equivalence check")
    return report


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--input", required=True, help="source ONNX; never modified")
    value.add_argument("--output", required=True, help="isolated rewritten ONNX destination")
    value.add_argument(
        "--node",
        action="append",
        dest="nodes",
        default=[],
        help=f"exact Conv node name (repeatable; default: {DEFAULT_NODE})",
    )
    value.add_argument(
        "--all-eligible",
        action="store_true",
        help="rewrite every grouped Conv with more than one output per group",
    )
    value.add_argument("--report", help="optional JSON provenance report")
    value.add_argument(
        "--verify-seed",
        action="append",
        type=int,
        dest="verify_seeds",
        default=[],
        help="run an ONNX Runtime equivalence check for this deterministic seed (repeatable)",
    )
    value.add_argument(
        "--fixture-dir",
        help=(
            "directory of fixed NPZ fixtures containing an 'input' array; compare every "
            "source/rewritten ONNX output before any board ATC conversion"
        ),
    )
    value.add_argument(
        "--fixture-input-key",
        default="input",
        help="NPZ key used for source model input (default: input)",
    )
    value.add_argument(
        "--fixture-min-cosine",
        type=float,
        default=0.999999,
        help="minimum source/rewritten output cosine per fixture (default: 0.999999)",
    )
    value.add_argument(
        "--fixture-max-abs",
        type=float,
        default=1e-5,
        help="maximum absolute source/rewritten output difference per fixture (default: 1e-5)",
    )
    value.add_argument(
        "--fixture-rtol",
        type=float,
        default=1e-5,
        help="allclose relative tolerance for fixture equivalence (default: 1e-5)",
    )
    value.add_argument(
        "--fixture-atol",
        type=float,
        default=1e-6,
        help="allclose absolute tolerance for fixture equivalence (default: 1e-6)",
    )
    value.add_argument("--force", action="store_true", help="allow replacing the target file")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = rewrite_file(
            Path(args.input),
            Path(args.output),
            node_names=args.nodes,
            all_eligible=args.all_eligible,
            report_path=Path(args.report) if args.report else None,
            verify_seeds=tuple(args.verify_seeds),
            fixture_dir=Path(args.fixture_dir) if args.fixture_dir else None,
            fixture_input_key=args.fixture_input_key,
            fixture_min_cosine=args.fixture_min_cosine,
            fixture_max_abs=args.fixture_max_abs,
            fixture_rtol=args.fixture_rtol,
            fixture_atol=args.fixture_atol,
            force=args.force,
        )
    except (OSError, RewriteError, onnx.onnx_cpp2py_export.checker.ValidationError) as exc:
        print(f"[error] {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
