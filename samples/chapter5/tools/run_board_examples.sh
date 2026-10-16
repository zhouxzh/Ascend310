#!/usr/bin/env bash
# 顺序执行第5章全部样例（板端便捷入口）。本脚本必须保持 LF 换行。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-/usr/local/miniconda3/bin/python}"

exec "$PY" "$HERE/run_board_examples.py" "$@"
