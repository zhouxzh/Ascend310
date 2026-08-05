"""Validate a Piano-DDSP OM against a 10,000-frame ONNX reference on Ascend."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from piano_ddsp_runtime.acl_model import PianoAclModel
from piano_ddsp_runtime.bundle import load_bundle, sha256_file


CONTROL_NAMES = (
    "amplitudes",
    "harmonic_distribution",
    "inharmonicity",
    "f0_hz",
    "noise_magnitudes",
)
EXPECTED_MODEL_IDS = {
    "gru_ir_96_64",
    "film_fdn_128_96",
    "gru_ir_fullwet_96_64",
    "film_ir_fullwet_96_64",
}
EXPECTED_RELEASE = "model-suite-v1.0.1"
EXPECTED_SOURCE_COMMIT = "c41911aa7de454aeacf0b3edbb2d06a0801fb3ff"


def nrmse(reference: np.ndarray, actual: np.ndarray) -> float:
    difference = np.asarray(actual, dtype=np.float64) - np.asarray(reference, dtype=np.float64)
    denominator = max(float(np.linalg.norm(reference.astype(np.float64))), 1e-12)
    return float(np.linalg.norm(difference) / denominator)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--model-id", default="gru_ir_96_64")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--frames", type=int, default=10_000)
    parser.add_argument("--activate", action="store_true")
    return parser.parse_args()


def validate_reference_provenance(reference_path: Path, model_id: str) -> str:
    report_path = reference_path.parent / "report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"Reference provenance report is missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reference_hash = sha256_file(reference_path)
    schema = report.get("schema")
    common_matches = (
        report.get("model_id") == model_id
        and report.get("npz") == reference_path.name
        and report.get("npz_sha256") == reference_hash
        and int(report.get("frames", 0)) >= 10_000
    )
    if schema == "piano-ddsp-reference/v1":
        valid = common_matches
    elif schema == "piano-ddsp-onnx-reference/v2":
        inputs_name = report.get("inputs")
        reference_root = reference_path.parent.parent.resolve()
        inputs_path = (reference_root / str(inputs_name)).resolve()
        valid = (
            common_matches
            and report.get("release") == EXPECTED_RELEASE
            and report.get("source_hf_commit") == EXPECTED_SOURCE_COMMIT
            and isinstance(inputs_name, str)
            and reference_root in inputs_path.parents
            and inputs_path.is_file()
            and report.get("inputs_sha256") == sha256_file(inputs_path)
        )
    else:
        valid = False
    if not valid:
        raise ValueError(f"Reference provenance does not match model {model_id!r}")
    return reference_hash


def load_reference_arrays(reference_path: Path, frames_requested: int) -> tuple[int, dict[str, np.ndarray]]:
    report = json.loads((reference_path.parent / "report.json").read_text(encoding="utf-8"))
    schema = report.get("schema")
    output_names = CONTROL_NAMES + ("next_context_state", "next_monophonic_state")
    if schema == "piano-ddsp-onnx-reference/v2":
        inputs_path = reference_path.parent.parent / str(report["inputs"])
        with np.load(inputs_path, allow_pickle=False) as input_archive:
            inputs = {name: input_archive[name] for name in input_archive.files}
        with np.load(reference_path, allow_pickle=False) as output_archive:
            outputs = {name: output_archive[name] for name in output_names}
        reference = {**inputs, **outputs}
    else:
        legacy_names = (
            "conditioning",
            "pedal",
            "piano_model",
            "extended_pitch",
            "context_state_out",
            "monophonic_state_out",
        ) + CONTROL_NAMES
        with np.load(reference_path, allow_pickle=False) as archive:
            missing = set(legacy_names) - set(archive.files)
            if missing:
                raise ValueError(f"Reference is missing arrays: {sorted(missing)}")
            reference = {name: archive[name] for name in legacy_names}
        reference["next_context_state"] = reference.pop("context_state_out")
        reference["next_monophonic_state"] = reference.pop("monophonic_state_out")
    required = {
        "conditioning",
        "pedal",
        "piano_model",
        "extended_pitch",
        *output_names,
    }
    missing = required - set(reference)
    if missing:
        raise ValueError(f"Reference is missing arrays: {sorted(missing)}")
    available_frames = int(reference["conditioning"].shape[0])
    if frames_requested <= 0 or frames_requested > available_frames:
        raise ValueError(
            f"--frames must be between 1 and {available_frames}, received {frames_requested}"
        )
    frames = int(frames_requested)
    return frames, {
        name: value if name == "piano_model" else value[:frames]
        for name, value in reference.items()
    }


def record_bundle_validation(
    bundle: object,
    model_id: str,
    report: dict[str, object],
) -> None:
    manifest_path = bundle.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    models = manifest.get("models")
    if not isinstance(models, dict) or not isinstance(models.get(model_id), dict):
        raise ValueError(f"Bundle manifest has no model entry {model_id!r}")
    validation_path = manifest_path.parent / "validation" / f"{model_id}.json"
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_tmp = validation_path.with_suffix(".json.part")
    validation_tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(validation_tmp, validation_path)
    models[model_id]["validation"] = {
        "path": validation_path.relative_to(manifest_path.parent).as_posix(),
        "sha256": sha256_file(validation_path),
        "frames": report["frames"],
        "passed": report["passed"],
        "om_sha256": report["om_sha256"],
    }
    manifest["complete"] = EXPECTED_MODEL_IDS.issubset(models) and all(
        isinstance(models[name], dict)
        and isinstance(models[name].get("validation"), dict)
        and models[name]["validation"].get("passed") is True
        for name in EXPECTED_MODEL_IDS
    )
    manifest_tmp = manifest_path.with_suffix(".json.part")
    manifest_tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(manifest_tmp, manifest_path)


def activate_bundle(bundle: object) -> None:
    active = bundle.manifest_path.parent.parent.parent / "active-bundle.json"
    pointer = {
        "schema": "piano-ddsp-active-bundle/v1",
        "bundle_id": bundle.id,
        "manifest": f"bundles/{bundle.manifest_path.parent.name}/manifest.json",
    }
    temporary = active.with_suffix(".json.part")
    temporary.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, active)


def main() -> None:
    args = parse_args()
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RuntimeError("OM validation must run on the Ascend 310B board")
    # A legacy or stale qualification record must not prevent re-validating the
    # immutable OM and metadata assets. Runtime discovery keeps strict loading.
    bundle = load_bundle(args.bundle, validate_qualification=False)
    if args.model_id not in bundle.models:
        raise KeyError(args.model_id)
    reference_hash = validate_reference_provenance(args.reference, args.model_id)
    frames, reference = load_reference_arrays(args.reference, args.frames)
    asset = bundle.models[args.model_id]
    context = np.zeros((1, 1, 64), dtype=np.float32)
    monophonic = np.zeros((1, 16, 192), dtype=np.float32)
    names = CONTROL_NAMES + ("next_context_state", "next_monophonic_state")
    accumulators = {
        name: {"error_sq": 0.0, "reference_sq": 0.0, "max_abs": 0.0, "finite": True}
        for name in names
    }
    timings: list[float] = []
    with PianoAclModel(asset.om_path, asset.metadata, args.device_id) as model:
        for frame in range(frames):
            inputs = {
                "conditioning": reference["conditioning"][frame].reshape(1, 1, 16, 2),
                "pedal": reference["pedal"][frame].reshape(1, 1, 4),
                "piano_model": reference["piano_model"],
                "extended_pitch": reference["extended_pitch"][frame].reshape(1, 1, 16, 1),
                "context_state": context,
                "monophonic_state": monophonic,
            }
            started = time.perf_counter()
            outputs = model.infer(inputs)
            timings.append((time.perf_counter() - started) * 1000.0)
            context = outputs["next_context_state"]
            monophonic = outputs["next_monophonic_state"]
            pairs = {
                **{name: (reference[name][frame], outputs[name][0, 0]) for name in CONTROL_NAMES},
                "next_context_state": (reference["next_context_state"][frame], context[0, 0]),
                "next_monophonic_state": (
                    reference["next_monophonic_state"][frame],
                    monophonic[0],
                ),
            }
            for name, (expected, actual) in pairs.items():
                expected64 = np.asarray(expected, dtype=np.float64)
                actual64 = np.asarray(actual, dtype=np.float64)
                difference = actual64 - expected64
                stats = accumulators[name]
                stats["error_sq"] += float(np.sum(np.square(difference)))
                stats["reference_sq"] += float(np.sum(np.square(expected64)))
                stats["max_abs"] = max(float(stats["max_abs"]), float(np.max(np.abs(difference))))
                stats["finite"] = bool(stats["finite"]) and bool(np.all(np.isfinite(actual64)))
            progress_interval = max(1, min(1000, frames // 10))
            if (frame + 1) % progress_interval == 0:
                print(f"OM validation {frame + 1}/{frames}", flush=True)
    comparisons: dict[str, dict[str, object]] = {}
    for name, stats in accumulators.items():
        finite = bool(stats["finite"])
        score = math.sqrt(float(stats["error_sq"]) / max(float(stats["reference_sq"]), 1e-24))
        threshold = 1e-5 if name == "f0_hz" else 0.003
        comparisons[name] = {
            "nrmse": score,
            "threshold": threshold,
            "finite": finite,
            "passed": finite and score <= threshold,
            "max_abs": float(stats["max_abs"]),
        }
    percentiles = np.quantile(np.asarray(timings), [0.5, 0.95, 0.99])
    checks_passed = all(bool(value["passed"]) for value in comparisons.values()) and (
        float(percentiles[2]) < 4.0
    )
    qualified = checks_passed and frames >= 10_000
    report = {
        "schema": "piano-ddsp-om-validation/v1",
        "bundle_id": bundle.id,
        "model_id": args.model_id,
        "frames": frames,
        "qualification_frames": 10_000,
        "reference": args.reference.name,
        "reference_sha256": reference_hash,
        "om_sha256": asset.om_sha256,
        "comparisons": comparisons,
        "timing_ms": {
            "p50": float(percentiles[0]),
            "p95": float(percentiles[1]),
            "p99": float(percentiles[2]),
            "target_p99": 4.0,
        },
        "checks_passed": checks_passed,
        "qualified": qualified,
        "passed": qualified,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    record_bundle_validation(bundle, args.model_id, report)
    if args.activate:
        if not report["passed"]:
            raise RuntimeError("Refusing to activate a failed Piano-DDSP OM validation")
        activate_bundle(bundle)
    print(json.dumps(report, indent=2))
    if not report["checks_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
