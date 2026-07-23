from __future__ import annotations

import unittest

import numpy as np

from tools.compare_onnx_om_precision import (
    METRIC_NAMES,
    OUTPUT_SHAPES,
    sequence_report,
    summarize_timing,
)
from tools.summarize_all_om_results import (
    METRICS,
    build_aggregate,
    parse_ais_bench,
    percent_change,
    percent_reduction,
    precision_report_complete,
    render_markdown,
)


class TimingSummaryTest(unittest.TestCase):
    def test_sequence_report_contains_metrics_and_invariants(self) -> None:
        outputs = {
            name: np.zeros((2, *shape), dtype=np.float32)
            for name, shape in OUTPUT_SHAPES.items()
        }
        outputs["amplitude"].fill(1.0)
        outputs["harmonics"].fill(1.0 / OUTPUT_SHAPES["harmonics"][0])

        report = sequence_report(outputs, outputs, elapsed=0.2)

        self.assertEqual(set(report["outputs"]), set(METRIC_NAMES))
        self.assertTrue(report["invariants"]["all_finite"])
        self.assertAlmostEqual(report["average_inference_ms"], 100.0)

    def test_reports_per_inference_distribution(self) -> None:
        report = summarize_timing([1.024, 2.048], steps=1024)

        self.assertEqual(report["repeats"], 2)
        self.assertEqual(report["per_repeat_average_inference_ms"], [1.0, 2.0])
        self.assertAlmostEqual(report["mean_average_inference_ms"], 1.5)
        self.assertAlmostEqual(report["median_average_inference_ms"], 1.5)
        self.assertAlmostEqual(report["p95_average_inference_ms"], 1.95)

    def test_rejects_missing_samples(self) -> None:
        with self.assertRaises(ValueError):
            summarize_timing([], steps=1024)


class AisBenchParserTest(unittest.TestCase):
    def test_extracts_last_performance_summary(self) -> None:
        text = (
            "progress\r\n"
            "NPU_compute_time (ms): min = 0.25, max = 0.35, "
            "mean = 0.27, median = 0.26, percentile(99%) = 0.34\n"
        )

        metrics = parse_ais_bench(text)

        self.assertEqual(
            metrics,
            {
                "min": 0.25,
                "max": 0.35,
                "mean": 0.27,
                "median": 0.26,
                "p99": 0.34,
            },
        )

    def test_rejects_log_without_summary(self) -> None:
        with self.assertRaises(ValueError):
            parse_ais_bench("model loaded")


class AggregateResultsTest(unittest.TestCase):
    def test_precision_report_requires_complete_metric_sections(self) -> None:
        outputs = {
            metric: {
                "normalized_rmse": 0.01,
                "cosine_similarity": 0.999,
                "max_abs": 0.02,
                "p99_abs": 0.01,
            }
            for metric in METRICS
        }
        report = {
            "teacher_forced": {
                "outputs": outputs,
                "invariants": {"all_finite": True},
            },
            "closed_loop": {
                "outputs": outputs,
                "invariants": {"all_finite": True},
            },
            "closed_loop_timing": {
                "repeats": 5,
                "median_average_inference_ms": 0.8,
                "p95_average_inference_ms": 0.9,
            },
        }

        self.assertTrue(precision_report_complete(report, timing_repeats=5))
        report["closed_loop"] = None
        self.assertFalse(precision_report_complete(report, timing_repeats=5))

    def make_row(self, reduction: float, speed_delta: float) -> dict[str, object]:
        row: dict[str, object] = {
            "complete": True,
            "fp16_conversion_ok": True,
            "mixed_conversion_ok": True,
            "npu_median_delta_pct": speed_delta,
            "closed_loop_median_delta_pct": speed_delta / 2.0,
        }
        for metric in METRICS:
            row[f"{metric}_closed_nrmse_reduction_pct"] = reduction
            row[f"{metric}_teacher_nrmse_reduction_pct"] = reduction + 5.0
        return row

    def test_builds_cross_model_medians_and_counts(self) -> None:
        aggregate = build_aggregate(
            [self.make_row(25.0, -2.0), self.make_row(50.0, 4.0)]
        )

        self.assertEqual(aggregate["complete_model_count"], 2)
        self.assertEqual(aggregate["successful_om_count"], 4)
        self.assertEqual(aggregate["npu_median_delta_pct"]["median"], 1.0)
        for metric in METRICS:
            item = aggregate["precision"][metric]
            self.assertEqual(item["reduction_pct"]["median"], 37.5)
            self.assertEqual(item["mixed_better_count"], 2)
            self.assertEqual(item["teacher_reduction_pct"]["median"], 42.5)
            self.assertEqual(item["teacher_mixed_better_count"], 2)

    def test_percentage_helpers_use_expected_signs(self) -> None:
        self.assertAlmostEqual(percent_change(1.1, 1.0), 10.0)
        self.assertAlmostEqual(percent_reduction(0.75, 1.0), 25.0)
        self.assertIsNone(percent_change(1.0, 0.0))

    def test_runtime_report_uses_runtime_title_and_status(self) -> None:
        row = self.make_row(25.0, -2.0)
        row.update(
            {
                "model": "Violin",
                "fp16_npu_median_ms": 0.2,
                "mixed_npu_median_ms": 0.19,
                "fp16_closed_loop_median_ms": 0.8,
                "mixed_closed_loop_median_ms": 0.7,
            }
        )
        for metric in METRICS:
            row[f"fp16_{metric}_closed_nrmse"] = 0.01
            row[f"mixed_{metric}_closed_nrmse"] = 0.005
        aggregate = build_aggregate([row])

        markdown = render_markdown(
            [row], aggregate, "Ascend 20T Runtime Results", runtime_only=True
        )

        self.assertIn("# Ascend 20T Runtime Results", markdown)
        self.assertIn("## OM runtime and speed", markdown)


if __name__ == "__main__":
    unittest.main()
