#!/usr/bin/env bash
# Board-only, fail-closed workflow for the fixed Qwen1.5 ONNX -> OM -> ACL
# experiment. This script installs only reviewed hash-locked runtime wheels
# and never provides a CPU/Torch/cloud fallback. Run each gate explicitly and retain the generated
# logs under CASE9_LOCAL_CHAT_HOME/reports/acl-om.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="${CASE9_LOCAL_MODEL_MANIFEST:-$REPO_DIR/local_model_manifest.json}"
HOME_DIR="${CASE9_LOCAL_CHAT_HOME:-$HOME/case9-local-chat}"
ARTIFACT_DIR="${CASE9_ACL_OM_ARTIFACT_DIR:-$HOME_DIR/artifacts/acl-om}"
REPORT_DIR="${CASE9_ACL_OM_REPORT_DIR:-$HOME_DIR/reports/acl-om}"
LOCK_FILE="${CASE9_ACL_OM_LOCK_FILE:-$ARTIFACT_DIR/acl-om-artifacts.lock.json}"
ENV_NAME="${CASE9_ACL_OM_ENV:-case9-acl-om}"
CONDA_ROOT="${CASE9_CONDA_ROOT:-/usr/local/miniconda3}"
CONDA_SH="${CASE9_CONDA_SH:-$CONDA_ROOT/etc/profile.d/conda.sh}"
CANN_ENV="${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
SOC_VERSION="Ascend310B4"
MODEL_ARTIFACT="acl_om_llm"
TOKENIZER_ARTIFACT="acl_om_tokenizer"
CONTRACT_PATH="${CASE9_ACL_OM_CONTRACT:-$REPORT_DIR/qwen1.5-0.5b-acl-contract.json}"
INSPECT_REPORT_PATH="${CASE9_ACL_OM_INSPECT_REPORT:-$REPORT_DIR/qwen1.5-0.5b-onnx-inspection.json}"
OM_DIR="${CASE9_ACL_OM_DIR:-$ARTIFACT_DIR/om}"
OM_PREFIX="${CASE9_ACL_OM_PREFIX:-$OM_DIR/qwen1.5-0.5b-chat-acl-om}"
SERVICE_SCRIPT="${CASE9_ACL_OM_SERVICE_SCRIPT:-$REPO_DIR/acl_om_service.py}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

FORBIDDEN_MODULES=(
  torch torch_npu torchaudio mindtorch torchvision xformers
  transformers vllm mindie qwen_ascend_llm onnxruntime
)

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_acl_om_board.sh <command>

Commands:
  check       Verify aarch64, conda env, CANN/ACL, Ascend310B4, and no Torch.
  create-env  Create only the case9-acl-om Python 3.9 conda environment.
  install-runtime  Legacy Qwen path; disabled unless
                   CASE9_ALLOW_LEGACY_QWEN_ACL_OM=1 is explicit.
  download    Download fixed ONNX/tokenizer/config artifacts and verify bytes/SHA.
  inspect     Inspect ONNX graph and write the strict ACL contract/report.
  convert     Run ATC only when the strict contract is admitted.
  smoke       Run one native ACL Chinese generation and capture npu-smi evidence.
  serve       Launch acl_om_service.py on 127.0.0.1:8080 after all gates.

The script never removes an environment or edits shell startup files. The
create-env command only creates the exact named Python 3.9 environment.
install-runtime uses requirements-acl-om.txt with --no-deps and
--require-hashes. No Torch-family package is permitted.
This legacy Qwen workflow is disabled by default; set
CASE9_ALLOW_LEGACY_QWEN_ACL_OM=1 only with separate approval. Use
scripts/provision_tinyllama_board.sh for the current no-Torch path.
EOF
}

die() {
  echo "ACL/OM gate failed: $*" >&2
  exit 1
}

require_board() {
  [[ "$(uname -m)" == "aarch64" ]] || die "run this command on the Ascend board (aarch64)"
}

require_manifest() {
  [[ -f "$MANIFEST" ]] || die "manifest is unavailable: $MANIFEST"
  python3 - "$MANIFEST" <<'PY' || exit 1
import json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    document = json.load(source)
artifacts = document.get("artifacts", {})
for name in ("acl_om_llm", "acl_om_tokenizer"):
    if name not in artifacts:
        raise SystemExit(f"manifest is missing required artifact {name}")
    item = artifacts[name]
    for field in ("repository", "revision", "filename", "url", "expected_bytes", "sha256"):
        if not item.get(field):
            raise SystemExit(f"manifest artifact {name} has no {field}")
    if int(item["expected_bytes"]) <= 0:
        raise SystemExit(f"manifest artifact {name} has invalid expected_bytes")
    if len(str(item["sha256"])) != 64:
        raise SystemExit(f"manifest artifact {name} has invalid SHA-256")
PY
}

manifest_value() {
  local artifact="$1" field="$2"
  python3 - "$MANIFEST" "$artifact" "$field" <<'PY'
import json, sys
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

safe_relative_filename() {
  local filename="$1"
  # Bash variables cannot contain NUL bytes; validate the remaining path
  # boundary explicitly instead of using an empty ``$'\0'`` pattern.
  [[ -n "$filename" && "$filename" != /* ]] || die "invalid artifact filename"
  case "/$filename/" in
    */../*|*/./*) die "artifact filename contains a traversal component: $filename" ;;
  esac
  printf '%s\n' "$filename"
}

artifact_path() {
  local artifact="$1"
  local filename
  filename="$(safe_relative_filename "$(manifest_value "$artifact" filename)")"
  printf '%s/%s\n' "$ARTIFACT_DIR" "$filename"
}

source_cann() {
  [[ -f "$CANN_ENV" ]] || die "CANN environment script is unavailable: $CANN_ENV"
  [[ -z "${ASCEND_CUSTOM_OPP_PATH:-}" ]] || die "ASCEND_CUSTOM_OPP_PATH is set before CANN setup; custom OPP is forbidden"
  # CANN's script reads optional unset variables, so nounset must be relaxed
  # only for this source operation.
  set +u
  # shellcheck disable=SC1090
  source "$CANN_ENV"
  set -u
  [[ -z "${ASCEND_CUSTOM_OPP_PATH:-}" ]] \
    || die "ASCEND_CUSTOM_OPP_PATH was set by CANN setup; custom OPP is forbidden"
}

activate_runtime() {
  [[ -f "$CONDA_SH" ]] || die "conda profile script is unavailable: $CONDA_SH"
  # shellcheck disable=SC1090
  set +u
  source "$CONDA_SH"
  set -u
  conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME" \
    || die "isolated conda environment is missing: $ENV_NAME (no environment is created by this script)"
  conda activate "$ENV_NAME"
  export PYTHONNOUSERSITE=1
  [[ "$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" == "3.9" ]] \
    || die "the active environment must be Python 3.9"
}

check_forbidden_modules() {
  local result
  result="$(python - "${FORBIDDEN_MODULES[@]}" <<'PY'
import importlib.util, sys
found = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is not None]
print(" ".join(found))
PY
)"
  [[ -z "$result" ]] || die "forbidden Torch-family modules are installed: $result"
  echo "forbidden_modules=none (${FORBIDDEN_MODULES[*]})"
}

create_env() {
  require_board
  [[ -f "$CONDA_SH" ]] || die "conda profile script is unavailable: $CONDA_SH"
  [[ "$ENV_NAME" == "case9-acl-om" ]] \
    || die "refusing to create an unexpected environment name: $ENV_NAME"
  set +u
  source "$CONDA_SH"
  set -u
  if conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
    echo "Conda environment already exists: $ENV_NAME"
    activate_runtime
    check_forbidden_modules
    return 0
  fi
  conda create --yes --name "$ENV_NAME" python=3.9
  conda run --no-capture-output --name "$ENV_NAME" python -c \
    'import sys; assert sys.version_info[:2] == (3, 9); print(sys.executable)'
  activate_runtime
  check_forbidden_modules
}

install_runtime() {
  [[ "${CASE9_ALLOW_LEGACY_QWEN_ACL_OM:-}" == "1" ]] \
    || die "legacy Qwen ACL/OM runtime is disabled; use provision_tinyllama_board.sh"
  require_board
  activate_runtime
  check_forbidden_modules
  [[ -f "$REPO_DIR/requirements-acl-om.txt" ]] \
    || die "locked ACL/OM requirements are unavailable"
  mkdir -p "$REPORT_DIR"
  local install_log="$REPORT_DIR/$TIMESTAMP-install-runtime.log"
  {
    echo "utc=$TIMESTAMP"
    echo "environment=$ENV_NAME"
    echo "python=$(python -c 'import sys; print(sys.executable)')"
    echo "command=python -m pip install --no-deps --require-hashes --only-binary=:all: --no-index -r requirements-acl-om.txt"
    python -m pip install --disable-pip-version-check --no-deps --require-hashes \
      --only-binary=:all: --no-index -r "$REPO_DIR/requirements-acl-om.txt"
  } >"$install_log" 2>&1 || die "locked runtime installation failed; log=$install_log"
  check_forbidden_modules
  python - <<'PY' || die "installed runtime import check failed"
import numpy
import onnx
import tokenizers
print("numpy", numpy.__version__)
print("onnx", onnx.__version__)
print("tokenizers", tokenizers.__version__)
PY
  echo "No-Torch ACL/OM runtime installation passed; log=$install_log"
}

capture() {
  local label="$1"; shift
  mkdir -p "$REPORT_DIR"
  local path="$REPORT_DIR/${TIMESTAMP}-${label}.log"
  set +e
  "$@" >"$path" 2>&1
  local status=$?
  set -e
  echo "[$label] exit=$status log=$path"
  [[ "$status" -eq 0 ]] || return "$status"
}

check_environment() {
  require_board
  require_manifest
  mkdir -p "$REPORT_DIR"
  local disk_log="$REPORT_DIR/${TIMESTAMP}-disk.log"
  df -Pk "$HOME_DIR" >"$disk_log" 2>&1 || die "cannot inspect free disk space; log=$disk_log"
  cat "$disk_log"
  local available_kib
  available_kib="$(awk 'NR == 2 {print $4}' "$disk_log")"
  [[ "$available_kib" =~ ^[0-9]+$ && "$available_kib" -ge 2097152 ]] \
    || die "at least 2 GiB of free disk space is required for the pinned ONNX workflow; log=$disk_log"
  activate_runtime
  source_cann
  command -v atc >/dev/null || die "atc is unavailable after sourcing CANN"
  command -v npu-smi >/dev/null || die "npu-smi is unavailable"
  command -v curl >/dev/null || die "curl is unavailable"
  command -v sha256sum >/dev/null || die "sha256sum is unavailable"
  python -c 'import acl; print("acl import OK")' \
    || die "native acl cannot be imported in the isolated environment"
  check_forbidden_modules
  local npu_log="$REPORT_DIR/${TIMESTAMP}-npu-smi.log"
  set +e
  npu-smi info >"$npu_log" 2>&1
  local npu_status=$?
  set -e
  cat "$npu_log"
  [[ "$npu_status" -eq 0 ]] || die "npu-smi info failed; log=$npu_log"
  grep -Eiq '310B4' "$npu_log" \
    || die "npu-smi did not identify Ascend310B4; log=$npu_log"
  local cann_log="$REPORT_DIR/${TIMESTAMP}-cann-version.log"
  {
    echo "ASCEND_TOOLKIT_HOME=${ASCEND_TOOLKIT_HOME:-}"
    echo "ASCEND_HOME_PATH=${ASCEND_HOME_PATH:-}"
    echo "ASCEND_CUSTOM_OPP_PATH=${ASCEND_CUSTOM_OPP_PATH:-}"
    echo "ATC_PATH=$(command -v atc)"
    for component in toolkit atc runtime; do
      version_file="${ASCEND_TOOLKIT_HOME:-}/$component/version.info"
      if [[ -f "$version_file" ]]; then
        echo "[$version_file]"
        cat "$version_file"
      fi
    done
    python -c 'import sys; print(sys.executable); print(sys.version)'
  } >"$cann_log" 2>&1
  echo "Environment check passed; reports=$REPORT_DIR"
}

is_lfs_pointer() {
  local path="$1"
  [[ "$(head -c 128 "$path" 2>/dev/null || true)" == *"version https://git-lfs.github.com/spec/v1"* ]]
}

verify_artifact() {
  local artifact="$1"
  local path expected_bytes expected_sha actual_bytes actual_sha repository revision url locked_sha
  path="$(artifact_path "$artifact")"
  [[ -f "$path" ]] || die "$artifact is unavailable: $path"
  if is_lfs_pointer "$path"; then
    die "$artifact is a Git LFS pointer, not a model artifact: $path"
  fi
  expected_bytes="$(manifest_value "$artifact" expected_bytes)"
  expected_sha="$(manifest_value "$artifact" sha256)"
  actual_bytes="$(stat -c '%s' "$path")"
  [[ "$actual_bytes" == "$expected_bytes" ]] \
    || die "$artifact byte mismatch: got $actual_bytes expected $expected_bytes"
  actual_sha="$(sha256sum "$path" | awk '{print $1}')"
  if [[ -n "$expected_sha" ]]; then
    [[ "$actual_sha" == "$expected_sha" ]] \
      || die "$artifact SHA-256 mismatch: got $actual_sha expected $expected_sha"
  else
    locked_sha="$(python3 - "$LOCK_FILE" "$artifact" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as source:
        print(json.load(source).get("artifacts", {}).get(sys.argv[2], {}).get("sha256", ""))
except FileNotFoundError:
    print("")
PY
)"
    if [[ -n "$locked_sha" && "$actual_sha" != "$locked_sha" ]]; then
      die "$artifact SHA-256 differs from its board-local lock: got $actual_sha expected $locked_sha"
    fi
  fi
  repository="$(manifest_value "$artifact" repository)"
  revision="$(manifest_value "$artifact" revision)"
  url="$(manifest_value "$artifact" url)"
  [[ "$url" == *"huggingface.co/$repository/resolve/$revision/"* ]] \
    || die "$artifact URL is not pinned to repository/revision: $url"
  python3 - "$LOCK_FILE" "$artifact" "$path" "$repository" "$revision" "$actual_bytes" "$actual_sha" <<'PY'
import json, os, sys
from datetime import datetime, timezone
lock, artifact, path, repository, revision, size, sha = sys.argv[1:]
try:
    with open(lock, encoding="utf-8") as source:
        document = json.load(source)
except FileNotFoundError:
    document = {"schema_version": 1, "artifacts": {}}
document.setdefault("schema_version", 1)
document.setdefault("artifacts", {})
old = document["artifacts"].get(artifact)
if old and old.get("sha256") and old["sha256"] != sha:
    raise SystemExit(f"locked SHA changed for {artifact}")
document["artifacts"][artifact] = {
    "path": path,
    "repository": repository,
    "revision": revision,
    "bytes": int(size),
    "sha256": sha,
    "verified_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}
temporary = lock + ".tmp"
os.makedirs(os.path.dirname(lock), exist_ok=True)
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(document, output, ensure_ascii=True, indent=2, sort_keys=True)
    output.write("\n")
os.replace(temporary, lock)
PY
  echo "Verified $artifact: bytes=$actual_bytes sha256=$actual_sha path=$path"
}

download_artifact() {
  local artifact="$1"
  local path url expected_bytes expected_sha part actual_sha locked_sha download_log curl_status
  path="$(artifact_path "$artifact")"
  url="$(manifest_value "$artifact" url)"
  expected_bytes="$(manifest_value "$artifact" expected_bytes)"
  expected_sha="$(manifest_value "$artifact" sha256)"
  mkdir -p "$(dirname "$path")"
  if [[ -f "$path" ]]; then
    verify_artifact "$artifact"
    return 0
  fi
  part="$path.part"
  [[ ! -e "$part" ]] || die "stale partial download exists; inspect or remove explicitly: $part"
  echo "Downloading pinned $artifact to $path"
  download_log="$REPORT_DIR/${TIMESTAMP}-${artifact}-download.log"
  set +e
  curl --http1.1 --fail --location --retry 4 --retry-delay 3 \
    --connect-timeout 15 --max-time 3600 --output "$part" "$url" \
    >"$download_log" 2>&1
  curl_status=$?
  set -e
  echo "curl_exit=$curl_status" >>"$download_log"
  cat "$download_log"
  [[ "$curl_status" -eq 0 ]] || die "$artifact download failed; log=$download_log"
  if is_lfs_pointer "$part"; then
    die "$artifact download is a Git LFS pointer: $part"
  fi
  [[ "$(stat -c '%s' "$part")" == "$expected_bytes" ]] \
    || die "$artifact download size mismatch; retaining failed file $part"
  actual_sha="$(sha256sum "$part" | awk '{print $1}')"
  if [[ -n "$expected_sha" ]]; then
    [[ "$actual_sha" == "$expected_sha" ]] \
      || die "$artifact download SHA-256 mismatch; retaining failed file $part"
  else
    locked_sha="$(python3 - "$LOCK_FILE" "$artifact" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as source:
        print(json.load(source).get("artifacts", {}).get(sys.argv[2], {}).get("sha256", ""))
except FileNotFoundError:
    print("")
PY
)"
    [[ -z "$locked_sha" || "$actual_sha" == "$locked_sha" ]] \
      || die "$artifact download SHA-256 differs from board-local lock"
  fi
  mv "$part" "$path"
  verify_artifact "$artifact"
}

download_artifacts() {
  require_board
  require_manifest
  mkdir -p "$ARTIFACT_DIR" "$REPORT_DIR"
  # Downloading does not require Python packages or CANN, but the board and
  # manifest gates still apply.  Never follow an operator-supplied mirror.
  download_artifact "$MODEL_ARTIFACT"
  download_artifact "$TOKENIZER_ARTIFACT"
  for optional in acl_om_config acl_om_tokenizer_config; do
    if python3 - "$MANIFEST" "$optional" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as source:
    raise SystemExit(0 if sys.argv[2] in json.load(source).get("artifacts", {}) else 1)
PY
    then
      download_artifact "$optional"
    fi
  done
}

inspect_onnx() {
  require_board
  require_manifest
  activate_runtime
  mkdir -p "$REPORT_DIR"
  local model_path
  model_path="$(artifact_path "$MODEL_ARTIFACT")"
  verify_artifact "$MODEL_ARTIFACT"
  [[ -f "$REPO_DIR/scripts/inspect_qwen_onnx.py" ]] \
    || die "ONNX inspector is unavailable in the repository"
  set +e
  python "$REPO_DIR/scripts/inspect_qwen_onnx.py" \
    --model "$model_path" --output "$CONTRACT_PATH" --report "$INSPECT_REPORT_PATH" \
    --source-revision "$(manifest_value "$MODEL_ARTIFACT" revision)"
  local status=$?
  set -e
  echo "ONNX inspection exit=$status contract=$CONTRACT_PATH report=$INSPECT_REPORT_PATH"
  [[ "$status" -eq 0 ]] || die "ONNX graph was not admitted; inspect report=$INSPECT_REPORT_PATH"
}

contract_is_admitted() {
  python3 - "$CONTRACT_PATH" "$MANIFEST" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
if not path.is_file():
    raise SystemExit(f"contract is missing: {path}")
with path.open(encoding="utf-8") as source:
    document = json.load(source)
with manifest_path.open(encoding="utf-8") as source:
    manifest = json.load(source)
expected = manifest["artifacts"]["acl_om_llm"]
expected_inputs = {
    "input_ids": {"name": "input_ids", "dtype": "int64", "shape": [1, 2048]},
    "attention_mask": {"name": "attention_mask", "dtype": "int64", "shape": [1, 2048]},
    "position_ids": {"name": "position_ids", "dtype": "int64", "shape": [1, 2048]},
}
expected_output = {"name": "logits", "dtype": "float16", "shape": [1, 2048, 151936]}
acl = document.get("acl_om", {})
if document.get("schema_version") != 1:
    raise SystemExit("unsupported contract schema")
if document.get("model", {}).get("model_id") != "qwen1.5-0.5b-chat-acl-om":
    raise SystemExit("contract model id mismatch")
source = document.get("source_artifact", {})
if source.get("bytes") != int(expected["expected_bytes"]):
    raise SystemExit("contract source byte count is not bound to the manifest")
if source.get("sha256") != str(expected["sha256"]).lower():
    raise SystemExit("contract source SHA-256 is not bound to the manifest")
if document.get("source_revision") != expected["revision"]:
    raise SystemExit("contract source revision is not bound to the manifest")
if acl.get("supported_autoregressive_qwen_layout") is not True:
    raise SystemExit(f"contract is not admitted: {acl.get('support_reason', 'no reason')}")
if acl.get("execution_mode") != "full_context_logits" or acl.get("static_sequence_length") != 2048:
    raise SystemExit("contract execution mode or sequence length mismatch")
if acl.get("input_order") != ["input_ids", "attention_mask", "position_ids"] or acl.get("input_order_verified") is not True:
    raise SystemExit("contract input order is not explicitly verified")
if acl.get("inputs") != expected_inputs or acl.get("output", {}).get("logits") != expected_output:
    raise SystemExit("contract tensor descriptors mismatch")
audit = acl.get("operator_audit", {})
opset = audit.get("opset")
if isinstance(opset, bool) or not isinstance(opset, int) or not 13 <= opset <= 18:
    raise SystemExit("contract ONNX opset is outside the admitted range")
if audit.get("unsupported_operators"):
    raise SystemExit("contract contains unsupported ONNX operators")
PY
}

verify_contract_source() {
  local model_path
  model_path="$(artifact_path "$MODEL_ARTIFACT")"
  verify_artifact "$MODEL_ARTIFACT"
  contract_is_admitted || die "contract is not bound to the verified ONNX artifact"
  python3 - "$CONTRACT_PATH" "$model_path" <<'PY'
import hashlib, json, sys
from pathlib import Path
contract_path, model_path = map(Path, sys.argv[1:])
with contract_path.open(encoding="utf-8") as source:
    contract = json.load(source)
artifact = contract.get("source_artifact", {})
actual_size = model_path.stat().st_size
if artifact.get("bytes") != actual_size:
    raise SystemExit("contract source bytes differ from the current ONNX file")
digest = hashlib.sha256()
with model_path.open("rb") as source:
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
if artifact.get("sha256") != digest.hexdigest():
    raise SystemExit("contract source SHA-256 differs from the current ONNX file")
PY
}

verify_om_lock() {
  local om_path="$1"
  local lock_path="$OM_DIR/om.lock.json"
  [[ -f "$om_path" ]] || die "OM is unavailable: $om_path"
  [[ -f "$lock_path" ]] || die "OM provenance lock is unavailable: $lock_path"
  local om_bytes om_sha contract_sha
  om_bytes="$(stat -c '%s' "$om_path")"
  [[ "$om_bytes" -gt 0 ]] || die "OM is empty: $om_path"
  om_sha="$(sha256sum "$om_path" | awk '{print $1}')"
  contract_sha="$(sha256sum "$CONTRACT_PATH" | awk '{print $1}')"
  python3 - "$lock_path" "$om_path" "$CONTRACT_PATH" "$om_bytes" "$om_sha" "$contract_sha" <<'PY'
import json, sys
from pathlib import Path

lock_path, om_path, contract_path, om_bytes, om_sha, contract_sha = sys.argv[1:]
with Path(lock_path).open(encoding="utf-8") as source:
    lock = json.load(source)
if lock.get("schema_version") != 1:
    raise SystemExit("unsupported OM provenance lock schema")
if Path(lock.get("om_path", "")).resolve() != Path(om_path).resolve():
    raise SystemExit("OM provenance lock path does not match the requested OM")
if int(lock.get("bytes", -1)) != int(om_bytes):
    raise SystemExit("OM bytes differ from the conversion lock")
if str(lock.get("sha256", "")).lower() != om_sha.lower():
    raise SystemExit("OM SHA-256 differs from the conversion lock")
if Path(lock.get("contract_path", "")).resolve() != Path(contract_path).resolve():
    raise SystemExit("OM lock contract path does not match the active contract")
if str(lock.get("contract_sha256", "")).lower() != contract_sha.lower():
    raise SystemExit("OM lock contract SHA-256 differs from the active contract")
PY
}

convert_onnx() {
  require_board
  require_manifest
  activate_runtime
  source_cann
  check_forbidden_modules
  verify_contract_source || die "ATC is blocked until the strict ONNX contract is bound to the verified ONNX artifact"
  local model_path om_path atc_log
  model_path="$(artifact_path "$MODEL_ARTIFACT")"
  verify_artifact "$MODEL_ARTIFACT"
  mkdir -p "$OM_DIR" "$REPORT_DIR"
  om_path="${OM_PREFIX}.om"
  if [[ -e "$om_path" ]]; then
    die "refusing to overwrite an existing OM; choose CASE9_ACL_OM_PREFIX explicitly: $om_path"
  fi
  atc_log="$REPORT_DIR/${TIMESTAMP}-atc.log"
  local shape="input_ids:1,2048;attention_mask:1,2048;position_ids:1,2048"
  local command_text
  command_text="atc --model=$model_path --framework=5 --output=$OM_PREFIX --input_format=ND --input_shape=$shape --soc_version=$SOC_VERSION --output_type=FP16"
  {
    echo "utc=$TIMESTAMP"
    echo "command=$command_text"
    echo "model=$model_path"
    echo "contract=$CONTRACT_PATH"
    echo "soc_version=$SOC_VERSION"
    echo "ASCEND_TOOLKIT_HOME=${ASCEND_TOOLKIT_HOME:-}"
    echo "ASCEND_CUSTOM_OPP_PATH=${ASCEND_CUSTOM_OPP_PATH:-}"
    set +e
    atc --model="$model_path" --framework=5 --output="$OM_PREFIX" \
      --input_format=ND --input_shape="$shape" --soc_version="$SOC_VERSION" \
      --output_type=FP16
    local status=$?
    set -e
    echo "atc_exit=$status"
    if [[ "$status" -ne 0 ]]; then
      exit "$status"
    fi
  } >"$atc_log" 2>&1 || die "ATC failed; log=$atc_log"
  [[ -f "$om_path" ]] || die "ATC returned success without OM: $om_path"
  if is_lfs_pointer "$om_path"; then
    die "ATC output is unexpectedly a Git LFS pointer: $om_path"
  fi
  local om_bytes om_sha contract_sha lock_path
  om_bytes="$(stat -c '%s' "$om_path")"
  [[ "$om_bytes" -gt 0 ]] || die "ATC produced an empty OM: $om_path"
  om_sha="$(sha256sum "$om_path" | awk '{print $1}')"
  contract_sha="$(sha256sum "$CONTRACT_PATH" | awk '{print $1}')"
  lock_path="$OM_DIR/om.lock.json"
  python3 - "$lock_path" "$om_path" "$om_bytes" "$om_sha" "$CONTRACT_PATH" "$contract_sha" "$atc_log" <<'PY'
import hashlib, json, os, sys
from datetime import datetime, timezone
lock, om_path, size, sha, contract, contract_sha, log = sys.argv[1:]
payload = {
    "schema_version": 1,
    "recorded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "om_path": om_path,
    "bytes": int(size),
    "sha256": sha,
    "contract_path": contract,
    "contract_sha256": contract_sha,
    "atc_log": log,
}
temporary = lock + ".tmp"
with open(temporary, "w", encoding="utf-8") as output:
    json.dump(payload, output, ensure_ascii=True, indent=2, sort_keys=True)
    output.write("\n")
os.replace(temporary, lock)
PY
  echo "ATC produced OM: bytes=$om_bytes sha256=$om_sha path=$om_path"
}

smoke_acl() {
  require_board
  require_manifest
  activate_runtime
  source_cann
  check_forbidden_modules
  verify_contract_source || die "ACL smoke is blocked until the strict contract is bound to the verified ONNX artifact"
  [[ -f "$SERVICE_SCRIPT" ]] || die "ACL service entrypoint is unavailable: $SERVICE_SCRIPT"
  local tokenizer_path om_path smoke_log before_log after_log timeout_seconds execution_timeout
  tokenizer_path="$(artifact_path "$TOKENIZER_ARTIFACT")"
  om_path="${OM_PREFIX}.om"
  verify_artifact "$MODEL_ARTIFACT"
  verify_artifact "$TOKENIZER_ARTIFACT"
  verify_om_lock "$om_path"
  timeout_seconds="${CASE9_ACL_OM_SMOKE_TIMEOUT:-300}"
  execution_timeout="${CASE9_ACL_OM_EXECUTION_TIMEOUT:-300}"
  smoke_log="$REPORT_DIR/${TIMESTAMP}-acl-smoke.log"
  before_log="$REPORT_DIR/${TIMESTAMP}-acl-smoke-npu-before.log"
  after_log="$REPORT_DIR/${TIMESTAMP}-acl-smoke-npu-after.log"
  npu-smi info >"$before_log" 2>&1 || die "pre-smoke npu-smi failed; log=$before_log"
  {
    echo "utc=$TIMESTAMP"
    echo "command=python $SERVICE_SCRIPT smoke --contract $CONTRACT_PATH --om $om_path --tokenizer $tokenizer_path --prompt 你好 --execution-timeout $execution_timeout"
    echo "cpu_fallback=disabled"
    echo "timeout_seconds=$timeout_seconds"
    set +e
    timeout "$timeout_seconds" python "$SERVICE_SCRIPT" smoke \
      --contract "$CONTRACT_PATH" --om "$om_path" --tokenizer "$tokenizer_path" \
      --prompt "你好" --execution-timeout "$execution_timeout"
    local status=$?
    set -e
    echo "smoke_exit=$status"
    if [[ "$status" -ne 0 ]]; then
      exit "$status"
    fi
  } >"$smoke_log" 2>&1 || {
    npu-smi info >"$after_log" 2>&1 || true
    die "ACL smoke failed; log=$smoke_log before=$before_log after=$after_log"
  }
  npu-smi info >"$after_log" 2>&1 || die "post-smoke npu-smi failed; log=$after_log"
  echo "ACL smoke passed; log=$smoke_log before=$before_log after=$after_log"
}

serve_acl() {
  require_board
  require_manifest
  activate_runtime
  source_cann
  check_forbidden_modules
  verify_contract_source || die "service launch is blocked until the strict contract is bound to the verified ONNX artifact"
  [[ -f "$SERVICE_SCRIPT" ]] || die "ACL service entrypoint is unavailable: $SERVICE_SCRIPT"
  local tokenizer_path om_path host port execution_timeout
  tokenizer_path="$(artifact_path "$TOKENIZER_ARTIFACT")"
  om_path="${OM_PREFIX}.om"
  verify_artifact "$MODEL_ARTIFACT"
  verify_artifact "$TOKENIZER_ARTIFACT"
  verify_om_lock "$om_path"
  host="${CASE9_ACL_OM_HOST:-127.0.0.1}"
  port="${CASE9_ACL_OM_PORT:-8080}"
  execution_timeout="${CASE9_ACL_OM_EXECUTION_TIMEOUT:-300}"
  [[ "$host" == "127.0.0.1" ]] || die "service host must remain 127.0.0.1 for this experiment"
  exec python "$SERVICE_SCRIPT" serve --host "$host" --port "$port" \
    --contract "$CONTRACT_PATH" --om "$om_path" --tokenizer "$tokenizer_path" \
    --execution-timeout "$execution_timeout"
}

command="${1:-}"
case "$command" in
  check) check_environment ;;
  create-env) create_env ;;
  install-runtime) install_runtime ;;
  download) download_artifacts ;;
  inspect) inspect_onnx ;;
  convert) convert_onnx ;;
  smoke) smoke_acl ;;
  serve) serve_acl ;;
  -h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
