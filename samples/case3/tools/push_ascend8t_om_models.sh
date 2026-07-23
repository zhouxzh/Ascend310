#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODEL_DIR="$ROOT_DIR/models/om/ascend8t/all_models"
DESTINATION=""

usage() {
    cat <<'EOF'
Push Ascend OM models to an authenticated HTTP PUT receiver.

Usage:
  bash tools/push_ascend8t_om_models.sh --destination URL [--model-dir PATH]

The destination URL includes the receiver token. Models are uploaded to:
  <URL>/om/fp16/
  <URL>/om/mixed_precision/

Existing files with the same SHA256 are skipped.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --destination)
            [[ -n "${2:-}" ]] || { echo "ERROR: --destination requires a URL" >&2; exit 2; }
            DESTINATION="${2%/}"
            shift 2
            ;;
        --model-dir)
            [[ -n "${2:-}" ]] || { echo "ERROR: --model-dir requires a path" >&2; exit 2; }
            MODEL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            exit 2
            ;;
    esac
done

[[ -n "$DESTINATION" ]] || { echo "ERROR: --destination is required" >&2; exit 2; }
[[ -d "$MODEL_DIR" ]] || { echo "ERROR: model directory not found: $MODEL_DIR" >&2; exit 1; }

upload_model() {
    local file="$1"
    local category="$2"
    local name sha256 url headers remote_sha256
    name="$(basename "$file")"
    sha256="$(sha256sum "$file" | awk '{print $1}')"
    url="$DESTINATION/om/$category/$name"
    headers="$(curl --silent --show-error --head \
        --connect-timeout 5 --max-time 30 "$url" 2>/dev/null || true)"
    remote_sha256="$(printf '%s\n' "$headers" \
        | tr -d '\r' \
        | awk 'tolower($1) == "x-content-sha256:" {print tolower($2)}')"
    if [[ "$remote_sha256" == "$sha256" ]]; then
        echo "SKIP $category/$name $sha256"
        return 0
    fi
    curl --fail --silent --show-error \
        --retry 20 --retry-delay 2 --retry-all-errors \
        --connect-timeout 5 --max-time 600 \
        --header "X-Content-SHA256: $sha256" \
        --upload-file "$file" "$url"
    echo "UPLOADED $category/$name $sha256"
}

shopt -s nullglob
fp16_models=("$MODEL_DIR"/*_force_fp16.om)
mixed_models=("$MODEL_DIR"/*_mixed_float16.om)
[[ ${#fp16_models[@]} -eq 11 ]] || {
    echo "ERROR: expected 11 FP16 models, found ${#fp16_models[@]}" >&2
    exit 1
}
[[ ${#mixed_models[@]} -eq 11 ]] || {
    echo "ERROR: expected 11 mixed-precision models, found ${#mixed_models[@]}" >&2
    exit 1
}

for file in "${fp16_models[@]}"; do
    upload_model "$file" fp16
done
for file in "${mixed_models[@]}"; do
    upload_model "$file" mixed_precision
done

echo "Completed 22 model uploads."
