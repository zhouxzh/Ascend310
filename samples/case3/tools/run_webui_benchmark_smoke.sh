#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports/webui/benchmark-smoke}"

export REPORT_DIR
export ACCURACY_RUNS="${ACCURACY_RUNS:-2}"
export PYACL_WARMUP="${PYACL_WARMUP:-2}"
export PYACL_LOOPS="${PYACL_LOOPS:-5}"
export TIMING_REPEATS="${TIMING_REPEATS:-2}"
export AIS_WARMUP="${AIS_WARMUP:-2}"
export AIS_LOOPS="${AIS_LOOPS:-5}"

bash "$SCRIPT_DIR/benchmark_midi_ddsp_ascend.sh"
python "$SCRIPT_DIR/summarize_midi_ddsp_benchmark.py" \
  --report-dir "$REPORT_DIR" \
  --output-prefix "$REPORT_DIR/summary"
