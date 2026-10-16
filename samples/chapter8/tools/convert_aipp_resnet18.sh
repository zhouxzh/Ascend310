#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/convert_aipp_resnet18.sh [options]

Options:
  --onnx PATH        Input ONNX model. Default: model/resnet18_tiny_imagenet.onnx
  --aipp-cfg PATH    Static AIPP config. Default: model/resnet18_rgb_static_aipp.cfg
  --output PREFIX    Output OM prefix or .om path. Default: model/resnet18_tiny_imagenet_aipp
  --soc VERSION      ATC soc_version. Default: Ascend310B4
  -h, --help         Show this help.

Run this script on Ascend 310B after sourcing CANN set_env.sh.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ONNX="${CHAPTER_DIR}/model/resnet18_tiny_imagenet.onnx"
AIPP_CFG="${CHAPTER_DIR}/model/resnet18_rgb_static_aipp.cfg"
OUTPUT="${CHAPTER_DIR}/model/resnet18_tiny_imagenet_aipp"
SOC_VERSION="Ascend310B4"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --onnx)
      ONNX="$2"
      shift 2
      ;;
    --aipp-cfg)
      AIPP_CFG="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --soc)
      SOC_VERSION="$2"
      shift 2
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

if ! command -v atc >/dev/null 2>&1; then
  echo "atc not found. Source CANN set_env.sh first, for example:" >&2
  echo "  source /usr/local/Ascend/ascend-toolkit/set_env.sh" >&2
  exit 1
fi

if [[ ! -f "${ONNX}" ]]; then
  echo "ONNX model not found: ${ONNX}" >&2
  echo "Run: python3 tools/download_model.py --all" >&2
  exit 1
fi

if [[ ! -f "${AIPP_CFG}" ]]; then
  echo "AIPP config not found: ${AIPP_CFG}" >&2
  exit 1
fi

if [[ "${OUTPUT}" == *.om ]]; then
  OUTPUT="${OUTPUT%.om}"
fi
mkdir -p "$(dirname "${OUTPUT}")"

echo "ATC AIPP conversion"
echo "  onnx:     ${ONNX}"
echo "  aipp cfg: ${AIPP_CFG}"
echo "  output:   ${OUTPUT}.om"
echo "  soc:      ${SOC_VERSION}"

atc \
  --model="${ONNX}" \
  --framework=5 \
  --output="${OUTPUT}" \
  --soc_version="${SOC_VERSION}" \
  --insert_op_conf="${AIPP_CFG}"

echo "AIPP OM generated: ${OUTPUT}.om"
