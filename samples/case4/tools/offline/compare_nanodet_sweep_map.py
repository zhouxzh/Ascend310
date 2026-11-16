#!/usr/bin/env python3
"""Join ACE2 ONNX and 8T/20T OM NanoDet mAP reports."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load_models(path: Path) -> dict[str, dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: item for item in value.get("models", [])}


def metrics(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("metrics", {}).get("metrics", {})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx-map", type=Path, required=True)
    parser.add_argument("--om-map", type=Path, required=True)
    parser.add_argument("--tier", choices=("8t", "20t"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    onnx = load_models(args.onnx_map)
    om = load_models(args.om_map)
    rows = []
    for name in sorted(set(onnx) | set(om)):
        reference = metrics(onnx.get(name, {}))
        observed = metrics(om.get(name, {}))
        rows.append({
            "name": name,
            "tier": args.tier,
            "onnx_status": onnx.get(name, {}).get("status"),
            "om_status": om.get(name, {}).get("status"),
            "onnx_mAP50_95": reference.get("mAP50_95"),
            "om_mAP50_95": observed.get("mAP50_95"),
            "delta_mAP50_95": (
                observed["mAP50_95"] - reference["mAP50_95"]
                if observed.get("mAP50_95") is not None and reference.get("mAP50_95") is not None else None
            ),
            "onnx_AP50": reference.get("AP50"),
            "om_AP50": observed.get("AP50"),
            "delta_AP50": (
                observed["AP50"] - reference["AP50"]
                if observed.get("AP50") is not None and reference.get("AP50") is not None else None
            ),
            "onnx_AP75": reference.get("AP75"),
            "om_AP75": observed.get("AP75"),
            "delta_AP75": (
                observed["AP75"] - reference["AP75"]
                if observed.get("AP75") is not None and reference.get("AP75") is not None else None
            ),
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(
        json.dumps({"schema_version": 1, "kind": "nanodet_onnx_vs_om_map", "tier": args.tier, "rows": rows},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = list(rows[0]) if rows else ["name"]
    with (args.output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        f"# NanoDet ONNX vs {args.tier.upper()} OM",
        "",
        "| Model | ONNX mAP | OM mAP | Delta | ONNX AP50 | OM AP50 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        def fmt(value: Any) -> str:
            return "--" if value is None else f"{value:.6f}"
        lines.append(
            f"| {row['name']} | {fmt(row['onnx_mAP50_95'])} | {fmt(row['om_mAP50_95'])} | "
            f"{fmt(row['delta_mAP50_95'])} | {fmt(row['onnx_AP50'])} | {fmt(row['om_AP50'])} |"
        )
    (args.output_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    failures = [row["name"] for row in rows if row["om_status"] != "completed"]
    print(json.dumps({"models": len(rows), "failures": failures}, ensure_ascii=False))
    return 0 if rows and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
