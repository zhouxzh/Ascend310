#!/usr/bin/env bash

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_DIR="$ROOT_DIR/models/cross_soc/mixed_precision"
REPORT_DIR="$ROOT_DIR/reports/cross_soc/all_mixed"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-60}"

mkdir -p "$REPORT_DIR"
exec > >(tee "$REPORT_DIR/run.log") 2>&1

die() {
    echo "ERROR: $*" >&2
    exit 2
}

[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "invalid TIMEOUT_SECONDS: $TIMEOUT_SECONDS"
[[ -d "$MODEL_DIR" ]] || die "model directory not found: $MODEL_DIR"

source /usr/local/miniconda3/etc/profile.d/conda.sh || die "failed to load conda"
conda activate base || die "failed to activate conda base"
source /usr/local/Ascend/ascend-toolkit/set_env.sh || die "failed to load CANN"
python -m ais_bench --help >/dev/null 2>&1 || die "ais_bench is not available"

printf 'model\tresult\texit_code\tsize_bytes\tsha256\tmedian_ms\tp99_ms\n' > "$REPORT_DIR/status.tsv"

shopt -s nullglob
models=("$MODEL_DIR"/*.om)
[[ ${#models[@]} -gt 0 ]] || die "no OM files found in $MODEL_DIR"

for model in "${models[@]}"; do
    filename="$(basename "$model")"
    stem="${filename%.om}"
    log_file="$REPORT_DIR/${stem}.ais_bench.log"
    model_size="$(stat -c '%s' "$model")"
    model_sha256="$(sha256sum "$model" | awk '{print $1}')"

    echo
    echo "===== Testing $filename ====="
    timeout --signal=TERM --kill-after=5s "${TIMEOUT_SECONDS}s" \
        python -m ais_bench \
        --model "$model" \
        --batchsize 1 \
        --warmup_count 10 \
        --loop 100 > "$log_file" 2>&1
    status=$?
    result="failed"
    [[ "$status" -eq 0 ]] && result="success"
    [[ "$status" -eq 124 ]] && result="timeout"

    timing_line="$(grep -F 'NPU_compute_time (ms):' "$log_file" | tail -1)"
    median_ms="$(printf '%s\n' "$timing_line" | sed -n 's/.*median = \([^,]*\).*/\1/p')"
    p99_ms="$(printf '%s\n' "$timing_line" | sed -n 's/.*percentile(99%) = \([^ ]*\).*/\1/p')"
    [[ -n "$median_ms" ]] || median_ms="NA"
    [[ -n "$p99_ms" ]] || p99_ms="NA"

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$filename" "$result" "$status" "$model_size" "$model_sha256" "$median_ms" "$p99_ms" \
        >> "$REPORT_DIR/status.tsv"
    grep -Ein \
        'load model.*success|unload model success|mismatch|failed|ERROR|FATAL|E[0-9]{4,}|EZ[0-9]+' \
        "$log_file" > "$REPORT_DIR/${stem}.error_scan.txt" 2>/dev/null || true
    sleep 1
done

npu-smi info > "$REPORT_DIR/npu_smi_after.txt" 2>&1 || true
echo
cat "$REPORT_DIR/status.tsv"
