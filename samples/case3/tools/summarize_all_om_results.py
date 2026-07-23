#!/usr/bin/env python3
"""Summarize all-model Ascend OM conversion, precision, and speed results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Dict, Iterable, Mapping, Sequence

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MODELS_DIR = ROOT_DIR / "models" / "ddsp_vst"
DEFAULT_OM_DIR = ROOT_DIR / "models" / "om" / "ascend8t" / "all_models"
DEFAULT_REPORT_DIR = ROOT_DIR / "reports" / "ascend8t" / "all_models"

MODES = {
    "fp16": "force_fp16",
    "mixed": "mixed_float16",
}
METRICS = (
    "amplitude",
    "harmonic_amplitudes",
    "noise_amps",
    "state_out",
)
AIS_BENCH_PATTERN = re.compile(
    r"NPU_compute_time \(ms\):\s*"
    r"min\s*=\s*(?P<min>[0-9.eE+-]+),\s*"
    r"max\s*=\s*(?P<max>[0-9.eE+-]+),\s*"
    r"mean\s*=\s*(?P<mean>[0-9.eE+-]+),\s*"
    r"median\s*=\s*(?P<median>[0-9.eE+-]+),\s*"
    r"percentile\(99%\)\s*=\s*(?P<p99>[0-9.eE+-]+)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--om-dir", type=Path, default=DEFAULT_OM_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--output-prefix", type=Path)
    parser.add_argument("--steps", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--timing-repeats", type=int, default=5)
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help=(
            "Treat a non-empty OM as available without requiring a local ATC "
            "summary. Use this when benchmarking prebuilt models on another board."
        ),
    )
    parser.add_argument(
        "--title",
        default="Ascend 8T All-model FP16 vs Mixed-precision Results",
        help="Markdown report title.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_atc_summary(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key.upper() == key and " " not in key:
            values[key] = value
    return values


def parse_ais_bench(text: str) -> Dict[str, float]:
    matches = list(AIS_BENCH_PATTERN.finditer(text.replace("\r", "\n")))
    if not matches:
        raise ValueError("ais_bench performance summary not found")
    values = {key: float(value) for key, value in matches[-1].groupdict().items()}
    if not all(math.isfinite(value) and value >= 0.0 for value in values.values()):
        raise ValueError("ais_bench metrics must be finite and non-negative")
    return values


def percent_change(new: object, baseline: object) -> float | None:
    if not isinstance(new, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    if not math.isfinite(float(new)) or not math.isfinite(float(baseline)):
        return None
    if float(baseline) == 0.0:
        return None
    return (float(new) / float(baseline) - 1.0) * 100.0


def percent_reduction(new: object, baseline: object) -> float | None:
    change = percent_change(new, baseline)
    return -change if change is not None else None


def all_numbers_finite(value: object) -> bool:
    if isinstance(value, Mapping):
        return all(all_numbers_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(all_numbers_finite(item) for item in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value))
    return True


def load_reference_metadata(path: Path) -> Dict[str, object]:
    with np.load(path, allow_pickle=False) as data:
        return json.loads(str(data["metadata_json"].item()))


def get_nested(data: Mapping[str, object], *keys: str) -> object | None:
    current: object = data
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def precision_report_complete(
    data: Mapping[str, object], timing_repeats: int
) -> bool:
    timing = data.get("closed_loop_timing")
    if not isinstance(timing, Mapping):
        return False
    if timing.get("repeats") != timing_repeats:
        return False
    for key in (
        "median_average_inference_ms",
        "p95_average_inference_ms",
    ):
        if not isinstance(timing.get(key), (int, float)):
            return False
    for section_name in ("teacher_forced", "closed_loop"):
        section = data.get(section_name)
        if not isinstance(section, Mapping):
            return False
        outputs = section.get("outputs")
        invariants = section.get("invariants")
        if not isinstance(outputs, Mapping) or not isinstance(invariants, Mapping):
            return False
        if invariants.get("all_finite") is not True:
            return False
        for metric in METRICS:
            values = outputs.get(metric)
            if not isinstance(values, Mapping):
                return False
            for key in (
                "normalized_rmse",
                "cosine_similarity",
                "max_abs",
                "p99_abs",
            ):
                if not isinstance(values.get(key), (int, float)):
                    return False
    return all_numbers_finite(data)


def load_model_row(
    model_path: Path,
    om_dir: Path,
    report_dir: Path,
    steps: int,
    seed: int,
    timing_repeats: int,
    runtime_only: bool = False,
) -> Dict[str, object]:
    model = model_path.stem
    onnx_sha256 = sha256_file(model_path)
    reference_path = report_dir / "references" / f"{model}_onnx_reference_{steps}.npz"
    row: Dict[str, object] = {
        "model": model,
        "onnx_sha256": onnx_sha256,
        "reference_valid": False,
        "reference_steps": None,
        "reference_seed": None,
    }
    if reference_path.is_file():
        try:
            metadata = load_reference_metadata(reference_path)
            row["reference_steps"] = metadata.get("steps")
            row["reference_seed"] = metadata.get("seed")
            row["reference_valid"] = (
                metadata.get("onnx_sha256") == onnx_sha256
                and metadata.get("steps") == steps
                and metadata.get("seed") == seed
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            pass

    for prefix, tag in MODES.items():
        om_path = om_dir / f"{model}_{tag}.om"
        summary_path = om_dir / f"{model}_{tag}.atc.summary.txt"
        precision_path = (
            report_dir / "precision" / f"{model}_{tag}_precision_{steps}.json"
        )
        benchmark_path = report_dir / "benchmarks" / f"{model}_{tag}.ais_bench.log"
        summary = parse_atc_summary(summary_path)
        expected_v2 = "mixed_float16" if prefix == "mixed" else "ATC default"
        om_available = om_path.is_file() and om_path.stat().st_size > 0
        conversion_ok = om_available and (
            runtime_only
            or (
                summary.get("ATC_EXIT_CODE") == "0"
                and summary.get("OM_UPDATED") == "yes"
                and summary.get("SOC_VERSION") == "Ascend310B4"
                and summary.get("PRECISION_MODE_V2") == expected_v2
                and summary.get("OPERATOR_COMPATIBILITY")
                == "no incompatibility pattern found"
                and summary.get("ERROR_LINES") == "none"
            )
        )
        row[f"{prefix}_conversion_ok"] = conversion_ok
        row[f"{prefix}_atc_exit_code"] = summary.get("ATC_EXIT_CODE")
        row[f"{prefix}_operator_compatibility"] = summary.get(
            "OPERATOR_COMPATIBILITY"
        )
        row[f"{prefix}_om_bytes"] = om_path.stat().st_size if om_path.is_file() else None
        row[f"{prefix}_om_sha256"] = sha256_file(om_path) if om_path.is_file() else None

        precision: Dict[str, object] = {}
        if precision_path.is_file():
            try:
                precision = json.loads(precision_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                precision = {}
        metadata = precision.get("reference_metadata", {})
        precision_ok = (
            conversion_ok
            and bool(precision)
            and precision_report_complete(precision, timing_repeats)
            and precision.get("om_sha256") == row[f"{prefix}_om_sha256"]
            and isinstance(metadata, Mapping)
            and metadata.get("onnx_sha256") == onnx_sha256
            and precision.get("steps") == steps
        )
        row[f"{prefix}_precision_report_ok"] = precision_ok
        row[f"{prefix}_all_finite"] = bool(precision) and all_numbers_finite(precision)

        timing = precision.get("closed_loop_timing", {})
        row[f"{prefix}_closed_loop_median_ms"] = get_nested(
            timing, "median_average_inference_ms"
        )
        row[f"{prefix}_closed_loop_p95_ms"] = get_nested(
            timing, "p95_average_inference_ms"
        )
        row[f"{prefix}_closed_loop_repeats"] = get_nested(timing, "repeats")
        for metric in METRICS:
            row[f"{prefix}_{metric}_teacher_nrmse"] = get_nested(
                precision, "teacher_forced", "outputs", metric, "normalized_rmse"
            )
            row[f"{prefix}_{metric}_closed_nrmse"] = get_nested(
                precision, "closed_loop", "outputs", metric, "normalized_rmse"
            )
            row[f"{prefix}_{metric}_closed_cosine"] = get_nested(
                precision, "closed_loop", "outputs", metric, "cosine_similarity"
            )
            row[f"{prefix}_{metric}_closed_max_abs"] = get_nested(
                precision, "closed_loop", "outputs", metric, "max_abs"
            )
            row[f"{prefix}_{metric}_closed_p99_abs"] = get_nested(
                precision, "closed_loop", "outputs", metric, "p99_abs"
            )

        benchmark: Dict[str, float] = {}
        if benchmark_path.is_file():
            try:
                benchmark = parse_ais_bench(
                    benchmark_path.read_text(encoding="utf-8", errors="replace")
                )
            except (OSError, ValueError):
                benchmark = {}
        row[f"{prefix}_benchmark_ok"] = bool(benchmark)
        for key in ("min", "max", "mean", "median", "p99"):
            row[f"{prefix}_npu_{key}_ms"] = benchmark.get(key)

    row["npu_median_delta_pct"] = percent_change(
        row.get("mixed_npu_median_ms"), row.get("fp16_npu_median_ms")
    )
    row["closed_loop_median_delta_pct"] = percent_change(
        row.get("mixed_closed_loop_median_ms"),
        row.get("fp16_closed_loop_median_ms"),
    )
    for metric in METRICS:
        row[f"{metric}_teacher_nrmse_reduction_pct"] = percent_reduction(
            row.get(f"mixed_{metric}_teacher_nrmse"),
            row.get(f"fp16_{metric}_teacher_nrmse"),
        )
        row[f"{metric}_closed_nrmse_reduction_pct"] = percent_reduction(
            row.get(f"mixed_{metric}_closed_nrmse"),
            row.get(f"fp16_{metric}_closed_nrmse"),
        )
    row["complete"] = bool(row["reference_valid"]) and all(
        bool(row[f"{prefix}_{key}"])
        for prefix in MODES
        for key in ("conversion_ok", "precision_report_ok", "benchmark_ok")
    )
    return row


def numeric_values(rows: Iterable[Mapping[str, object]], key: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def describe(values: Sequence[float]) -> Dict[str, float] | None:
    if not values:
        return None
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def build_aggregate(rows: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    aggregate: Dict[str, object] = {
        "model_count": len(rows),
        "complete_model_count": sum(bool(row.get("complete")) for row in rows),
        "successful_om_count": sum(
            bool(row.get(f"{prefix}_conversion_ok"))
            for row in rows
            for prefix in MODES
        ),
        "npu_median_delta_pct": describe(
            numeric_values(rows, "npu_median_delta_pct")
        ),
        "closed_loop_median_delta_pct": describe(
            numeric_values(rows, "closed_loop_median_delta_pct")
        ),
        "precision": {},
    }
    precision = aggregate["precision"]
    assert isinstance(precision, dict)
    for metric in METRICS:
        teacher_values = numeric_values(
            rows, f"{metric}_teacher_nrmse_reduction_pct"
        )
        closed_values = numeric_values(rows, f"{metric}_closed_nrmse_reduction_pct")
        precision[metric] = {
            "teacher_reduction_pct": describe(teacher_values),
            "teacher_mixed_better_count": sum(
                value > 0.0 for value in teacher_values
            ),
            "teacher_compared_count": len(teacher_values),
            "reduction_pct": describe(closed_values),
            "mixed_better_count": sum(value > 0.0 for value in closed_values),
            "compared_count": len(closed_values),
        }
    return aggregate


def format_number(value: object, digits: int = 4) -> str:
    return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "N/A"


def format_nrmse_pair(
    row: Mapping[str, object], metric: str, regime: str = "closed"
) -> str:
    fp16 = row.get(f"fp16_{metric}_{regime}_nrmse")
    mixed = row.get(f"mixed_{metric}_{regime}_nrmse")
    if not isinstance(fp16, (int, float)) or not isinstance(mixed, (int, float)):
        return "N/A"
    return f"{fp16 * 100.0:.4f}% / {mixed * 100.0:.4f}%"


def render_markdown(
    rows: Sequence[Mapping[str, object]],
    aggregate: Mapping[str, object],
    title: str = "Ascend 8T All-model FP16 vs Mixed-precision Results",
    runtime_only: bool = False,
) -> str:
    status_label = "OM runtime" if runtime_only else "Conversion"
    lines = [
        f"# {title}",
        "",
        "Positive speed delta means mixed precision is slower. Positive NRMSE reduction means mixed precision is more accurate.",
        "",
        f"## {status_label} and speed",
        "",
        "| Model | FP16 | Mixed | FP16 NPU median (ms) | Mixed NPU median (ms) | NPU delta | FP16 closed median (ms) | Mixed closed median (ms) | Closed delta |",
        "| :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {model} | {fp16} | {mixed} | {fp16_npu} | {mixed_npu} | {npu_delta}% | {fp16_closed} | {mixed_closed} | {closed_delta}% |".format(
                model=row["model"],
                fp16="OK" if row.get("fp16_conversion_ok") else "FAIL",
                mixed="OK" if row.get("mixed_conversion_ok") else "FAIL",
                fp16_npu=format_number(row.get("fp16_npu_median_ms"), 6),
                mixed_npu=format_number(row.get("mixed_npu_median_ms"), 6),
                npu_delta=format_number(row.get("npu_median_delta_pct"), 2),
                fp16_closed=format_number(row.get("fp16_closed_loop_median_ms"), 6),
                mixed_closed=format_number(row.get("mixed_closed_loop_median_ms"), 6),
                closed_delta=format_number(row.get("closed_loop_median_delta_pct"), 2),
            )
        )
    lines.extend(
        [
            "",
            "## Teacher-forced NRMSE (FP16 / mixed)",
            "",
            "| Model | Amplitude | Effective harmonics | Noise | State |",
            "| :--- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['model']} | {format_nrmse_pair(row, 'amplitude', 'teacher')} | "
            f"{format_nrmse_pair(row, 'harmonic_amplitudes', 'teacher')} | "
            f"{format_nrmse_pair(row, 'noise_amps', 'teacher')} | "
            f"{format_nrmse_pair(row, 'state_out', 'teacher')} |"
        )
    lines.extend(
        [
            "",
            "## Closed-loop NRMSE (FP16 / mixed)",
            "",
            "| Model | Amplitude | Effective harmonics | Noise | State |",
            "| :--- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['model']} | {format_nrmse_pair(row, 'amplitude')} | "
            f"{format_nrmse_pair(row, 'harmonic_amplitudes')} | "
            f"{format_nrmse_pair(row, 'noise_amps')} | "
            f"{format_nrmse_pair(row, 'state_out')} |"
        )
    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Complete models: {aggregate['complete_model_count']} / {aggregate['model_count']}",
            f"- Successful OM models: {aggregate['successful_om_count']} / {len(rows) * 2}",
        ]
    )
    for key, label in (
        ("npu_median_delta_pct", "NPU median speed delta"),
        ("closed_loop_median_delta_pct", "Closed-loop median speed delta"),
    ):
        stats = aggregate.get(key)
        if isinstance(stats, Mapping):
            lines.append(
                f"- {label}: median {stats['median']:.2f}% "
                f"(min {stats['min']:.2f}%, max {stats['max']:.2f}%)"
            )
    precision = aggregate.get("precision", {})
    if isinstance(precision, Mapping):
        for metric in METRICS:
            item = precision.get(metric, {})
            if not isinstance(item, Mapping):
                continue
            teacher_stats = item.get("teacher_reduction_pct")
            if isinstance(teacher_stats, Mapping):
                lines.append(
                    f"- {metric} teacher-forced NRMSE reduction: median "
                    f"{teacher_stats['median']:.2f}%; mixed better for "
                    f"{item['teacher_mixed_better_count']} / "
                    f"{item['teacher_compared_count']} models"
                )
            stats = item.get("reduction_pct")
            if isinstance(stats, Mapping):
                lines.append(
                    f"- {metric} closed-loop NRMSE reduction: median "
                    f"{stats['median']:.2f}%; mixed better for "
                    f"{item['mixed_better_count']} / {item['compared_count']} models"
                )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    models_dir = args.models_dir.resolve()
    om_dir = args.om_dir.resolve()
    report_dir = args.report_dir.resolve()
    model_paths = sorted(models_dir.glob("*.onnx"), key=lambda path: path.stem.lower())
    if not model_paths:
        raise FileNotFoundError(f"No ONNX models found in {models_dir}")
    rows = [
        load_model_row(
            path,
            om_dir,
            report_dir,
            args.steps,
            args.seed,
            args.timing_repeats,
            args.runtime_only,
        )
        for path in model_paths
    ]
    aggregate = build_aggregate(rows)
    output_prefix = (
        args.output_prefix.resolve()
        if args.output_prefix
        else report_dir / "summary"
    )
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    markdown_path = output_prefix.with_suffix(".md")
    payload = {
        "models_dir": str(models_dir),
        "om_dir": str(om_dir),
        "report_dir": str(report_dir),
        "steps": args.steps,
        "seed": args.seed,
        "timing_repeats": args.timing_repeats,
        "rows": rows,
        "aggregate": aggregate,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with csv_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    markdown_path.write_text(
        render_markdown(rows, aggregate, args.title, args.runtime_only),
        encoding="utf-8",
    )
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"Markdown: {markdown_path}")
    print(
        f"Complete models: {aggregate['complete_model_count']} / "
        f"{aggregate['model_count']}"
    )
    return 0 if aggregate["complete_model_count"] == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
