#!/usr/bin/env python3
"""Summarize MIDI-DDSP FP16 and mixed-precision benchmark results."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Dict, Mapping, Sequence


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
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    return parser.parse_args()


def parse_ais_bench(path: Path) -> Dict[str, float]:
    match = AIS_BENCH_PATTERN.search(path.read_text(encoding="utf-8", errors="replace"))
    if not match:
        raise ValueError(f"cannot find NPU timing summary in {path}")
    return {name: float(value) for name, value in match.groupdict().items()}


def delta_pct(mixed: float, fp16: float) -> float:
    return (mixed / fp16 - 1.0) * 100.0


def median_nrmse(report: Mapping[str, object], source: str, output: str) -> float:
    return float(
        report["accuracy"][f"against_{source}"][output]["aggregate"]
        ["normalized_rmse"]["median"]
    )


def median_repeatability(report: Mapping[str, object], output: str) -> float:
    aggregate = report["repeatability"][output]["aggregate"]
    if int(aggregate["run_count"]) == 0:
        return 0.0
    return float(aggregate["normalized_rmse"]["median"])


def load_rows(report_dir: Path) -> list[Dict[str, object]]:
    rows: list[Dict[str, object]] = []
    for report_path in sorted((report_dir / "precision").glob("*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        stem = report_path.stem
        npu = parse_ais_bench(report_dir / "ais_bench" / f"{stem}.log")
        outputs = list(report["accuracy"]["against_tensorflow"])
        row: Dict[str, object] = {
            "component": report["component"],
            "precision": report["precision_mode"],
            "model": stem,
            "om_sha256": report["om_sha256"],
            "om_size_bytes": report["om_size_bytes"],
            "reference_sha256": report["reference_sha256"],
            "all_outputs_finite": report["all_outputs_finite"],
            "npu_mean_ms": npu["mean"],
            "npu_median_ms": npu["median"],
            "npu_p99_ms": npu["p99"],
            "end_to_end_mean_ms": report["end_to_end_timing"]["mean_ms"],
            "end_to_end_median_ms": report["end_to_end_timing"]["median_ms"],
            "end_to_end_p95_ms": report["end_to_end_timing"]["p95_ms"],
        }
        for output in outputs:
            row[f"{output}_tf_nrmse"] = median_nrmse(report, "tensorflow", output)
            row[f"{output}_onnx_nrmse"] = median_nrmse(report, "onnx", output)
            row[f"{output}_repeatability_nrmse"] = median_repeatability(report, output)
        rows.append(row)
    if len(rows) != 4:
        raise AssertionError(f"expected 4 precision reports, found {len(rows)}")
    return rows


def build_comparisons(rows: Sequence[Mapping[str, object]]) -> list[Dict[str, object]]:
    comparisons: list[Dict[str, object]] = []
    for component in ("expression", "synthesis"):
        by_precision = {
            str(row["precision"]): row
            for row in rows
            if row["component"] == component
        }
        fp16 = by_precision["force_fp16"]
        mixed = by_precision["mixed_float16"]
        comparisons.append(
            {
                "component": component,
                "npu_median_delta_pct": delta_pct(
                    float(mixed["npu_median_ms"]), float(fp16["npu_median_ms"])
                ),
                "end_to_end_median_delta_pct": delta_pct(
                    float(mixed["end_to_end_median_ms"]),
                    float(fp16["end_to_end_median_ms"]),
                ),
            }
        )
    return comparisons


def format_pct(value: object) -> str:
    return f"{float(value) * 100.0:.6f}%"


def render_markdown(
    rows: Sequence[Mapping[str, object]], comparisons: Sequence[Mapping[str, object]]
) -> str:
    lines = [
        "# MIDI-DDSP Ascend 8T2 FP16 vs Mixed Precision",
        "",
        "Positive speed delta means mixed precision is slower than FP16.",
        "",
        "## Speed",
        "",
        "| Component | Precision | NPU mean (ms) | NPU median (ms) | NPU p99 (ms) | End-to-end median (ms) | End-to-end p95 (ms) |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['component']} | {row['precision']} | "
            f"{float(row['npu_mean_ms']):.6f} | {float(row['npu_median_ms']):.6f} | "
            f"{float(row['npu_p99_ms']):.6f} | "
            f"{float(row['end_to_end_median_ms']):.6f} | "
            f"{float(row['end_to_end_p95_ms']):.6f} |"
        )
    lines.extend(
        [
            "",
            "| Component | Mixed NPU median delta | Mixed end-to-end median delta |",
            "| :--- | ---: | ---: |",
        ]
    )
    for item in comparisons:
        lines.append(
            f"| {item['component']} | {float(item['npu_median_delta_pct']):+.2f}% | "
            f"{float(item['end_to_end_median_delta_pct']):+.2f}% |"
        )

    lines.extend(["", "## Accuracy against TensorFlow", ""])
    for component in ("expression", "synthesis"):
        component_rows = [row for row in rows if row["component"] == component]
        outputs = sorted(
            key[: -len("_tf_nrmse")]
            for key in component_rows[0]
            if key.endswith("_tf_nrmse")
        )
        lines.extend(
            [
                f"### {component}",
                "",
                "| Output | FP16 median NRMSE | Mixed median NRMSE | FP16 repeatability NRMSE | Mixed repeatability NRMSE |",
                "| :--- | ---: | ---: | ---: | ---: |",
            ]
        )
        by_precision = {str(row["precision"]): row for row in component_rows}
        fp16 = by_precision["force_fp16"]
        mixed = by_precision["mixed_float16"]
        for output in outputs:
            lines.append(
                f"| {output} | {format_pct(fp16[f'{output}_tf_nrmse'])} | "
                f"{format_pct(mixed[f'{output}_tf_nrmse'])} | "
                f"{format_pct(fp16[f'{output}_repeatability_nrmse'])} | "
                f"{format_pct(mixed[f'{output}_repeatability_nrmse'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir.resolve()
    output_prefix = args.output_prefix.resolve()
    rows = load_rows(report_dir)
    comparisons = build_comparisons(rows)
    payload = {"rows": rows, "comparisons": comparisons}
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    output_prefix.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with output_prefix.with_suffix(".csv").open(
        "w", encoding="utf-8", newline=""
    ) as target:
        fieldnames = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    output_prefix.with_suffix(".md").write_text(
        render_markdown(rows, comparisons), encoding="utf-8"
    )
    print(output_prefix.with_suffix(".json"))
    print(output_prefix.with_suffix(".csv"))
    print(output_prefix.with_suffix(".md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
