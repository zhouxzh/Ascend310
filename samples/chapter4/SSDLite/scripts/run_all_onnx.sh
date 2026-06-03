#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODEL_PATTERN="${CASE_DIR}/weights/*.onnx"
LOG_DIR="${CASE_DIR}/logs/run_all_onnx"
MAX_SAMPLES="50"
CONTINUE_ON_ERROR=0
EXTRA_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/run_all_onnx.sh [options] [-- extra inference_cpu.py args]

Options:
  --pattern GLOB           ONNX file glob. Default: weights/*.onnx
  --log-dir DIR            Directory for per-model logs. Default: logs/run_all_onnx
  --max-samples N          Validation sample limit. Default: 50. Use 0 for all samples.
  --continue-on-error      Continue after a model fails.
  -h, --help               Show this help.

Examples:
  scripts/run_all_onnx.sh
  scripts/run_all_onnx.sh --max-samples 0
  scripts/run_all_onnx.sh --pattern 'weights/ssd320_mobilenetv4*.onnx'
  scripts/run_all_onnx.sh -- --skip-eval --num-visualizations 5
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pattern)
      MODEL_PATTERN="$2"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --max-samples)
      MAX_SAMPLES="$2"
      shift 2
      ;;
    --continue-on-error)
      CONTINUE_ON_ERROR=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA_ARGS+=("$@")
      break
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$MODEL_PATTERN" in
  /*) ;;
  *) MODEL_PATTERN="${CASE_DIR}/${MODEL_PATTERN}" ;;
esac

case "$LOG_DIR" in
  /*) ;;
  *) LOG_DIR="${CASE_DIR}/${LOG_DIR}" ;;
esac

mkdir -p "$LOG_DIR"

shopt -s nullglob
models=( $MODEL_PATTERN )
shopt -u nullglob

if [[ ${#models[@]} -eq 0 ]]; then
  echo "No ONNX files matched: $MODEL_PATTERN" >&2
  exit 1
fi

echo "Found ${#models[@]} ONNX model(s)."
echo "Logs: $LOG_DIR"

success=0
failed=0

for model in "${models[@]}"; do
  name="$(basename "$model" .onnx)"
  backbone="${name#ssd320_}"
  backbone="${backbone#ssd_}"
  log_file="${LOG_DIR}/${name}.log"

  echo "Running ${name} (backbone=${backbone})"
  (
    cd "$CASE_DIR"
    python scripts/inference_cpu.py \
      --model "$model" \
      --backbone "$backbone" \
      --max-samples "$MAX_SAMPLES" \
      "${EXTRA_ARGS[@]}"
  ) 2>&1 | tee "$log_file"

  status=${PIPESTATUS[0]}
  if [[ $status -eq 0 ]]; then
    success=$((success + 1))
  else
    failed=$((failed + 1))
    echo "Failed: ${name}, exit=${status}. Log: ${log_file}" >&2
    if [[ $CONTINUE_ON_ERROR -ne 1 ]]; then
      echo "Stop on first failure. Use --continue-on-error to run remaining models." >&2
      exit "$status"
    fi
  fi
done

echo "Done. success=${success}, failed=${failed}, total=${#models[@]}"
if [[ $failed -eq 0 ]]; then
  exit 0
fi
exit 1
