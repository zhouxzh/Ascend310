"""Diagnose numerical differences between an ONNX model and its board OM model."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort

from ..npu import AscendOmRunner
from .inference_manifest import load_inference_manifest
from .inference_manifest import verify_artifact_hashes
from .safe_json import write_new_json
from .verify_inference_model import deterministic_model_input


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize_difference(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    rtol: float,
    atol: float,
    limit: int,
) -> dict[str, object]:
    if isinstance(rtol, bool) or not np.isfinite(rtol) or rtol < 0.0:
        raise ValueError("rtol must be a finite non-negative number")
    if isinstance(atol, bool) or not np.isfinite(atol) or atol < 0.0:
        raise ValueError("atol must be a finite non-negative number")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be positive")
    expected = np.asarray(reference, dtype=np.float32)
    actual = np.asarray(candidate, dtype=np.float32)
    if expected.shape != actual.shape:
        return {"shape_mismatch": [list(expected.shape), list(actual.shape)]}
    reference_finite = bool(np.all(np.isfinite(expected)))
    candidate_finite = bool(np.all(np.isfinite(actual)))
    if not reference_finite or not candidate_finite:
        return {
            "shape": list(expected.shape),
            "finite": False,
            "reference_finite": reference_finite,
            "om_finite": candidate_finite,
            "reason": "nonfinite_values",
        }
    difference = np.abs(actual - expected)
    allowed = atol + rtol * np.abs(expected)
    violation = difference > allowed
    flat_order = np.argsort(difference.reshape(-1))[-limit:][::-1]
    worst: list[dict[str, object]] = []
    for flat_index in flat_order:
        coordinate = tuple(int(value) for value in np.unravel_index(flat_index, expected.shape))
        worst.append(
            {
                "index": list(coordinate),
                "reference": float(expected[coordinate]),
                "om": float(actual[coordinate]),
                "absolute_error": float(difference[coordinate]),
                "allowed_error": float(allowed[coordinate]),
                "violates_tolerance": bool(violation[coordinate]),
            }
        )
    result: dict[str, object] = {
        "shape": list(expected.shape),
        "finite": True,
        "reference_finite": True,
        "om_finite": True,
        "max_absolute_error": float(difference.max(initial=0.0)),
        "mean_absolute_error": float(difference.mean()),
        "violating_values": int(np.count_nonzero(violation)),
        "total_values": int(difference.size),
        "worst_values": worst,
    }
    if expected.ndim >= 3:
        reduction_axes = tuple(axis for axis in range(expected.ndim) if axis != 1)
        channel_max = difference.max(axis=reduction_axes)
        channel_mean = difference.mean(axis=reduction_axes)
        channel_bad = violation.sum(axis=reduction_axes)
        channel_order = np.argsort(channel_max)[::-1]
        result["channels"] = [
            {
                "channel": int(channel),
                "max_absolute_error": float(channel_max[channel]),
                "mean_absolute_error": float(channel_mean[channel]),
                "violating_values": int(channel_bad[channel]),
            }
            for channel in channel_order[: min(limit, channel_order.size)]
        ]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input-npy", type=Path)
    parser.add_argument("--rtol", type=float, default=1.0e-2)
    parser.add_argument("--atol", type=float, default=1.0e-3)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if isinstance(args.limit, bool) or args.limit <= 0:
        raise ValueError("limit must be positive")
    manifest = load_inference_manifest(args.manifest, require_accepted=False)
    verify_artifact_hashes(manifest)
    if args.report is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = Path("data/model_admission") / manifest.model_id / f"diagnosis_{stamp}.json"
    else:
        report_path = args.report.resolve()
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing diagnosis report: {report_path}")
    if args.input_npy is None:
        source = deterministic_model_input(manifest)
        input_provenance: dict[str, object] = {"kind": "deterministic", "seed": 310_005}
    else:
        try:
            source = np.ascontiguousarray(np.load(args.input_npy, allow_pickle=False), dtype=np.float32)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"unable to load --input-npy: {args.input_npy}") from exc
        input_provenance = {"kind": "npy", "sha256": sha256(args.input_npy)}
    if tuple(source.shape) != manifest.input_shape:
        raise ValueError("input shape does not match manifest")
    if not np.all(np.isfinite(source)):
        raise ValueError("input contains NaN or Inf")
    ort_session = ort.InferenceSession(str(manifest.onnx_path), providers=["CPUExecutionProvider"])
    expected_outputs = ort_session.run(list(manifest.output_names), {manifest.input_name: source})
    runner = AscendOmRunner(manifest.om_path)
    initialized = runner.initialize()
    if not initialized.ready:
        raise RuntimeError(initialized.message)
    try:
        actual_outputs = runner.run_all(source)
    finally:
        runner.close()
    if len(expected_outputs) != len(actual_outputs):
        raise RuntimeError("ONNX Runtime and OM returned different output counts")
    report = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": manifest.model_id,
        "backend": initialized.backend,
        "input_provenance": input_provenance,
        "outputs": [
            summarize_difference(
                expected,
                actual,
                rtol=args.rtol,
                atol=args.atol,
                limit=args.limit,
            )
            for expected, actual in zip(expected_outputs, actual_outputs)
        ],
    }
    report_path = write_new_json(report_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
