#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_DIR="${MODEL_DIR:-$ROOT_DIR/models/midi_ddsp/om/ascend8t2}"
ONNX_DIR="${ONNX_DIR:-$ROOT_DIR/models/midi_ddsp/onnx}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports/ascend8t2/midi_ddsp/benchmark}"
ACCURACY_RUNS="${ACCURACY_RUNS:-5}"
PYACL_WARMUP="${PYACL_WARMUP:-20}"
PYACL_LOOPS="${PYACL_LOOPS:-100}"
TIMING_REPEATS="${TIMING_REPEATS:-5}"
AIS_WARMUP="${AIS_WARMUP:-100}"
AIS_LOOPS="${AIS_LOOPS:-1000}"

die() {
    echo "ERROR: $*" >&2
    exit 2
}

activate_existing_environment() {
    local conda_script=""
    for candidate in \
        "$HOME/anaconda3/etc/profile.d/conda.sh" \
        "$HOME/miniconda3/etc/profile.d/conda.sh" \
        "/usr/local/miniconda3/etc/profile.d/conda.sh"; do
        if [[ -f "$candidate" ]]; then
            conda_script="$candidate"
            break
        fi
    done
    [[ -n "$conda_script" ]] || die "existing conda installation not found"
    # shellcheck disable=SC1090
    source "$conda_script"
    conda activate "${CONDA_ENV:-base}" || die "cannot activate existing conda env"

    local cann_script=""
    for candidate in \
        "$HOME/Ascend/latest/set_env.sh" \
        "/usr/local/Ascend/ascend-toolkit/set_env.sh" \
        "/usr/local/Ascend/latest/set_env.sh"; do
        if [[ -f "$candidate" ]]; then
            cann_script="$candidate"
            break
        fi
    done
    [[ -n "$cann_script" ]] || die "existing CANN environment not found"
    # shellcheck disable=SC1090
    source "$cann_script"
}

mkdir -p "$REPORT_DIR/precision" "$REPORT_DIR/outputs" "$REPORT_DIR/ais_bench"
activate_existing_environment
python -c 'import ais_bench, numpy' >/dev/null 2>&1 || \
    die "ais_bench or numpy is unavailable in the existing conda environment"

models=(
    "expression|force_fp16|midi_ddsp_expression_notes32_force_fp16|midi_ddsp_expression_notes32_reference.npz"
    "expression|mixed_float16|midi_ddsp_expression_notes32_mixed_float16|midi_ddsp_expression_notes32_reference.npz"
    "synthesis|force_fp16|midi_ddsp_synthesis_params_frames64_force_fp16|midi_ddsp_synthesis_params_frames64_reference.npz"
    "synthesis|mixed_float16|midi_ddsp_synthesis_params_frames64_mixed_float16|midi_ddsp_synthesis_params_frames64_reference.npz"
)

printf 'model\tprecision_exit\tais_bench_exit\n' > "$REPORT_DIR/status.tsv"
failed=0
for entry in "${models[@]}"; do
    IFS='|' read -r component precision stem reference_name <<< "$entry"
    om="$MODEL_DIR/$stem.om"
    reference="$ONNX_DIR/$reference_name"
    [[ -s "$om" ]] || die "OM model not found: $om"
    [[ -s "$reference" ]] || die "reference not found: $reference"

    set +e
    python "$SCRIPT_DIR/compare_midi_ddsp_om.py" \
        --component "$component" \
        --om "$om" \
        --reference "$reference" \
        --report "$REPORT_DIR/precision/$stem.json" \
        --outputs "$REPORT_DIR/outputs/$stem.npz" \
        --precision-label "$precision" \
        --accuracy-runs "$ACCURACY_RUNS" \
        --warmup "$PYACL_WARMUP" \
        --loops "$PYACL_LOOPS" \
        --timing-repeats "$TIMING_REPEATS" \
        > "$REPORT_DIR/precision/$stem.log" 2>&1
    precision_status=$?

    python -m ais_bench \
        --model "$om" \
        --batchsize 1 \
        --warmup_count "$AIS_WARMUP" \
        --loop "$AIS_LOOPS" \
        > "$REPORT_DIR/ais_bench/$stem.log" 2>&1
    ais_status=$?
    set -e

    printf '%s\t%s\t%s\n' "$stem" "$precision_status" "$ais_status" \
        | tee -a "$REPORT_DIR/status.tsv"
    if [[ "$precision_status" -ne 0 || "$ais_status" -ne 0 ]]; then
        failed=1
    fi
done

{
    echo "host=$(hostname)"
    echo "date=$(date --iso-8601=seconds)"
    echo "conda_env=${CONDA_DEFAULT_ENV:-unknown}"
    echo "python=$(python --version 2>&1)"
    echo "accuracy_runs=$ACCURACY_RUNS"
    echo "pyacl_warmup=$PYACL_WARMUP"
    echo "pyacl_loops=$PYACL_LOOPS"
    echo "timing_repeats=$TIMING_REPEATS"
    echo "ais_warmup=$AIS_WARMUP"
    echo "ais_loops=$AIS_LOOPS"
    echo
    npu-smi info 2>&1 || true
} > "$REPORT_DIR/run_environment.txt"

exit "$failed"
