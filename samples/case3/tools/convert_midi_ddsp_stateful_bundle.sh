#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPORT_DIR="${1:-$ROOT_DIR/models/midi_ddsp/stateful_v2/onnx}"
BUNDLE_DIR="${2:-$ROOT_DIR/models/midi_ddsp/bundles/google-urmp-stateful-v2-mixed_float16}"
EXPORT_MANIFEST="$EXPORT_DIR/export_manifest.json"
SOC_VERSION="${SOC_VERSION:-Ascend310B4}"
LOG_DIR="$ROOT_DIR/models/conversion_logs/midi_ddsp_stateful_v2"

[[ -f "$EXPORT_MANIFEST" ]] || {
    echo "Missing export manifest: $EXPORT_MANIFEST" >&2
    exit 2
}

mkdir -p "$BUNDLE_DIR" "$LOG_DIR"

mapfile -t COMPONENTS < <(
    python - "$EXPORT_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for name, component in manifest["components"].items():
    shapes = []
    for item in component["inputs"]:
        shape = ",".join(str(value) for value in item["shape"])
        shapes.append(f'{item["name"]}:{shape}')
    print(f'{name}\t{component["file"]}\t{";".join(shapes)}')
PY
)

for row in "${COMPONENTS[@]}"; do
    IFS=$'\t' read -r name onnx_file input_shape <<<"$row"
    output_prefix="$BUNDLE_DIR/${name}_mixed_float16"
    bash "$SCRIPT_DIR/convert_onnx_to_om.sh" \
        --model "$EXPORT_DIR/$onnx_file" \
        --output "$output_prefix" \
        --input-shape "$input_shape" \
        --soc-version "$SOC_VERSION" \
        --precision-mode-v2 mixed_float16 \
        --log-file "$LOG_DIR/${name}_mixed_float16.atc.log" \
        --summary-file "$LOG_DIR/${name}_mixed_float16.atc.summary.txt"
done

python "$SCRIPT_DIR/finalize_midi_ddsp_stateful_bundle.py" \
    --export-manifest "$EXPORT_MANIFEST" \
    --bundle-dir "$BUNDLE_DIR" \
    --precision mixed_float16

echo "Stateful MIDI-DDSP bundle created: $BUNDLE_DIR"
