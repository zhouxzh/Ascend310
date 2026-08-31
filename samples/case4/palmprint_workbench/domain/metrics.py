"""Dependency-light verification metrics and timing summaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Callable, Mapping

import numpy as np


@dataclass(frozen=True)
class TimingSummary:
    iterations: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    fps: float


def benchmark_call(
    function: Callable[[], object], *, warmup: int = 50, loops: int = 500
) -> TimingSummary:
    for _ in range(warmup):
        function()
    samples = np.empty(loops, dtype=np.float64)
    for index in range(loops):
        started = time.perf_counter_ns()
        function()
        samples[index] = (time.perf_counter_ns() - started) / 1e6
    mean_ms = float(samples.mean())
    return TimingSummary(
        iterations=loops,
        mean_ms=mean_ms,
        p50_ms=float(np.percentile(samples, 50)),
        p95_ms=float(np.percentile(samples, 95)),
        fps=float(1000.0 / mean_ms) if mean_ms > 0 else 0.0,
    )


def _verification_operating_points(
    positive: np.ndarray, negative: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return score-threshold ROC operating points with tied scores grouped."""

    # Grouping tied scores preserves the matcher rule (accept when score >=
    # threshold) and avoids arbitrary stable-sort ordering inside a tie.
    scores = np.concatenate([positive, negative])
    labels = np.concatenate(
        [np.ones(positive.size, dtype=np.int8), np.zeros(negative.size, dtype=np.int8)]
    )
    order = np.argsort(-scores, kind="mergesort")
    sorted_scores = scores[order]
    sorted_labels = labels[order]
    starts = np.r_[0, np.flatnonzero(sorted_scores[1:] != sorted_scores[:-1]) + 1]
    group_sizes = np.diff(np.r_[starts, sorted_scores.size])
    positive_counts = np.add.reduceat(sorted_labels.astype(np.int64), starts)
    negative_counts = group_sizes - positive_counts

    cumulative_positive = np.cumsum(positive_counts)
    cumulative_negative = np.cumsum(negative_counts)
    thresholds = np.r_[np.inf, sorted_scores[starts], -np.inf]
    fars = np.r_[0.0, cumulative_negative / negative.size, 1.0]
    frrs = np.r_[1.0, (positive.size - cumulative_positive) / positive.size, 0.0]
    tprs = 1.0 - frrs
    return (
        thresholds,
        fars,
        frrs,
        tprs,
        positive_counts,
        negative_counts,
        cumulative_negative,
    )


def verification_metrics(genuine: np.ndarray, impostor: np.ndarray) -> dict[str, float]:
    positive = np.asarray(genuine, dtype=np.float64).reshape(-1)
    negative = np.asarray(impostor, dtype=np.float64).reshape(-1)
    if positive.size == 0 or negative.size == 0:
        raise ValueError("Both genuine and impostor scores are required")
    if not np.isfinite(positive).all() or not np.isfinite(negative).all():
        raise ValueError("Verification scores must be finite")

    (
        thresholds,
        fars,
        frrs,
        tprs,
        positive_counts,
        negative_counts,
        cumulative_negative,
    ) = _verification_operating_points(positive, negative)

    # The two endpoint sentinels model reject-all / accept-all, but are not
    # observed score thresholds. Calibration must return a threshold that can
    # be passed unchanged to the matcher, so consider only real score values.
    attainable = np.arange(1, thresholds.size - 1)
    balance_gap = np.abs(fars[attainable] - frrs[attainable])
    candidates = attainable[balance_gap == balance_gap.min()]
    # Equal gaps are possible with finite samples. Prefer the lower empirical
    # error, then retain the first candidate (the higher score threshold) for
    # a deterministic, conservative final tie break.
    empirical_error = (fars[candidates] + frrs[candidates]) / 2.0
    candidates = candidates[empirical_error == empirical_error.min()]
    index = int(candidates[0])
    eer = float((fars[index] + frrs[index]) / 2.0)

    # Standard rank/Mann-Whitney AUC: P(genuine > impostor) + 0.5 * P(tie).
    # This is invariant to repeated FPR values and exact score ties, unlike a
    # row-order-dependent integration over an ungrouped ROC trace.
    lower_negative = negative.size - cumulative_negative
    wins = np.sum(
        positive_counts.astype(np.float64) * lower_negative.astype(np.float64), dtype=np.float64
    )
    ties = np.sum(
        positive_counts.astype(np.float64) * negative_counts.astype(np.float64), dtype=np.float64
    )
    auc = float((wins + 0.5 * ties) / (positive.size * negative.size))
    result = {
        "threshold": float(thresholds[index]),
        "eer": eer,
        "far_at_threshold": float(fars[index]),
        "frr_at_threshold": float(frrs[index]),
        "eer_balance_gap": float(abs(fars[index] - frrs[index])),
        "auc": auc,
    }
    for target in (1e-2, 1e-3):
        valid = np.where(fars <= target)[0]
        result[f"tar_at_far_{target:g}"] = float(tprs[valid].max()) if valid.size else 0.0
    return result


def rank1_decision(identity_scores: Mapping[str, float]) -> tuple[str, tuple[str, ...]]:
    """Choose a reproducible Rank-1 identity and expose exact-score ties.

    The returned winner is the lexicographically first identity among the
    maximum-score candidates. Callers can report the tied candidate tuple
    instead of silently relying on dictionary insertion order.
    """

    if not identity_scores:
        raise ValueError("At least one identity score is required")
    scores = {identity: float(score) for identity, score in identity_scores.items()}
    if not np.isfinite(list(scores.values())).all():
        raise ValueError("Rank-1 scores must be finite")
    best_score = max(scores.values())
    tied = tuple(sorted(identity for identity, score in scores.items() if score == best_score))
    return tied[0], tied


def compare_embeddings(reference: np.ndarray, candidate: np.ndarray) -> dict[str, float]:
    first = np.asarray(reference, dtype=np.float32)
    second = np.asarray(candidate, dtype=np.float32)
    if first.shape != second.shape:
        raise ValueError(f"Embedding shape mismatch: {first.shape} != {second.shape}")
    first_norm = first / np.maximum(np.linalg.norm(first, axis=1, keepdims=True), 1e-12)
    second_norm = second / np.maximum(np.linalg.norm(second, axis=1, keepdims=True), 1e-12)
    cosines = np.sum(first_norm * second_norm, axis=1)
    differences = np.abs(first - second)
    return {
        "mean_cosine": float(cosines.mean()),
        "min_cosine": float(cosines.min()),
        "max_abs_error": float(differences.max()),
        "mean_abs_error": float(differences.mean()),
    }


def timing_as_dict(summary: TimingSummary) -> dict[str, float | int]:
    return asdict(summary)
