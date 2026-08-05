#!/usr/bin/env python3
"""Build DDSP feature references locally and validate an OM on Ascend."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Mapping, Sequence
import wave

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_NAMES = ("f0_scaled", "pw_scaled", "f0_hz", "pw_db")
OUTPUT_SHAPES = {name: (1,) for name in OUTPUT_NAMES}
CONTROL_INPUTS = ("state", "f0_scaled", "pw_scaled")
CONTROL_OUTPUTS = ("amplitude", "harmonics", "noise_amps", "state_out")
REFERENCE_SAMPLE_RATE = 16_000
WINDOW_SIZE = 1024
HOP_SIZE = 320


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pcm16_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2 or handle.getcomptype() != "NONE":
            raise ValueError("Reference WAV must be uncompressed 16-bit PCM")
        channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    samples = samples.reshape(-1, channels).astype(np.float32) / 32768.0
    return np.mean(samples, axis=1, dtype=np.float32), sample_rate


def _resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.ascontiguousarray(samples, dtype=np.float32)
    output_size = max(1, int(round(samples.size * target_rate / source_rate)))
    source_positions = np.arange(output_size, dtype=np.float64) * source_rate / target_rate
    output = np.interp(source_positions, np.arange(samples.size), samples)
    return np.ascontiguousarray(output, dtype=np.float32)


def generate_frames(audio_path: Path, steps: int, seed: int) -> np.ndarray:
    if steps < 1:
        raise ValueError("steps must be positive")
    samples, source_rate = _read_pcm16_mono(audio_path)
    samples = _resample_linear(samples, source_rate, REFERENCE_SAMPLE_RATE)
    frames: list[np.ndarray] = []
    for start in range(0, max(0, samples.size - WINDOW_SIZE + 1), HOP_SIZE):
        frames.append(samples[start : start + WINDOW_SIZE].copy())
        if len(frames) == steps:
            break

    rng = np.random.default_rng(seed)
    timeline = np.arange(WINDOW_SIZE, dtype=np.float32) / REFERENCE_SAMPLE_RATE
    while len(frames) < steps:
        index = len(frames)
        if index % 41 == 0:
            frame = np.zeros(WINDOW_SIZE, dtype=np.float32)
        else:
            note = 32 + index % 55
            frequency = 440.0 * 2.0 ** ((note - 69) / 12.0)
            amplitude = 0.015 + 0.22 * ((index % 19) / 18.0)
            phase = (index * 0.37) % (2.0 * np.pi)
            frame = amplitude * np.sin(2.0 * np.pi * frequency * timeline + phase)
            frame += 0.22 * amplitude * np.sin(
                4.0 * np.pi * frequency * timeline + phase * 0.5
            )
            frame += rng.normal(0.0, 0.0015, WINDOW_SIZE)
            frame = np.clip(frame, -0.95, 0.95).astype(np.float32)
        frames.append(np.ascontiguousarray(frame, dtype=np.float32))
    return np.stack(frames)


def _validate_onnx_contract(session: object) -> None:
    inputs = {item.name: tuple(item.shape) for item in session.get_inputs()}
    outputs = {item.name: tuple(item.shape) for item in session.get_outputs()}
    if inputs != {"audio": (WINDOW_SIZE,)}:
        raise AssertionError(f"Unexpected ONNX inputs: {inputs}")
    if outputs != OUTPUT_SHAPES:
        raise AssertionError(f"Unexpected ONNX outputs: {outputs}")


def create_reference(args: argparse.Namespace) -> int:
    import onnxruntime as ort

    onnx_path = args.onnx.resolve()
    audio_path = args.audio.resolve()
    if not onnx_path.is_file() or not audio_path.is_file():
        raise FileNotFoundError("ONNX model and reference WAV are required")
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    _validate_onnx_contract(session)
    frames = generate_frames(audio_path, args.steps, args.seed)
    outputs = {name: [] for name in OUTPUT_NAMES}
    started = time.perf_counter()
    for frame in frames:
        values = session.run(list(OUTPUT_NAMES), {"audio": frame})
        for name, value in zip(OUTPUT_NAMES, values):
            array = np.asarray(value, dtype=np.float32).reshape(OUTPUT_SHAPES[name])
            if not np.all(np.isfinite(array)):
                raise AssertionError(f"Non-finite ONNX output: {name}")
            outputs[name].append(array)
    elapsed = time.perf_counter() - started
    metadata = {
        "schema": "ddsp-vst-feature-reference/v1",
        "onnx": str(onnx_path),
        "onnx_sha256": sha256_file(onnx_path),
        "audio": str(audio_path),
        "audio_sha256": sha256_file(audio_path),
        "upstream_revision": args.upstream_revision,
        "steps": args.steps,
        "seed": args.seed,
        "window_size": WINDOW_SIZE,
        "hop_size": HOP_SIZE,
        "sample_rate": REFERENCE_SAMPLE_RATE,
        "onnxruntime_version": ort.__version__,
        "elapsed_seconds": elapsed,
    }
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        metadata_json=np.asarray(json.dumps(metadata, sort_keys=True)),
        audio=frames,
        **{name: np.stack(values) for name, values in outputs.items()},
    )
    print(json.dumps({**metadata, "output": str(output_path)}, indent=2))
    return 0


def _canonical_name(raw: str, expected: Sequence[str]) -> str:
    matches = [name for name in expected if name in raw]
    if len(matches) != 1:
        raise AssertionError(f"Cannot map OM tensor name {raw!r} to {list(expected)}")
    return matches[0]


def _timing(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "mean_ms": float(np.mean(array)),
        "median_ms": float(np.median(array)),
        "p95_ms": float(np.quantile(array, 0.95)),
        "max_ms": float(np.max(array)),
    }


def _metrics(actual: np.ndarray, expected: np.ndarray) -> dict[str, float]:
    difference = np.abs(
        np.asarray(actual, dtype=np.float64) - np.asarray(expected, dtype=np.float64)
    )
    return {
        "max_absolute_error": float(np.max(difference)),
        "mean_absolute_error": float(np.mean(difference)),
        "p99_absolute_error": float(np.quantile(difference, 0.99)),
    }


def _infer_by_name(session: object, inputs: Mapping[str, np.ndarray], expected_outputs: Sequence[str]):
    descriptors = session.get_inputs()
    feeds = [inputs[_canonical_name(item.name, tuple(inputs))] for item in descriptors]
    raw = session.infer(feeds, mode="static")
    output_descriptors = session.get_outputs()
    if len(raw) != len(output_descriptors):
        raise AssertionError("OM output count does not match its descriptor")
    return {
        _canonical_name(item.name, expected_outputs): np.asarray(value, dtype=np.float32)
        for item, value in zip(output_descriptors, raw)
    }


def compare_om(args: argparse.Namespace) -> int:
    from ais_bench.infer.interface import InferSession

    om_path = args.om.resolve()
    control_path = args.control_om.resolve()
    reference_path = args.reference.resolve()
    for path in (om_path, control_path, reference_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    with np.load(reference_path, allow_pickle=False) as archive:
        reference = {name: archive[name].copy() for name in archive.files}
    steps = int(reference["audio"].shape[0])
    if reference["audio"].shape != (steps, WINDOW_SIZE):
        raise AssertionError(f"Unexpected reference audio shape: {reference['audio'].shape}")

    feature = InferSession(args.device, str(om_path))
    control = InferSession(args.device, str(control_path))
    state = np.zeros(512, dtype=np.float32)
    for index in range(min(args.warmup, steps)):
        values = _infer_by_name(feature, {"audio": reference["audio"][index]}, OUTPUT_NAMES)
        control_values = _infer_by_name(
            control,
            {"state": state, "f0_scaled": values["f0_scaled"], "pw_scaled": values["pw_scaled"]},
            CONTROL_OUTPUTS,
        )
        state = control_values["state_out"].reshape(512)

    actual = {name: [] for name in OUTPUT_NAMES}
    feature_times: list[float] = []
    combined_times: list[float] = []
    state.fill(0.0)
    for frame in reference["audio"]:
        combined_start = time.perf_counter()
        feature_start = time.perf_counter()
        values = _infer_by_name(feature, {"audio": frame}, OUTPUT_NAMES)
        feature_times.append((time.perf_counter() - feature_start) * 1000.0)
        control_values = _infer_by_name(
            control,
            {"state": state, "f0_scaled": values["f0_scaled"], "pw_scaled": values["pw_scaled"]},
            CONTROL_OUTPUTS,
        )
        combined_times.append((time.perf_counter() - combined_start) * 1000.0)
        state = control_values["state_out"].reshape(512)
        for name in OUTPUT_NAMES:
            actual[name].append(values[name].reshape(OUTPUT_SHAPES[name]))
    stacked = {name: np.stack(values) for name, values in actual.items()}
    thresholds = {
        "f0_scaled": args.f0_scaled_atol,
        "pw_scaled": args.pw_scaled_atol,
        "f0_hz": args.f0_hz_atol,
        "pw_db": args.pw_db_atol,
    }
    metrics = {
        name: {
            **_metrics(stacked[name], reference[name]),
            "absolute_tolerance": thresholds[name],
        }
        for name in OUTPUT_NAMES
    }
    precision_passed = all(
        metrics[name]["max_absolute_error"] <= thresholds[name] for name in OUTPUT_NAMES
    )
    feature_timing = _timing(feature_times)
    combined_timing = _timing(combined_times)
    latency_passed = combined_timing["p95_ms"] < args.max_combined_p95_ms
    metadata = json.loads(str(reference["metadata_json"].item()))
    report = {
        "schema": "ddsp-vst-feature-om-validation/v1",
        "passed": precision_passed and latency_passed,
        "precision_passed": precision_passed,
        "latency_passed": latency_passed,
        "feature_om": str(om_path),
        "feature_om_sha256": sha256_file(om_path),
        "control_om": str(control_path),
        "control_om_sha256": sha256_file(control_path),
        "reference": str(reference_path),
        "reference_metadata": metadata,
        "steps": steps,
        "device": args.device,
        "metrics": metrics,
        "feature_timing": feature_timing,
        "combined_timing": combined_timing,
        "max_combined_p95_ms": args.max_combined_p95_ms,
        "feature_contract": {
            "inputs": [item.name for item in feature.get_inputs()],
            "outputs": [item.name for item in feature.get_outputs()],
        },
        "control_contract": {
            "inputs": [item.name for item in control.get_inputs()],
            "outputs": [item.name for item in control.get_outputs()],
        },
    }
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    reference = subparsers.add_parser("reference")
    reference.add_argument("--onnx", type=Path, required=True)
    reference.add_argument("--audio", type=Path, required=True)
    reference.add_argument("--output", type=Path, required=True)
    reference.add_argument("--steps", type=int, default=1000)
    reference.add_argument("--seed", type=int, default=20260803)
    reference.add_argument("--upstream-revision", required=True)

    om = subparsers.add_parser("om")
    om.add_argument("--om", type=Path, required=True)
    om.add_argument("--control-om", type=Path, required=True)
    om.add_argument("--reference", type=Path, required=True)
    om.add_argument("--report", type=Path, required=True)
    om.add_argument("--device", type=int, default=0)
    om.add_argument("--warmup", type=int, default=10)
    om.add_argument("--f0-scaled-atol", type=float, default=0.02)
    om.add_argument("--pw-scaled-atol", type=float, default=0.02)
    om.add_argument("--f0-hz-atol", type=float, default=5.0)
    om.add_argument("--pw-db-atol", type=float, default=0.5)
    om.add_argument("--max-combined-p95-ms", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "reference":
        return create_reference(args)
    return compare_om(args)


if __name__ == "__main__":
    raise SystemExit(main())
