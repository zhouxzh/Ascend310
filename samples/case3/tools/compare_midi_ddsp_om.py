#!/usr/bin/env python3
"""Compare a MIDI-DDSP OM with saved TensorFlow and ONNX references."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Dict, Mapping, Sequence

import numpy as np


CONTRACTS = {
    "expression": {
        "inputs": {
            "note_pitch": ((1, 32), np.int64),
            "note_length": ((1, 32, 1), np.float32),
            "instrument_id": ((1,), np.int64),
        },
        "outputs": {
            "expression_controls": {
                "shape": (1, 32, 6),
                "tensorflow": "tf__expression_controls",
                "onnx": "onnx__Identity_0",
            },
        },
    },
    "synthesis": {
        "inputs": {
            "volume": ((1, 64, 1), np.float32),
            "vol_fluc": ((1, 64, 1), np.float32),
            "vibrato": ((1, 64, 1), np.float32),
            "brightness": ((1, 64, 1), np.float32),
            "attack": ((1, 64, 1), np.float32),
            "vol_peak_pos": ((1, 64, 1), np.float32),
            "q_pitch": ((1, 64, 1), np.float32),
            "onsets": ((1, 64), np.int64),
            "offsets": ((1, 64), np.int64),
            "instrument_id": ((1,), np.int64),
        },
        "outputs": {
            "f0_hz": {
                "shape": (1, 64, 1),
                "tensorflow": "tf__f0_hz",
                "onnx": "onnx__Identity_0",
            },
            "amplitudes": {
                "shape": (1, 64, 1),
                "tensorflow": "tf__amplitudes",
                "onnx": "onnx__Identity_1_0",
            },
            "harmonic_distribution": {
                "shape": (1, 64, 60),
                "tensorflow": "tf__harmonic_distribution",
                "onnx": "onnx__Identity_2_0",
            },
            "noise_magnitudes": {
                "shape": (1, 64, 65),
                "tensorflow": "tf__noise_magnitudes",
                "onnx": "onnx__Identity_3_0",
            },
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=sorted(CONTRACTS), required=True)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--precision-label", required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--accuracy-runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--loops", type=int, default=100)
    parser.add_argument("--timing-repeats", type=int, default=5)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_metrics(actual: np.ndarray, reference: np.ndarray) -> Dict[str, float | bool]:
    actual64 = np.asarray(actual, dtype=np.float64)
    reference64 = np.asarray(reference, dtype=np.float64)
    if actual64.shape != reference64.shape:
        raise AssertionError(
            f"shape mismatch: actual={actual64.shape}, reference={reference64.shape}"
        )
    finite = bool(np.all(np.isfinite(actual64)) and np.all(np.isfinite(reference64)))
    if not finite:
        return {"all_finite": False}

    difference = actual64 - reference64
    absolute = np.abs(difference)
    rmse = math.sqrt(float(np.mean(difference * difference)))
    reference_rms = math.sqrt(float(np.mean(reference64 * reference64)))
    actual_flat = actual64.reshape(-1)
    reference_flat = reference64.reshape(-1)
    denominator = float(np.linalg.norm(actual_flat) * np.linalg.norm(reference_flat))
    cosine = (
        float(np.dot(actual_flat, reference_flat) / denominator)
        if denominator > 0.0
        else 1.0
    )
    return {
        "all_finite": True,
        "max_abs": float(np.max(absolute)),
        "mean_abs": float(np.mean(absolute)),
        "p99_abs": float(np.quantile(absolute, 0.99)),
        "rmse": rmse,
        "normalized_rmse": rmse / max(reference_rms, 1e-12),
        "cosine_similarity": cosine,
        "reference_min": float(np.min(reference64)),
        "reference_max": float(np.max(reference64)),
        "actual_min": float(np.min(actual64)),
        "actual_max": float(np.max(actual64)),
    }


def aggregate_metric_runs(runs: Sequence[Mapping[str, float | bool]]) -> Dict[str, object]:
    if not runs:
        return {"run_count": 0}
    result: Dict[str, object] = {
        "run_count": len(runs),
        "all_finite": all(bool(item.get("all_finite")) for item in runs),
    }
    numeric_keys = sorted(
        key
        for key in runs[0]
        if key != "all_finite" and isinstance(runs[0][key], (int, float))
    )
    for key in numeric_keys:
        values = np.asarray([float(item[key]) for item in runs], dtype=np.float64)
        result[key] = {
            "mean": float(np.mean(values)),
            "median": float(np.median(values)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
    return result


def descriptor_info(descriptor: object) -> Dict[str, object]:
    shape = getattr(descriptor, "shape", None)
    if shape is not None:
        shape = [int(value) for value in shape]
    dtype = getattr(descriptor, "datatype", getattr(descriptor, "dtype", "unknown"))
    return {
        "name": str(getattr(descriptor, "name", "unknown")),
        "shape": shape,
        "dtype": str(dtype),
    }


def validate_positive_args(args: argparse.Namespace) -> None:
    for name in ("accuracy_runs", "loops", "timing_repeats"):
        if getattr(args, name) < 1:
            raise ValueError(f"--{name.replace('_', '-')} must be at least 1")
    if args.warmup < 0:
        raise ValueError("--warmup must be non-negative")


def load_contract_data(
    reference_path: Path, component: str
) -> tuple[Dict[str, np.ndarray], Dict[str, Dict[str, np.ndarray]]]:
    contract = CONTRACTS[component]
    with np.load(reference_path, allow_pickle=False) as loaded:
        available = set(loaded.files)
        required = {f"input__{name}" for name in contract["inputs"]}
        for details in contract["outputs"].values():
            required.add(details["tensorflow"])
            required.add(details["onnx"])
        missing = sorted(required - available)
        if missing:
            raise KeyError(f"reference archive is missing: {', '.join(missing)}")

        inputs: Dict[str, np.ndarray] = {}
        for name, (shape, dtype) in contract["inputs"].items():
            value = np.ascontiguousarray(loaded[f"input__{name}"], dtype=dtype)
            if value.shape != shape:
                raise AssertionError(
                    f"unexpected input shape for {name}: {value.shape}, expected {shape}"
                )
            inputs[name] = value

        references: Dict[str, Dict[str, np.ndarray]] = {
            "tensorflow": {},
            "onnx": {},
        }
        for name, details in contract["outputs"].items():
            for source in references:
                value = np.asarray(loaded[details[source]], dtype=np.float32)
                if value.shape != details["shape"]:
                    raise AssertionError(
                        f"unexpected {source} shape for {name}: "
                        f"{value.shape}, expected {details['shape']}"
                    )
                references[source][name] = value.copy()
    return inputs, references


def run_inference(
    session: object,
    feeds: Sequence[np.ndarray],
    output_names: Sequence[str],
    output_shapes: Mapping[str, Sequence[int]],
) -> Dict[str, np.ndarray]:
    raw_outputs = session.infer(feeds, mode="static")
    if len(raw_outputs) != len(output_names):
        raise AssertionError(
            f"OM returned {len(raw_outputs)} outputs, expected {len(output_names)}"
        )
    outputs: Dict[str, np.ndarray] = {}
    for name, shape, value in zip(
        output_names, (output_shapes[name] for name in output_names), raw_outputs
    ):
        array = np.asarray(value, dtype=np.float32)
        if array.size != int(np.prod(shape)):
            raise AssertionError(
                f"unexpected OM output size for {name}: {array.shape}, expected {tuple(shape)}"
            )
        array = array.reshape(shape)
        if not np.all(np.isfinite(array)):
            raise AssertionError(f"non-finite OM output: {name}")
        outputs[name] = array.copy()
    return outputs


def summarize_timing(samples_ms: Sequence[float], loops: int) -> Dict[str, object]:
    values = np.asarray(samples_ms, dtype=np.float64)
    if values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("timing samples must be finite and non-empty")
    return {
        "loops_per_repeat": loops,
        "repeat_count": int(values.size),
        "per_repeat_average_ms": values.tolist(),
        "mean_ms": float(np.mean(values)),
        "median_ms": float(np.median(values)),
        "p95_ms": float(np.quantile(values, 0.95)),
        "min_ms": float(np.min(values)),
        "max_ms": float(np.max(values)),
    }


def main() -> int:
    args = parse_args()
    validate_positive_args(args)
    try:
        from ais_bench.infer.interface import InferSession
        import ais_bench
    except ImportError as exc:
        raise RuntimeError("ais_bench is required in the existing board environment") from exc

    om_path = args.om.resolve()
    reference_path = args.reference.resolve()
    if not om_path.is_file():
        raise FileNotFoundError(f"OM model not found: {om_path}")
    if not reference_path.is_file():
        raise FileNotFoundError(f"reference archive not found: {reference_path}")

    contract = CONTRACTS[args.component]
    inputs, references = load_contract_data(reference_path, args.component)
    session = InferSession(args.device, str(om_path))
    input_descriptors = session.get_inputs()
    output_descriptors = session.get_outputs()
    actual_input_names = [str(item.name) for item in input_descriptors]
    expected_input_names = list(contract["inputs"])
    if actual_input_names != expected_input_names:
        raise AssertionError(
            f"unexpected OM input order: {actual_input_names}, expected {expected_input_names}"
        )
    output_names = list(contract["outputs"])
    if len(output_descriptors) != len(output_names):
        raise AssertionError(
            f"unexpected OM output count: {len(output_descriptors)}, expected {len(output_names)}"
        )
    output_shapes = {
        name: tuple(details["shape"])
        for name, details in contract["outputs"].items()
    }
    feeds = [inputs[item.name] for item in input_descriptors]

    accuracy_outputs = [
        run_inference(session, feeds, output_names, output_shapes)
        for _ in range(args.accuracy_runs)
    ]
    accuracy: Dict[str, object] = {}
    for source, source_outputs in references.items():
        source_report: Dict[str, object] = {}
        for name in output_names:
            metrics = [
                array_metrics(run[name], source_outputs[name])
                for run in accuracy_outputs
            ]
            source_report[name] = {
                "runs": metrics,
                "aggregate": aggregate_metric_runs(metrics),
            }
        accuracy[f"against_{source}"] = source_report

    repeatability: Dict[str, object] = {}
    for name in output_names:
        metrics = [
            array_metrics(run[name], accuracy_outputs[0][name])
            for run in accuracy_outputs[1:]
        ]
        repeatability[name] = {
            "comparisons_to_first_run": metrics,
            "aggregate": aggregate_metric_runs(metrics),
        }

    for _ in range(args.warmup):
        run_inference(session, feeds, output_names, output_shapes)
    timing_samples_ms = []
    for _ in range(args.timing_repeats):
        started = time.perf_counter()
        for _ in range(args.loops):
            run_inference(session, feeds, output_names, output_shapes)
        elapsed = time.perf_counter() - started
        timing_samples_ms.append(elapsed * 1000.0 / args.loops)

    report = {
        "component": args.component,
        "precision_mode": args.precision_label,
        "device": args.device,
        "om_model": str(om_path),
        "om_sha256": sha256_file(om_path),
        "om_size_bytes": om_path.stat().st_size,
        "reference": str(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "ais_bench_version": getattr(ais_bench, "__version__", "unknown"),
        "input_descriptors": [descriptor_info(item) for item in input_descriptors],
        "output_descriptors": [descriptor_info(item) for item in output_descriptors],
        "accuracy_runs": args.accuracy_runs,
        "accuracy": accuracy,
        "repeatability": repeatability,
        "all_outputs_finite": all(
            np.all(np.isfinite(run[name]))
            for run in accuracy_outputs
            for name in output_names
        ),
        "end_to_end_timing": summarize_timing(timing_samples_ms, args.loops),
    }

    report_path = args.report.resolve()
    outputs_path = args.outputs.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    outputs_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    np.savez_compressed(
        outputs_path,
        **{
            f"run_{index}__{name}": value
            for index, run in enumerate(accuracy_outputs)
            for name, value in run.items()
        },
    )

    print(f"component={args.component}")
    print(f"precision={args.precision_label}")
    print(f"all_outputs_finite={report['all_outputs_finite']}")
    print(
        "end_to_end_median_ms="
        f"{report['end_to_end_timing']['median_ms']:.6f}"
    )
    for source in ("tensorflow", "onnx"):
        for name in output_names:
            metric = report["accuracy"][f"against_{source}"][name]["aggregate"]
            print(
                f"{source}.{name}.median_nrmse="
                f"{metric['normalized_rmse']['median']:.9g}"
            )
    print(f"report={report_path}")
    print(f"outputs={outputs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
