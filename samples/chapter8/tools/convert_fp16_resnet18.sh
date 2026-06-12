#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  cd samples/chapter8
  bash tools/convert_fp16_resnet18.sh [options]

Options:
  --onnx PATH        Input ONNX model. Default: model/resnet18_tiny_imagenet.onnx
  --output PREFIX    Output OM prefix or .om path. Default: model/resnet18_tiny_imagenet_fp16
  --input-name NAME  ONNX input name. Default: input.1
  --input-shape S    Input shape after input name. Default: 1,3,64,64
  --soc VERSION      ATC soc_version. Default: Ascend310B4
  -h, --help         Show this help.

Run this script on Ascend 310B after sourcing CANN set_env.sh.
This script only calls atc; it does not create or activate Python environments.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ONNX="${CHAPTER_DIR}/model/resnet18_tiny_imagenet.onnx"
OUTPUT="${CHAPTER_DIR}/model/resnet18_tiny_imagenet_fp16"
INPUT_NAME="input.1"
INPUT_SHAPE="1,3,64,64"
SOC_VERSION="Ascend310B4"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --onnx)
      ONNX="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --input-name)
      INPUT_NAME="$2"
      shift 2
      ;;
    --input-shape)
      INPUT_SHAPE="$2"
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

if [[ "${OUTPUT}" == *.om ]]; then
  OUTPUT="${OUTPUT%.om}"
fi

if ! command -v atc >/dev/null 2>&1; then
  echo "atc not found. Source CANN set_env.sh first, for example:" >&2
  echo "  source /usr/local/Ascend/ascend-toolkit/set_env.sh" >&2
  exit 1
fi

if [[ ! -f "${ONNX}" ]]; then
  echo "ONNX model not found: ${ONNX}" >&2
  echo "Run from samples/chapter8:" >&2
  echo "  python3 tools/download_model.py" >&2
  exit 1
fi

mkdir -p "$(dirname "${OUTPUT}")"

echo "ATC FP16 conversion"
echo "  onnx:        ${ONNX}"
echo "  output:      ${OUTPUT}.om"
echo "  input_shape: ${INPUT_NAME}:${INPUT_SHAPE}"
echo "  soc:         ${SOC_VERSION}"

atc \
  --model="${ONNX}" \
  --framework=5 \
  --output="${OUTPUT}" \
  --input_format=NCHW \
  --input_shape="${INPUT_NAME}:${INPUT_SHAPE}" \
  --soc_version="${SOC_VERSION}" \
  --precision_mode=allow_fp32_to_fp16 \
  --log=info

echo "FP16 OM generated: ${OUTPUT}.om"
