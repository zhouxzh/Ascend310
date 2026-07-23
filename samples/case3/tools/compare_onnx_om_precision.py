#!/usr/bin/env python3
"""Generate ONNX references and compare them with Ascend OM inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Dict, List, Mapping, Sequence

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ONNX = ROOT_DIR / "models" / "ddsp_vst" / "Violin.onnx"
DEFAULT_OM = ROOT_DIR / "models" / "om" / "Violin.om"
DEFAULT_REFERENCE = ROOT_DIR / "reports" / "Violin_onnx_reference.npz"
DEFAULT_REPORT = ROOT_DIR / "reports" / "Violin_fp16_precision.json"

INPUT_NAMES = ("state", "f0_scaled", "pw_scaled")
OUTPUT_NAMES = ("amplitude", "harmonics", "noise_amps", "state_out")
METRIC_NAMES = (*OUTPUT_NAMES, "harmonic_amplitudes")
OUTPUT_SHAPES = {
    "amplitude": (1,),
    "harmonics": (60,),
    "noise_amps": (65,),
    "state_out": (512,),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    reference = subparsers.add_parser(
        "reference", help="Run ONNX Runtime locally and save deterministic references"
    )
    reference.add_argument("--onnx", type=Path, default=DEFAULT_ONNX)
    reference.add_argument("--output", type=Path, default=DEFAULT_REFERENCE)
    reference.add_argument("--steps", type=int, default=128)
    reference.add_argument("--seed", type=int, default=20260720)

    compare = subparsers.add_parser(
        "om", help="Run an OM model on Ascend and compare it with ONNX references"
    )
    compare.add_argument("--om", type=Path, default=DEFAULT_OM)
    compare.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    compare.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    compare.add_argument("--device", type=int, default=0)
    compare.add_argument(
        "--precision-label",
        default="unspecified",
        help="Human-readable precision mode recorded in the JSON report",
    )
    compare.add_argument(
        "--timing-repeats",
        type=int,
        default=1,
        help="Number of closed-loop timing runs. Default: 1",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_controls(steps: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    if steps < 2:
        raise ValueError("--steps must be at least 2")
    rng = np.random.default_rng(seed)
    f0 = rng.uniform(0.1, 0.9, size=(steps, 1)).astype(np.float32)
    power = rng.uniform(0.1, 0.9, size=(steps, 1)).astype(np.float32)

    anchors = (
        (0.1, 0.1),
        (0.9, 0.9),
        (0.5, 0.1),
        (0.5, 0.9),
        (0.1, 0.9),
        (0.9, 0.1),
    )
    stride = max(1, steps // len(anchors))
    for index, (f0_value, power_value) in enumerate(anchors):
        step = min(index * stride, steps - 1)
        f0[step, 0] = f0_value
        power[step, 0] = power_value
    return f0, power


def validate_onnx_contract(session: object) -> None:
    actual_inputs = {item.name: tuple(item.shape) for item in session.get_inputs()}
    actual_outputs = {item.name: tuple(item.shape) for item in session.get_outputs()}
    expected_inputs = {
        "state": (512,),
        "f0_scaled": (1,),
        "pw_scaled": (1,),
    }
    if actual_inputs != expected_inputs:
        raise AssertionError(f"Unexpected ONNX inputs: {actual_inputs}")
    if actual_outputs != OUTPUT_SHAPES:
        raise AssertionError(f"Unexpected ONNX outputs: {actual_outputs}")


def generate_reference(args: argparse.Namespace) -> int:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "ONNX Runtime is required for the reference phase"
        ) from exc

    onnx_path = args.onnx.resolve()
    if not onnx_path.is_file():
        raise FileNotFoundError(f"ONNX model not found: {onnx_path}")

    session = ort.InferenceSession(
        str(onnx_path), providers=["CPUExecutionProvider"]
    )
    validate_onnx_contract(session)
    f0_values, power_values = generate_controls(args.steps, args.seed)

    state = np.zeros((512,), dtype=np.float32)
    state_inputs: List[np.ndarray] = []
    outputs: Dict[str, List[np.ndarray]] = {name: [] for name in OUTPUT_NAMES}

    started = time.perf_counter()
    for step in range(args.steps):
        state_inputs.append(state.copy())
        values = session.run(
            list(OUTPUT_NAMES),
            {
                "state": state,
                "f0_scaled": f0_values[step],
                "pw_scaled": power_values[step],
            },
        )
        for name, value in zip(OUTPUT_NAMES, values):
            array = np.asarray(value, dtype=np.float32).reshape(OUTPUT_SHAPES[name])
            if not np.all(np.isfinite(array)):
                raise AssertionError(f"Non-finite ONNX output: {name}, step {step}")
            outputs[name].append(array.copy())
        state = outputs["state_out"][-1]
    elapsed = time.perf_counter() - started

    metadata = {
        "onnx_model": str(onnx_path),
        "onnx_sha256": sha256_file(onnx_path),
        "onnxruntime_version": ort.__version__,
        "steps": args.steps,
        "seed": args.seed,
        "providers": session.get_providers(),
        "input_names": list(INPUT_NAMES),
        "output_names": list(OUTPUT_NAMES),
        "elapsed_seconds": elapsed,
    }

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        metadata_json=np.asarray(json.dumps(metadata, ensure_ascii=False)),
        f0_scaled=f0_values,
        pw_scaled=power_values,
        state_inputs=np.stack(state_inputs),
        **{name: np.stack(values) for name, values in outputs.items()},
    )

    print(f"ONNX reference: {output_path}")
    print(f"Steps: {args.steps}")
    print(f"Elapsed: {elapsed:.6f} s")
    print(f"SHA256: {metadata['onnx_sha256']}")
    return 0


def canonical_output_name(name: str) -> str:
    matches = [expected for expected in OUTPUT_NAMES if expected in name]
    if len(matches) != 1:
        raise AssertionError(f"Cannot map OM output name: {name}")
    return matches[0]


def validate_reference(data: Mapping[str, np.ndarray]) -> int:
    required = {
        "metadata_json",
        "f0_scaled",
        "pw_scaled",
        "state_inputs",
        *OUTPUT_NAMES,
    }
    missing = sorted(required - set(data.keys()))
    if missing:
        raise KeyError(f"Reference archive is missing: {', '.join(missing)}")

    steps = int(data["f0_scaled"].shape[0])
    expected_shapes = {
        "f0_scaled": (steps, 1),
        "pw_scaled": (steps, 1),
        "state_inputs": (steps, 512),
        **{name: (steps, *shape) for name, shape in OUTPUT_SHAPES.items()},
    }
    for name, expected in expected_shapes.items():
        actual = tuple(data[name].shape)
        if actual != expected:
            raise AssertionError(
                f"Unexpected reference shape for {name}: {actual}, expected {expected}"
            )
    return steps


def run_om_sequence(
    session: object,
    input_descriptors: Sequence[object],
    output_descriptors: Sequence[object],
    reference: Mapping[str, np.ndarray],
    teacher_forced: bool,
) -> tuple[Dict[str, np.ndarray], float]:
    steps = int(reference["f0_scaled"].shape[0])
    collected: Dict[str, List[np.ndarray]] = {name: [] for name in OUTPUT_NAMES}
    state = np.asarray(reference["state_inputs"][0], dtype=np.float32).copy()

    started = time.perf_counter()
    for step in range(steps):
        if teacher_forced:
            state = np.asarray(reference["state_inputs"][step], dtype=np.float32)
        feed_by_name = {
            "state": state,
            "f0_scaled": np.asarray(reference["f0_scaled"][step], dtype=np.float32),
            "pw_scaled": np.asarray(reference["pw_scaled"][step], dtype=np.float32),
        }
        feeds = [feed_by_name[item.name] for item in input_descriptors]
        raw_outputs = session.infer(feeds, mode="static")
        if len(raw_outputs) != len(output_descriptors):
            raise AssertionError(
                f"OM returned {len(raw_outputs)} outputs, expected {len(output_descriptors)}"
            )

        step_outputs: Dict[str, np.ndarray] = {}
        for descriptor, value in zip(output_descriptors, raw_outputs):
            name = canonical_output_name(descriptor.name)
            array = np.asarray(value, dtype=np.float32).reshape(OUTPUT_SHAPES[name])
            if not np.all(np.isfinite(array)):
                raise AssertionError(f"Non-finite OM output: {name}, step {step}")
            step_outputs[name] = array.copy()
            collected[name].append(array.copy())
        state = step_outputs["state_out"]
    elapsed = time.perf_counter() - started
    return {name: np.stack(values) for name, values in collected.items()}, elapsed


def array_metrics(actual: np.ndarray, reference: np.ndarray) -> Dict[str, object]:
    actual64 = np.asarray(actual, dtype=np.float64)
    reference64 = np.asarray(reference, dtype=np.float64)
    difference = actual64 - reference64
    absolute = np.abs(difference)
    flattened_actual = actual64.reshape(-1)
    flattened_reference = reference64.reshape(-1)
    flattened_absolute = absolute.reshape(-1)

    reference_rms = math.sqrt(float(np.mean(reference64 * reference64)))
    rmse = math.sqrt(float(np.mean(difference * difference)))
    relative_floor = max(float(np.max(np.abs(reference64))) * 1e-3, 1e-7)
    relative = absolute / np.maximum(np.abs(reference64), relative_floor)
    denominator = float(
        np.linalg.norm(flattened_actual) * np.linalg.norm(flattened_reference)
    )
    cosine = (
        float(np.dot(flattened_actual, flattened_reference) / denominator)
        if denominator > 0.0
        else 1.0
    )
    width = int(np.prod(reference64.shape[1:])) if reference64.ndim > 1 else 1
    worst_flat_index = int(np.argmax(flattened_absolute))
    per_step_max = absolute.reshape(reference64.shape[0], -1).max(axis=1)

    return {
        "max_abs": float(flattened_absolute[worst_flat_index]),
        "mean_abs": float(np.mean(absolute)),
        "p99_abs": float(np.quantile(absolute, 0.99)),
        "rmse": rmse,
        "normalized_rmse": rmse / max(reference_rms, 1e-12),
        "max_relative": float(np.max(relative)),
        "mean_relative": float(np.mean(relative)),
        "cosine_similarity": cosine,
        "worst_step": worst_flat_index // width,
        "final_step_max_abs": float(per_step_max[-1]),
        "reference_min": float(np.min(reference64)),
        "reference_max": float(np.max(reference64)),
        "actual_min": float(np.min(actual64)),
        "actual_max": float(np.max(actual64)),
    }


def sequence_report(
    actual: Mapping[str, np.ndarray],
    reference: Mapping[str, np.ndarray],
    elapsed: float,
) -> Dict[str, object]:
    actual_metrics = dict(actual)
    reference_metrics = {name: reference[name] for name in OUTPUT_NAMES}
    actual_metrics["harmonic_amplitudes"] = (
        actual["amplitude"] * actual["harmonics"]
    )
    reference_metrics["harmonic_amplitudes"] = (
        reference["amplitude"] * reference["harmonics"]
    )
    metrics = {
        name: array_metrics(actual_metrics[name], reference_metrics[name])
        for name in METRIC_NAMES
    }
    harmonic_sums = np.sum(actual["harmonics"], axis=1)
    return {
        "elapsed_seconds": elapsed,
        "average_inference_ms": elapsed / actual["amplitude"].shape[0] * 1000.0,
        "outputs": metrics,
        "invariants": {
            "all_finite": all(np.all(np.isfinite(actual[name])) for name in OUTPUT_NAMES),
            "amplitude_min": float(np.min(actual["amplitude"])),
            "harmonics_min": float(np.min(actual["harmonics"])),
            "noise_amps_min": float(np.min(actual["noise_amps"])),
            "harmonics_sum_max_error": float(
                np.max(np.abs(harmonic_sums - 1.0))
            ),
        },
    }


def summarize_timing(elapsed_seconds: Sequence[float], steps: int) -> Dict[str, object]:
    if steps < 1:
        raise ValueError("steps must be positive")
    if not elapsed_seconds:
        raise ValueError("at least one timing sample is required")
    average_ms = np.asarray(elapsed_seconds, dtype=np.float64) * 1000.0 / steps
    if not np.all(np.isfinite(average_ms)) or np.any(average_ms < 0.0):
        raise ValueError("timing samples must be finite and non-negative")
    return {
        "repeats": int(average_ms.size),
        "per_repeat_average_inference_ms": average_ms.tolist(),
        "mean_average_inference_ms": float(np.mean(average_ms)),
        "median_average_inference_ms": float(np.median(average_ms)),
        "p95_average_inference_ms": float(np.quantile(average_ms, 0.95)),
        "min_average_inference_ms": float(np.min(average_ms)),
        "max_average_inference_ms": float(np.max(average_ms)),
    }


def print_metrics(mode: str, report: Mapping[str, object]) -> None:
    print(f"[{mode}]")
    print(
        "output          max_abs       mean_abs      nrmse         "
        "cosine        worst_step  final_max_abs"
    )
    outputs = report["outputs"]
    for name in METRIC_NAMES:
        metrics = outputs[name]
        print(
            f"{name:<15} "
            f"{metrics['max_abs']:<13.6g} "
            f"{metrics['mean_abs']:<13.6g} "
            f"{metrics['normalized_rmse']:<13.6g} "
            f"{metrics['cosine_similarity']:<13.9f} "
            f"{metrics['worst_step']:<11d} "
            f"{metrics['final_step_max_abs']:.6g}"
        )
    invariants = report["invariants"]
    print(
        "harmonics_sum_max_error="
        f"{invariants['harmonics_sum_max_error']:.6g}, "
        f"average_inference_ms={report['average_inference_ms']:.6f}"
    )


def compare_om(args: argparse.Namespace) -> int:
    try:
        from ais_bench.infer.interface import InferSession
    except ImportError as exc:
        raise RuntimeError(
            "ais_bench is required for the OM comparison phase"
        ) from exc

    om_path = args.om.resolve()
    reference_path = args.reference.resolve()
    if not om_path.is_file():
        raise FileNotFoundError(f"OM model not found: {om_path}")
    if not reference_path.is_file():
        raise FileNotFoundError(f"Reference archive not found: {reference_path}")
    if args.timing_repeats < 1:
        raise ValueError("--timing-repeats must be at least 1")

    with np.load(reference_path, allow_pickle=False) as loaded:
        reference = {name: loaded[name].copy() for name in loaded.files}
    steps = validate_reference(reference)
    reference_metadata = json.loads(str(reference["metadata_json"].item()))

    session = InferSession(args.device, str(om_path))
    input_descriptors = session.get_inputs()
    output_descriptors = session.get_outputs()
    input_names = [item.name for item in input_descriptors]
    if input_names != list(INPUT_NAMES):
        raise AssertionError(f"Unexpected OM input order: {input_names}")
    mapped_outputs = [canonical_output_name(item.name) for item in output_descriptors]
    if mapped_outputs != list(OUTPUT_NAMES):
        raise AssertionError(f"Unexpected OM output order: {mapped_outputs}")

    teacher_outputs, teacher_elapsed = run_om_sequence(
        session,
        input_descriptors,
        output_descriptors,
        reference,
        teacher_forced=True,
    )
    closed_outputs = None
    closed_elapsed_samples: List[float] = []
    for _ in range(args.timing_repeats):
        outputs, elapsed = run_om_sequence(
            session,
            input_descriptors,
            output_descriptors,
            reference,
            teacher_forced=False,
        )
        if closed_outputs is None:
            closed_outputs = outputs
        closed_elapsed_samples.append(elapsed)
    assert closed_outputs is not None
    closed_elapsed = closed_elapsed_samples[0]

    report = {
        "om_model": str(om_path),
        "om_sha256": sha256_file(om_path),
        "reference": str(reference_path),
        "reference_metadata": reference_metadata,
        "device": args.device,
        "steps": steps,
        "precision_mode": args.precision_label,
        "teacher_forced": sequence_report(teacher_outputs, reference, teacher_elapsed),
        "closed_loop": sequence_report(closed_outputs, reference, closed_elapsed),
        "closed_loop_timing": summarize_timing(closed_elapsed_samples, steps),
    }

    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"OM model: {om_path}")
    print(f"Reference: {reference_path}")
    print(f"Steps: {steps}")
    print_metrics("teacher_forced", report["teacher_forced"])
    print_metrics("closed_loop", report["closed_loop"])
    timing = report["closed_loop_timing"]
    print(
        "closed_loop_timing: "
        f"repeats={timing['repeats']}, "
        f"median_ms={timing['median_average_inference_ms']:.6f}, "
        f"p95_ms={timing['p95_average_inference_ms']:.6f}"
    )
    print(f"Report: {report_path}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "reference":
        return generate_reference(args)
    if args.command == "om":
        return compare_om(args)
    raise AssertionError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
