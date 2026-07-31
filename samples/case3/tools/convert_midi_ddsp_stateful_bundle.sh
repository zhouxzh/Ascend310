#!/usr/bin/env bash

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPORT_DIR="${1:-$ROOT_DIR/models/midi_ddsp/stateful_v2_batched/onnx}"
BUNDLE_DIR="${2:-$ROOT_DIR/models/midi_ddsp/bundles/google-urmp-stateful-v2-batched-origin}"
EXPORT_MANIFEST="$EXPORT_DIR/export_manifest.json"
SOC_VERSION="${SOC_VERSION:-Ascend310B4}"
LOG_DIR="${LOG_DIR:-$BUNDLE_DIR/logs}"
VOICE_BATCH_SIZES="${VOICE_BATCH_SIZES:-}"
RESUME_CONVERSION="${RESUME_CONVERSION:-0}"
FINALIZE_BUNDLE="${FINALIZE_BUNDLE:-1}"
DRY_RUN_CONVERSION="${DRY_RUN_CONVERSION:-0}"
PYTHON_BIN="${PYTHON_BIN:-python}"

[[ -f "$EXPORT_MANIFEST" ]] || {
    echo "Missing export manifest: $EXPORT_MANIFEST" >&2
    exit 2
}
command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
    echo "Python executable not found: $PYTHON_BIN; activate the intended conda environment first" >&2
    exit 2
}

mkdir -p "$BUNDLE_DIR" "$LOG_DIR"

mapfile -t COMPONENTS < <(
    "$PYTHON_BIN" - "$EXPORT_MANIFEST" "$VOICE_BATCH_SIZES" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
selected = {
    int(value.strip())
    for value in sys.argv[2].split(",")
    if value.strip()
}
for name, component in manifest["components"].items():
    if selected and int(component.get("voice_batch_size", 1)) not in selected:
        continue
    shapes = []
    for item in component["inputs"]:
        shape = ",".join(str(value) for value in item["shape"])
        shapes.append(f'{item["name"]}:{shape}')
    print(f'{name}\t{component["file"]}\t{";".join(shapes)}\t{component["sha256"]}')
PY
)
[[ ${#COMPONENTS[@]} -gt 0 ]] || {
    echo "No MIDI-DDSP components were selected for conversion" >&2
    exit 2
}

for row in "${COMPONENTS[@]}"; do
    IFS=$'\t' read -r name onnx_file input_shape onnx_sha256 <<<"$row"
    output_prefix="$BUNDLE_DIR/${name}_origin"
    summary_file="$LOG_DIR/${name}_origin.atc.summary.txt"
    log_file="$LOG_DIR/${name}_origin.atc.log"
    provenance_file="$LOG_DIR/${name}_origin.provenance.json"
    provenance_args=(
        --onnx "$EXPORT_DIR/$onnx_file"
        --expected-onnx-sha256 "$onnx_sha256"
        --om "${output_prefix}.om"
        --log "$log_file"
        --summary "$summary_file"
        --provenance "$provenance_file"
        --soc-version "$SOC_VERSION"
        --input-shape "$input_shape"
        --precision-mode-v2 origin
    )
    if [[ "$RESUME_CONVERSION" == "1" \
        && -s "${output_prefix}.om" \
        && -s "$summary_file" \
        && -s "$provenance_file" ]] \
        && "$PYTHON_BIN" "$SCRIPT_DIR/midi_ddsp_conversion_provenance.py" \
            validate "${provenance_args[@]}" >/dev/null; then
        echo "Skipping validated existing OM: ${output_prefix}.om"
        continue
    fi
    command=(bash "$SCRIPT_DIR/convert_onnx_to_om.sh" \
        --model "$EXPORT_DIR/$onnx_file" \
        --output "$output_prefix" \
        --input-shape "$input_shape" \
        --soc-version "$SOC_VERSION" \
        --precision-mode-v2 origin \
        --log-file "$log_file" \
        --summary-file "$summary_file")
    if [[ "$DRY_RUN_CONVERSION" == "1" ]]; then
        command+=(--dry-run)
    fi
    "${command[@]}"
    if [[ "$DRY_RUN_CONVERSION" != "1" ]]; then
        "$PYTHON_BIN" "$SCRIPT_DIR/midi_ddsp_conversion_provenance.py" \
            record "${provenance_args[@]}"
    fi
done

if [[ "$FINALIZE_BUNDLE" != "1" ]]; then
    echo "Stateful MIDI-DDSP origin conversion completed without finalization."
    exit 0
fi

FINALIZE_ARGS=(
    --export-manifest "$EXPORT_MANIFEST"
    --bundle-dir "$BUNDLE_DIR"
    --conversion-log-dir "$LOG_DIR"
)
if [[ -n "$VOICE_BATCH_SIZES" ]]; then
    FINALIZE_ARGS+=(--voice-batch-sizes "$VOICE_BATCH_SIZES")
fi
"$PYTHON_BIN" "$SCRIPT_DIR/finalize_midi_ddsp_stateful_bundle.py" \
    "${FINALIZE_ARGS[@]}"

echo "Stateful MIDI-DDSP origin bundle created: $BUNDLE_DIR"
