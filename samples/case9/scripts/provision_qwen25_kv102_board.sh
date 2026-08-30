#!/usr/bin/env bash
# Board-side, no-Torch workflow for the Qwen2.5 1024-token static-KV candidate.
# This script is deliberately explicit: it never installs packages, changes
# system CANN/OPP, stops an unrelated process, or promotes the candidate.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT="${CASE9_QWEN25_KV_ROOT:-$HOME/case9-qwen25-kv1024}"
ARTIFACTS="${ROOT}/artifacts"
CONTRACTS="${ROOT}/contracts"
REPORTS="${ROOT}/reports"
LOGS="${ROOT}/logs"
RUN="${ROOT}/run"
ATC_DEPS="${CASE9_QWEN25_KV_ATC_DEPS:-${RUN}/atc-pythonpath}"
ATC_DECORATOR="${CASE9_QWEN25_KV_ATC_DECORATOR:-/usr/local/miniconda3/lib/python3.9/site-packages/decorator.py}"
ENV_NAME="${CASE9_QWEN25_KV_ENV:-case9-acl-om}"
ALLOW_DIRTY_BASE="${CASE9_QWEN25_KV_ALLOW_DIRTY_BASE:-0}"
CONDA_SH="${CASE9_CONDA_SH:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
CANN_ENV="${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
SOC_VERSION="${CASE9_QWEN25_KV_SOC_VERSION:-Ascend310B4}"
SEQUENCE_LENGTH=1024
MASK_LENGTH=1024
PORT="${CASE9_QWEN25_KV_PORT:-8084}"
MAX_TOKENS="${CASE9_QWEN25_KV_MAX_TOKENS:-80}"
MODEL_ID="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
ONNX="${CASE9_QWEN25_KV_ONNX:-${ARTIFACTS}/qwen25-static-kv-1024-v2.onnx}"
OM_PREFIX="${CASE9_QWEN25_KV_OM_PREFIX:-${ARTIFACTS}/qwen25-static-kv-1024-v2}"
OM="${OM_PREFIX}.om"
SOURCE_OM="${CASE9_QWEN25_KV_SOURCE_OM:-${ARTIFACTS}/qwen25-static-kv-1024-v2.om}"
TOKENIZER="${CASE9_QWEN25_KV_TOKENIZER:-${ARTIFACTS}/tokenizer.json}"
TOKENIZER_CONFIG="${CASE9_QWEN25_KV_TOKENIZER_CONFIG:-${ARTIFACTS}/tokenizer_config.json}"
OM_LOCK="${CASE9_QWEN25_KV_OM_LOCK:-${OM}.lock.json}"
TOKENIZER_LOCK="${CASE9_QWEN25_KV_TOKENIZER_LOCK:-${TOKENIZER}.lock.json}"
CONTRACT="${CASE9_QWEN25_KV_CONTRACT:-${CONTRACTS}/qwen25-static-kv-1024-fp32-contract.json}"
OM_CONTRACT="${CASE9_QWEN25_KV_OM_CONTRACT:-${CONTRACTS}/qwen25-static-kv-1024-v2-om-contract.json}"
INSPECT_REPORT="${CASE9_QWEN25_KV_INSPECT_REPORT:-${REPORTS}/qwen25-static-kv-1024-fp32-inspect.json}"
SERVICE_LOG="${CASE9_QWEN25_KV_SERVICE_LOG:-${LOGS}/qwen25-static-kv-1024-fp32-service-${PORT}.log}"
INSPECTOR="${CASE9_QWEN25_KV_INSPECTOR:-${REPO_DIR}/tools/inspect_qwen25_static_onnx.py}"

FORBIDDEN=(torch torch_npu torchaudio transformers onnxruntime mindspore mindtorch vllm mindie qwen_ascend_llm)

validate_soc_version() {
  case "$SOC_VERSION" in
    Ascend310B1|Ascend310B4) ;;
    *) die "unsupported CASE9_QWEN25_KV_SOC_VERSION: $SOC_VERSION (expected Ascend310B1 or Ascend310B4)" ;;
  esac
}

die() { echo "qwen25-kv102 gate failed: $*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: bash scripts/provision_qwen25_kv102_board.sh <command>

Commands:
  check    verify board, CANN, ACL, NPU and forbidden-package gates
  inspect  inspect the transferred static ONNX and write contract/report
  convert  run ATC from the admitted contract and write OM provenance
  smoke    run one native ACL request and save npu-smi evidence
  serve    launch the loopback-only candidate on port 8084
  status   print candidate paths and active process/listener information

The ONNX file must be transferred by an operator or deployment wrapper. This
script does not download from an unpinned URL and never changes system CANN.
EOF
}

require_board() { [[ "$(uname -m)" == "aarch64" ]] || die "run on the Ascend board"; }
source_runtime() {
  [[ -r "$CONDA_SH" ]] || die "missing conda profile: $CONDA_SH"
  # CANN scripts may reference unset variables; relax nounset only while sourcing.
  set +u; source "$CONDA_SH"; set -u
  conda activate "$ENV_NAME" || die "cannot activate conda environment: $ENV_NAME"
  # Some board shells leave the system Python first on PATH even after
  # conda activation.  Put the activated prefix first and fail closed if the
  # process is not the dedicated Python 3.9 runtime.
  [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]] || die "conda did not expose a usable prefix for $ENV_NAME"
  export PATH="${CONDA_PREFIX}/bin:${PATH}"
  hash -r 2>/dev/null || true
  export PYTHONNOUSERSITE=1
  [[ -r "$CANN_ENV" ]] || die "missing CANN environment: $CANN_ENV"
  set +u; source "$CANN_ENV"; set -u
  python - <<'PY'
import os
import pathlib
import sys
expected = pathlib.Path(os.environ["CONDA_PREFIX"]) / "bin" / "python"
if sys.version_info[:2] != (3, 9):
    raise SystemExit(f"case9-acl-om requires Python 3.9, got {sys.version}")
if pathlib.Path(sys.prefix).resolve() != pathlib.Path(os.environ["CONDA_PREFIX"]).resolve():
    raise SystemExit(f"wrong conda prefix: {sys.prefix}; expected {os.environ['CONDA_PREFIX']}")
if pathlib.Path(sys.executable).resolve().parent != expected.resolve().parent:
    raise SystemExit(f"wrong interpreter: {sys.executable}; expected a binary in {expected.parent}")
print(f"python={sys.executable} version={sys.version.split()[0]}")
PY
}
check_forbidden() {
  local found
  found="$(python - "${FORBIDDEN[@]}" <<'PY'
import importlib.util, sys
names = [name for name in sys.argv[1:] if importlib.util.find_spec(name) is not None]
print(" ".join(names))
PY
)"
  if [[ -n "$found" ]]; then
    if [[ "$ALLOW_DIRTY_BASE" != "1" || "$ENV_NAME" != "base" ]]; then
      die "forbidden packages are importable: $found"
    fi
    echo "WARNING: explicit dirty-base test override; forbidden packages remain importable: $found" >&2
  fi
  python - "${FORBIDDEN[@]}" <<'PY'
import pathlib
import site
import sys
blocked = set(sys.argv[1:])
try:
    user_site = pathlib.Path(site.getusersitepackages()).resolve()
except Exception:
    user_site = None
present = set()
if user_site is not None and user_site.is_dir():
    for entry in user_site.iterdir():
        name = entry.name.lower()
        for suffix in (".dist-info", ".egg-info"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        name = name.split("-")[0].replace("-", "_")
        if name in blocked or entry.name.replace("-", "_").lower() in blocked:
            present.add(name)
print("user_site=" + str(user_site) + " preexisting_forbidden=" + (" ".join(sorted(present)) if present else "none"))
PY
  python -c 'import acl; print("acl import: ok")' || die "acl import failed"
}
prepare_dirs() { mkdir -p "$ARTIFACTS" "$CONTRACTS" "$REPORTS" "$LOGS" "$RUN"; }
prepare_atc_pythonpath() {
  # CANN 8.0's TBE loader imports decorator, but the isolated runtime env is
  # intentionally free of the base environment's packages.  Expose only the
  # preinstalled single-file CANN dependency through a private path; never
  # add the system site-packages directory to PYTHONPATH.
  mkdir -p "$ATC_DEPS"
  if python -c 'import decorator' >/dev/null 2>&1; then
    echo "atc-decorator=runtime"
    return 0
  fi
  [[ -r "$ATC_DECORATOR" ]] || die "CANN ATC requires decorator.py, but no preinstalled isolated copy was found: $ATC_DECORATOR"
  local link="$ATC_DEPS/decorator.py"
  if [[ -e "$link" && ! -L "$link" ]]; then
    die "refusing to overwrite non-symlink ATC dependency path: $link"
  fi
  ln -sfn "$ATC_DECORATOR" "$link"
  python -c 'import decorator' >/dev/null 2>&1 || die "isolated CANN decorator.py is not importable"
  echo "atc-decorator=$ATC_DECORATOR sha256=$(sha256sum "$ATC_DECORATOR" | awk '{print $1}')"
}
check() {
  require_board; validate_soc_version; prepare_dirs; source_runtime
  command -v atc >/dev/null || die "atc is unavailable"
  command -v npu-smi >/dev/null || die "npu-smi is unavailable"
  command -v sha256sum >/dev/null || die "sha256sum is unavailable"
  check_forbidden
  local stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local npu_report="${REPORTS}/npu-before-${stamp}.txt"
  npu-smi info | tee "$npu_report"
  grep -Eq "${SOC_VERSION}|${SOC_VERSION#Ascend}" "$npu_report" || die "$SOC_VERSION was not identified"
  {
    echo "timestamp_utc=$stamp"
    echo "hostname=$(hostname)"
    echo "arch=$(uname -m)"
    uname -a
    echo
    echo "disk"
    df -h "$ROOT"
    echo
    echo "memory"
    if command -v free >/dev/null 2>&1; then free -h; else cat /proc/meminfo; fi
    echo
    echo "hugepages"
    grep -E 'HugePages|Hugetlb' /proc/meminfo || true
    echo
    echo "kernel_npu_version"
    cat /proc/driver/npu/version 2>/dev/null || echo unavailable
    echo
    echo "cann_version"
    for version_file in \
      "${ASCEND_TOOLKIT_HOME:-}/latest/version.cfg" \
      "/usr/local/Ascend/ascend-toolkit/latest/version.cfg" \
      "/usr/local/Ascend/ascend-toolkit/version.cfg"; do
      if [[ -r "$version_file" ]]; then
        echo "file=$version_file"
        sed -n '1,80p' "$version_file"
        break
      fi
    done
  } | tee "${REPORTS}/system-${stamp}.txt"
  python - <<'PY'
import sys
assert sys.version_info[:2] == (3, 9), sys.version
print(sys.executable)
PY
  echo "check passed: root=$ROOT env=$ENV_NAME soc=$SOC_VERSION model=$MODEL_ID"
}
require_onnx() {
  [[ -f "$ONNX" ]] || die "ONNX is missing: $ONNX"
  [[ "$(stat -Lc '%s' "$ONNX")" -gt 0 ]] || die "ONNX is empty"
  head -c 128 "$ONNX" | grep -q 'version https://git-lfs.github.com/spec/v1' && die "ONNX is an LFS pointer"
  sha256sum "$ONNX" | tee "${REPORTS}/onnx-sha256.txt"
}
ensure_tokenizer_lock() {
  [[ -f "$TOKENIZER" ]] || die "tokenizer is missing: $TOKENIZER"
  local bytes sha
  # Artifact paths may be symlinks into a separately verified model store;
  # lock the target file size, matching sha256sum and Python Path.stat().
  bytes="$(stat -Lc '%s' "$TOKENIZER")"
  sha="$(sha256sum "$TOKENIZER" | awk '{print $1}')"
  [[ "$bytes" =~ ^[0-9]+$ && "$bytes" -gt 0 ]] || die "tokenizer is empty: $TOKENIZER"
  [[ "$sha" =~ ^[0-9a-fA-F]{64}$ ]] || die "could not hash tokenizer: $TOKENIZER"
  if [[ -e "$TOKENIZER_LOCK" ]]; then
    [[ -f "$TOKENIZER_LOCK" ]] || die "tokenizer lock is not a regular file: $TOKENIZER_LOCK"
    python - "$TOKENIZER_LOCK" "$bytes" "$sha" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
raw = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(raw, dict) or raw.get("bytes") != int(sys.argv[2]) or str(raw.get("sha256", "")).lower() != sys.argv[3].lower():
    raise SystemExit("tokenizer lock does not match the verified tokenizer")
PY
  else
    local temporary="${TOKENIZER_LOCK}.part"
    mkdir -p "$(dirname "$TOKENIZER_LOCK")"
    python - "$temporary" "$TOKENIZER" "$bytes" "$sha" <<'PY'
import json, pathlib, sys
destination, tokenizer, size, digest = sys.argv[1:]
path = pathlib.Path(destination)
path.write_text(json.dumps({"schema_version": 1, "path": tokenizer, "bytes": int(size), "sha256": digest}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
path.replace(path.with_name(path.name.removesuffix(".part")))
PY
  fi
  echo "tokenizer lock verified: bytes=$bytes sha256=$sha path=$TOKENIZER_LOCK"
}
record_runtime_contract_lock() {
  # ``convert`` can only attest to the controller/export contract.  The ACL
  # smoke below is the first point where ATC's actual descriptor names and
  # order are known, so record its distinct hash only after that smoke passes.
  [[ -f "$OM_LOCK" ]] || die "OM lock is missing after conversion: $OM_LOCK"
  [[ -f "$CONTRACT" && -f "$OM_CONTRACT" ]] || die "controller or runtime contract is missing"
  python - "$OM_LOCK" "$OM" "$CONTRACT" "$OM_CONTRACT" "$SOC_VERSION" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

lock_path, om_path, controller_path, runtime_path = map(Path, sys.argv[1:5])
expected_soc = sys.argv[5]

def digest(path):
    state = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            state.update(block)
    return state.hexdigest()

try:
    raw = json.loads(lock_path.read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid OM lock: {exc}") from exc
if not isinstance(raw, dict):
    raise SystemExit("OM lock root must be an object")
om_bytes, om_sha = om_path.stat().st_size, digest(om_path)
if raw.get("bytes") != om_bytes or str(raw.get("sha256", "")).lower() != om_sha:
    raise SystemExit("OM lock no longer matches the converted OM")
if raw.get("soc_version") != expected_soc:
    raise SystemExit("OM lock SoC does not match the requested conversion target")
controller_sha = digest(controller_path)
for field in ("controller_contract_sha256", "contract_sha256"):
    value = raw.get(field)
    if value is not None and str(value).lower() != controller_sha:
        raise SystemExit(f"OM lock {field} does not match the controller contract")
runtime_sha = digest(runtime_path)
raw.update({
    "schema_version": max(2, int(raw.get("schema_version", 1))),
    # Keep the old fields as the controller/export proof.  New strict
    # launchers use runtime_contract_sha256 for the ACL descriptor contract.
    "contract_path": str(controller_path),
    "contract_sha256": controller_sha,
    "controller_contract_path": str(controller_path),
    "controller_contract_sha256": controller_sha,
    "runtime_contract_path": str(runtime_path),
    "runtime_contract_sha256": runtime_sha,
})
temporary = lock_path.with_name(lock_path.name + ".part")
temporary.write_text(json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, lock_path)
print(json.dumps({
    "status": "runtime_contract_lock_verified",
    "lock": str(lock_path),
    "controller_contract_sha256": controller_sha,
    "runtime_contract_sha256": runtime_sha,
}, sort_keys=True))
PY
}
inspect() {
  require_board; prepare_dirs; source_runtime; check_forbidden; require_onnx
  [[ -f "$CONTRACT" ]] || die "transfer the controller-generated contract before board inspect: $CONTRACT"
  # The board environment intentionally has no ONNX/Transformers package.
  # Validate the controller contract and its artifact hash here; run the full
  # protobuf inspector only when an already-approved onnx module is present.
  python - "$ONNX" "$CONTRACT" <<'PY'
import hashlib, json, pathlib, sys
from qwen25_kv_acl_contract import Qwen25Contract
onnx = pathlib.Path(sys.argv[1]); contract_path = pathlib.Path(sys.argv[2])
contract = Qwen25Contract.load(contract_path)
expected = json.loads(contract_path.read_text(encoding="utf-8"))
artifact = expected.get("source_artifact", {})
digest = hashlib.sha256(onnx.read_bytes()).hexdigest()
if int(artifact.get("bytes", -1)) != onnx.stat().st_size or artifact.get("sha256") != digest:
    raise SystemExit("contract source_artifact does not match transferred ONNX")
print("controller contract/hash verified", contract.model_id, digest)
PY
  if python -c 'import onnx' >/dev/null 2>&1; then
    if [[ ! -f "$INSPECTOR" && -f "$REPO_DIR/inspect_qwen25_static_onnx.py" ]]; then
      INSPECTOR="$REPO_DIR/inspect_qwen25_static_onnx.py"
    fi
    [[ -f "$INSPECTOR" ]] || die "inspector is missing: $INSPECTOR"
    python "$INSPECTOR" \
      --model "$ONNX" --output "$CONTRACT" --report "$INSPECT_REPORT" \
      --source-revision "${CASE9_QWEN25_KV_SOURCE_REVISION:-local-static-kv-export}" \
      | tee "${REPORTS}/inspect-console.txt"
  else
    printf '%s\n' '{"status":"controller_contract_verified","onnx_parser":"not-installed-by-policy"}' >"${INSPECT_REPORT}"
    echo "board onnx parser unavailable; reused controller inspection by verified hash"
  fi
  echo "contract=$CONTRACT"
}
convert() {
  require_board; validate_soc_version; prepare_dirs; source_runtime; check_forbidden; require_onnx
  [[ -f "$CONTRACT" ]] || die "run inspect first: $CONTRACT"
  [[ ! -e "$OM" ]] || die "refusing to overwrite existing OM: $OM"
  # Derive ATC names and shapes from the inspected contract.  The shell does
  # not carry a second hand-written cache schema that could drift from ONNX.
  local shape
  shape="$(python - "$CONTRACT" <<'PY'
import sys
from qwen25_kv_acl_contract import Qwen25Contract
contract = Qwen25Contract.load(sys.argv[1])
if contract.cache_layout != "split" or len(contract.cache_inputs) != 48:
    raise SystemExit("contract is not the admitted 48-tensor split layout")
print(";".join(f"{item.name}:{','.join(str(dim) for dim in item.shape)}" for item in contract.inputs))
PY
)" || die "could not derive ATC input shape from contract"
  local log="${LOGS}/atc-$(date -u +%Y%m%dT%H%M%SZ).log"
  local atc_path
  atc_path="$(prepare_atc_pythonpath)"
  {
    echo "$atc_path"
    echo "command=atc --framework=5 --model=$ONNX --output=$OM_PREFIX --input_format=ND --input_shape=$shape --soc_version=$SOC_VERSION --precision_mode=must_keep_origin_dtype"
    PYTHONPATH="$ATC_DEPS${PYTHONPATH:+:$PYTHONPATH}" atc --framework=5 --model="$ONNX" --output="$OM_PREFIX" --input_format=ND \
      --input_shape="$shape" --soc_version="$SOC_VERSION" \
      --precision_mode=must_keep_origin_dtype
  } >"$log" 2>&1 || die "ATC failed; log=$log"
  [[ -f "$OM" ]] || die "ATC returned without OM"
  local bytes sha contract_sha lock_temporary
  bytes="$(stat -c '%s' "$OM")"; sha="$(sha256sum "$OM" | awk '{print $1}')"
  contract_sha="$(sha256sum "$CONTRACT" | awk '{print $1}')"
  [[ ! -e "$OM_LOCK" ]] || die "refusing to overwrite existing OM lock: $OM_LOCK"
  lock_temporary="${OM_LOCK}.part"
  mkdir -p "$(dirname "$OM_LOCK")"
  python - "$OM" "$CONTRACT" "$log" "$bytes" "$sha" "$contract_sha" >"$lock_temporary" <<'PY'
import json, os, sys
from datetime import datetime, timezone

om, contract, log, size, sha, contract_sha = sys.argv[1:]
json.dump({"schema_version": 2, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
           "om_path": om, "bytes": int(size), "sha256": sha,
           "soc_version": os.environ.get("CASE9_QWEN25_KV_SOC_VERSION", "Ascend310B4"),
           "chip_tier": "20T" if os.environ.get("CASE9_QWEN25_KV_SOC_VERSION", "Ascend310B4") == "Ascend310B1" else "8T",
           "contract_path": contract, "contract_sha256": contract_sha,
           "controller_contract_path": contract, "controller_contract_sha256": contract_sha,
           "atc_log": log},
          sys.stdout, ensure_ascii=True, indent=2, sort_keys=True)
print()
PY
  mv -f "$lock_temporary" "$OM_LOCK"
  echo "OM verified: bytes=$bytes sha256=$sha path=$OM"
}
smoke() {
  require_board; validate_soc_version; source_runtime; check_forbidden
  if [[ "$SOC_VERSION" != "Ascend310B4" && "$(realpath -m -- "$OM")" == "$(realpath -m -- "$SOURCE_OM")" ]]; then
    die "a non-B4 board must use a board-specific rebuilt OM; set CASE9_QWEN25_KV_OM"
  fi
  [[ -f "$OM" && -f "$CONTRACT" && -f "$TOKENIZER" ]] || die "OM, contract or tokenizer is missing"
  ensure_tokenizer_lock
  local stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  npu-smi info >"${REPORTS}/${stamp}-smoke-before.txt" 2>&1
  local sampler_pid=""
  (
    while true; do
      printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      npu-smi info || true
      sleep 1
    done
  ) >"${REPORTS}/${stamp}-smoke-during.txt" 2>&1 &
  sampler_pid=$!
  set +e
  python - "$OM" "$TOKENIZER" "$TOKENIZER_CONFIG" "$CONTRACT" "$OM_CONTRACT" >"${REPORTS}/${stamp}-acl-smoke.txt" 2>&1 <<'PY'
import json, sys
import hashlib
from pathlib import Path
from qwen25_kv_acl_contract import Qwen25Contract
from qwen25_kv_acl_runtime import Qwen25AclRuntime
om, tokenizer, tokenizer_config, contract, om_contract = map(Path, sys.argv[1:])

def write_contract(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)

# First bind the ONNX contract only to prove that the OM has the expected
# tensor count/shape/dtype.  Then retain the actual OM descriptor names (ATC
# may rewrite them) as the runtime contract used by the service.
controller_contract = Qwen25Contract.load(contract)
runtime = Qwen25AclRuntime(om, tokenizer, contract_path=None, tokenizer_config_path=tokenizer_config, max_tokens=2, require_artifact_locks=False)
try:
    runtime.start()
    descriptor_contract = runtime.contract
    if descriptor_contract is None:
        raise RuntimeError("runtime did not produce an OM descriptor contract")
    if len(descriptor_contract.cache_inputs) != len(controller_contract.cache_inputs) or len(descriptor_contract.cache_outputs) != len(controller_contract.cache_outputs):
        raise RuntimeError("OM descriptor cache count differs from controller contract")
    for actual, expected in zip(descriptor_contract.inputs, controller_contract.inputs):
        if actual.dtype != expected.dtype or actual.shape != expected.shape or actual.byte_size != expected.byte_size:
            raise RuntimeError(f"OM input descriptor differs from controller contract: {actual.name}")
    for actual, expected in zip(descriptor_contract.outputs, controller_contract.outputs):
        if actual.dtype != expected.dtype or actual.shape != expected.shape or actual.byte_size != expected.byte_size:
            raise RuntimeError(f"OM output descriptor differs from controller contract: {actual.name}")
    descriptor_value = descriptor_contract.as_dict()
    digest_state = hashlib.sha256()
    with om.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest_state.update(block)
    digest = digest_state.hexdigest()
    descriptor_value["om_artifact"] = {
        "path": str(om),
        "bytes": om.stat().st_size,
        "sha256": digest,
        "descriptor_order_source": "ATC OM descriptor order; must preserve ONNX layer->key,value order",
    }
    write_contract(om_contract, descriptor_value)
    result = runtime.complete([{"role": "user", "content": "你好"}], 2)
    if not result.text and result.completion_tokens <= 0:
        raise RuntimeError("ACL smoke returned no generated token")
    print(json.dumps({"status": "passed", "text": result.text, "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens}, ensure_ascii=False))
finally:
    runtime.close()
PY
  local smoke_status=$?
  set -e
  if [[ -n "$sampler_pid" ]]; then
    kill "$sampler_pid" 2>/dev/null || true
    wait "$sampler_pid" 2>/dev/null || true
  fi
  (( smoke_status == 0 )) || die "ACL smoke failed; report=${REPORTS}/${stamp}-acl-smoke.txt"
  record_runtime_contract_lock
  npu-smi info >"${REPORTS}/${stamp}-smoke-after.txt" 2>&1
  echo "ACL smoke passed; reports=${REPORTS}/${stamp}-* (during sampler pid=${sampler_pid})"
}
serve() {
  require_board; validate_soc_version; source_runtime; check_forbidden
  if [[ "$SOC_VERSION" != "Ascend310B4" && "$(realpath -m -- "$OM")" == "$(realpath -m -- "$SOURCE_OM")" ]]; then
    die "a non-B4 board must use a board-specific rebuilt OM; set CASE9_QWEN25_KV_OM"
  fi
  [[ -f "$OM" && -f "$OM_CONTRACT" && -f "$TOKENIZER" ]] || die "serve prerequisites are missing; run smoke first to create $OM_CONTRACT"
  [[ -f "$OM_LOCK" ]] || die "serve requires an OM lock: $OM_LOCK"
  ensure_tokenizer_lock
  [[ "$PORT" == "8084" ]] || die "candidate service must remain on 8084 until promotion"
  [[ "$MAX_TOKENS" =~ ^[0-9]+$ ]] && (( MAX_TOKENS >= 1 && MAX_TOKENS <= 80 )) || die "CASE9_QWEN25_KV_MAX_TOKENS must be between 1 and 80"
  mkdir -p "$(dirname "$SERVICE_LOG")"
  exec python "$REPO_DIR/scripts/serve_qwen25_kv_acl.py" --host 127.0.0.1 --port "$PORT" \
    --root "$ROOT" --om "$OM" --tokenizer "$TOKENIZER" \
    --tokenizer-config "$TOKENIZER_CONFIG" --contract "$OM_CONTRACT" \
    --lock "$OM_LOCK" --tokenizer-lock "$TOKENIZER_LOCK" --max-tokens "$MAX_TOKENS" \
    >"$SERVICE_LOG" 2>&1
}
status() {
  printf 'root=%s\nonnx=%s\nom=%s\ncontract=%s\nport=%s\nmodel=%s\n' "$ROOT" "$ONNX" "$OM" "$CONTRACT" "$PORT" "$MODEL_ID"
  ss -ltnp 2>/dev/null | grep -E ':808[234] ' || true
}

command="${1:-}"
case "$command" in
  check) check ;;
  inspect) inspect ;;
  convert) convert ;;
  smoke) smoke ;;
  serve) serve ;;
  status) status ;;
  *) usage; exit 2 ;;
esac
