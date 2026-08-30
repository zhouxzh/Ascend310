#!/usr/bin/env bash
# Board-only TinyLlama artifact and ACL gate workflow.
#
# This script deliberately does not install Torch-family packages, custom OPP,
# or system CANN files.  Every operation is explicit; no command performs a
# CPU/cloud fallback.  Run it on the target aarch64 board after reviewing the
# generated logs.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="${CASE9_LOCAL_MODEL_MANIFEST:-$REPO_DIR/local_model_manifest.json}"
TINY_REQUIREMENTS="${CASE9_TINYLLAMA_REQUIREMENTS:-$REPO_DIR/requirements-tinyllama-acl-om.txt}"
HOME_DIR="${CASE9_TINYLLAMA_HOME:-${CASE9_DIR:-$HOME/case9-tinyllama}}"
ARTIFACT_DIR="${CASE9_TINYLLAMA_ARTIFACT_DIR:-$HOME_DIR/artifacts}"
REPORT_DIR="${CASE9_TINYLLAMA_REPORT_DIR:-$HOME_DIR/reports}"
LOCK_FILE="${CASE9_TINYLLAMA_LOCK_FILE:-$ARTIFACT_DIR/tinyllama-artifacts.lock.json}"
TOKENIZER_DIR="${CASE9_TINYLLAMA_TOKENIZER_DIR:-$ARTIFACT_DIR/tokenizer}"
CONTRACT_PATH="${CASE9_TINYLLAMA_CONTRACT:-$REPORT_DIR/tinyllama-acl-contract.json}"
ONNX_CONTRACT_REPORT="${CASE9_TINYLLAMA_ONNX_CONTRACT_REPORT:-$REPORT_DIR/tinyllama-onnx-contract.json}"
ONNX_CONTRACT_SHA256="${CASE9_TINYLLAMA_ONNX_CONTRACT_SHA256:-}"
ENV_NAME="${CASE9_ACL_OM_ENV:-case9-acl-om}"
CONDA_ROOT="${CASE9_CONDA_ROOT:-/usr/local/miniconda3}"
CONDA_SH="${CASE9_CONDA_SH:-$CONDA_ROOT/etc/profile.d/conda.sh}"
CANN_ENV="${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
SERVICE_SCRIPT="${CASE9_TINYLLAMA_SERVICE_SCRIPT:-$REPO_DIR/tinyllama_acl_service.py}"
SOC_VERSION="Ascend310B4"
OM_KEY="tinyllama_acl_om"
TOKENIZER_KEY="tinyllama_tokenizer_zip"
ONNX_KEY="tinyllama_onnx"
OM_PATH="${CASE9_TINYLLAMA_OM:-$ARTIFACT_DIR/tiny-llama.om}"
TOKENIZER_ZIP="${CASE9_TINYLLAMA_TOKENIZER_ZIP:-$ARTIFACT_DIR/tokenizer.zip}"
ONNX_PATH="${CASE9_TINYLLAMA_ONNX:-$ARTIFACT_DIR/tiny-llama.onnx}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

FORBIDDEN_MODULES=(
  torch torch_npu torchaudio mindtorch torchvision xformers
  transformers vllm mindie qwen_ascend_llm onnxruntime
)
AUXILIARY_MODULES=(sentencepiece mindspore)

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_tinyllama_board.sh <command>

Commands:
  check          Verify aarch64, Ascend310B4, CANN/ACL, disk, and no Torch.
  create-env     Create only the exact case9-acl-om Python 3.9 environment.
  install-runtime Install only numpy/tokenizers wheels from the locked file.
  download       Download and verify the prebuilt OM and tokenizer archive.
  download-onnx  Explicitly download the optional source ONNX (no ATC).
  inspect        Load the OM descriptor and write the TinyLlama contract.
  smoke          Run one native ACL greedy generation with npu-smi evidence.
  convert        Optional ATC branch; requires explicit approval and admitted ONNX contract.
  serve          Launch the loopback-only TinyLlama OpenAI service.

The default service port is the final loopback port 8080.  Pass
CASE9_TINYLLAMA_PORT=8081 only for an isolated validation run.
EOF
}

die() {
  echo "TinyLlama gate failed: $*" >&2
  exit 1
}

require_board() {
  [[ "$(uname -m)" == "aarch64" ]] || die "run this command on the Ascend board (aarch64)"
}

require_manifest() {
  [[ -f "$MANIFEST" ]] || die "manifest is unavailable: $MANIFEST"
  python3 - "$MANIFEST" <<'PY' || exit 1
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    artifacts = json.load(source).get("artifacts", {})

required = ("tinyllama_acl_om", "tinyllama_tokenizer_zip", "tinyllama_onnx")
for name in required:
    item = artifacts.get(name)
    if not isinstance(item, dict):
        raise SystemExit(f"manifest is missing artifact {name}")
    for field in ("revision", "filename", "url", "expected_bytes"):
        if not item.get(field):
            raise SystemExit(f"manifest artifact {name} has no {field}")
    if int(item["expected_bytes"]) <= 0:
        raise SystemExit(f"manifest artifact {name} has invalid expected_bytes")
    digest = item.get("sha256")
    if name in ("tinyllama_acl_om", "tinyllama_tokenizer_zip"):
        if not digest or len(str(digest)) != 64 or any(c not in "0123456789abcdefABCDEF" for c in str(digest)):
            raise SystemExit(f"manifest artifact {name} must have a fixed SHA-256")
    elif digest is not None and (len(str(digest)) != 64 or any(c not in "0123456789abcdefABCDEF" for c in str(digest))):
        raise SystemExit(f"manifest artifact {name} has invalid SHA-256")
if artifacts["tinyllama_acl_om"]["revision"] != artifacts["tinyllama_tokenizer_zip"]["revision"]:
    raise SystemExit("OM and tokenizer revisions differ")
PY
}

manifest_value() {
  local artifact="$1" field="$2"
  python3 - "$MANIFEST" "$artifact" "$field" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    value = json.load(source)["artifacts"][sys.argv[2]].get(sys.argv[3])
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))
else:
    print(value)
PY
}

source_cann() {
  [[ -f "$CANN_ENV" ]] || die "CANN environment script is unavailable: $CANN_ENV"
  [[ -z "${ASCEND_CUSTOM_OPP_PATH:-}" ]] || die "ASCEND_CUSTOM_OPP_PATH is set; custom OPP is forbidden"
  set +u
  # shellcheck disable=SC1090
  source "$CANN_ENV"
  set -u
  [[ -z "${ASCEND_CUSTOM_OPP_PATH:-}" ]] || die "CANN setup exposed ASCEND_CUSTOM_OPP_PATH"
}

source_cann_for_authorized_atc() {
  local custom_opp="${ASCEND_CUSTOM_OPP_PATH:-}"
  [[ -n "$custom_opp" ]] || die "authorized ATC branch requires ASCEND_CUSTOM_OPP_PATH"
  [[ -d "$custom_opp" ]] || die "isolated custom OPP directory is unavailable: $custom_opp"
  case "$custom_opp" in
    "$HOME_DIR"/*) ;;
    *) die "custom OPP must be under the TinyLlama quarantine directory: $HOME_DIR" ;;
  esac
  [[ -f "$CANN_ENV" ]] || die "CANN environment script is unavailable: $CANN_ENV"
  set +u
  # shellcheck disable=SC1090
  source "$CANN_ENV"
  set -u
  export ASCEND_CUSTOM_OPP_PATH="$custom_opp"
}

activate_runtime() {
  [[ -f "$CONDA_SH" ]] || die "conda profile script is unavailable: $CONDA_SH"
  set +u
  # shellcheck disable=SC1090
  source "$CONDA_SH"
  set -u
  conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME" \
    || die "isolated environment is missing: $ENV_NAME; run create-env explicitly"
  conda activate "$ENV_NAME"
  export PYTHONNOUSERSITE=1
  [[ "$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" == "3.9" ]] \
    || die "active environment must be Python 3.9"
}

check_forbidden_modules() {
  local result
  result="$(python - "${FORBIDDEN_MODULES[@]}" <<'PY'
import importlib.util
import sys

found = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is not None]
print(" ".join(found))
PY
)"
  [[ -z "$result" ]] || die "forbidden Torch-family modules are installed: $result"
  echo "forbidden_modules=none (${FORBIDDEN_MODULES[*]})"
  python - "${AUXILIARY_MODULES[@]}" <<'PY'
import importlib.util
import sys

present = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is not None]
print("auxiliary_preexisting=" + (", ".join(present) if present else "none"))
print("auxiliary_modules_are_not_imported_by_tinyllama_runtime")
PY
  # The dedicated environment disables user-site imports. Report packages
  # already present in ~/.local separately so this gate is not mistaken for a
  # board-wide clean image; do not delete or import them.
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
}

create_env() {
  require_board
  [[ "$ENV_NAME" == "case9-acl-om" ]] || die "unexpected environment name: $ENV_NAME"
  [[ -f "$CONDA_SH" ]] || die "conda profile script is unavailable: $CONDA_SH"
  set +u
  source "$CONDA_SH"
  set -u
  if conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
    echo "Conda environment already exists: $ENV_NAME"
  else
    conda create --yes --name "$ENV_NAME" python=3.9
  fi
  activate_runtime
  check_forbidden_modules
}

install_runtime() {
  require_board
  activate_runtime
  check_forbidden_modules
  [[ -f "$TINY_REQUIREMENTS" ]] || die "TinyLlama locked requirements are unavailable: $TINY_REQUIREMENTS"
  mkdir -p "$HOME_DIR" "$REPORT_DIR"
  local log="$REPORT_DIR/${TIMESTAMP}-tinyllama-install-runtime.log"
  {
    echo "utc=$TIMESTAMP"
    echo "environment=$ENV_NAME"
    echo "python=$(python -c 'import sys; print(sys.executable)')"
    echo "packages=numpy tokenizers"
    python3 - "$TINY_REQUIREMENTS" <<'PY' >"$REPORT_DIR/${TIMESTAMP}-tinyllama-runtime-wheels.txt"
import sys

allowed = {"numpy", "tokenizers"}
for line in open(sys.argv[1], encoding="utf-8"):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    name = stripped.split(" @", 1)[0].strip().lower()
    if name in allowed:
        print(stripped)
PY
    mapfile -t wheels <"$REPORT_DIR/${TIMESTAMP}-tinyllama-runtime-wheels.txt"
    [[ "${#wheels[@]}" -eq 2 ]] || { echo "expected exactly numpy/tokenizers wheels"; exit 1; }
    # Keep the PEP 508 direct references and their --hash options in a
    # requirements file.  Passing each complete line as one shell argument
    # makes pip treat the embedded --hash token as part of the path and fail
    # before it can verify either wheel.
    python -m pip install --disable-pip-version-check --force-reinstall --no-deps --require-hashes \
      --only-binary=:all: -r "$REPORT_DIR/${TIMESTAMP}-tinyllama-runtime-wheels.txt"
  } >"$log" 2>&1 || die "runtime installation failed; log=$log"
  check_forbidden_modules
  python - <<'PY' >>"$log" 2>&1
import numpy
import tokenizers
print("numpy", numpy.__version__)
print("tokenizers", tokenizers.__version__)
PY
  echo "No-Torch TinyLlama runtime installation passed; log=$log"
}

artifact_path() {
  case "$1" in
    "$OM_KEY") printf '%s\n' "$OM_PATH" ;;
    "$TOKENIZER_KEY") printf '%s\n' "$TOKENIZER_ZIP" ;;
    "$ONNX_KEY") printf '%s\n' "$ONNX_PATH" ;;
    *) die "unknown TinyLlama artifact key: $1" ;;
  esac
}

is_lfs_pointer() {
  # Do not place binary bytes in a shell variable: OM headers commonly contain
  # NUL bytes and Bash warns (or truncates) command substitutions.  A textual
  # LFS pointer is small and can be detected directly through the pipe.
  if LC_ALL=C head -c 256 "$1" 2>/dev/null | grep -Fq "version https://git-lfs.github.com/spec/v1"; then
    return 1
  fi
  return 0
}

lock_sha() {
  python3 - "$LOCK_FILE" "$1" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as source:
        print(json.load(source).get("artifacts", {}).get(sys.argv[2], {}).get("sha256", ""))
except FileNotFoundError:
    print("")
PY
}

write_lock() {
  local artifact="$1" path="$2" actual_bytes="$3" actual_sha="$4"
  local source_revision source_url source_repository
  source_revision="$(manifest_value "$artifact" revision)"
  source_url="$(manifest_value "$artifact" url)"
  source_repository="$(manifest_value "$artifact" repository)"
  python3 - "$LOCK_FILE" "$artifact" "$path" "$actual_bytes" "$actual_sha" "$source_revision" "$source_url" "$source_repository" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

lock, artifact, path, size, digest, revision, url, repository = sys.argv[1:]
try:
    with open(lock, encoding="utf-8") as source:
        document = json.load(source)
except FileNotFoundError:
    document = {"schema_version": 1, "artifacts": {}}
old = document.setdefault("artifacts", {}).get(artifact)
if old and old.get("sha256") and old["sha256"].lower() != digest.lower():
    raise SystemExit(f"locked SHA changed for {artifact}")
document["artifacts"][artifact] = {
    "path": path,
    "bytes": int(size),
    "sha256": digest.lower(),
    "revision": revision,
    "url": url,
    "repository": repository,
    "verified_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}
parent = os.path.dirname(lock)
if parent:
    os.makedirs(parent, exist_ok=True)
temporary = lock + ".tmp"
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(document, output, ensure_ascii=True, indent=2, sort_keys=True)
    output.write("\n")
os.replace(temporary, lock)
PY
}

verify_artifact() {
  local artifact="$1" path expected_bytes expected_sha actual_bytes actual_sha board_sha
  path="$(artifact_path "$artifact")"
  [[ -f "$path" ]] || die "$artifact is unavailable: $path"
  is_lfs_pointer "$path" || die "$artifact is an LFS pointer: $path"
  expected_bytes="$(manifest_value "$artifact" expected_bytes)"
  expected_sha="$(manifest_value "$artifact" sha256)"
  actual_bytes="$(stat -c '%s' "$path")"
  [[ "$actual_bytes" == "$expected_bytes" ]] || die "$artifact byte mismatch: got $actual_bytes expected $expected_bytes"
  actual_sha="$(sha256sum "$path" | awk '{print $1}')"
  if [[ -n "$expected_sha" ]]; then
    [[ "$actual_sha" == "${expected_sha,,}" ]] || die "$artifact SHA-256 mismatch"
  else
    board_sha="$(lock_sha "$artifact")"
    if [[ -n "$board_sha" ]]; then
      [[ "$actual_sha" == "${board_sha,,}" ]] || die "$artifact differs from board-local lock"
    fi
  fi
  write_lock "$artifact" "$path" "$actual_bytes" "$actual_sha"
  echo "verified artifact=$artifact bytes=$actual_bytes sha256=$actual_sha path=$path"
}

download_artifact() {
  local artifact="$1" path url expected_bytes expected_sha part log status
  path="$(artifact_path "$artifact")"
  url="$(manifest_value "$artifact" url)"
  expected_bytes="$(manifest_value "$artifact" expected_bytes)"
  expected_sha="$(manifest_value "$artifact" sha256)"
  mkdir -p "$(dirname "$path")" "$REPORT_DIR"
  if [[ -f "$path" ]]; then
    verify_artifact "$artifact"
    return 0
  fi
  part="$path.part"
  [[ ! -e "$part" ]] || die "stale partial download exists; inspect explicitly: $part"
  log="$REPORT_DIR/${TIMESTAMP}-${artifact}-download.log"
  set +e
  curl --http1.1 --fail --location --retry 4 --retry-delay 3 \
    --connect-timeout 15 --max-time 3600 --output "$part" "$url" >"$log" 2>&1
  status=$?
  set -e
  echo "curl_exit=$status" >>"$log"
  [[ "$status" -eq 0 ]] || die "$artifact download failed; log=$log"
  is_lfs_pointer "$part" || die "$artifact download is an LFS pointer: $part"
  [[ "$(stat -c '%s' "$part")" == "$expected_bytes" ]] || die "$artifact size mismatch; retaining $part"
  if [[ -n "$expected_sha" ]]; then
    [[ "$(sha256sum "$part" | awk '{print $1}')" == "${expected_sha,,}" ]] \
      || die "$artifact download SHA-256 mismatch; retaining $part"
  fi
  mv "$part" "$path"
  verify_artifact "$artifact"
}

extract_tokenizer() {
  require_board
  verify_artifact "$TOKENIZER_KEY"
  if [[ "$TOKENIZER_DIR" != "$ARTIFACT_DIR"/* ]]; then
    die "tokenizer directory must remain under the TinyLlama artifact directory: $TOKENIZER_DIR"
  fi
  if [[ -d "$TOKENIZER_DIR" ]] && verify_extracted_tokenizer "$TOKENIZER_DIR"; then
    echo "Tokenizer files already pass the manifest-bound hash check: $TOKENIZER_DIR"
    return 0
  fi
  local log="$REPORT_DIR/${TIMESTAMP}-tinyllama-tokenizer-extract.log"
  local staging
  staging="$(mktemp -d "$ARTIFACT_DIR/.tokenizer.extract.XXXXXX")"
  python3 - "$TOKENIZER_ZIP" "$staging" <<'PY' >"$log" 2>&1
import os
import sys
import zipfile

archive, destination = sys.argv[1:]
required = {"tokenizer.json", "tokenizer.model", "tokenizer_config.json", "special_tokens_map.json"}
with zipfile.ZipFile(archive) as source:
    names = source.namelist()
    for name in names:
        normalized = os.path.normpath(name)
        if normalized.startswith("../") or normalized.startswith("/") or "/../" in normalized:
            raise SystemExit(f"unsafe archive member: {name}")
    present = set(names)
    missing = required - present
    if missing:
        raise SystemExit(f"tokenizer archive misses: {sorted(missing)}")
    if any(name not in required for name in names if os.path.basename(name) in required):
        raise SystemExit("required tokenizer files must be archive-root members")
    for name in sorted(required):
        with source.open(name) as input_file, open(os.path.join(destination, name), "xb") as output:
            output.write(input_file.read())
print("extracted", ",".join(sorted(required)))
PY
  if ! verify_extracted_tokenizer "$staging"; then
    echo "Tokenizer extraction hash check failed; staging=$staging log=$log" >&2
    exit 1
  fi
  if [[ -e "$TOKENIZER_DIR" ]]; then
    mv "$TOKENIZER_DIR" "$TOKENIZER_DIR.stale-$TIMESTAMP"
  fi
  mv "$staging" "$TOKENIZER_DIR"
  [[ -f "$TOKENIZER_DIR/tokenizer.json" ]] || die "tokenizer extraction failed; log=$log"
  echo "Tokenizer extraction passed; log=$log directory=$TOKENIZER_DIR"
}

verify_extracted_tokenizer() {
  local directory="${1:-$TOKENIZER_DIR}"
  python3 - "$MANIFEST" "$directory" <<'PY'
import hashlib
import json
import os
import sys

manifest_path, directory = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as source:
    item = json.load(source)["artifacts"]["tinyllama_tokenizer_zip"]
expected_files = item.get("extracted_files")
if not isinstance(expected_files, dict) or not expected_files:
    raise SystemExit("manifest has no extracted tokenizer hash contract")
required = {"tokenizer.json", "tokenizer.model", "special_tokens_map.json", "tokenizer_config.json"}
if set(expected_files) != required:
    raise SystemExit("manifest extracted tokenizer file set is not the four admitted files")
root = os.path.realpath(directory)
for name, expected in expected_files.items():
    if os.path.basename(name) != name or not isinstance(expected, dict):
        raise SystemExit(f"invalid extracted tokenizer manifest entry: {name}")
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root or not os.path.isfile(path):
        raise SystemExit(f"missing extracted tokenizer file: {name}")
    actual_bytes = os.path.getsize(path)
    if actual_bytes != int(expected["expected_bytes"]):
        raise SystemExit(f"{name} byte mismatch: {actual_bytes} != {expected['expected_bytes']}")
    digest = hashlib.sha256()
    with open(path, "rb") as binary:
        for block in iter(lambda: binary.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != str(expected["sha256"]).lower():
        raise SystemExit(f"{name} SHA-256 mismatch")
print("extracted_tokenizer_hashes=verified")
PY
}

check_environment() {
  require_board
  require_manifest
  mkdir -p "$HOME_DIR" "$REPORT_DIR"
  local disk_log="$REPORT_DIR/${TIMESTAMP}-tinyllama-disk.log"
  df -Pk "$HOME_DIR" >"$disk_log" 2>&1 || die "cannot inspect disk; log=$disk_log"
  cat "$disk_log"
  local available_kib
  available_kib="$(awk 'NR == 2 {print $4}' "$disk_log")"
  [[ "$available_kib" =~ ^[0-9]+$ && "$available_kib" -ge 2097152 ]] || die "at least 2 GiB free disk required"
  activate_runtime
  source_cann
  command -v npu-smi >/dev/null || die "npu-smi unavailable"
  command -v sha256sum >/dev/null || die "sha256sum unavailable"
  command -v curl >/dev/null || die "curl unavailable"
  python -c 'import acl; print("acl import OK")' || die "acl import failed"
  python -c 'import numpy, tokenizers; print("numpy", numpy.__version__); print("tokenizers", tokenizers.__version__)' \
    || die "numpy/tokenizers import failed; run install-runtime explicitly"
  local version_cfg="${ASCEND_TOOLKIT_HOME:-}/version.cfg"
  [[ -f "$version_cfg" ]] || die "CANN version.cfg is unavailable: $version_cfg"
  python3 - "$version_cfg" <<'PY' || die "CANN runtime version is not the admitted 8.0.0"
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"^runtime_running_version=\[[^:\n]+:(8\.0\.0)\]$", text, re.MULTILINE)
if match is None:
    raise SystemExit("runtime_running_version is not 8.0.0")
print("cann_runtime_version=8.0.0")
PY
  check_forbidden_modules
  local npu_log="$REPORT_DIR/${TIMESTAMP}-tinyllama-npu-smi.log"
  npu-smi info >"$npu_log" 2>&1 || die "npu-smi failed; log=$npu_log"
  cat "$npu_log"
  grep -Eiq '310B4' "$npu_log" || die "npu-smi did not identify $SOC_VERSION"
  {
    echo "utc=$TIMESTAMP"
    echo "python=$(python -c 'import sys; print(sys.executable); print(sys.version.replace(chr(10), " "))')"
    echo "cann_env=$CANN_ENV"
    echo "ascend_toolkit_home=${ASCEND_TOOLKIT_HOME:-}"
    echo "cann_version_cfg=$version_cfg"
    grep -E '^(runtime|compiler|opp)_running_version=' "$version_cfg" || true
    echo "ascend_custom_opp_path=${ASCEND_CUSTOM_OPP_PATH:-}"
    if command -v atc >/dev/null 2>&1; then
      echo "atc=$(command -v atc)"
    else
      echo "atc=unavailable (optional prebuilt-OM path)"
    fi
    python -m pip show numpy tokenizers || true
    uname -a
    echo "kernel_release=$(uname -r)"
    echo "npu_smi_version="
    npu-smi -v 2>&1 || true
    for driver_info in /usr/local/Ascend/driver/version.info /usr/local/Ascend/driver/version.cfg; do
      if [[ -f "$driver_info" ]]; then
        echo "driver_info=$driver_info"
        cat "$driver_info"
      fi
    done
    free -h || true
  } >"$REPORT_DIR/${TIMESTAMP}-tinyllama-environment.log" 2>&1
  echo "TinyLlama environment check passed; reports=$REPORT_DIR"
}

download() {
  require_board
  require_manifest
  mkdir -p "$ARTIFACT_DIR" "$REPORT_DIR"
  download_artifact "$OM_KEY"
  download_artifact "$TOKENIZER_KEY"
  extract_tokenizer
}

download_onnx() {
  require_board
  require_manifest
  mkdir -p "$ARTIFACT_DIR" "$REPORT_DIR"
  download_artifact "$ONNX_KEY"
}

inspect_model() {
  require_board
  require_manifest
  activate_runtime
  source_cann
  [[ -z "${ASCEND_CUSTOM_OPP_PATH:-}" ]] || die "ASCEND_CUSTOM_OPP_PATH is set; custom OPP is forbidden"
  check_forbidden_modules
  [[ -f "$SERVICE_SCRIPT" ]] || die "TinyLlama service is unavailable: $SERVICE_SCRIPT"
  verify_artifact "$OM_KEY"
  [[ -f "$TOKENIZER_DIR/tokenizer.json" ]] || extract_tokenizer
  verify_extracted_tokenizer "$TOKENIZER_DIR"
  mkdir -p "$REPORT_DIR"
  local inspect_args=(inspect --om "$OM_PATH" --tokenizer "$TOKENIZER_DIR/tokenizer.json")
  if [[ -f "$CONTRACT_PATH" ]]; then
    inspect_args+=(--contract "$CONTRACT_PATH")
  fi
  local inspect_output="$REPORT_DIR/${TIMESTAMP}-tinyllama-inspect.json"
  local inspect_error="$REPORT_DIR/${TIMESTAMP}-tinyllama-inspect.log"
  set +e
  python "$SERVICE_SCRIPT" "${inspect_args[@]}" >"$inspect_output" 2>"$inspect_error"
  local inspect_status=$?
  set -e
  [[ "$inspect_status" -eq 0 ]] || die "TinyLlama descriptor inspection failed; stdout=$inspect_output stderr=$inspect_error"
  python3 - "$inspect_output" "$CONTRACT_PATH" <<'PY'
import json
import os
import sys

source_path, contract_path = sys.argv[1:]
with open(source_path, encoding="utf-8") as source:
    value = json.load(source)
if not isinstance(value, dict) or value.get("schema_version") != 1:
    raise SystemExit("inspect output is not a TinyLlama contract")
temporary = contract_path + ".tmp"
os.makedirs(os.path.dirname(contract_path), exist_ok=True)
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(value, output, ensure_ascii=False, indent=2)
    output.write("\n")
os.replace(temporary, contract_path)
PY
  [[ -f "$CONTRACT_PATH" ]] || die "inspect did not write contract: $CONTRACT_PATH"
  bind_contract_source
  python3 - "$CONTRACT_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    contract = json.load(source)
if contract.get("schema_version") != 1:
    raise SystemExit("unsupported TinyLlama contract schema")
if contract.get("model", {}).get("family") != "tinyllama":
    raise SystemExit("contract family is not tinyllama")
if contract.get("model", {}).get("model_id") != "tiny-llama-1.1b-acl-om":
    raise SystemExit("contract model id mismatch")
model = contract.get("model", {})
expected = {"vocabulary_size": 32000, "num_layers": 22, "num_kv_heads": 4, "head_dim": 64, "max_sequence_length": 1024}
for field, value in expected.items():
    if model.get(field) != value:
        raise SystemExit(f"contract {field} must be {value}")
acl_om = contract.get("acl_om", {})
if acl_om.get("execution_mode") != "kv_cache_token":
    raise SystemExit("contract execution mode is not kv_cache_token")
if contract.get("acl_om", {}).get("input_order_verified") is not True:
    raise SystemExit("input order was not verified")
print("contract_admitted_candidate=true")
PY
  echo "TinyLlama descriptor inspection passed; contract=$CONTRACT_PATH"
}

bind_contract_source() {
  local actual_bytes actual_sha revision
  actual_bytes="$(stat -c '%s' "$OM_PATH")"
  actual_sha="$(sha256sum "$OM_PATH" | awk '{print $1}')"
  revision="$(manifest_value "$OM_KEY" revision)"
  python3 - "$CONTRACT_PATH" "$MANIFEST" "$actual_bytes" "$actual_sha" "$revision" <<'PY'
import json
import os
import sys

contract_path, manifest_path, actual_bytes, actual_sha, revision = sys.argv[1:]
with open(contract_path, encoding="utf-8") as source:
    contract = json.load(source)
with open(manifest_path, encoding="utf-8") as source:
    manifest = json.load(source)
expected = manifest["artifacts"]["tinyllama_acl_om"]
if int(actual_bytes) != int(expected["expected_bytes"]):
    raise SystemExit("contract source bytes do not match manifest")
if expected.get("sha256") and actual_sha.lower() != expected["sha256"].lower():
    raise SystemExit("contract source SHA-256 does not match manifest")
if revision != expected["revision"]:
    raise SystemExit("contract source revision does not match manifest")
existing = contract.get("source_artifact")
if existing and (int(existing.get("bytes", -1)) != int(actual_bytes) or str(existing.get("sha256", "")).lower() != actual_sha.lower()):
    raise SystemExit("existing contract source binding differs from current OM")
contract["source_artifact"] = {"bytes": int(actual_bytes), "sha256": actual_sha.lower()}
contract["source_revision"] = revision
temporary = contract_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(contract, output, ensure_ascii=False, indent=2)
    output.write("\n")
os.replace(temporary, contract_path)
print("source_artifact_bound=true")
PY
}

verify_contract_binding() {
  local actual_bytes actual_sha revision
  [[ -f "$CONTRACT_PATH" ]] || die "contract is unavailable: $CONTRACT_PATH"
  actual_bytes="$(stat -c '%s' "$OM_PATH")"
  actual_sha="$(sha256sum "$OM_PATH" | awk '{print $1}')"
  revision="$(manifest_value "$OM_KEY" revision)"
  python3 - "$CONTRACT_PATH" "$actual_bytes" "$actual_sha" "$revision" <<'PY'
import json
import sys

contract_path, actual_bytes, actual_sha, revision = sys.argv[1:]
with open(contract_path, encoding="utf-8") as source:
    contract = json.load(source)
artifact = contract.get("source_artifact", {})
if int(artifact.get("bytes", -1)) != int(actual_bytes):
    raise SystemExit("contract is not bound to the current OM byte count")
if str(artifact.get("sha256", "")).lower() != actual_sha.lower():
    raise SystemExit("contract is not bound to the current OM SHA-256")
if contract.get("source_revision") != revision:
    raise SystemExit("contract is not bound to the manifest revision")
model = contract.get("model", {})
for field, expected in {
    "vocabulary_size": 32000,
    "num_layers": 22,
    "num_kv_heads": 4,
    "head_dim": 64,
    "max_sequence_length": 1024,
}.items():
    if model.get(field) != expected:
        raise SystemExit(f"contract {field} is not admitted")
if contract.get("acl_om", {}).get("execution_mode") != "kv_cache_token":
    raise SystemExit("contract execution mode is not admitted")
print("contract_source_binding=verified")
PY
}

verify_onnx_contract_binding() {
  local report_sha onnx_bytes onnx_sha locked_sha
  [[ -f "$ONNX_CONTRACT_REPORT" ]] || die "ONNX static contract report is unavailable: $ONNX_CONTRACT_REPORT"
  case "$ONNX_CONTRACT_REPORT" in
    "$HOME_DIR"/*) ;;
    *) die "ONNX static contract report must remain under the TinyLlama quarantine directory: $HOME_DIR" ;;
  esac
  [[ "$ONNX_CONTRACT_SHA256" =~ ^[0-9a-fA-F]{64}$ ]] \
    || die "set CASE9_TINYLLAMA_ONNX_CONTRACT_SHA256 to the reviewed report SHA-256"
  report_sha="$(sha256sum "$ONNX_CONTRACT_REPORT" | awk '{print $1}')"
  [[ "$report_sha" == "${ONNX_CONTRACT_SHA256,,}" ]] \
    || die "ONNX static contract report SHA-256 does not match the approved digest"
  onnx_bytes="$(stat -c '%s' "$ONNX_PATH")"
  onnx_sha="$(sha256sum "$ONNX_PATH" | awk '{print $1}')"
  locked_sha="$(lock_sha "$ONNX_KEY")"
  [[ -n "$locked_sha" && "$locked_sha" == "${onnx_sha,,}" ]] \
    || die "ONNX artifact is not bound to a board-local verified SHA-256 lock"
  python3 - "$ONNX_CONTRACT_REPORT" "$onnx_bytes" "$onnx_sha" <<'PY'
import json
import sys

report_path, actual_bytes, actual_sha = sys.argv[1:]
with open(report_path, encoding="utf-8") as source:
    report = json.load(source)
if not isinstance(report, dict):
    raise SystemExit("ONNX static contract report must be a JSON object")
if report.get("status") not in {"admitted", "contract_admitted"}:
    raise SystemExit("ONNX static contract report is not admitted")
artifact = report.get("source_artifact") or report.get("artifact")
if not isinstance(artifact, dict):
    raise SystemExit("ONNX static contract report has no source_artifact binding")
if int(artifact.get("bytes", -1)) != int(actual_bytes):
    raise SystemExit("ONNX static contract report byte count differs from the graph")
if str(artifact.get("sha256", "")).lower() != actual_sha.lower():
    raise SystemExit("ONNX static contract report SHA-256 differs from the graph")
contract = report.get("contract") or report
expected = {
    "input_ids": [1, 1],
    "attention_mask": [1, 1025],
    "position_ids": [1, 1],
    "past_key_values": [22, 2, 1, 4, 1024, 64],
}
inputs = contract.get("inputs")
if isinstance(inputs, dict):
    normalized = {str(name): list(value.get("shape", value)) if isinstance(value, dict) else list(value)
                  for name, value in inputs.items()}
elif isinstance(contract.get("input_shape"), dict):
    normalized = {str(name): list(value) for name, value in contract["input_shape"].items()}
else:
    raise SystemExit("ONNX static contract report has no input shape map")
if normalized != expected:
    raise SystemExit(f"ONNX input contract differs from the admitted static shape: {normalized!r}")
print("onnx_static_contract=verified")
PY
}

smoke_model() {
  require_board
  require_manifest
  activate_runtime
  source_cann
  check_forbidden_modules
  [[ -f "$SERVICE_SCRIPT" ]] || die "TinyLlama service is unavailable: $SERVICE_SCRIPT"
  verify_artifact "$OM_KEY"
  verify_artifact "$TOKENIZER_KEY"
  [[ -f "$TOKENIZER_DIR/tokenizer.json" ]] || extract_tokenizer
  verify_extracted_tokenizer "$TOKENIZER_DIR"
  verify_contract_binding
  local before="$REPORT_DIR/${TIMESTAMP}-tinyllama-smoke-npu-before.log"
  local after="$REPORT_DIR/${TIMESTAMP}-tinyllama-smoke-npu-after.log"
  local log="$REPORT_DIR/${TIMESTAMP}-tinyllama-smoke.log"
  npu-smi info >"$before" 2>&1 || die "pre-smoke npu-smi failed"
  set +e
  # Follow a TERM with a hard kill so a native ACL call cannot leave the
  # diagnostic process holding device resources after the smoke deadline.
  timeout --kill-after=5s "${CASE9_TINYLLAMA_SMOKE_TIMEOUT:-60}" python "$SERVICE_SCRIPT" smoke \
    --contract "$CONTRACT_PATH" --om "$OM_PATH" --tokenizer "$TOKENIZER_DIR/tokenizer.json" \
    --manifest "$MANIFEST" \
    --prompt "你好" --max-tokens 8 >"$log" 2>&1
  local status=$?
  set -e
  npu-smi info >"$after" 2>&1 || true
  echo "smoke_exit=$status" >>"$log"
  [[ "$status" -eq 0 ]] || die "TinyLlama ACL smoke failed; log=$log before=$before after=$after"
  echo "TinyLlama ACL smoke passed; log=$log before=$before after=$after"
}

convert_model() {
  require_board
  require_manifest
  [[ "${CASE9_TINYLLAMA_ALLOW_ATC:-}" == "1" ]] \
    || die "ATC/custom OPP branch is blocked; set CASE9_TINYLLAMA_ALLOW_ATC=1 only after explicit approval"
  [[ "${CASE9_TINYLLAMA_ONNX_CONTRACT:-}" == "admitted" ]] \
    || die "ONNX contract is not admitted; set CASE9_TINYLLAMA_ONNX_CONTRACT=admitted only after a separate static audit"
  [[ -n "${ASCEND_CUSTOM_OPP_PATH:-}" ]] || die "authorized ATC branch requires an isolated ASCEND_CUSTOM_OPP_PATH"
  [[ -f "$ONNX_PATH" ]] || die "optional ONNX is unavailable; run download-onnx explicitly"
  [[ -n "$(lock_sha "$ONNX_KEY")" ]] || die "download-onnx must verify and lock the ONNX SHA-256 before ATC"
  verify_artifact "$ONNX_KEY"
  verify_onnx_contract_binding
  activate_runtime
  source_cann_for_authorized_atc
  check_forbidden_modules
  command -v atc >/dev/null || die "atc unavailable"
  mkdir -p "$ARTIFACT_DIR/om" "$REPORT_DIR"
  local final_om="$ARTIFACT_DIR/om/tiny-llama.om"
  local staging="$ARTIFACT_DIR/om/.tiny-llama-atc-$TIMESTAMP"
  local prefix="$staging/tiny-llama"
  local log="$REPORT_DIR/${TIMESTAMP}-tinyllama-atc.log"
  mkdir -p "$staging"
  {
    echo "utc=$TIMESTAMP"
    echo "command=atc --framework=5 --model=$ONNX_PATH --output=$prefix --input_format=ND --input_shape=input_ids:1,1;attention_mask:1,1025;position_ids:1,1;past_key_values:22,2,1,4,1024,64 --soc_version=$SOC_VERSION --precision_mode=must_keep_origin_dtype"
    atc --framework=5 --model="$ONNX_PATH" --output="$prefix" --input_format=ND \
      --input_shape="input_ids:1,1;attention_mask:1,1025;position_ids:1,1;past_key_values:22,2,1,4,1024,64" \
      --soc_version="$SOC_VERSION" --precision_mode=must_keep_origin_dtype
  } >"$log" 2>&1 || die "ATC failed; log=$log"
  [[ -f "${prefix}.om" ]] || die "ATC returned success but OM is missing; log=$log"
  if [[ -e "$final_om" ]]; then
    mv "$final_om" "$final_om.stale-$TIMESTAMP"
  fi
  mv "${prefix}.om" "$final_om"
  rmdir "$staging" 2>/dev/null || true
  sha256sum "$final_om" >"$REPORT_DIR/${TIMESTAMP}-tinyllama-om.sha256"
  stat -c '%n bytes=%s' "$final_om" >>"$REPORT_DIR/${TIMESTAMP}-tinyllama-om.sha256"
  echo "ATC succeeded only in explicitly authorized branch; log=$log"
}

serve_model() {
  require_board
  require_manifest
  activate_runtime
  source_cann
  check_forbidden_modules
  [[ -f "$SERVICE_SCRIPT" ]] || die "TinyLlama service is unavailable: $SERVICE_SCRIPT"
  verify_artifact "$OM_KEY"
  verify_artifact "$TOKENIZER_KEY"
  [[ -f "$TOKENIZER_DIR/tokenizer.json" ]] || extract_tokenizer
  verify_extracted_tokenizer "$TOKENIZER_DIR"
  verify_contract_binding
  local host="${CASE9_TINYLLAMA_HOST:-127.0.0.1}"
  local port="${CASE9_TINYLLAMA_PORT:-8080}"
  [[ "$host" == "127.0.0.1" ]] || die "TinyLlama service must remain loopback-only"
  exec python "$SERVICE_SCRIPT" serve --contract "$CONTRACT_PATH" --om "$OM_PATH" \
    --tokenizer "$TOKENIZER_DIR/tokenizer.json" --manifest "$MANIFEST" \
    --host "$host" --port "$port"
}

command="${1:-}"
case "$command" in
  check) check_environment ;;
  create-env) create_env ;;
  install-runtime) install_runtime ;;
  download) download ;;
  download-onnx) download_onnx ;;
  inspect) inspect_model ;;
  smoke) smoke_model ;;
  convert) convert_model ;;
  serve) serve_model ;;
  -h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
