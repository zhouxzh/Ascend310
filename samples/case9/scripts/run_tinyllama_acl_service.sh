#!/usr/bin/env bash
# Start the TinyLlama ACL service after the explicit board gates have passed.
# This wrapper never creates environments, downloads artifacts, or exposes the
# service beyond loopback.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOME_DIR="${CASE9_TINYLLAMA_HOME:-${CASE9_DIR:-$HOME/case9-tinyllama}}"
ARTIFACT_DIR="${CASE9_TINYLLAMA_ARTIFACT_DIR:-$HOME_DIR/artifacts}"
REPORT_DIR="${CASE9_TINYLLAMA_REPORT_DIR:-$HOME_DIR/reports}"
MANIFEST="${CASE9_LOCAL_MODEL_MANIFEST:-$REPO_DIR/local_model_manifest.json}"
TOKENIZER_DIR="${CASE9_TINYLLAMA_TOKENIZER_DIR:-$ARTIFACT_DIR/tokenizer}"
OM_PATH="${CASE9_TINYLLAMA_OM:-$ARTIFACT_DIR/tiny-llama.om}"
CONTRACT_PATH="${CASE9_TINYLLAMA_CONTRACT:-$REPORT_DIR/tinyllama-acl-contract.json}"
ENV_NAME="${CASE9_ACL_OM_ENV:-case9-acl-om}"
CONDA_ROOT="${CASE9_CONDA_ROOT:-/usr/local/miniconda3}"
CONDA_SH="${CASE9_CONDA_SH:-$CONDA_ROOT/etc/profile.d/conda.sh}"
CANN_ENV="${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
SERVICE_SCRIPT="${CASE9_TINYLLAMA_SERVICE_SCRIPT:-$REPO_DIR/tinyllama_acl_service.py}"
TOKENIZER_PATH="${CASE9_TINYLLAMA_TOKENIZER:-$TOKENIZER_DIR/tokenizer.json}"
TOKENIZER_ZIP="${CASE9_TINYLLAMA_TOKENIZER_ZIP:-$ARTIFACT_DIR/tokenizer.zip}"
HOST="${CASE9_TINYLLAMA_HOST:-127.0.0.1}"
PORT="${CASE9_TINYLLAMA_PORT:-8080}"
DEVICE_ID="${CASE9_TINYLLAMA_DEVICE_ID:-0}"
FORBIDDEN_MODULES=(
  torch torch_npu torchaudio mindtorch torchvision xformers
  transformers vllm mindie qwen_ascend_llm onnxruntime
)
AUXILIARY_MODULES=(sentencepiece mindspore)

usage() {
  cat <<'EOF'
Usage: bash scripts/run_tinyllama_acl_service.sh [options]

Options:
  --host HOST          Loopback host only (default: 127.0.0.1).
  --port PORT          Service port (default: 8080; use 8081 only for isolation).
  --device-id ID       Ascend device ID (default: 0).
  --max-tokens N       Service generation cap, if supported by the runtime.
  --execution-timeout S  Native ACL call deadline (default: 50 seconds).
  --help               Show this help.

The OM, tokenizer, contract, conda environment, and CANN paths are supplied
through CASE9_TINYLLAMA_* variables or their documented defaults. Run the
provisioning script's check, download, inspect, and smoke gates first.
EOF
}

die() {
  echo "TinyLlama service launch refused: $*" >&2
  exit 1
}

MAX_TOKENS=""
EXECUTION_TIMEOUT=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --host) [[ "$#" -ge 2 ]] || die "--host requires a value"; HOST="$2"; shift 2 ;;
    --port) [[ "$#" -ge 2 ]] || die "--port requires a value"; PORT="$2"; shift 2 ;;
    --device-id) [[ "$#" -ge 2 ]] || die "--device-id requires a value"; DEVICE_ID="$2"; shift 2 ;;
    --max-tokens) [[ "$#" -ge 2 ]] || die "--max-tokens requires a value"; MAX_TOKENS="$2"; shift 2 ;;
    --execution-timeout) [[ "$#" -ge 2 ]] || die "--execution-timeout requires a value"; EXECUTION_TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$HOST" == "127.0.0.1" ]] || die "host must remain 127.0.0.1"
[[ "$PORT" =~ ^[0-9]+$ && "$PORT" -ge 1 && "$PORT" -le 65535 ]] || die "invalid port"
[[ "$DEVICE_ID" =~ ^[0-9]+$ ]] || die "invalid device id"
[[ -f "$SERVICE_SCRIPT" ]] || die "service entrypoint unavailable: $SERVICE_SCRIPT"
[[ -f "$OM_PATH" ]] || die "OM unavailable: $OM_PATH"
[[ -f "$TOKENIZER_PATH" ]] || die "tokenizer unavailable: $TOKENIZER_PATH"
[[ -f "$CONTRACT_PATH" ]] || die "contract unavailable: $CONTRACT_PATH"
[[ -f "$MANIFEST" ]] || die "manifest unavailable: $MANIFEST"
[[ -f "$CONDA_SH" ]] || die "conda profile unavailable: $CONDA_SH"
[[ -f "$CANN_ENV" ]] || die "CANN environment unavailable: $CANN_ENV"

python3 - "$MANIFEST" "$OM_PATH" "$TOKENIZER_ZIP" "$TOKENIZER_PATH" "$CONTRACT_PATH" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path, om_path, tokenizer_zip, tokenizer_path, contract_path = map(Path, sys.argv[1:])
with manifest_path.open(encoding="utf-8") as source:
    artifacts = json.load(source)["artifacts"]
if artifacts["tinyllama_acl_om"]["revision"] != artifacts["tinyllama_tokenizer_zip"]["revision"]:
    raise SystemExit("OM and tokenizer revisions differ")

def verify(name, path):
    item = artifacts[name]
    if path.stat().st_size != int(item["expected_bytes"]):
        raise SystemExit(f"{name} byte count does not match manifest")
    digest_ctx = hashlib.sha256()
    with path.open("rb") as binary:
        for block in iter(lambda: binary.read(1024 * 1024), b""):
            digest_ctx.update(block)
    digest = digest_ctx.hexdigest()
    expected = item.get("sha256")
    if name in {"tinyllama_acl_om", "tinyllama_tokenizer_zip"} and (
        not expected or len(str(expected)) != 64
    ):
        raise SystemExit(f"{name} must have a fixed SHA-256")
    if expected and digest != expected.lower():
        raise SystemExit(f"{name} SHA-256 does not match manifest")
    return digest

om_sha = verify("tinyllama_acl_om", om_path)
verify("tinyllama_tokenizer_zip", tokenizer_zip)
tokenizer_item = artifacts["tinyllama_tokenizer_zip"]
expected_files = tokenizer_item.get("extracted_files")
if not isinstance(expected_files, dict) or not expected_files:
    raise SystemExit("manifest has no extracted tokenizer hash contract")
required_files = {"tokenizer.json", "tokenizer.model", "special_tokens_map.json", "tokenizer_config.json"}
if set(expected_files) != required_files:
    raise SystemExit("manifest extracted tokenizer file set is not the four admitted files")
for name, item in expected_files.items():
    if Path(name).name != name or not isinstance(item, dict):
        raise SystemExit(f"invalid extracted tokenizer manifest entry: {name}")
    path = tokenizer_path.parent / name
    if not path.is_file():
        raise SystemExit(f"missing extracted tokenizer file: {path}")
    if path.stat().st_size != int(item["expected_bytes"]):
        raise SystemExit(f"{name} byte count does not match manifest")
    digest_ctx = hashlib.sha256()
    with path.open("rb") as binary:
        for block in iter(lambda: binary.read(1024 * 1024), b""):
            digest_ctx.update(block)
    if digest_ctx.hexdigest().lower() != str(item["sha256"]).lower():
        raise SystemExit(f"{name} SHA-256 does not match manifest")
if tokenizer_path.name != "tokenizer.json":
    raise SystemExit("TinyLlama runtime must load tokenizer.json")
with contract_path.open(encoding="utf-8") as source:
    contract = json.load(source)
bound = contract.get("source_artifact", {})
if int(bound.get("bytes", -1)) != om_path.stat().st_size or str(bound.get("sha256", "")).lower() != om_sha:
    raise SystemExit("contract is not bound to the current OM")
if contract.get("source_revision") != artifacts["tinyllama_acl_om"]["revision"]:
    raise SystemExit("contract revision is not bound to the manifest")
print("artifact_and_contract_binding=verified")
PY

set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
set -u
conda activate "$ENV_NAME" || die "cannot activate conda environment: $ENV_NAME"
export PYTHONNOUSERSITE=1
[[ "$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" == "3.9" ]] \
  || die "active environment must be Python 3.9"

set +u
# shellcheck disable=SC1090
source "$CANN_ENV"
set -u
[[ -z "${ASCEND_CUSTOM_OPP_PATH:-}" ]] || die "ASCEND_CUSTOM_OPP_PATH is set; custom OPP is forbidden"
command -v npu-smi >/dev/null || die "npu-smi unavailable"
npu-smi info | grep -Eiq '310B4' || die "npu-smi did not identify Ascend310B4"
python -c 'import acl; print("acl import OK")' || die "acl import failed"
python -c 'import numpy, tokenizers; print("numpy", numpy.__version__); print("tokenizers", tokenizers.__version__)' \
  || die "numpy/tokenizers import failed"
[[ -f "${ASCEND_TOOLKIT_HOME:-}/version.cfg" ]] || die "CANN version.cfg unavailable"
grep -Eq '^runtime_running_version=\[[^:]+:8\.0\.0\]$' "${ASCEND_TOOLKIT_HOME}/version.cfg" \
  || die "CANN runtime version is not the admitted 8.0.0"

python - "${FORBIDDEN_MODULES[@]}" <<'PY'
import importlib.util
import sys

forbidden = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is not None]
if forbidden:
    raise SystemExit("forbidden modules installed: " + ", ".join(forbidden))
print("forbidden_modules=none")
PY

python - "${AUXILIARY_MODULES[@]}" <<'PY'
import importlib.util
import sys

present = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is not None]
print("auxiliary_preexisting=" + (", ".join(present) if present else "none"))
print("auxiliary_modules_are_not_imported_by_tinyllama_runtime")
PY

# Keep pre-existing user-site packages visible in the audit without importing
# them into this process. The service remains isolated by PYTHONNOUSERSITE=1.
env -u PYTHONNOUSERSITE python - "${FORBIDDEN_MODULES[@]}" "${AUXILIARY_MODULES[@]}" <<'PY'
import site
import sys
from pathlib import Path

user_site = Path(site.getusersitepackages()).resolve()
names = sys.argv[1:]
normalized = {name.replace("-", "_").lower(): name for name in names}
found = set()
if user_site.is_dir():
    for entry in user_site.iterdir():
        stem = entry.name.lower()
        if stem.endswith(".dist-info") or stem.endswith(".egg-info"):
            stem = stem.rsplit("-", 1)[0]
        stem = stem.replace("-", "_")
        for key, original in normalized.items():
            if stem == key or stem.startswith(key + "."):
                found.add(original)
        if entry.is_dir() and entry.name.replace("-", "_").lower() in normalized:
            found.add(normalized[entry.name.replace("-", "_").lower()])
print("user_site=" + str(user_site))
print("user_site_preexisting=" + (", ".join(sorted(found)) if found else "none"))
print("user_site_packages_are_not_loaded_when_PYTHONNOUSERSITE=1")
PY

args=(serve --contract "$CONTRACT_PATH" --om "$OM_PATH" --tokenizer "$TOKENIZER_PATH" \
  --manifest "$MANIFEST" --host "$HOST" --port "$PORT" --device-id "$DEVICE_ID")
if [[ -n "$MAX_TOKENS" ]]; then
  args+=(--max-tokens "$MAX_TOKENS")
fi
if [[ -n "$EXECUTION_TIMEOUT" ]]; then
  args+=(--execution-timeout "$EXECUTION_TIMEOUT")
fi

echo "Starting TinyLlama ACL service on ${HOST}:${PORT}; model artifacts remain board-local."
exec python "$SERVICE_SCRIPT" "${args[@]}"
