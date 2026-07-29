#!/usr/bin/env python3
"""Replace single-frame ONNX GRUs with static primitive operations and verify them."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import onnx
from onnx import helper, numpy_helper
import onnxruntime as ort


SOURCE_COMMIT = "1f7cf65ff9c58968bc3b605ee571db928d1ac37a"
CONTROL_NAMES = (
    "amplitudes",
    "harmonic_distribution",
    "inharmonicity",
    "f0_hz",
    "noise_magnitudes",
)
STATE_NAMES = ("next_context_state", "next_monophonic_state")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_source_worktree(path: Path) -> None:
    if not (path / ".git").exists():
        # A linked worktree uses a .git file rather than a directory.
        if not (path / ".git").is_file():
            raise ValueError(f"Not a Git worktree: {path}")
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != SOURCE_COMMIT:
        raise ValueError(f"Source worktree is {head}, expected {SOURCE_COMMIT}")
    if status:
        raise ValueError(f"Source worktree must be clean, found:\n{status}")


def _initializer_map(model: onnx.ModelProto) -> dict[str, np.ndarray]:
    return {
        tensor.name: numpy_helper.to_array(tensor).astype(np.float32, copy=False)
        for tensor in model.graph.initializer
    }


def _unroll_gru(
    node: onnx.NodeProto,
    initializers: dict[str, np.ndarray],
    index: int,
) -> tuple[list[onnx.NodeProto], list[onnx.TensorProto]]:
    attributes = {item.name: helper.get_attribute_value(item) for item in node.attribute}
    hidden = int(attributes.get("hidden_size", 0))
    if hidden <= 0 or int(attributes.get("linear_before_reset", 0)) != 1:
        raise ValueError(f"Unsupported GRU attributes for {node.name}: {attributes}")
    if len(node.input) < 6 or node.input[4] or not node.input[5]:
        raise ValueError(f"GRU must have fixed weights, no sequence lengths, and initial state: {node}")
    weights = initializers[node.input[1]]
    recurrent = initializers[node.input[2]]
    bias = initializers[node.input[3]]
    if weights.shape[0] != 1 or recurrent.shape[0] != 1 or bias.shape != (1, hidden * 6):
        raise ValueError(f"Unexpected GRU parameter shapes for {node.name}")

    prefix = f"gru_unrolled_{index}"
    names: dict[str, str] = {}
    tensors: list[onnx.TensorProto] = []

    def constant(name: str, value: np.ndarray) -> str:
        full_name = f"{prefix}_{name}"
        names[name] = full_name
        tensors.append(numpy_helper.from_array(np.asarray(value), full_name))
        return full_name

    w = weights[0]
    r = recurrent[0]
    b = bias[0]
    w_bias, r_bias = b[: hidden * 3], b[hidden * 3 :]
    gate_slices = {"z": slice(0, hidden), "r": slice(hidden, hidden * 2), "h": slice(hidden * 2, hidden * 3)}
    for gate, selection in gate_slices.items():
        constant(f"w_{gate}", w[selection].T.copy())
        constant(f"r_{gate}", r[selection].T.copy())
    constant("b_z", (w_bias[gate_slices["z"]] + r_bias[gate_slices["z"]]).copy())
    constant("b_r", (w_bias[gate_slices["r"]] + r_bias[gate_slices["r"]]).copy())
    constant("wb_h", w_bias[gate_slices["h"]].copy())
    constant("rb_h", r_bias[gate_slices["h"]].copy())
    axes0 = constant("axes0", np.asarray([0], dtype=np.int64))
    axes1 = constant("axes1", np.asarray([1], dtype=np.int64))
    one = constant("one", np.asarray([1.0], dtype=np.float32))

    def value(name: str) -> str:
        return f"{prefix}_{name}"

    nodes: list[onnx.NodeProto] = [
        helper.make_node("Squeeze", [node.input[0], axes0], [value("x")], name=value("squeeze_x")),
        helper.make_node("Squeeze", [node.input[5], axes0], [value("previous")], name=value("squeeze_h")),
    ]
    for gate in ("z", "r"):
        nodes.extend(
            [
                helper.make_node("MatMul", [value("x"), names[f"w_{gate}"]], [value(f"x_{gate}")], name=value(f"x_{gate}_matmul")),
                helper.make_node("MatMul", [value("previous"), names[f"r_{gate}"]], [value(f"h_{gate}")], name=value(f"h_{gate}_matmul")),
                helper.make_node("Add", [value(f"x_{gate}"), value(f"h_{gate}")], [value(f"sum_{gate}")], name=value(f"sum_{gate}")),
                helper.make_node("Add", [value(f"sum_{gate}"), names[f"b_{gate}"]], [value(f"pre_{gate}")], name=value(f"bias_{gate}")),
                helper.make_node("Sigmoid", [value(f"pre_{gate}")], [value(gate)], name=value(f"sigmoid_{gate}")),
            ]
        )
    nodes.extend(
        [
            helper.make_node("MatMul", [value("x"), names["w_h"]], [value("x_h")], name=value("x_h_matmul")),
            helper.make_node("Add", [value("x_h"), names["wb_h"]], [value("x_h_bias")], name=value("x_h_bias_add")),
            helper.make_node("MatMul", [value("previous"), names["r_h"]], [value("h_h")], name=value("h_h_matmul")),
            helper.make_node("Add", [value("h_h"), names["rb_h"]], [value("h_h_bias")], name=value("h_h_bias_add")),
            helper.make_node("Mul", [value("r"), value("h_h_bias")], [value("reset_h")], name=value("reset_mul")),
            helper.make_node("Add", [value("x_h_bias"), value("reset_h")], [value("candidate_pre")], name=value("candidate_add")),
            helper.make_node("Tanh", [value("candidate_pre")], [value("candidate")], name=value("candidate_tanh")),
            helper.make_node("Sub", [one, value("z")], [value("one_minus_z")], name=value("one_minus_z")),
            helper.make_node("Mul", [value("one_minus_z"), value("candidate")], [value("candidate_mix")], name=value("candidate_mix")),
            helper.make_node("Mul", [value("z"), value("previous")], [value("previous_mix")], name=value("previous_mix")),
            helper.make_node("Add", [value("candidate_mix"), value("previous_mix")], [value("next")], name=value("next_add")),
            helper.make_node("Unsqueeze", [value("next"), axes0], [node.output[1]], name=value("state_unsqueeze")),
            helper.make_node("Unsqueeze", [value("next"), axes0], [value("sequence")], name=value("sequence_unsqueeze")),
            helper.make_node("Unsqueeze", [value("sequence"), axes1], [node.output[0]], name=value("direction_unsqueeze")),
        ]
    )
    return nodes, tensors


def transform(input_path: Path, output_path: Path) -> tuple[Counter[str], Counter[str]]:
    model = onnx.load(str(input_path))
    before = Counter(node.op_type for node in model.graph.node)
    initializers = _initializer_map(model)
    rewritten: list[onnx.NodeProto] = []
    additions: list[onnx.TensorProto] = []
    replaced_parameters: set[str] = set()
    count = 0
    for node in model.graph.node:
        if node.op_type != "GRU":
            rewritten.append(node)
            continue
        nodes, tensors = _unroll_gru(node, initializers, count)
        replaced_parameters.update(node.input[1:4])
        rewritten.extend(nodes)
        additions.extend(tensors)
        count += 1
    if count != 2:
        raise ValueError(f"Expected two GRU nodes, found {count}")
    del model.graph.node[:]
    model.graph.node.extend(rewritten)
    model.graph.initializer.extend(additions)
    used_inputs = {name for item in model.graph.node for name in item.input if name}
    retained = [
        item
        for item in model.graph.initializer
        if item.name not in replaced_parameters or item.name in used_inputs
    ]
    del model.graph.initializer[:]
    model.graph.initializer.extend(retained)
    model.doc_string = (
        f"Piano-DDSP static single-frame GRU expansion from source {SOURCE_COMMIT}."
    )
    onnx.checker.check_model(model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(output_path))
    checked = onnx.load(str(output_path))
    onnx.checker.check_model(checked)
    after = Counter(node.op_type for node in checked.graph.node)
    if after.get("GRU", 0):
        raise RuntimeError("Transformed graph still contains GRU")
    return before, after


def compare(
    original_path: Path,
    candidate_path: Path,
    reference_path: Path,
    frames: int,
) -> tuple[dict[str, dict[str, object]], dict[str, float]]:
    required = ("conditioning", "pedal", "piano_model", "extended_pitch")
    with np.load(reference_path, allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in required}
    if arrays["conditioning"].shape[0] < frames:
        raise ValueError(f"Reference has fewer than {frames} frames")
    sessions = [
        ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        for path in (original_path, candidate_path)
    ]
    output_names = [item.name for item in sessions[0].get_outputs()]
    if output_names != [item.name for item in sessions[1].get_outputs()]:
        raise ValueError("Original and candidate output names differ")
    compared_names = CONTROL_NAMES + STATE_NAMES
    stats = {
        name: {"error_sq": 0.0, "reference_sq": 0.0, "max_abs": 0.0, "finite": True}
        for name in compared_names
    }
    states = [
        {
            "context_state": np.zeros((1, 1, 64), dtype=np.float32),
            "monophonic_state": np.zeros((1, 16, 192), dtype=np.float32),
        }
        for _ in sessions
    ]
    started = time.perf_counter()
    reverb_max_abs = 0.0
    for frame in range(frames):
        outputs: list[dict[str, np.ndarray]] = []
        for session, state in zip(sessions, states):
            inputs = {
                "conditioning": arrays["conditioning"][frame].reshape(1, 1, 16, 2),
                "pedal": arrays["pedal"][frame].reshape(1, 1, 4),
                "piano_model": arrays["piano_model"],
                "extended_pitch": arrays["extended_pitch"][frame].reshape(1, 1, 16, 1),
                **state,
            }
            values = session.run(output_names, inputs)
            result = dict(zip(output_names, values))
            state["context_state"] = result["next_context_state"]
            state["monophonic_state"] = result["next_monophonic_state"]
            outputs.append(result)
        expected, actual = outputs
        if frame == 0:
            reverb_name = "reverb_ir" if "reverb_ir" in output_names else "reverb_controls"
            reverb_max_abs = float(np.max(np.abs(expected[reverb_name] - actual[reverb_name])))
        for name in compared_names:
            expected64 = expected[name].astype(np.float64, copy=False)
            actual64 = actual[name].astype(np.float64, copy=False)
            difference = actual64 - expected64
            current = stats[name]
            current["error_sq"] += float(np.sum(np.square(difference)))
            current["reference_sq"] += float(np.sum(np.square(expected64)))
            current["max_abs"] = max(float(current["max_abs"]), float(np.max(np.abs(difference))))
            current["finite"] = bool(current["finite"]) and bool(np.all(np.isfinite(actual64)))
        if (frame + 1) % 1000 == 0:
            print(f"GRU comparison {frame + 1}/{frames}", flush=True)
    comparisons: dict[str, dict[str, object]] = {}
    for name, current in stats.items():
        score = math.sqrt(float(current["error_sq"]) / max(float(current["reference_sq"]), 1e-24))
        threshold = 1e-5 if name == "f0_hz" else 0.003
        comparisons[name] = {
            "nrmse": score,
            "threshold": threshold,
            "max_abs": float(current["max_abs"]),
            "finite": bool(current["finite"]),
            "passed": bool(current["finite"]) and score <= threshold,
        }
    return comparisons, {
        "seconds": time.perf_counter() - started,
        "reverb_first_frame_max_abs": reverb_max_abs,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-worktree", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--expected-original-sha256", required=True)
    parser.add_argument("--frames", type=int, default=10_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    check_source_worktree(args.source_worktree.resolve())
    original = args.original.resolve()
    if sha256_file(original) != args.expected_original_sha256:
        raise ValueError("Original ONNX does not match the pinned release hash")
    before, after = transform(original, args.output.resolve())
    comparisons, timing = compare(
        original, args.output.resolve(), args.reference.resolve(), args.frames
    )
    passed = all(bool(item["passed"]) for item in comparisons.values()) and timing[
        "reverb_first_frame_max_abs"
    ] <= 1e-6
    report = {
        "schema": "piano-ddsp-gru-unrolled-validation/v1",
        "source_commit": SOURCE_COMMIT,
        "variant": "gru-unrolled",
        "frames": args.frames,
        "original": str(original),
        "original_sha256": sha256_file(original),
        "candidate": str(args.output.resolve()),
        "candidate_sha256": sha256_file(args.output.resolve()),
        "operator_counts_before": dict(sorted(before.items())),
        "operator_counts_after": dict(sorted(after.items())),
        "comparisons": comparisons,
        "timing": timing,
        "passed": passed,
    }
    report_path = args.output.with_suffix(".validation.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    metadata.update(
        {
            "onnx": args.output.name,
            "artifact_name": args.output.stem,
            "export_variant": "gru-unrolled",
            "original_onnx_sha256": args.expected_original_sha256,
            "onnx_sha256": report["candidate_sha256"],
            "gru_unrolled_validation": report_path.name,
            "operator_counts": report["operator_counts_after"],
        }
    )
    metadata_path = args.output.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
