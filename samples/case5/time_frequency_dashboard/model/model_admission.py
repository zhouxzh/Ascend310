"""Numerical admission metrics shared by model verification and tests."""

from __future__ import annotations

from typing import Any

import numpy as np


def compare_model_outputs(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    task: str,
    rtol: float = 1.0e-2,
    atol: float = 1.0e-3,
) -> dict[str, Any]:
    if not np.isfinite(rtol) or rtol < 0.0:
        raise ValueError("rtol must be a finite non-negative number")
    if not np.isfinite(atol) or atol < 0.0:
        raise ValueError("atol must be a finite non-negative number")
    reference_source = np.asarray(reference)
    candidate_source = np.asarray(candidate)
    if any(
        not np.issubdtype(values.dtype, np.number)
        or np.issubdtype(values.dtype, np.complexfloating)
        or values.dtype == np.bool_
        for values in (reference_source, candidate_source)
    ):
        raise ValueError("model outputs must contain real float values")
    try:
        expected = np.asarray(reference_source, dtype=np.float32)
        actual = np.asarray(candidate_source, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError("model outputs must contain real float values") from exc
    if expected.shape != actual.shape:
        return {
            "passed": False,
            "reason": "shape_mismatch",
            "reference_shape": list(expected.shape),
            "candidate_shape": list(actual.shape),
        }
    if expected.ndim < 2 or expected.shape[0] <= 0 or expected.size == 0:
        return {
            "passed": False,
            "reason": "invalid_output_shape",
            "reference_shape": list(expected.shape),
            "candidate_shape": list(actual.shape),
        }
    reference_finite = bool(np.all(np.isfinite(expected)))
    candidate_finite = bool(np.all(np.isfinite(actual)))
    if not reference_finite or not candidate_finite:
        return {
            "passed": False,
            "reason": "nonfinite_values",
            "reference_finite": reference_finite,
            "candidate_finite": candidate_finite,
            "rtol": rtol,
            "atol": atol,
        }
    difference = np.abs(actual - expected)
    peak = max(float(np.max(np.abs(expected))), 1.0e-12)
    flat_expected = expected.reshape(expected.shape[0], -1).astype(np.float64)
    flat_actual = actual.reshape(actual.shape[0], -1).astype(np.float64)
    numerator = np.sum(flat_expected * flat_actual, axis=1)
    denominator = np.linalg.norm(flat_expected, axis=1) * np.linalg.norm(flat_actual, axis=1)
    cosine = np.divide(
        numerator,
        denominator,
        out=np.ones_like(numerator),
        where=denominator > 1.0e-12,
    )
    allclose = bool(np.allclose(actual, expected, rtol=rtol, atol=atol))
    metrics: dict[str, Any] = {
        "passed": allclose,
        "reference_finite": reference_finite,
        "candidate_finite": candidate_finite,
        "allclose": allclose,
        "rtol": rtol,
        "atol": atol,
        "max_abs": float(difference.max(initial=0.0)),
        "mean_abs": float(difference.mean()),
        "max_abs_relative_to_reference_peak": float(difference.max(initial=0.0) / peak),
        "mean_cosine_similarity": float(cosine.mean()),
    }
    if task == "iq_classification":
        reference_top1 = np.argmax(expected, axis=1)
        candidate_top1 = np.argmax(actual, axis=1)
        agreement = float(np.mean(reference_top1 == candidate_top1))
        metrics["top1_agreement"] = agreement
        metrics["passed"] = bool(
            metrics["passed"] and metrics["mean_cosine_similarity"] >= 0.999 and agreement >= 0.99
        )
    return metrics
