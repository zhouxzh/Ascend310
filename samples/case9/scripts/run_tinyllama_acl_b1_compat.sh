#!/usr/bin/env bash
# Explicit B4 TinyLlama OM -> B1 compatibility launcher for the isolated 20T
# campaign.  It does not replace the established B4-only TinyLlama launcher.
set -Eeuo pipefail

ROOT="${CASE9_OM20T_ROOT:-/home/HwHiAiUser/case9-om-20t-20260914}"
SOURCE_DIR="${CASE9_TINYLLAMA_B1_COMPAT_SOURCE_DIR:-$ROOT/src}"
CONDA_SH="${CASE9_OM20T_CONDA_SH:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
CANN_ENV="${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"

usage() {
  cat <<'EOF'
Usage:
  CASE9_TINYLLAMA_B1_COMPAT_ALLOW_DIRTY_BASE=1 \
    bash scripts/run_tinyllama_acl_b1_compat.sh inspect|smoke|serve [options]

This is a cross-SoC compatibility experiment only:
  source OM: Ascend310B4
  target board: Ascend310B1 / 20T

It requires the documented dirty `base` environment and an explicit opt-in.
It verifies the OM, tokenizer archive, extracted tokenizer files, manifest, and
descriptor contract before it can execute ACL.  `inspect` creates a new
manifest-bound contract and refuses to overwrite an existing one.  `smoke`
defaults to one generated token.  `serve` stays on 127.0.0.1:18103 unless
overridden with `--host`/`--port`.

No ATC conversion, package installation, CANN/driver/OPP change, or public
network binding occurs in this launcher.
EOF
}

die() {
  echo "TinyLlama B4-to-B1 compatibility launch refused: $*" >&2
  exit 2
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

[[ "${CASE9_TINYLLAMA_B1_COMPAT_ALLOW_DIRTY_BASE:-0}" == "1" ]] \
  || die "set CASE9_TINYLLAMA_B1_COMPAT_ALLOW_DIRTY_BASE=1 explicitly"
[[ -f "$CONDA_SH" ]] || die "conda activation script is missing: $CONDA_SH"
[[ -f "$CANN_ENV" ]] || die "CANN environment script is missing: $CANN_ENV"
[[ -f "$SOURCE_DIR/tinyllama_acl_b1_compat.py" ]] \
  || die "compatibility Python entrypoint is missing: $SOURCE_DIR/tinyllama_acl_b1_compat.py"
[[ -f "$SOURCE_DIR/local_model_manifest.json" ]] \
  || die "model manifest is missing: $SOURCE_DIR/local_model_manifest.json"

set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
set -u
conda activate base || die "cannot activate board base environment"
[[ "${CONDA_DEFAULT_ENV:-}" == "base" ]] || die "active conda environment is not base"

set +u
# shellcheck disable=SC1090
source "$CANN_ENV"
set -u
export PYTHONNOUSERSITE=1
export PYTHONPATH="$ROOT/overlay:$SOURCE_DIR${PYTHONPATH:+:$PYTHONPATH}"

OM="$ROOT/artifacts/tinyllama/tiny-llama.om"
TOKENIZER_DIR="$ROOT/artifacts/tinyllama/tokenizer"
CONTRACT="$ROOT/reports/tinyllama-b4-to-b1-compat-contract.json"

exec python "$SOURCE_DIR/tinyllama_acl_b1_compat.py" \
  --compatibility-experiment \
  --manifest "$SOURCE_DIR/local_model_manifest.json" \
  --om "$OM" \
  --tokenizer-zip "$TOKENIZER_DIR/tokenizer.zip" \
  --tokenizer "$TOKENIZER_DIR/tokenizer.json" \
  --tokenizer-config "$TOKENIZER_DIR/tokenizer_config.json" \
  --contract "$CONTRACT" \
  "$@"
