from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


COLUMNS = ["case", "variant", "mean_ms", "p95_ms", "fps", "runs", "speedup", "note"]


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    rows = obj.get("summary_rows", [])
    for row in rows:
        row.setdefault("source", path.name)
    return rows


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def markdown_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| 案例 | 变体 | 平均耗时(ms) | p95(ms) | FPS | 样本数 | 加速比 | 说明 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        values = [format_value(row.get(column, "")) for column in COLUMNS]
        lines.append(
            f"| {values[0]} | `{values[1]}` | {values[2]} | {values[3]} | "
            f"{values[4]} | {values[5]} | {values[6]} | {values[7]} |"
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize chapter7 JSON metrics as a Markdown table.")
    parser.add_argument("json_files", nargs="+", help="Metrics JSON files.")
    parser.add_argument("--output", help="Optional Markdown output file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: list[dict[str, Any]] = []
    for text in args.json_files:
        rows.extend(load_rows(Path(text)))
    table = markdown_table(rows)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
