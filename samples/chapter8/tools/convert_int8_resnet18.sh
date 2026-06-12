#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  cd samples/chapter8
  bash tools/convert_int8_resnet18.sh [options]

Options:
  --onnx PATH             Input FP32 ONNX model. Default: model/resnet18_tiny_imagenet.onnx
  --deploy-onnx PATH      AMCT deploy ONNX model. Default: model/resnet18_tiny_imagenet_int8_deploy.onnx
  --output PREFIX         Output OM prefix or .om path. Default: model/resnet18_tiny_imagenet_int8
  --calib-list PATH       Calibration list. Default: calibration/calib_list.txt
  --samples N             Calibration samples used by PTQ. Default: 50
  --input-name NAME       ONNX input name. Default: input.1
  --input-shape S         Input shape after input name. Default: 1,3,64,64
  --amct-opset N          Convert FP32 ONNX to this opset before AMCT. Use 0 to disable. Default: 11
  --soc VERSION           ATC soc_version. Default: Ascend310B4
  --python PATH           Python used for AMCT quantization.
  --work-dir PATH         AMCT intermediate directory. Default: outputs/int8_amct
  --skip-layers LIST      Comma-separated AMCT layer names to skip.
  --force-quant           Regenerate AMCT deploy ONNX even if it already exists.
  --skip-atc              Only generate deploy ONNX and skip ATC conversion.
  --precision-mode M      Optional ATC precision mode. Default: unset.
  --extra-atc-arg ARG     Extra ATC argument. Can be repeated.
  -h, --help              Show this help.

This script performs Ascend AMCT static PTQ first, then calls ATC:
  FP32 ONNX + calibration data -> AMCT deploy ONNX -> INT8 OM
Python is used only for AMCT quantization. ATC is called directly by this shell script.
For AMCT deploy ONNX, this script does not set --precision_mode by default.
Do not replace the AMCT deploy ONNX with an ONNX Runtime QDQ/QOperator model;
this CANN/ATC path does not parse QuantizeLinear/DequantizeLinear/QLinearConv.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ONNX="${CHAPTER_DIR}/model/resnet18_tiny_imagenet.onnx"
DEPLOY_ONNX="${CHAPTER_DIR}/model/resnet18_tiny_imagenet_int8_deploy.onnx"
OUTPUT="${CHAPTER_DIR}/model/resnet18_tiny_imagenet_int8"
CALIB_LIST="${CHAPTER_DIR}/calibration/calib_list.txt"
INPUT_NAME="input.1"
INPUT_SHAPE="1,3,64,64"
SOC_VERSION="Ascend310B4"
PYTHON_BIN="${PYTHON:-}"
SAMPLES=50
AMCT_OPSET=11
WORK_DIR="${CHAPTER_DIR}/outputs/int8_amct"
SKIP_LAYERS=""
FORCE_QUANT=0
SKIP_ATC=0
PRECISION_MODE=""
EXTRA_ATC_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --onnx)
      ONNX="$2"
      shift 2
      ;;
    --deploy-onnx|--int8-onnx)
      DEPLOY_ONNX="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --calib-list)
      CALIB_LIST="$2"
      shift 2
      ;;
    --samples)
      SAMPLES="$2"
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
    --amct-opset)
      AMCT_OPSET="$2"
      shift 2
      ;;
    --soc)
      SOC_VERSION="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    --work-dir)
      WORK_DIR="$2"
      shift 2
      ;;
    --skip-layers)
      SKIP_LAYERS="$2"
      shift 2
      ;;
    --force-quant)
      FORCE_QUANT=1
      shift
      ;;
    --skip-atc)
      SKIP_ATC=1
      shift
      ;;
    --precision-mode)
      PRECISION_MODE="$2"
      shift 2
      ;;
    --extra-atc-arg)
      EXTRA_ATC_ARGS+=("$2")
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

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "/home/HwHiAiUser/.conda/envs/npu/bin/python" ]]; then
    PYTHON_BIN="/home/HwHiAiUser/.conda/envs/npu/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if [[ ! -f "${ONNX}" ]]; then
  echo "ONNX model not found: ${ONNX}" >&2
  echo "Run from samples/chapter8:" >&2
  echo "  python3 tools/download_model.py" >&2
  exit 1
fi

if [[ ! -f "${CALIB_LIST}" ]]; then
  echo "Calibration list not found: ${CALIB_LIST}" >&2
  echo "Run from samples/chapter8:" >&2
  echo "  python3 01_collect_calibration_list.py --count 50" >&2
  exit 1
fi

mkdir -p "$(dirname "${DEPLOY_ONNX}")"
if [[ ! -f "${DEPLOY_ONNX}" || "${FORCE_QUANT}" -eq 1 ]]; then
  QUANT_CMD=(
    "${PYTHON_BIN}"
    "${SCRIPT_DIR}/quantize_int8_resnet18.py"
    "--onnx" "${ONNX}"
    "--deploy-onnx" "${DEPLOY_ONNX}"
    "--work-dir" "${WORK_DIR}"
    "--calib-list" "${CALIB_LIST}"
    "--samples" "${SAMPLES}"
    "--input-name" "${INPUT_NAME}"
    "--amct-opset" "${AMCT_OPSET}"
  )
  IFS=',' read -r -a SKIP_LAYERS_ARRAY <<< "${SKIP_LAYERS}"
  if [[ ${#SKIP_LAYERS_ARRAY[@]} -gt 0 && -n "${SKIP_LAYERS_ARRAY[0]}" ]]; then
    QUANT_CMD+=("--skip-layers")
    for layer_name in "${SKIP_LAYERS_ARRAY[@]}"; do
      QUANT_CMD+=("${layer_name}")
    done
  fi

  echo "Run AMCT INT8 PTQ quantization"
  printf 'command:'
  printf ' %q' "${QUANT_CMD[@]}"
  printf '\n'
  TORCH_DEVICE_BACKEND_AUTOLOAD=0 "${QUANT_CMD[@]}"
else
  echo "AMCT deploy ONNX exists, skip quantization: ${DEPLOY_ONNX}"
  echo "Use --force-quant to regenerate it."
fi

if [[ "${SKIP_ATC}" -eq 1 ]]; then
  echo "Skip ATC conversion. AMCT deploy ONNX is ready: ${DEPLOY_ONNX}"
  exit 0
fi

ATC_CMD=(
  atc
  "--model=${DEPLOY_ONNX}"
  "--framework=5"
  "--output=${OUTPUT}"
  "--input_format=NCHW"
  "--input_shape=${INPUT_NAME}:${INPUT_SHAPE}"
  "--soc_version=${SOC_VERSION}"
  "--log=info"
)

if [[ -n "${PRECISION_MODE}" ]]; then
  ATC_CMD+=("--precision_mode=${PRECISION_MODE}")
fi

if [[ ${#EXTRA_ATC_ARGS[@]} -gt 0 ]]; then
  ATC_CMD+=("${EXTRA_ATC_ARGS[@]}")
fi

echo "ATC INT8 OM conversion"
echo "  deploy onnx: ${DEPLOY_ONNX}"
echo "  output:      ${OUTPUT}.om"
echo "  input_shape: ${INPUT_NAME}:${INPUT_SHAPE}"
echo "  soc:         ${SOC_VERSION}"
echo "  precision:   ${PRECISION_MODE:-'(unset)'}"
echo
printf 'command:'
printf ' %q' "${ATC_CMD[@]}"
printf '\n'

if ! command -v atc >/dev/null 2>&1; then
  echo "atc not found. Source CANN set_env.sh first, for example:" >&2
  echo "  source /usr/local/Ascend/ascend-toolkit/set_env.sh" >&2
  exit 1
fi

mkdir -p "$(dirname "${OUTPUT}")"
"${ATC_CMD[@]}"

echo "INT8 OM generated: ${OUTPUT}.om"
