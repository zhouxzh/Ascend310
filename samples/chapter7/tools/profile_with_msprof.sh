#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/profile_with_msprof.sh --name baseline -- python3 01_baseline_resnet_sync.py --runs 200

Options:
  --name NAME       Profile output name under outputs/msprof.
  --output DIR      Output root. Default: this chapter's outputs/msprof.
  --                Command to run under msprof.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

NAME="run"
OUTPUT_ROOT="${CHAPTER_DIR}/outputs/msprof"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --output)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "Missing command after --" >&2
  usage >&2
  exit 2
fi

if ! command -v msprof >/dev/null 2>&1; then
  echo "msprof not found. Run this on Ascend 310B after sourcing CANN set_env.sh." >&2
  exit 1
fi

mkdir -p "${OUTPUT_ROOT}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${OUTPUT_ROOT}/${NAME}-${STAMP}"
APP_CMD="$*"

echo "msprof output: ${OUT_DIR}"
echo "application: ${APP_CMD}"

msprof \
  --output="${OUT_DIR}" \
  --application="${APP_CMD}" \
  --task-time=on \
  --aic-metrics=PipeUtilization \
  --sys-hardware-mem=on

echo "msprof finished: ${OUT_DIR}"
