#!/usr/bin/env bash
set -euo pipefail

# Sweep calibration sample counts for INT8 PTQ.
#
# Run from samples/chapter8:
#   bash tools/sweep_calibration_samples.sh
#   bash tools/sweep_calibration_samples.sh 20 50 100 200 400

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${CHAPTER_DIR}"

if [ "$#" -gt 0 ]; then
  SAMPLE_COUNTS=("$@")
else
  read -r -a SAMPLE_COUNTS <<< "${SAMPLE_COUNTS:-20 50 100 200 400}"
fi

ONNX_MODEL="${ONNX_MODEL:-model/resnet18_tiny_imagenet.onnx}"
FP32_OM="${FP32_OM:-model/resnet18_tiny_imagenet_fp32.om}"
FP16_OM="${FP16_OM:-model/resnet18_tiny_imagenet_fp16.om}"
CALIB_LIST="${CALIB_LIST:-data/calib_list.txt}"
VAL_LIST="${VAL_LIST:-data/val_list.txt}"
OUT_DIR="${OUT_DIR:-outputs/calibration_sweep}"
SOC_VERSION="${SOC_VERSION:-Ascend310B4}"
VAL_SAMPLES="${VAL_SAMPLES:-0}"
SWEEP_SEED="${SWEEP_SEED:-2024}"

# Keep ATC memory usage friendly on 310B boards.
export TE_PARALLEL_COMPILER="${TE_PARALLEL_COMPILER:-1}"
export MAX_COMPILE_CORE_NUMBER="${MAX_COMPILE_CORE_NUMBER:-1}"

mkdir -p "${OUT_DIR}"
SUMMARY="${OUT_DIR}/summary.csv"
FIRST_ROW=1

for samples in "${SAMPLE_COUNTS[@]}"; do
  POINT_DIR="${OUT_DIR}/samples_${samples}"
  WORK_DIR="${POINT_DIR}/int8_amct"
  SUBSET_LIST="$(dirname "${CALIB_LIST}")/calib_sweep_${samples}.txt"
  DEPLOY_ONNX="${POINT_DIR}/resnet18_tiny_imagenet_int8_s${samples}_deploy.onnx"
  INT8_PREFIX="${POINT_DIR}/resnet18_tiny_imagenet_int8_s${samples}"
  INT8_OM="${INT8_PREFIX}.om"
  REPORT="${POINT_DIR}/accuracy_compare.json"

  mkdir -p "${POINT_DIR}" "${WORK_DIR}"

  echo
  echo "=== calibration samples: ${samples} ==="

  python tools/calibration_sweep_helper.py make-subset \
    --source "${CALIB_LIST}" \
    --output "${SUBSET_LIST}" \
    --count "${samples}" \
    --seed "${SWEEP_SEED}"

  python 03_prepare_quantization.py \
    --onnx "${ONNX_MODEL}" \
    --work-dir "${WORK_DIR}" \
    --calib-list "${SUBSET_LIST}"

  python 04_calibrate_quantization.py \
    --work-dir "${WORK_DIR}" \
    --calib-list "${SUBSET_LIST}" \
    --deploy-onnx "${DEPLOY_ONNX}"

  atc \
    --model="${DEPLOY_ONNX}" \
    --framework=5 \
    --output="${INT8_PREFIX}" \
    --input_format=NCHW \
    --input_shape="input.1:1,3,64,64" \
    --soc_version="${SOC_VERSION}" \
    --log=info

  python 05_validate_accuracy.py \
    --val-list "${VAL_LIST}" \
    --samples "${VAL_SAMPLES}" \
    --om-models "${FP32_OM}" "${FP16_OM}" "${INT8_OM}" \
    --labels fp32 fp16 "int8_s${samples}" \
    --output "${REPORT}"

  if [ "${FIRST_ROW}" -eq 1 ]; then
    python tools/calibration_sweep_helper.py append-summary \
      --init \
      --report "${REPORT}" \
      --summary "${SUMMARY}" \
      --calibration-samples "${samples}" \
      --int8-label "int8_s${samples}" \
      --int8-model "${INT8_OM}"
    FIRST_ROW=0
  else
    python tools/calibration_sweep_helper.py append-summary \
      --report "${REPORT}" \
      --summary "${SUMMARY}" \
      --calibration-samples "${samples}" \
      --int8-label "int8_s${samples}" \
      --int8-model "${INT8_OM}"
  fi
done

echo
echo "summary saved: ${SUMMARY}"
echo
echo "Tip: plot calibration_samples vs int8_top1_pct/int8_top5_pct from summary.csv to find the accuracy plateau."
