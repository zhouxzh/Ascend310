#!/usr/bin/env bash

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_ID="${TARGET_ID:-ascend8t2}"
SOC_VERSION="${SOC_VERSION:-Ascend310B4}"
MODEL="$ROOT_DIR/models/ddsp_vst/Violin.onnx"
CONVERTER="$SCRIPT_DIR/convert_onnx_to_om.sh"
OUTPUT_DIR="$ROOT_DIR/models/om/$TARGET_ID"
REPORT_DIR="$ROOT_DIR/reports/$TARGET_ID"
EXPECTED_MODEL_SHA256="82d6191868d36f967e8739887edba8e911e2bba6e09a63b514c5f3b8380996a5"

mkdir -p "$OUTPUT_DIR" "$REPORT_DIR"
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
    command -v atc >/dev/null || die "atc not found after loading CANN"
}

capture_preflight_environment() {
    local compiler_version_file=""
    compiler_version_file="$(find /usr/local/Ascend/ascend-toolkit -path '*/compiler/version.info' -type f 2>/dev/null | sort | tail -1)"
    {
        echo "captured_at=$(date --iso-8601=seconds)"
        echo "target_id=$TARGET_ID"
        echo "soc_version=$SOC_VERSION"
        echo "hostname=$(hostname)"
        echo "user=$(whoami)"
        echo "kernel=$(uname -a)"
        echo
        echo "[CANN compiler version]"
        if [[ -n "$compiler_version_file" ]]; then
            cat "$compiler_version_file" 2>&1 || true
        else
            echo "compiler version.info not found"
        fi
        echo
        echo "[NPU]"
        npu-smi info 2>&1 || true
        echo
        echo "[Memory]"
        free -h
        echo
        echo "[Disk]"
        df -h "$ROOT_DIR"
    } > "$REPORT_DIR/preflight_environment.txt"
}

capture_environment() {
    local compiler_version_file=""
    compiler_version_file="$(find /usr/local/Ascend/ascend-toolkit -path '*/compiler/version.info' -type f 2>/dev/null | sort | tail -1)"
    {
        echo "captured_at=$(date --iso-8601=seconds)"
        echo "target_id=$TARGET_ID"
        echo "soc_version=$SOC_VERSION"
        echo "hostname=$(hostname)"
        echo "user=$(whoami)"
        echo "kernel=$(uname -a)"
        echo "conda_environment=${CONDA_DEFAULT_ENV:-unknown}"
        echo "python=$(command -v python)"
        python --version 2>&1
        echo "atc=$(command -v atc)"
        echo "ASCEND_TOOLKIT_HOME=${ASCEND_TOOLKIT_HOME:-unknown}"
        echo "TE_PARALLEL_COMPILER=${TE_PARALLEL_COMPILER:-unset}"
        echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-unset}"
        echo
        echo "[CANN compiler version]"
        if [[ -n "$compiler_version_file" ]]; then
            cat "$compiler_version_file" 2>&1 || true
        else
            echo "compiler version.info not found"
        fi
        echo
        echo "[CANN version configuration]"
        cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg 2>&1 || true
        echo
        echo "[NPU]"
        npu-smi info 2>&1 || true
        echo
        echo "[Memory]"
        free -h
        echo
        echo "[Disk]"
        df -h "$ROOT_DIR"
    } > "$REPORT_DIR/environment.txt"
}

run_conversion() {
    local label="$1"
    shift
    local output="$OUTPUT_DIR/Violin_${label}"
    local log_file="$OUTPUT_DIR/Violin_${label}.atc.log"
    local summary_file="$OUTPUT_DIR/Violin_${label}.atc.summary.txt"

    echo
    echo "===== Converting $label ====="
    bash "$CONVERTER" \
        --model "$MODEL" \
        --output "$output" \
        --input-shape "state:512;f0_scaled:1;pw_scaled:1" \
        --input-format ND \
        --soc-version "$SOC_VERSION" \
        --log-level info \
        --log-file "$log_file" \
        --summary-file "$summary_file" \
        "$@"
    local status=$?
    local om_state="missing"
    [[ -s "$output.om" ]] && om_state="created"
    printf '%s\t%s\t%s\n' "$label" "$status" "$om_state" >> "$REPORT_DIR/conversion_status.tsv"
    return 0
}

validate_om() {
    local label="$1"
    local om_file="$OUTPUT_DIR/Violin_${label}.om"
    local log_file="$REPORT_DIR/Violin_${label}.ais_bench.log"

    [[ -s "$om_file" ]] || return 0
    if ! python -c 'import ais_bench' >/dev/null 2>&1; then
        echo -e "$label\tSKIPPED\tais_bench module not available" >> "$REPORT_DIR/inference_status.tsv"
        return 0
    fi

    echo
    echo "===== Loading $label with ais_bench ====="
    python -m ais_bench \
        --model "$om_file" \
        --batchsize 1 \
        --warmup_count 1 \
        --loop 1 > "$log_file" 2>&1
    local status=$?
    if [[ "$status" -eq 0 ]]; then
        echo -e "$label\t0\tloaded" >> "$REPORT_DIR/inference_status.tsv"
    else
        echo -e "$label\t$status\tfailed" >> "$REPORT_DIR/inference_status.tsv"
    fi
    return 0
}

[[ -f "$MODEL" ]] || die "model not found: $MODEL"
[[ -f "$CONVERTER" ]] || die "converter not found: $CONVERTER"
[[ "$TARGET_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid TARGET_ID: $TARGET_ID"
[[ "$SOC_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid SOC_VERSION: $SOC_VERSION"

capture_preflight_environment
activate_environment
export TE_PARALLEL_COMPILER=1
export OMP_NUM_THREADS=1

MODEL_SHA256="$(sha256sum "$MODEL" | awk '{print $1}')"
[[ "$MODEL_SHA256" == "$EXPECTED_MODEL_SHA256" ]] || \
    die "Violin.onnx SHA256 mismatch: $MODEL_SHA256"

capture_environment
printf 'model\tsha256\nViolin.onnx\t%s\n' "$MODEL_SHA256" > "$REPORT_DIR/source_model.tsv"
printf 'precision\texit_code\tom_state\n' > "$REPORT_DIR/conversion_status.tsv"
printf 'precision\texit_code\tload_state\n' > "$REPORT_DIR/inference_status.tsv"

bash -n "$CONVERTER" || die "converter syntax check failed"
bash "$CONVERTER" \
    --model "$MODEL" \
    --output "$OUTPUT_DIR/Violin_force_fp16" \
    --soc-version "$SOC_VERSION" \
    --dry-run > "$REPORT_DIR/Violin_force_fp16.dry_run.txt"
bash "$CONVERTER" \
    --model "$MODEL" \
    --output "$OUTPUT_DIR/Violin_mixed_float16" \
    --soc-version "$SOC_VERSION" \
    --precision-mode-v2 mixed_float16 \
    --dry-run > "$REPORT_DIR/Violin_mixed_float16.dry_run.txt"

run_conversion force_fp16
run_conversion mixed_float16 --precision-mode-v2 mixed_float16

validate_om force_fp16
validate_om mixed_float16

{
    echo "[Output files]"
    find "$OUTPUT_DIR" -maxdepth 1 -type f -printf '%f\t%s bytes\n' | sort
    echo
    echo "[SHA256]"
    find "$OUTPUT_DIR" -maxdepth 1 -type f -name '*.om' -print0 | sort -z | xargs -0 -r sha256sum
} > "$REPORT_DIR/artifacts.txt"

grep -Ein \
    'unsupported|not supported|not support|No parser|No supported Ops kernel|No OpKernel|select.*kernel.*failed|ERROR|FATAL|Traceback|ATC run failed|Segmentation fault|core dumped' \
    "$OUTPUT_DIR"/*.atc.log > "$REPORT_DIR/atc_error_scan.txt" 2>/dev/null || true

npu-smi info > "$REPORT_DIR/npu_smi_after.txt" 2>&1 || true
free -h > "$REPORT_DIR/memory_after.txt"

echo
echo "===== Conversion status ====="
cat "$REPORT_DIR/conversion_status.tsv"
echo "===== Inference load status ====="
cat "$REPORT_DIR/inference_status.tsv"
echo "===== ATC error scan ====="
cat "$REPORT_DIR/atc_error_scan.txt"
