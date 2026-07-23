#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_DIR="${MODEL_DIR:-$ROOT_DIR/models/midi_ddsp/om/ascend8t2}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/reports/ascend8t2/midi_ddsp/runtime}"

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

[[ -d "$MODEL_DIR" ]] || die "model directory not found: $MODEL_DIR"
mkdir -p "$REPORT_DIR"
activate_existing_environment
python -c 'import ais_bench' >/dev/null 2>&1 || \
    die "ais_bench is not available in the existing conda environment"

mapfile -t models < <(find "$MODEL_DIR" -maxdepth 1 -type f -name '*.om' | sort)
[[ "${#models[@]}" -gt 0 ]] || die "no OM models found in $MODEL_DIR"

printf 'model\texit_code\tstatus\n' > "$REPORT_DIR/runtime_status.tsv"
for model in "${models[@]}"; do
    stem="$(basename "${model%.om}")"
    log="$REPORT_DIR/${stem}.ais_bench.log"
    set +e
    python -m ais_bench \
        --model "$model" \
        --batchsize 1 \
        --warmup_count 1 \
        --loop 1 > "$log" 2>&1
    status=$?
    set -e
    if [[ "$status" -eq 0 ]]; then
        state="inferred"
    else
        state="failed"
    fi
    printf '%s\t%s\t%s\n' "$(basename "$model")" "$status" "$state" \
        | tee -a "$REPORT_DIR/runtime_status.tsv"
done

{
    echo "host=$(hostname)"
    echo "conda_env=${CONDA_DEFAULT_ENV:-unknown}"
    echo "python=$(python --version 2>&1)"
    echo "model_dir=$(realpath "$MODEL_DIR")"
    echo
    find "$MODEL_DIR" -maxdepth 1 -type f -name '*.om' \
        -printf '%f\t%s bytes\n' | sort
    echo
    find "$MODEL_DIR" -maxdepth 1 -type f -name '*.om' -print0 \
        | sort -z | xargs -0 -r sha256sum
} > "$REPORT_DIR/artifacts.txt"

npu-smi info > "$REPORT_DIR/npu_smi.txt" 2>&1 || true

if awk -F '\t' 'NR > 1 && $2 != 0 {failed=1} END {exit failed}' \
    "$REPORT_DIR/runtime_status.tsv"; then
    echo "All MIDI-DDSP OM models loaded and inferred successfully."
else
    echo "One or more MIDI-DDSP OM models failed runtime validation." >&2
    exit 1
fi
