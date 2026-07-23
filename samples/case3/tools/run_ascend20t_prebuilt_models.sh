#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODELS_DIR="$ROOT_DIR/models/ddsp_vst"
OM_DIR="$ROOT_DIR/models/om/all_models"
REPORT_DIR="$ROOT_DIR/reports/ascend20t/all_models_runtime"
STEPS=1024
SEED=20260721
DEVICE=0
TIMING_REPEATS=5
AIS_WARMUP=100
AIS_LOOP=1000
PHASE="all"
FORCE=0

usage() {
    cat <<'EOF'
Test prebuilt DDSP-VST FP16 and mixed-precision OM models on Ascend 20T.

Usage:
  bash tools/run_ascend20t_prebuilt_models.sh [options]

Options:
  --phase NAME       all, precision, benchmark, or summary. Default: all
  --force            Regenerate valid existing reports.
  --steps N          Reference sequence length. Default: 1024
  --seed N           Reference random seed. Default: 20260721
  --device N         Ascend device ID. Default: 0
  --timing-repeats N Closed-loop timing repeats. Default: 5
  -h, --help         Show this help.
EOF
}

require_value() {
    [[ -n "${2:-}" ]] || {
        echo "ERROR: $1 requires a value" >&2
        exit 2
    }
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase)
            require_value "$1" "${2:-}"
            PHASE="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --steps)
            require_value "$1" "${2:-}"
            STEPS="$2"
            shift 2
            ;;
        --seed)
            require_value "$1" "${2:-}"
            SEED="$2"
            shift 2
            ;;
        --device)
            require_value "$1" "${2:-}"
            DEVICE="$2"
            shift 2
            ;;
        --timing-repeats)
            require_value "$1" "${2:-}"
            TIMING_REPEATS="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            exit 2
            ;;
    esac
done

case "$PHASE" in
    all|precision|benchmark|summary) ;;
    *)
        echo "ERROR: unsupported phase: $PHASE" >&2
        exit 2
        ;;
esac

for numeric in "$STEPS" "$SEED" "$DEVICE" "$TIMING_REPEATS"; do
    [[ "$numeric" =~ ^[0-9]+$ ]] || {
        echo "ERROR: numeric options must be non-negative integers" >&2
        exit 2
    }
done
[[ "$STEPS" -ge 2 ]] || { echo "ERROR: --steps must be at least 2" >&2; exit 2; }
[[ "$TIMING_REPEATS" -ge 1 ]] || {
    echo "ERROR: --timing-repeats must be at least 1" >&2
    exit 2
}

activate_environment() {
    set +u
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
    [[ -n "$conda_script" ]] || {
        echo "ERROR: conda profile script not found" >&2
        exit 1
    }
    # shellcheck disable=SC1090
    source "$conda_script"
    conda activate base || exit 1

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
    [[ -n "$cann_script" ]] || {
        echo "ERROR: CANN set_env.sh not found" >&2
        exit 1
    }
    # shellcheck disable=SC1090
    source "$cann_script"
    set -u
}

activate_environment

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

mkdir -p "$REPORT_DIR/precision" "$REPORT_DIR/benchmarks"
STATUS_FILE="$REPORT_DIR/batch_status.tsv"
if [[ ! -f "$STATUS_FILE" ]]; then
    printf 'timestamp\tphase\tmodel\tmode\tstatus\tdetail\n' > "$STATUS_FILE"
fi

log_status() {
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date --iso-8601=seconds)" "$1" "$2" "$3" "$4" "$5" \
        | tee -a "$STATUS_FILE"
}

mapfile -t MODELS < <(find "$MODELS_DIR" -maxdepth 1 -type f -name '*.onnx' -print | sort)
[[ ${#MODELS[@]} -eq 11 ]] || {
    echo "ERROR: expected 11 ONNX models, found ${#MODELS[@]}" >&2
    exit 1
}

record_environment() {
    local output="$REPORT_DIR/environment.txt"
    {
        echo "captured_at=$(date --iso-8601=seconds)"
        echo "hostname=$(hostname)"
        echo "kernel=$(uname -a)"
        echo "conda_env=${CONDA_DEFAULT_ENV:-unknown}"
        echo "python=$(command -v python)"
        python --version
        python -m pip show numpy ais-bench 2>&1 | sed -n '/^Name:/p;/^Version:/p'
        if [[ -f /usr/local/Ascend/ascend-toolkit/latest/compiler/version.info ]]; then
            sed -n '1,20p' /usr/local/Ascend/ascend-toolkit/latest/compiler/version.info
        fi
        echo "load_before=$(uptime)"
        free -h
        npu-smi info
        echo "top_processes_before:"
        ps -eo pid,ppid,user,stat,etime,time,%cpu,%mem,comm,args --sort=-%cpu | head -20
    } > "$output" 2>&1
    sha256sum "${MODELS[@]}" > "$REPORT_DIR/source_onnx_sha256.txt"
    find "$OM_DIR" -maxdepth 1 -type f -name '*.om' -print0 \
        | sort -z | xargs -0 sha256sum > "$REPORT_DIR/source_om_sha256.txt"
    find "$REPORT_DIR/references" -maxdepth 1 -type f -name '*.npz' -print0 \
        | sort -z | xargs -0 sha256sum > "$REPORT_DIR/source_reference_sha256.txt"
}

model_artifacts_valid() {
    local model_path="$1"
    local mode="$2"
    local model om source_hash
    model="$(basename "${model_path%.onnx}")"
    om="$OM_DIR/${model}_${mode}.om"
    source_hash="$OM_DIR/${model}_${mode}.source.sha256"
    [[ -s "$om" && -s "$source_hash" ]] || return 1
    [[ "$(sha256sum "$model_path" | awk '{print $1}')" == "$(tr -d '[:space:]' < "$source_hash")" ]]
}

reference_valid() {
    local model_path="$1"
    local reference_path="$2"
    python - "$model_path" "$reference_path" "$STEPS" "$SEED" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

model, reference = map(Path, sys.argv[1:3])
steps, seed = map(int, sys.argv[3:5])
if not reference.is_file():
    raise SystemExit(1)
digest = hashlib.sha256(model.read_bytes()).hexdigest()
try:
    with np.load(reference, allow_pickle=False) as data:
        metadata = json.loads(str(data["metadata_json"].item()))
except Exception:
    raise SystemExit(1)
valid = (
    metadata.get("onnx_sha256") == digest
    and metadata.get("onnxruntime_version") == "1.20.1"
    and metadata.get("steps") == steps
    and metadata.get("seed") == seed
)
raise SystemExit(0 if valid else 1)
PY
}

precision_valid() {
    local model_path="$1"
    local om_path="$2"
    local report_path="$3"
    python - "$model_path" "$om_path" "$report_path" "$STEPS" "$SEED" "$TIMING_REPEATS" <<'PY'
import hashlib
import json
import math
from pathlib import Path
import sys

model, om, report = map(Path, sys.argv[1:4])
steps, seed, repeats = map(int, sys.argv[4:7])
if not report.is_file() or not om.is_file():
    raise SystemExit(1)
try:
    data = json.loads(report.read_text())
except Exception:
    raise SystemExit(1)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def finite(value):
    if isinstance(value, dict):
        return all(finite(item) for item in value.values())
    if isinstance(value, list):
        return all(finite(item) for item in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(value)
    return True

metadata = data.get("reference_metadata", {})
required_metrics = {
    "amplitude", "harmonics", "harmonic_amplitudes", "noise_amps", "state_out"
}
sections_valid = True
for section_name in ("teacher_forced", "closed_loop"):
    section = data.get(section_name)
    if not isinstance(section, dict):
        sections_valid = False
        break
    outputs = section.get("outputs")
    invariants = section.get("invariants")
    if not isinstance(outputs, dict) or not required_metrics.issubset(outputs):
        sections_valid = False
        break
    if not isinstance(invariants, dict) or invariants.get("all_finite") is not True:
        sections_valid = False
        break
timing = data.get("closed_loop_timing", {})
valid = (
    data.get("om_sha256") == digest(om)
    and metadata.get("onnx_sha256") == digest(model)
    and metadata.get("onnxruntime_version") == "1.20.1"
    and metadata.get("steps") == steps
    and metadata.get("seed") == seed
    and data.get("steps") == steps
    and isinstance(timing, dict)
    and timing.get("repeats") == repeats
    and isinstance(timing.get("median_average_inference_ms"), (int, float))
    and sections_valid
    and finite(data)
)
raise SystemExit(0 if valid else 1)
PY
}

run_one_precision() {
    local model_path="$1"
    local mode="$2"
    local model reference om report log status
    model="$(basename "${model_path%.onnx}")"
    reference="$REPORT_DIR/references/${model}_onnx_reference_${STEPS}.npz"
    om="$OM_DIR/${model}_${mode}.om"
    report="$REPORT_DIR/precision/${model}_${mode}_precision_${STEPS}.json"
    log="$REPORT_DIR/precision/${model}_${mode}.precision.log"
    if ! model_artifacts_valid "$model_path" "$mode"; then
        log_status precision "$model" "$mode" blocked invalid_om_or_source
        return 1
    fi
    if ! reference_valid "$model_path" "$reference"; then
        log_status precision "$model" "$mode" blocked invalid_reference
        return 1
    fi
    if [[ "$FORCE" -eq 0 ]] && precision_valid "$model_path" "$om" "$report"; then
        log_status precision "$model" "$mode" skipped valid
        return 0
    fi
    echo "[precision] $model $mode"
    timeout 900s python "$SCRIPT_DIR/compare_onnx_om_precision.py" om \
        --om "$om" \
        --reference "$reference" \
        --report "$report" \
        --device "$DEVICE" \
        --precision-label "Ascend8T_CANN_8.3.RC1_${mode}_on_Ascend20T" \
        --timing-repeats "$TIMING_REPEATS" > "$log" 2>&1
    status=$?
    if [[ "$status" -eq 0 ]] && precision_valid "$model_path" "$om" "$report"; then
        log_status precision "$model" "$mode" success compared
        return 0
    fi
    log_status precision "$model" "$mode" failed "exit=$status"
    return 1
}

run_precision() {
    local model_path
    for model_path in "${MODELS[@]}"; do
        run_one_precision "$model_path" force_fp16 || true
        run_one_precision "$model_path" mixed_float16 || true
    done
}

benchmark_valid() {
    local log="$1"
    [[ -s "$log" ]] || return 1
    grep -Fq 'NPU_compute_time (ms):' "$log" || return 1
    grep -Fq 'batchsize.mean(1)' "$log"
}

run_one_benchmark() {
    local model_path="$1"
    local mode="$2"
    local model om log status
    model="$(basename "${model_path%.onnx}")"
    om="$OM_DIR/${model}_${mode}.om"
    log="$REPORT_DIR/benchmarks/${model}_${mode}.ais_bench.log"
    if ! model_artifacts_valid "$model_path" "$mode"; then
        log_status benchmark "$model" "$mode" blocked invalid_om_or_source
        return 1
    fi
    if [[ "$FORCE" -eq 0 ]] && benchmark_valid "$log"; then
        log_status benchmark "$model" "$mode" skipped valid
        return 0
    fi
    echo "[benchmark] $model $mode"
    timeout 300s python -m ais_bench \
        --model "$om" \
        --loop "$AIS_LOOP" \
        --warmup_count "$AIS_WARMUP" \
        --pure_data_type random \
        --batchsize 1 \
        --device "$DEVICE" > "$log" 2>&1
    status=$?
    if [[ "$status" -eq 0 ]] && benchmark_valid "$log"; then
        log_status benchmark "$model" "$mode" success measured
        return 0
    fi
    log_status benchmark "$model" "$mode" failed "exit=$status"
    return 1
}

run_benchmarks() {
    local model_path
    for model_path in "${MODELS[@]}"; do
        run_one_benchmark "$model_path" force_fp16 || true
        run_one_benchmark "$model_path" mixed_float16 || true
    done
}

run_summary() {
    python "$SCRIPT_DIR/summarize_all_om_results.py" \
        --models-dir "$MODELS_DIR" \
        --om-dir "$OM_DIR" \
        --report-dir "$REPORT_DIR" \
        --output-prefix "$REPORT_DIR/summary" \
        --steps "$STEPS" \
        --seed "$SEED" \
        --timing-repeats "$TIMING_REPEATS" \
        --runtime-only \
        --title "Ascend 20T Prebuilt DDSP-VST FP16 vs Mixed-precision Results"
}

finish_environment() {
    {
        echo "captured_after=$(date --iso-8601=seconds)"
        echo "load_after=$(uptime)"
        echo "top_processes_after:"
        ps -eo pid,ppid,user,stat,etime,time,%cpu,%mem,comm,args --sort=-%cpu | head -20
    } >> "$REPORT_DIR/environment.txt" 2>&1
}

write_artifact_manifest() {
    find "$REPORT_DIR" -type f ! -name SHA256SUMS.txt -print0 \
        | sort -z | xargs -0 sha256sum > "$REPORT_DIR/SHA256SUMS.txt"
}

result=0
case "$PHASE" in
    all)
        record_environment
        run_precision
        run_benchmarks
        run_summary || result=$?
        finish_environment
        write_artifact_manifest
        ;;
    precision)
        run_precision
        ;;
    benchmark)
        run_benchmarks
        ;;
    summary)
        run_summary || result=$?
        write_artifact_manifest
        ;;
esac

exit "$result"
