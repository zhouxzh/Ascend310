from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT_DIR / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = load_module("compare_midi_ddsp_om", "tools/compare_midi_ddsp_om.py")
summarize = load_module(
    "summarize_midi_ddsp_benchmark",
    "tools/summarize_midi_ddsp_benchmark.py",
)


def test_array_metrics_exact_and_scaled() -> None:
    reference = np.asarray([1.0, 2.0], dtype=np.float32)
    exact = compare.array_metrics(reference, reference)
    assert exact["normalized_rmse"] == 0.0
    assert exact["cosine_similarity"] == pytest.approx(1.0)

    scaled = compare.array_metrics(reference * 2.0, reference)
    assert scaled["normalized_rmse"] == pytest.approx(1.0)
    assert scaled["cosine_similarity"] == pytest.approx(1.0)


def test_aggregate_metric_runs_uses_median() -> None:
    aggregate = compare.aggregate_metric_runs(
        [
            {"all_finite": True, "normalized_rmse": 0.1},
            {"all_finite": True, "normalized_rmse": 0.3},
            {"all_finite": True, "normalized_rmse": 0.2},
        ]
    )
    assert aggregate["run_count"] == 3
    assert aggregate["all_finite"] is True
    assert aggregate["normalized_rmse"]["median"] == pytest.approx(0.2)


def test_parse_ais_bench_summary(tmp_path: Path) -> None:
    log = tmp_path / "ais.log"
    log.write_text(
        "NPU_compute_time (ms): min = 1.0, max = 2.0, mean = 1.4, "
        "median = 1.3, percentile(99%) = 1.9\n",
        encoding="utf-8",
    )
    parsed = summarize.parse_ais_bench(log)
    assert parsed == {
        "min": 1.0,
        "max": 2.0,
        "mean": 1.4,
        "median": 1.3,
        "p99": 1.9,
    }


def test_speed_delta_positive_means_mixed_is_slower() -> None:
    assert summarize.delta_pct(1.1, 1.0) == pytest.approx(10.0)
