#!/usr/bin/env bash
# Run bounded, evidence-oriented ACL checks for the known Case9 LLM OM files
# on an Ascend 310B1 / 20T board.  The script deliberately has no ATC path:
# every profile is an immutable, prebuilt OM with a fixed byte/SHA-256 record.
#
# It is intended to be copied into <campaign-root>/src/scripts and executed on
# the board after activating nothing manually.  The script activates the named
# conda environment and sources CANN itself, but does not install, upgrade,
# delete, convert, or start a model service.  When an already-running loopback
# API is supplied, it additionally records JSON/SSE readiness through the
# standard-library probe.  It never treats a cross-SoC load as a native result.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="${CASE9_OM20T_SOURCE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CAMPAIGN_ROOT="${CASE9_OM20T_ROOT:-$HOME/case9-om-20t-20260914}"
REPORTS_DIR=""
PROFILE=""
API_URL=""
CONDA_ENV="${CASE9_OM20T_CONDA_ENV:-base}"
CONDA_SH="${CASE9_OM20T_CONDA_SH:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
CANN_ENV="${CASE9_OM20T_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
DEVICE_ID=0
MAX_TOKENS=2
REQUIRE_API=0
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

TARGET_SOC="Ascend310B1"
QWEN_KV_MODEL_ID="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
QWEN_STATIC_MODEL_ID="qwen2.5-0.5b-instruct-static-fp16-acl-om"
TINYLLAMA_MODEL_ID="tiny-llama-1.1b-acl-om"

usage() {
  cat <<'EOF'
Usage: bash scripts/run_case9_om20t_campaign.sh --profile NAME [options]

Profiles are fixed allowlists.  No arbitrary OM path is accepted:
  qwen25-kv1024-b1-native   Native Ascend310B1 Static-KV 1024 OM
  qwen25-kv1024-b4-compat   Ascend310B4 Static-KV 1024 cross-SoC experiment
  tinyllama-b4-compat       Ascend310B4 TinyLlama cross-SoC experiment
  qwen25-full2048-b4-compat Ascend310B4 full-context 2048 cross-SoC experiment
  qwen25-lastlogits2048-b4-compat
                            Ascend310B4 last-logits 2048 cross-SoC experiment
  all                        Run the five profiles sequentially

Options:
  --root DIR          Isolated board campaign root, default
                      ~/case9-om-20t-20260914
  --source-root DIR   Synced Case9 source root containing the ACL probes
  --reports-dir DIR   Report directory under <root>/reports/
  --conda-env NAME    Existing conda environment, default base
  --conda-sh PATH     Existing conda activation script
  --cann-env PATH     Existing CANN set_env.sh
  --device-id N       ACL device id, default 0
  --run-id ID         Safe evidence run identifier
  --api-url URL       Existing loopback service, e.g. http://127.0.0.1:18101
  --max-tokens N      JSON/SSE probe limit, 1..8, default 2
  --require-api       Fail the profile when no API URL is supplied
  --help              Show this help

The descriptor gate uses ACL only after exact byte/SHA-256 verification.  An
API URL is optional because this script intentionally never starts or stops a
service.  For a B4 artifact on this B1 board, reports always retain the
cross_soc_compatibility_experiment classification; a passing descriptor or API
does not promote it to a native 20T artifact.
EOF
}

die() {
  echo "Case9 20T OM campaign refused: $*" >&2
  exit 2
}

is_word() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]
}

is_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_loopback_url() {
  local value="$1" host_port port
  [[ "$value" =~ ^http://127\.0\.0\.1:[0-9]+$ ]] || return 1
  host_port="${value#http://127.0.0.1:}"
  port="$host_port"
  is_uint "$port" && (( port >= 1 && port <= 65535 ))
}

while (($#)); do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || die "--profile requires a value"; PROFILE="$2"; shift 2 ;;
    --root) [[ $# -ge 2 ]] || die "--root requires a value"; CAMPAIGN_ROOT="$2"; shift 2 ;;
    --source-root) [[ $# -ge 2 ]] || die "--source-root requires a value"; SOURCE_ROOT="$2"; shift 2 ;;
    --reports-dir) [[ $# -ge 2 ]] || die "--reports-dir requires a value"; REPORTS_DIR="$2"; shift 2 ;;
    --conda-env) [[ $# -ge 2 ]] || die "--conda-env requires a value"; CONDA_ENV="$2"; shift 2 ;;
    --conda-sh) [[ $# -ge 2 ]] || die "--conda-sh requires a value"; CONDA_SH="$2"; shift 2 ;;
    --cann-env) [[ $# -ge 2 ]] || die "--cann-env requires a value"; CANN_ENV="$2"; shift 2 ;;
    --device-id) [[ $# -ge 2 ]] || die "--device-id requires a value"; DEVICE_ID="$2"; shift 2 ;;
    --run-id) [[ $# -ge 2 ]] || die "--run-id requires a value"; RUN_ID="$2"; shift 2 ;;
    --api-url) [[ $# -ge 2 ]] || die "--api-url requires a value"; API_URL="$2"; shift 2 ;;
    --max-tokens) [[ $# -ge 2 ]] || die "--max-tokens requires a value"; MAX_TOKENS="$2"; shift 2 ;;
    --require-api) REQUIRE_API=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$PROFILE" ]] || die "--profile is required"
case "$PROFILE" in
  qwen25-kv1024-b1-native|qwen25-kv1024-b4-compat|tinyllama-b4-compat|qwen25-full2048-b4-compat|qwen25-lastlogits2048-b4-compat|all) ;;
  *) die "unknown profile: $PROFILE" ;;
esac
is_word "$CONDA_ENV" || die "unsafe conda environment name"
is_word "$RUN_ID" || die "unsafe run id"
is_uint "$DEVICE_ID" || die "--device-id must be an integer"
is_uint "$MAX_TOKENS" && (( MAX_TOKENS >= 1 && MAX_TOKENS <= 8 )) || die "--max-tokens must be 1..8"
[[ -z "$API_URL" || "$PROFILE" != "all" ]] || die "--api-url may only be used with one profile"
if [[ -n "$API_URL" ]] && ! is_loopback_url "$API_URL"; then
  die "--api-url must be an http://127.0.0.1:PORT URL"
fi
(( ! REQUIRE_API || -n "$API_URL" )) || die "--require-api needs --api-url"

[[ -d "$CAMPAIGN_ROOT" ]] || die "campaign root does not exist: $CAMPAIGN_ROOT"
CAMPAIGN_ROOT="$(cd "$CAMPAIGN_ROOT" && pwd)"
case "$CAMPAIGN_ROOT" in
  "/home/HwHiAiUser"/case9-om-20t-*) ;;
  *) die "campaign root must be an isolated /home/HwHiAiUser/case9-om-20t-* directory" ;;
esac
[[ -d "$SOURCE_ROOT" ]] || die "source root does not exist: $SOURCE_ROOT"
SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"
PROBE_ACL="$SOURCE_ROOT/scripts/probe_acl_om.py"
PROBE_API="$SOURCE_ROOT/scripts/probe_openai_compat_api.py"
[[ -r "$PROBE_ACL" ]] || die "missing ACL descriptor probe: $PROBE_ACL"
[[ -r "$PROBE_API" ]] || die "missing OpenAI API probe: $PROBE_API"
[[ -r "$CONDA_SH" ]] || die "conda profile is unavailable: $CONDA_SH"
[[ -r "$CANN_ENV" ]] || die "CANN environment is unavailable: $CANN_ENV"

if [[ -z "$REPORTS_DIR" ]]; then
  REPORTS_DIR="$CAMPAIGN_ROOT/reports/om20t-campaign"
fi
case "$REPORTS_DIR" in
  "$CAMPAIGN_ROOT"/reports/*) ;;
  *) die "reports directory must remain below $CAMPAIGN_ROOT/reports/" ;;
esac
mkdir -p "$REPORTS_DIR/$RUN_ID"
REPORTS_DIR="$(cd "$REPORTS_DIR" && pwd)"

activate_board_environment() {
  set +u
  # shellcheck disable=SC1090
  source "$CONDA_SH"
  set -u
  conda activate "$CONDA_ENV" || return 1
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  set +u
  # shellcheck disable=SC1090
  source "$CANN_ENV"
  set -u
  export PATH="${CONDA_PREFIX:-}/bin:$PATH"
  export PYTHONNOUSERSITE=1
  export PYTHONPATH="$SOURCE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
  hash -r 2>/dev/null || true
  command -v python >/dev/null
}

if ! activate_board_environment; then
  die "could not activate conda environment $CONDA_ENV and source CANN"
fi

RUN_DIR="$REPORTS_DIR/$RUN_ID"
ENVIRONMENT_REPORT="$RUN_DIR/environment.json"

python - "$ENVIRONMENT_REPORT" "$TARGET_SOC" "$CONDA_ENV" "$CANN_ENV" <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

output = Path(sys.argv[1])
expected_soc, conda_env, cann_env = sys.argv[2:]

def command(argv):
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

npu = command(["npu-smi", "info"])
npu_text = str(npu.get("stdout", "")) + "\n" + str(npu.get("stderr", ""))
acl_status = {"available": False}
try:
    import acl  # type: ignore
    acl_status["available"] = True
    acl_status["origin"] = getattr(acl, "__file__", None)
except Exception as exc:
    acl_status["error"] = f"{type(exc).__name__}: {exc}"
packages = {}
for name in ("numpy", "tokenizers", "torch", "torch_npu", "torchaudio", "mindspore", "onnxruntime"):
    try:
        spec = importlib.util.find_spec(name)
        packages[name] = {"present": spec is not None, "origin": getattr(spec, "origin", None) if spec else None}
    except (ImportError, AttributeError, ValueError) as exc:
        packages[name] = {"present": None, "error": f"{type(exc).__name__}: {exc}"}
dirty_packages = [
    name for name in ("torch", "torch_npu", "torchaudio", "mindspore", "onnxruntime")
    if packages.get(name, {}).get("present") is True
]
version_cfg = Path(os.environ.get("ASCEND_TOOLKIT_HOME", "")) / "version.cfg"
result = {
    "schema_version": 1,
    "kind": "case9_om20t_environment",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "expected_soc": expected_soc,
    "observed_target_soc": expected_soc if "310B1" in npu_text else None,
    "target_soc_match": "310B1" in npu_text,
    "hostname": platform.node(),
    "machine": platform.machine(),
    "python": {"executable": sys.executable, "version": sys.version.replace("\n", " "), "prefix": sys.prefix},
    "conda_environment": conda_env,
    "cann_environment": cann_env,
    "cann_version_cfg": str(version_cfg),
    "cann_version_cfg_contents": version_cfg.read_text(encoding="utf-8", errors="replace") if version_cfg.is_file() else None,
    "acl": acl_status,
    "packages": packages,
    "dirty_base": bool(dirty_packages),
    "dirty_packages": dirty_packages,
    "environment_classification": "experimental_dirty_base" if dirty_packages else "isolated_clean_environment",
    "npu_smi": npu,
}
result["status"] = "observed-pass" if result["target_soc_match"] and acl_status["available"] else "observed-fail"
temporary = output.with_name(output.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(output)
print(result["status"])
PY

environment_status="$(python - "$ENVIRONMENT_REPORT" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", "unknown"))
PY
)"

declare -a REQUIRED_REL=()
declare -a REQUIRED_BYTES=()
declare -a REQUIRED_SHA=()
declare -a SUPPORT_REL=()
OM_REL=""
SOURCE_SOC=""
CLASSIFICATION=""
MODEL_ID=""

add_required() {
  REQUIRED_REL+=("$1")
  REQUIRED_BYTES+=("$2")
  REQUIRED_SHA+=("$3")
}

set_profile() {
  local item="$1"
  REQUIRED_REL=()
  REQUIRED_BYTES=()
  REQUIRED_SHA=()
  SUPPORT_REL=()
  case "$item" in
    qwen25-kv1024-b1-native)
      SOURCE_SOC="Ascend310B1"
      CLASSIFICATION="native_20t_artifact"
      MODEL_ID="$QWEN_KV_MODEL_ID"
      OM_REL="artifacts/qwen25-b1/qwen25-static-kv-1024-b1.om"
      add_required "$OM_REL" 1266009438 6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609
      add_required "artifacts/qwen25-b1/qwen25-static-kv-1024-b1.om.lock.json" 793 47a4030202342132d5794bea1e1e5ae19316f6c132b2fa00895df87b4b9d3272
      add_required "artifacts/qwen25-b1/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
      add_required "artifacts/qwen25-b1/tokenizer.json.lock.json" 258 02643b3266f05fe36f5067f0142d0a6f2346bc13722702c901b9d8550f779aab
      add_required "artifacts/qwen25-b1/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
      add_required "artifacts/qwen25-b1/qwen25-static-kv-1024-fp32-contract.json" 34912 fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498
      add_required "artifacts/qwen25-b1/qwen25-static-kv-1024-v2-om-contract.json" 32225 3ff15ccf682e85e32f521417aa7204347ff5ab4fb0608edd18f3c9a639af3c21
      ;;
    qwen25-kv1024-b4-compat)
      SOURCE_SOC="Ascend310B4"
      CLASSIFICATION="cross_soc_compatibility_experiment"
      MODEL_ID="$QWEN_KV_MODEL_ID"
      OM_REL="artifacts/qwen25-b4/qwen25-static-kv-1024-v2.om"
      add_required "$OM_REL" 1266010586 f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8
      add_required "artifacts/qwen25-b4/qwen25-static-kv-1024-v2.om.lock.json" 1421 7d6b35de8261ce3e5f077a6d6d3b6e19df43a2fba2470a2ef30c0d217a8c2770
      add_required "artifacts/qwen25-b4/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
      add_required "artifacts/qwen25-b4/tokenizer.json.lock.json" 258 02643b3266f05fe36f5067f0142d0a6f2346bc13722702c901b9d8550f779aab
      add_required "artifacts/qwen25-b4/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
      add_required "artifacts/qwen25-b4/qwen25-static-kv-1024-v2-om-contract.json" 32172 923255c717165a2529e15563f2acd5cb1e31b67769e2a4afa4d3233053e310b6
      ;;
    tinyllama-b4-compat)
      SOURCE_SOC="Ascend310B4"
      CLASSIFICATION="cross_soc_compatibility_experiment"
      MODEL_ID="$TINYLLAMA_MODEL_ID"
      OM_REL="artifacts/tinyllama/tiny-llama.om"
      add_required "$OM_REL" 1493077371 604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967
      add_required "artifacts/tinyllama/tiny-llama.om.lock.json" 562 cda3af97a0bca7bb8c1e73ae1c9dbdcf6124a278b6b5994cb0d32b32d4e2303a
      add_required "artifacts/tinyllama/tokenizer/tokenizer.zip" 709459 d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917
      add_required "artifacts/tinyllama/tokenizer/tokenizer.zip.lock.json" 478 4a8b17be338b627064532271ba0566f07346fe08ef654e7d686ba6935d2253c5
      add_required "artifacts/tinyllama/tokenizer/tokenizer.json" 1842767 bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e
      add_required "artifacts/tinyllama/tokenizer/tokenizer.model" 499723 9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347
      add_required "artifacts/tinyllama/tokenizer/tokenizer_config.json" 932 bcdc6f267b05e1afd27fa622a62fea649bcb941e6dc705d2835883a0746192da
      add_required "artifacts/tinyllama/tokenizer/special_tokens_map.json" 414 6fa06efa2785e450051989a6f8fb4416b10149ded485ddd3f127a40734f5cfd0
      SUPPORT_REL+=("src/local_model_manifest.json")
      ;;
    qwen25-full2048-b4-compat)
      SOURCE_SOC="Ascend310B4"
      CLASSIFICATION="cross_soc_compatibility_experiment"
      MODEL_ID="$QWEN_STATIC_MODEL_ID"
      OM_REL="artifacts/qwen25-full-b4/qwen25-static-2048.om"
      add_required "$OM_REL" 1407111161 dc17b153ee1e76b3d31e971617d1cfdc7c56f366226212a61377a2c85e1d92b8
      add_required "artifacts/qwen25-full-b4/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
      add_required "artifacts/qwen25-full-b4/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
      SUPPORT_REL+=("artifacts/qwen25-full-b4/qwen25-static-contract.json")
      ;;
    qwen25-lastlogits2048-b4-compat)
      SOURCE_SOC="Ascend310B4"
      CLASSIFICATION="cross_soc_compatibility_experiment"
      MODEL_ID="$QWEN_STATIC_MODEL_ID"
      OM_REL="artifacts/qwen25-last-b4/qwen25-static-2048-last-retry2.om"
      add_required "$OM_REL" 1407130895 ae196fd8ba4ccb7c721f8fcb9d0ff4d9b5b72bfcdf9a77309c6fe51d5103c460
      add_required "artifacts/qwen25-last-b4/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
      add_required "artifacts/qwen25-last-b4/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
      add_required "artifacts/qwen25-last-b4/qwen25-static-2048-last-contract.json" 2802 b21609e3e9ddc05f6d6a930de0b8d268ed30b83c1cff6d9b5bb283d27d441726
      ;;
    *) die "internal profile allowlist error: $item" ;;
  esac
}

write_stage_status() {
  local path="$1" stage="$2" status="$3" reason="$4"
  python - "$path" "$stage" "$status" "$reason" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "schema_version": 1,
    "stage": sys.argv[2],
    "status": sys.argv[3],
    "reason": sys.argv[4],
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
}
temporary = path.with_name(path.name + ".part")
temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
PY
}

json_status() {
  python - "$1" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", "unknown"))
except Exception:
    print("unknown")
PY
}

write_artifact_report() {
  local path="$1" profile="$2"
  local -a values=("$path" "$CAMPAIGN_ROOT" "$profile" "$SOURCE_SOC" "$TARGET_SOC" "$CLASSIFICATION" "$OM_REL")
  local index
  for ((index = 0; index < ${#REQUIRED_REL[@]}; index += 1)); do
    values+=("${REQUIRED_REL[$index]}" "${REQUIRED_BYTES[$index]}" "${REQUIRED_SHA[$index]}")
  done
  values+=("--support-files")
  values+=("${SUPPORT_REL[@]}")
  python - "${values[@]}" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

report_path = Path(sys.argv[1])
root = Path(sys.argv[2]).resolve()
profile, source_soc, target_soc, classification, om_rel = sys.argv[3:8]
raw = list(sys.argv[8:])
try:
    split = raw.index("--support-files")
except ValueError:
    split = len(raw)
artifact_args, support_args = raw[:split], raw[split + 1:]
if len(artifact_args) % 3:
    raise SystemExit("internal artifact tuple error")

def under_root(relative):
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"unsafe profile path: {relative}")
    return candidate

def sha256(path):
    state = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            state.update(block)
    return state.hexdigest()

artifacts = []
for index in range(0, len(artifact_args), 3):
    relative, expected_bytes, expected_sha = artifact_args[index:index + 3]
    path = under_root(relative)
    item = {
        "relative_path": relative,
        "path": str(path),
        "expected_bytes": int(expected_bytes),
        "expected_sha256": expected_sha.lower(),
        "exists": path.is_file(),
    }
    if path.is_file():
        item["bytes"] = path.stat().st_size
        item["sha256"] = sha256(path)
        item["verified"] = item["bytes"] == item["expected_bytes"] and item["sha256"] == item["expected_sha256"]
    else:
        item["verified"] = False
    artifacts.append(item)
support_files = []
for relative in support_args:
    path = under_root(relative)
    support_files.append({"relative_path": relative, "path": str(path), "exists": path.is_file()})
result = {
    "schema_version": 1,
    "kind": "case9_om_artifact_admission",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "profile": profile,
    "source_soc": source_soc,
    "target_soc": target_soc,
    "classification": classification,
    "om_relative_path": om_rel,
    "artifacts": artifacts,
    "support_files": support_files,
}
result["status"] = "observed-pass" if all(item["verified"] for item in artifacts) else "observed-fail"
temporary = report_path.with_name(report_path.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(report_path)
print(result["status"])
PY
}

validate_api_report() {
  local raw_report="$1" admitted_report="$2" expected_model="$3" classification="$4" expected_om_sha="$5" source_soc="$6" target_soc="$7"
  python - "$raw_report" "$admitted_report" "$expected_model" "$classification" "$expected_om_sha" "$source_soc" "$target_soc" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

raw_path, admitted_path = map(Path, sys.argv[1:3])
expected_model, classification, expected_om_sha, source_soc, target_soc = sys.argv[3:]
try:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
except Exception as exc:
    raw = {"status": "observed-fail", "error": f"cannot read raw API probe: {type(exc).__name__}: {exc}"}
health = raw.get("health", {}) if isinstance(raw, dict) else {}
models = raw.get("models", {}) if isinstance(raw, dict) else {}
body = health.get("body", {}) if isinstance(health, dict) else {}
model_body = models.get("body", {}) if isinstance(models, dict) else {}
listed = [item.get("id") for item in model_body.get("data", []) if isinstance(item, dict)] if isinstance(model_body, dict) else []
checks = {
    "raw_probe_passed": raw.get("status") == "passed" if isinstance(raw, dict) else False,
    "health_model": body.get("model") == expected_model if isinstance(body, dict) else False,
    "model_listed": expected_model in listed,
    "health_ready": body.get("ready") is True if isinstance(body, dict) else False,
    "health_om_sha256": str(body.get("om_sha256", "")).lower() == expected_om_sha,
}
if classification == "cross_soc_compatibility_experiment":
    checks["cross_soc_marker"] = body.get("compatibility_experiment") is True
    # Static-KV uses ``target_soc`` for the OM's original target while the
    # explicit legacy runners call it ``source_soc``.  Both forms still bind
    # the request to a B4 artifact executing on this B1 board.
    checks["source_soc"] = body.get("source_soc", body.get("target_soc")) == source_soc
    checks["board_soc"] = body.get("board_soc") == target_soc
binding = "verified" if checks.get("health_om_sha256") else "mismatch_or_missing"
result = {
    "schema_version": 1,
    "kind": "case9_om_api_admission",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "expected_model": expected_model,
    "classification": classification,
    "expected_om_sha256": expected_om_sha,
    "api_artifact_binding": binding,
    "checks": checks,
    "raw_probe": str(raw_path),
}
result["status"] = "observed-pass" if all(checks.values()) else "observed-fail"
temporary = admitted_path.with_name(admitted_path.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(admitted_path)
print(result["status"])
PY
}

write_summary() {
  local path="$1" profile="$2" profile_dir="$3" artifact_report="$4" descriptor_report="$5" api_report="$6" api_admission="$7"
  python - "$path" "$profile" "$profile_dir" "$ENVIRONMENT_REPORT" "$artifact_report" "$descriptor_report" "$api_report" "$api_admission" "$SOURCE_SOC" "$TARGET_SOC" "$CLASSIFICATION" "$MODEL_ID" "$API_URL" "$REQUIRE_API" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(path, profile, profile_dir, environment_path, artifact_path, descriptor_path, api_path, api_admission_path,
 source_soc, target_soc, classification, model_id, api_url, require_api) = sys.argv[1:]

def read_json(value):
    try:
        return json.loads(Path(value).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}

environment = read_json(environment_path)
artifact = read_json(artifact_path)
descriptor = read_json(descriptor_path)
api = read_json(api_path)
api_admission = read_json(api_admission_path)
artifact_ok = artifact.get("status") == "observed-pass"
descriptor_ok = descriptor.get("status") == "passed"
api_requested = bool(api_url)
api_ok = api_admission.get("status") == "observed-pass"
environment_ok = environment.get("status") == "observed-pass"
if not environment_ok or not artifact_ok or not descriptor_ok:
    state = "observed-fail"
elif api_requested and not api_ok:
    state = "observed-fail"
elif not api_requested:
    state = "descriptor_passed_api_not_run"
else:
    state = "observed-pass"
result = {
    "schema_version": 1,
    "kind": "case9_om20t_profile_summary",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "profile": profile,
    "profile_report_directory": profile_dir,
    "source_soc": source_soc,
    "target_soc": target_soc,
    "classification": classification,
    "expected_model": model_id,
    "api_url": api_url or None,
    "require_api": require_api == "1",
    "evidence": {
        "environment": environment_path,
        "artifact": artifact_path,
        "descriptor": descriptor_path,
        "api_probe": api_path,
        "api_admission": api_admission_path,
    },
    "gates": {
        "environment": environment.get("status"),
        "artifact": artifact.get("status"),
        "descriptor": descriptor.get("status"),
        "api": api_admission.get("status"),
    },
    "status": state,
    "boundary": "No ATC conversion is executed. B4 artifacts remain compatibility experiments on this B1 board.",
}
output = Path(path)
temporary = output.with_name(output.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(output)
print(state)
PY
}

run_profile() {
  local item="$1"
  set_profile "$item"
  local profile_dir="$RUN_DIR/$item"
  local artifact_report="$profile_dir/artifact.json"
  local descriptor_report="$profile_dir/descriptor.json"
  local api_report="$profile_dir/api-probe.json"
  local api_admission="$profile_dir/api-admission.json"
  local summary="$profile_dir/summary.json"
  local artifact_status descriptor_status api_status summary_status
  mkdir -p "$profile_dir"

  python - "$profile_dir/profile.json" "$item" "$SOURCE_SOC" "$TARGET_SOC" "$CLASSIFICATION" "$MODEL_ID" "$OM_REL" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "schema_version": 1,
    "profile": sys.argv[2],
    "source_soc": sys.argv[3],
    "target_soc": sys.argv[4],
    "classification": sys.argv[5],
    "expected_model": sys.argv[6],
    "om_relative_path": sys.argv[7],
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
}
temporary = path.with_name(path.name + ".part")
temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
PY

  write_artifact_report "$artifact_report" "$item" >/dev/null
  artifact_status="$(json_status "$artifact_report")"
  if [[ "$environment_status" != "observed-pass" ]]; then
    write_stage_status "$descriptor_report" "acl_descriptor" "not-run" "20T environment identity or acl import gate failed"
  elif [[ "$artifact_status" != "observed-pass" ]]; then
    write_stage_status "$descriptor_report" "acl_descriptor" "not-run" "immutable artifact admission failed"
  else
    set +e
    python "$PROBE_ACL" --om "$CAMPAIGN_ROOT/$OM_REL" --report "$descriptor_report" --device-id "$DEVICE_ID" >"$profile_dir/descriptor.stdout.log" 2>"$profile_dir/descriptor.stderr.log"
    local descriptor_exit=$?
    set -e
    printf '%s\n' "$descriptor_exit" >"$profile_dir/descriptor.exit-code"
    if [[ ! -f "$descriptor_report" ]]; then
      write_stage_status "$descriptor_report" "acl_descriptor" "observed-fail" "probe exited $descriptor_exit without a descriptor report"
    fi
  fi
  descriptor_status="$(json_status "$descriptor_report")"

  if [[ -z "$API_URL" ]]; then
    write_stage_status "$api_report" "openai_api" "not-run" "no --api-url supplied; this script does not start services"
    write_stage_status "$api_admission" "openai_api_admission" "not-run" "no API endpoint was requested"
  elif [[ "$artifact_status" != "observed-pass" || "$descriptor_status" != "passed" ]]; then
    write_stage_status "$api_report" "openai_api" "not-run" "API probe withheld because artifact or descriptor gate did not pass"
    write_stage_status "$api_admission" "openai_api_admission" "not-run" "API probe withheld because an earlier gate failed"
  else
    set +e
    python "$PROBE_API" --base-url "$API_URL" --model "$MODEL_ID" --max-tokens "$MAX_TOKENS" --report "$api_report" >"$profile_dir/api.stdout.log" 2>"$profile_dir/api.stderr.log"
    local api_exit=$?
    set -e
    printf '%s\n' "$api_exit" >"$profile_dir/api.exit-code"
    if [[ -f "$api_report" ]]; then
      validate_api_report "$api_report" "$api_admission" "$MODEL_ID" "$CLASSIFICATION" "${REQUIRED_SHA[0]}" "$SOURCE_SOC" "$TARGET_SOC" >/dev/null
    else
      write_stage_status "$api_report" "openai_api" "observed-fail" "API probe exited $api_exit without a report"
      write_stage_status "$api_admission" "openai_api_admission" "observed-fail" "API probe did not produce a report"
    fi
  fi
  api_status="$(json_status "$api_admission")"

  write_summary "$summary" "$item" "$profile_dir" "$artifact_report" "$descriptor_report" "$api_report" "$api_admission" >/dev/null
  summary_status="$(json_status "$summary")"
  printf '%s\n' "profile=$item classification=$CLASSIFICATION artifact=$artifact_status descriptor=$descriptor_status api=$api_status status=$summary_status report=$summary"

  if [[ "$summary_status" == "observed-pass" || "$summary_status" == "descriptor_passed_api_not_run" ]]; then
    return 0
  fi
  return 1
}

profiles=("$PROFILE")
if [[ "$PROFILE" == "all" ]]; then
  profiles=(qwen25-kv1024-b1-native qwen25-kv1024-b4-compat tinyllama-b4-compat qwen25-full2048-b4-compat qwen25-lastlogits2048-b4-compat)
fi

failures=0
for item in "${profiles[@]}"; do
  if ! run_profile "$item"; then
    failures=$((failures + 1))
  fi
done

python - "$RUN_DIR/campaign-summary.json" "$ENVIRONMENT_REPORT" "$RUN_ID" "$failures" "${profiles[@]}" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
environment = Path(sys.argv[2])
run_id, failures = sys.argv[3:5]
profiles = sys.argv[5:]
items = []
for profile in profiles:
    summary_path = path.parent / profile / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        summary = {"profile": profile, "status": "unknown", "error": f"{type(exc).__name__}: {exc}"}
    items.append(summary)
result = {
    "schema_version": 1,
    "kind": "case9_om20t_campaign_summary",
    "run_id": run_id,
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "environment_report": str(environment),
    "profile_count": len(items),
    "failure_count": int(failures),
    "profiles": items,
    "status": "observed-pass" if int(failures) == 0 else "observed-fail",
    "boundary": "No ATC conversion was invoked. Artifact binaries and reports remain board-local.",
}
temporary = path.with_name(path.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
print(result["status"])
PY

echo "campaign_summary=$RUN_DIR/campaign-summary.json failures=$failures"
(( failures == 0 ))
