#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MODEL="$ROOT_DIR/models/ddsp_vst/Violin.onnx"
OUTPUT=""
INPUT_SHAPE="state:512;f0_scaled:1;pw_scaled:1"
INPUT_FORMAT="ND"
SOC_VERSION="Ascend310B1"
LOG_LEVEL="info"
PRECISION_MODE=""
PRECISION_MODE_V2=""
LOG_FILE=""
SUMMARY_FILE=""
DRY_RUN=0

usage() {
    cat <<'EOF'
Convert an ONNX model to Ascend OM with ATC and inspect compatibility logs.

Usage:
  bash tools/convert_onnx_to_om.sh [options]

Options:
  --model PATH          ONNX model path.
                        Default: models/ddsp_vst/Violin.onnx
  --output PATH         OM output prefix or .om path.
                        Default: models/om/<model-stem>
  --input-shape VALUE   ATC input shape specification.
                        Default: state:512;f0_scaled:1;pw_scaled:1
  --input-format VALUE  ATC input format. Default: ND
  --soc-version VALUE   ATC SoC version. Default: Ascend310B1
  --log-level VALUE     ATC log level. Default: info
  --precision-mode VALUE
                        Legacy ATC precision mode, for example allow_mix_precision.
  --precision-mode-v2 VALUE
                        ATC precision mode v2, for example mixed_float16.
  --log-file PATH       Captured ATC output log.
                        Default: <output>.atc.log
  --summary-file PATH   Compatibility summary.
                        Default: <output>.atc.summary.txt
  --dry-run             Print the resolved ATC command without running it.
  -h, --help            Show this help.

Example:
  bash tools/convert_onnx_to_om.sh \
    --model models/ddsp_vst/Violin.onnx \
    --soc-version Ascend310B1
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 2
}

require_value() {
    local option="$1"
    local value="${2:-}"
    [[ -n "$value" ]] || die "$option requires a value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            require_value "$1" "${2:-}"
            MODEL="$2"
            shift 2
            ;;
        --output)
            require_value "$1" "${2:-}"
            OUTPUT="$2"
            shift 2
            ;;
        --input-shape)
            require_value "$1" "${2:-}"
            INPUT_SHAPE="$2"
            shift 2
            ;;
        --input-format)
            require_value "$1" "${2:-}"
            INPUT_FORMAT="$2"
            shift 2
            ;;
        --soc-version)
            require_value "$1" "${2:-}"
            SOC_VERSION="$2"
            shift 2
            ;;
        --log-level)
            require_value "$1" "${2:-}"
            LOG_LEVEL="$2"
            shift 2
            ;;
        --precision-mode)
            require_value "$1" "${2:-}"
            PRECISION_MODE="$2"
            shift 2
            ;;
        --precision-mode-v2)
            require_value "$1" "${2:-}"
            PRECISION_MODE_V2="$2"
            shift 2
            ;;
        --log-file)
            require_value "$1" "${2:-}"
            LOG_FILE="$2"
            shift 2
            ;;
        --summary-file)
            require_value "$1" "${2:-}"
            SUMMARY_FILE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

if [[ -n "$PRECISION_MODE" && -n "$PRECISION_MODE_V2" ]]; then
    die "use only one of --precision-mode and --precision-mode-v2"
fi

[[ -f "$MODEL" ]] || die "ONNX model not found: $MODEL"
MODEL="$(realpath "$MODEL")"
MODEL_STEM="$(basename "${MODEL%.onnx}")"

if [[ -z "$OUTPUT" ]]; then
    OUTPUT="$ROOT_DIR/models/om/$MODEL_STEM"
fi
OUTPUT="${OUTPUT%.om}"
OUTPUT="$(realpath -m "$OUTPUT")"
OM_FILE="$OUTPUT.om"

if [[ -z "$LOG_FILE" ]]; then
    LOG_FILE="$OUTPUT.atc.log"
fi
LOG_FILE="$(realpath -m "$LOG_FILE")"

if [[ -z "$SUMMARY_FILE" ]]; then
    SUMMARY_FILE="$OUTPUT.atc.summary.txt"
fi
SUMMARY_FILE="$(realpath -m "$SUMMARY_FILE")"

mkdir -p "$(dirname "$OUTPUT")" "$(dirname "$LOG_FILE")" "$(dirname "$SUMMARY_FILE")"

activate_conda_base() {
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
    [[ -n "$conda_script" ]] || die "conda profile script not found"
    # shellcheck disable=SC1090
    source "$conda_script"
    conda activate "${CONDA_ENV:-base}" || die "failed to activate conda environment"
}

source_cann_env() {
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
    [[ -n "$cann_script" ]] || die "CANN set_env.sh not found"
    # shellcheck disable=SC1090
    source "$cann_script"
}

activate_conda_base
source_cann_env

ATC_BIN="$(command -v atc || true)"
[[ -n "$ATC_BIN" ]] || die "atc not found after sourcing CANN environment"

ATC_COMMAND=(
    "$ATC_BIN"
    "--model=$MODEL"
    "--framework=5"
    "--output=$OUTPUT"
    "--input_format=$INPUT_FORMAT"
    "--input_shape=$INPUT_SHAPE"
    "--soc_version=$SOC_VERSION"
    "--log=$LOG_LEVEL"
)

if [[ -n "$PRECISION_MODE" ]]; then
    ATC_COMMAND+=("--precision_mode=$PRECISION_MODE")
fi
if [[ -n "$PRECISION_MODE_V2" ]]; then
    ATC_COMMAND+=("--precision_mode_v2=$PRECISION_MODE_V2")
fi

print_command() {
    printf 'ATC command:'
    printf ' %q' "${ATC_COMMAND[@]}"
    printf '\n'
}

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Model: $MODEL"
    echo "Output: $OM_FILE"
    print_command
    exit 0
fi

MARKER="$(mktemp "$(dirname "$OUTPUT")/.atc-marker.XXXXXX")"
trap 'rm -f "$MARKER"' EXIT

{
    echo "ATC conversion started: $(date --iso-8601=seconds)"
    echo "Host: $(hostname)"
    echo "Conda environment: ${CONDA_DEFAULT_ENV:-unknown}"
    echo "Python: $(command -v python || true)"
    echo "CANN home: ${ASCEND_TOOLKIT_HOME:-unknown}"
    echo "ATC: $ATC_BIN"
    echo "Model: $MODEL"
    echo "Output: $OM_FILE"
    echo "Input shape: $INPUT_SHAPE"
    echo "Input format: $INPUT_FORMAT"
    echo "SoC version: $SOC_VERSION"
    echo "Precision mode: ${PRECISION_MODE:-ATC default}"
    echo "Precision mode v2: ${PRECISION_MODE_V2:-ATC default}"
    print_command
} >"$LOG_FILE"

set +e
"${ATC_COMMAND[@]}" 2>&1 | tee -a "$LOG_FILE"
ATC_STATUS=${PIPESTATUS[0]}
set -e

echo "ATC exit code: $ATC_STATUS" | tee -a "$LOG_FILE"
echo "ATC conversion finished: $(date --iso-8601=seconds)" | tee -a "$LOG_FILE"

DETAIL_LOGS=()
for log_root in "$HOME/ascend/log" "$HOME/var/log/npu"; do
    if [[ -d "$log_root" ]]; then
        while IFS= read -r -d '' detail_log; do
            DETAIL_LOGS+=("$detail_log")
        done < <(find "$log_root" -type f -newer "$MARKER" -print0 2>/dev/null)
    fi
done

SCAN_FILES=("$LOG_FILE")
if [[ ${#DETAIL_LOGS[@]} -gt 0 ]]; then
    SCAN_FILES+=("${DETAIL_LOGS[@]}")
fi

OPERATOR_PATTERN='unsupported|not supported|not support|No parser is registered for Op|No supported Ops kernel|No OpKernel|failed to select kernel|select.*kernel.*failed|op type.*not registered|cannot find.*op|EZ300[0-9]|E19010'
ERROR_PATTERN='(^|[^[:alpha:]])(ERROR|FATAL)([^[:alpha:]]|$)|Traceback|ATC run failed|E[0-9]{4,}'

OPERATOR_MATCHES="$(grep -EinH -m 200 "$OPERATOR_PATTERN" "${SCAN_FILES[@]}" 2>/dev/null || true)"
ERROR_MATCHES="$(grep -EinH -m 200 "$ERROR_PATTERN" "${SCAN_FILES[@]}" 2>/dev/null || true)"

OM_UPDATED="no"
if [[ -f "$OM_FILE" && "$OM_FILE" -nt "$MARKER" ]]; then
    OM_UPDATED="yes"
fi

{
    echo "ATC_EXIT_CODE=$ATC_STATUS"
    echo "OM_FILE=$OM_FILE"
    echo "OM_UPDATED=$OM_UPDATED"
    echo "CAPTURED_LOG=$LOG_FILE"
    echo "SOC_VERSION=$SOC_VERSION"
    echo "INPUT_SHAPE=$INPUT_SHAPE"
    echo "PRECISION_MODE=${PRECISION_MODE:-ATC default}"
    echo "PRECISION_MODE_V2=${PRECISION_MODE_V2:-ATC default}"
    echo
    echo "DETAIL_LOGS_CREATED=${#DETAIL_LOGS[@]}"
    if [[ ${#DETAIL_LOGS[@]} -gt 0 ]]; then
        printf '%s\n' "${DETAIL_LOGS[@]}"
    fi
    echo
    if [[ -n "$OPERATOR_MATCHES" ]]; then
        echo "OPERATOR_COMPATIBILITY=potential incompatibility found"
        echo "$OPERATOR_MATCHES"
    else
        echo "OPERATOR_COMPATIBILITY=no incompatibility pattern found"
    fi
    echo
    if [[ -n "$ERROR_MATCHES" ]]; then
        echo "ERROR_LINES=found"
        echo "$ERROR_MATCHES"
    else
        echo "ERROR_LINES=none"
    fi
} >"$SUMMARY_FILE"

cat "$SUMMARY_FILE"

if [[ "$ATC_STATUS" -ne 0 ]]; then
    echo "ATC conversion failed. Inspect: $LOG_FILE" >&2
    exit "$ATC_STATUS"
fi

if [[ "$OM_UPDATED" != "yes" ]]; then
    echo "ATC returned success but did not create a new OM file: $OM_FILE" >&2
    exit 3
fi

echo "OM conversion succeeded: $OM_FILE"
echo "ATC log: $LOG_FILE"
echo "Compatibility summary: $SUMMARY_FILE"
