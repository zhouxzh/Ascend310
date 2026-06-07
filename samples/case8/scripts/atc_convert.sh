#!/usr/bin/env bash
set -euo pipefail

SOC_VERSION="${SOC_VERSION:-Ascend310B4}"
IMG_SIZE="${IMG_SIZE:-640}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/atc_convert.sh
  bash scripts/atc_convert.sh <model.onnx> [output_prefix]

Without arguments, converts every models/*.onnx file to a same-name .om file.

Examples:
  SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
  SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh models/YOLOv10n_gestures.onnx models/YOLOv10n_gestures
EOF
}

read_metadata() {
  local metadata="$1"

  python3 - "${metadata}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    metadata = json.load(f)

input_shapes = metadata.get("input_shapes") or {}
inputs = metadata.get("inputs") or list(input_shapes)
if not inputs:
    raise SystemExit("metadata has no inputs")

name = inputs[0]
shape = input_shapes.get(name)
if not shape:
    raise SystemExit(f"metadata has no shape for input {name!r}")
if len(shape) != 4:
    raise SystemExit(f"expected NCHW rank-4 input, got {shape}")

print(name)
print(",".join(str(dim) for dim in shape))
PY
}

convert_one() {
  local onnx_model="$1"
  local output_prefix="${2:-${onnx_model%.onnx}}"
  output_prefix="${output_prefix%.om}"

  local metadata="${METADATA:-${onnx_model%.onnx}_metadata.json}"
  local metadata_input_name=""
  local metadata_input_shape=""
  local metadata_output=""

  if [[ ! -f "${onnx_model}" ]]; then
    echo "[ATC] ERROR: ONNX model not found: ${onnx_model}" >&2
    return 1
  fi

  if [[ -f "${metadata}" ]]; then
    metadata_output="$(read_metadata "${metadata}")"
    metadata_input_name="$(sed -n '1p' <<<"${metadata_output}")"
    metadata_input_shape="$(sed -n '2p' <<<"${metadata_output}")"
  fi

  local input_name="${INPUT_NAME:-${metadata_input_name:-images}}"
  local input_dims="${INPUT_DIMS:-${metadata_input_shape:-1,3,${IMG_SIZE},${IMG_SIZE}}}"

  echo "[ATC] model:       ${onnx_model}"
  echo "[ATC] output:      ${output_prefix}.om"
  echo "[ATC] soc_version: ${SOC_VERSION}"
  echo "[ATC] input_shape: ${input_name}:${input_dims}"

  atc \
    --framework=5 \
    --model="${onnx_model}" \
    --output="${output_prefix}" \
    --input_format=NCHW \
    --input_shape="${input_name}:${input_dims}" \
    --soc_version="${SOC_VERSION}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  shopt -s nullglob
  onnx_models=(models/*.onnx)
  shopt -u nullglob

  if [[ ${#onnx_models[@]} -eq 0 ]]; then
    echo "[ATC] ERROR: no ONNX models found under models/" >&2
    exit 1
  fi

  for onnx_model in "${onnx_models[@]}"; do
    convert_one "${onnx_model}" "${onnx_model%.onnx}"
  done
  exit 0
fi

if [[ $# -gt 2 ]]; then
  usage >&2
  exit 1
fi

convert_one "$1" "${2:-${1%.onnx}}"
