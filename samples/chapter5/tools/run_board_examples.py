"""顺序执行第5章全部样例，并把每条 RESULT 汇总成一份板端报告。

    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    python tools/run_board_examples.py

每个样例在子进程中运行，因此样例自身崩溃不会中断整轮测试；汇总报告会记录
命令、退出码与样例输出的 RESULT 行。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CHAPTER_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = CHAPTER_DIR / "outputs"

EXAMPLES: list[tuple[str, list[str]]] = [
    ("check_mindspore_env.py", ["--output", "env-report.json"]),
    ("01_tensor_ops.py", []),
    ("02_cell_network.py", []),
    ("03_dataset_pipeline.py", []),
    ("04_graph_mode.py", ["--mode", "pynative", "--output", "04_graph_mode_pynative.json"]),
    ("04_graph_mode.py", ["--mode", "graph", "--output", "04_graph_mode_graph.json"]),
    ("05_autograd_train.py", []),
    ("06_checkpoint_mindir.py", []),
    ("07_lite_inference.py", []),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run every chapter 5 sample and collect RESULT lines.")
    parser.add_argument("--timeout", type=int, default=900, help="Per-example timeout in seconds.")
    parser.add_argument("--only", default="", help="Comma separated script names to run.")
    return parser.parse_args()


def last_result(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        if line.startswith("RESULT "):
            try:
                payload = json.loads(line[len("RESULT ") :])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    only = {item.strip() for item in args.only.split(",") if item.strip()}

    records: list[dict[str, object]] = []
    for script, extra in EXAMPLES:
        if only and script not in only:
            continue
        command = [sys.executable, str(CHAPTER_DIR / script), *extra]
        print(f"\n=== RUN {script} {' '.join(extra)} ===", flush=True)
        try:
            completed = subprocess.run(
                command,
                cwd=str(CHAPTER_DIR),
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            exit_code: int | None = completed.returncode
        except subprocess.TimeoutExpired:
            output = ""
            exit_code = None
            print(f"TIMEOUT after {args.timeout}s", flush=True)

        result = last_result(output)
        tail = "\n".join([line for line in output.splitlines() if line.strip()][-6:])
        if tail:
            print(tail, flush=True)
        print(f"--- exit={exit_code} result={result.get('status') if result else 'missing'}", flush=True)

        records.append(
            {
                "script": script,
                "args": extra,
                "exit_code": exit_code,
                "result": result,
            }
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary = {
        "timestamp": stamp,
        "python": sys.executable,
        "chapter_dir": str(CHAPTER_DIR),
        "examples": records,
        "verdicts": {record["script"]: (record["result"] or {}).get("status", "missing") for record in records},
        "missing_verdicts": [record["script"] for record in records if not record["result"]],
    }
    summary_path = OUTPUT_DIR / f"board-run-{stamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("\n=== summary ===")
    for record in records:
        status = (record["result"] or {}).get("status", "missing")
        print(f"{record['script']:<26} exit={record['exit_code']} status={status}")
    print(f"summary: {summary_path}")

    return 0 if not summary["missing_verdicts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
