#!/usr/bin/env bash

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_DIR="$ROOT_DIR/models/cross_soc"
REPORT_DIR="$ROOT_DIR/reports/cross_soc"
NATIVE_MODEL="${NATIVE_MODEL:-$HOME/Documents/case3/models/om/Violin.om}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-60}"

mkdir -p "$REPORT_DIR"
exec > >(tee "$REPORT_DIR/run.log") 2>&1

die() {
    echo "ERROR: $*" >&2
    exit 2
}

activate_environment() {
    local conda_script="/usr/local/miniconda3/etc/profile.d/conda.sh"
    local cann_script="/usr/local/Ascend/ascend-toolkit/set_env.sh"

    [[ -f "$conda_script" ]] || die "conda profile script not found: $conda_script"
    [[ -f "$cann_script" ]] || die "CANN environment script not found: $cann_script"

    # shellcheck disable=SC1090
    source "$conda_script"
    conda activate base || die "failed to activate conda base"
    # shellcheck disable=SC1090
    source "$cann_script"

    command -v python >/dev/null || die "python not found in conda base"
    python -m ais_bench --help >/dev/null 2>&1 || die "ais_bench is not available"
}

run_case() {
    local label="$1"
    local build_soc="$2"
    local build_cann="$3"
    local precision="$4"
    local model="$5"
    local log_file="$REPORT_DIR/${label}.ais_bench.log"

    if [[ ! -s "$model" ]]; then
        printf '%s\t%s\t%s\t%s\tmissing\tNA\tNA\n' \
            "$label" "$build_soc" "$build_cann" "$precision" >> "$REPORT_DIR/status.tsv"
        return 0
    fi

    local model_sha256 model_size
    model_sha256="$(sha256sum "$model" | awk '{print $1}')"
    model_size="$(stat -c '%s' "$model")"
    printf '%s\t%s\t%s\t%s\n' "$label" "$model_size" "$model_sha256" "$model" \
        >> "$REPORT_DIR/model_inventory.tsv"

    echo
    echo "===== Testing $label ====="
    echo "Build SoC: $build_soc"
    echo "Build CANN: $build_cann"
    echo "Precision: $precision"
    echo "Model: $model"

    timeout --signal=TERM --kill-after=5s "${TIMEOUT_SECONDS}s" \
        python -m ais_bench \
        --model "$model" \
        --batchsize 1 \
        --warmup_count 1 \
        --loop 1 > "$log_file" 2>&1
    local status=$?
    local result="failed"
    [[ "$status" -eq 0 ]] && result="success"
    [[ "$status" -eq 124 ]] && result="timeout"

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$label" "$build_soc" "$build_cann" "$precision" "$result" "$status" "$log_file" \
        >> "$REPORT_DIR/status.tsv"
    grep -Ein \
        'load model.*success|model.*not match|mismatch|failed|ERROR|FATAL|E[0-9]{4,}|EZ[0-9]+' \
        "$log_file" > "$REPORT_DIR/${label}.error_scan.txt" 2>/dev/null || true
    sleep 2
    return 0
}

[[ "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "invalid TIMEOUT_SECONDS: $TIMEOUT_SECONDS"
activate_environment

{
    echo "captured_at=$(date --iso-8601=seconds)"
    echo "hostname=$(hostname)"
    echo "kernel=$(uname -a)"
    echo "conda_environment=${CONDA_DEFAULT_ENV:-unknown}"
    echo "python=$(command -v python)"
    python --version 2>&1
    echo "ASCEND_TOOLKIT_HOME=${ASCEND_TOOLKIT_HOME:-unknown}"
    npu-smi info 2>&1 || true
} > "$REPORT_DIR/environment.txt"

printf 'label\tbuild_soc\tbuild_cann\tprecision\tresult\texit_code\tlog\n' > "$REPORT_DIR/status.tsv"
printf 'label\tsize_bytes\tsha256\tpath\n' > "$REPORT_DIR/model_inventory.tsv"

run_case \
    native_20t_b1 Ascend310B1 CANN_8.0.0 force_fp16 "$NATIVE_MODEL"
run_case \
    ascend8t_b4_cann83_mixed Ascend310B4 CANN_8.3.RC1 mixed_float16 \
    "$MODEL_DIR/Violin_ascend8t_cann83_mixed_float16.om"
run_case \
    ascend8t2_b4_cann80_fp16 Ascend310B4 CANN_8.0.0 force_fp16 \
    "$MODEL_DIR/Violin_ascend8t2_cann80_force_fp16.om"
run_case \
    ascend8t2_b4_cann80_mixed Ascend310B4 CANN_8.0.0 mixed_float16 \
    "$MODEL_DIR/Violin_ascend8t2_cann80_mixed_float16.om"

npu-smi info > "$REPORT_DIR/npu_smi_after.txt" 2>&1 || true

echo
echo "===== Cross-SoC status ====="
cat "$REPORT_DIR/status.tsv"
