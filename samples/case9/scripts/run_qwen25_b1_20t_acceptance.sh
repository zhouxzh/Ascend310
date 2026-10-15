#!/usr/bin/env bash
# Start one isolated native Ascend310B1 Qwen2.5 Static-KV acceptance run.
#
# This is deliberately a board-side launcher, not a provisioning or conversion
# tool.  It only starts a new loopback service process group, drives the
# stdlib-only acceptance client, and preserves evidence below the selected
# campaign root.  It never changes a shared service or a model artifact.
set -Eeuo pipefail

MODEL_ID="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
TARGET_SOC="Ascend310B1"
DEFAULT_ROOT="/home/HwHiAiUser/case9-om-20t-20260914"

ROOT="${CASE9_OM20T_ROOT:-$DEFAULT_ROOT}"
SOURCE_ROOT=""
CAMPAIGN_LOCK_REL=""
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
PORT=18101
BOARD_IP="192.168.1.95"
STARTUP_TIMEOUT=300
REQUEST_TIMEOUT=300
SERVICE_MAX_TOKENS=80
SMOKE_MAX_TOKENS=2
LONG_BUDGETS="8,16,24,32"
STABILITY_LOOPS=10
STABILITY_MAX_TOKENS=2
PROBE_MAX_TOKENS=8
PERF_WARMUP=2
PERF_LOOPS=30
PERF_MAX_TOKENS=2
ALLOW_DIRTY_BASE=0

CONDA_SH="${CASE9_OM20T_CONDA_SH:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
CANN_ENV="${CASE9_OM20T_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"

RUN_DIR=""
PYTHON=""
BASE_PREFIX=""
SERVICE_PID=""
SERVICE_PGID=""
SERVICE_SETSID_PID=""
CLEANUP_STATUS="not-started"
ACCEPTANCE_EXIT="not-run"
RUN_PHASE="preflight"
ORIGINAL_ARGS=("$@")

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_qwen25_b1_20t_acceptance.sh \
    --campaign-lock artifacts/qwen25-b1/<generated-b1-campaign-lock>.json \
    --allow-dirty-base [options]

This runs only the native Ascend310B1 / 20T Qwen2.5 Static-KV OM profile. It
requires a separately generated schema-v2 B1 campaign lock and the existing
hash-pinned campaign overlay. The candidate service is loopback-only and uses
port 18101 by default. It never runs ATC, converts a model, installs packages,
or stops a process it did not start.

Options:
  --root DIR              Campaign root; must resolve below
                          /home/HwHiAiUser/case9-om-20t-*
  --source-root DIR       Synced Case9 source; defaults to <root>/src
  --campaign-lock PATH    Required path relative to <root>, created separately
  --allow-dirty-base      Required explicit acknowledgement of the shared base
  --run-id ID             Evidence directory identifier, default UTC timestamp
  --port PORT             Fresh loopback port, default 18101
  --board-ip IP           Evidence-only board IP, default 192.168.1.95
  --startup-timeout SEC   Wait for service readiness, default 300
  --request-timeout SEC   Per-request acceptance timeout, default 300
  --service-max-tokens N  Service cap, 1..80, default 80
  --smoke-max-tokens N    JSON/SSE smoke budget, 1..80, default 2
  --long-budgets LIST     Comma-separated budgets, each 1..80, default 8,16,24,32
  --stability-loops N     Acceptance stability rounds, 1..1000, default 10
  --stability-max-tokens N
                          Stability request budget, 1..80, default 2
  --probe-max-tokens N    Chinese probe request budget, 1..80, default 8
  --perf-warmup N         Performance warmup rounds, 0..1000, default 2
  --perf-loops N          Performance measurement rounds, 1..1000, default 30
  --perf-max-tokens N     Performance request budget, 1..80, default 2
  --help                  Show this message

Evidence is written atomically below:
  <root>/reports/qwen25-b1-native-acceptance/<run-id>/
EOF
}

die() {
  echo "Qwen2.5 B1 20T acceptance refused: $*" >&2
  exit 2
}

is_uint() {
  [[ "$1" =~ ^(0|[1-9][0-9]*)$ ]]
}

is_safe_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && [[ "$1" != *..* ]]
}

is_relative_file() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] && [[ "$1" != *..* ]]
}

canonical_directory() {
  (cd -- "$1" && pwd -P)
}

atomic_text() {
  local destination="$1"
  local temporary="${destination}.part"
  umask 077
  cat >"$temporary"
  mv -f -- "$temporary" "$destination"
}

atomic_number() {
  local destination="$1" value="$2"
  printf '%s\n' "$value" | atomic_text "$destination"
}

write_command_file() {
  local destination="$1"
  shift
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -Eeuo pipefail\n'
    printf 'exec '
    printf '%q ' "$@"
    printf '\n'
  } | atomic_text "$destination"
  chmod 0600 "$destination"
}

snapshot_npu() {
  local label="$1"
  local destination="$RUN_DIR/npu-${label}.txt"
  local temporary="${destination}.part"
  {
    printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'label=%s\n' "$label"
    if command -v npu-smi >/dev/null 2>&1; then
      npu-smi info
      printf 'npu_smi_exit_code=%s\n' "$?"
    else
      printf 'npu-smi is unavailable on PATH\n'
    fi
  } >"$temporary" 2>&1 || true
  mv -f -- "$temporary" "$destination"
}

write_json_with_python() {
  # The caller supplies a Python program through stdin.  The first argument is
  # the final JSON path; the program must atomically publish it itself.
  "$PYTHON" - "$@"
}

validate_number() {
  local label="$1" value="$2" minimum="$3" maximum="$4"
  is_uint "$value" || die "$label must be an integer"
  (( value >= minimum && value <= maximum )) || die "$label must be between $minimum and $maximum"
}

validate_long_budgets() {
  local value item maximum=0
  [[ -n "$LONG_BUDGETS" ]] || die "--long-budgets must not be empty"
  IFS=',' read -r -a _budget_items <<<"$LONG_BUDGETS"
  ((${#_budget_items[@]} > 0)) || die "--long-budgets must contain at least one value"
  for item in "${_budget_items[@]}"; do
    validate_number "--long-budgets value" "$item" 1 80
    (( item > maximum )) && maximum="$item"
  done
  LONG_BUDGET_MAX="$maximum"
}

port_preflight() {
  local destination="$RUN_DIR/port-preflight.json"
  "$PYTHON" - "$PORT" "$destination" <<'PY'
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

port = int(sys.argv[1])
destination = Path(sys.argv[2])
result = {
    "schema_version": 1,
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "host": "127.0.0.1",
    "port": port,
    "status": "failed",
}
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", port))
    finally:
        sock.close()
    result["status"] = "passed"
except OSError as exc:
    result["error"] = {"type": type(exc).__name__, "message": str(exc)}
temporary = destination.with_name(destination.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, destination)
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if result["status"] == "passed" else 2)
PY
}

write_environment_report() {
  local destination="$RUN_DIR/environment.json"
  "$PYTHON" - "$destination" "$ROOT/overlay" "$TARGET_SOC" <<'PY'
import importlib.util
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

destination = Path(sys.argv[1])
overlay = Path(sys.argv[2]).resolve()
target_soc = sys.argv[3]
forbidden = ("torch", "torch_npu", "torchaudio", "transformers", "onnxruntime", "mindspore", "mindtorch", "vllm", "mindie")
packages = {}
for name in forbidden:
    try:
        spec = importlib.util.find_spec(name)
        packages[name] = {"present": spec is not None, "origin": str(spec.origin) if spec and spec.origin else None}
    except (ImportError, AttributeError, ValueError) as exc:
        packages[name] = {"present": None, "error": f"{type(exc).__name__}: {exc}"}
result = {
    "schema_version": 1,
    "kind": "case9_qwen25_b1_native_acceptance_environment",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "target_soc": target_soc,
    "dirty_base_explicit": True,
    "classification": "experimental_dirty_base",
    "python": {"executable": sys.executable, "prefix": sys.prefix, "version": sys.version.replace("\n", " "), "machine": platform.machine()},
    "conda": {"default_env": os.environ.get("CONDA_DEFAULT_ENV"), "prefix": os.environ.get("CONDA_PREFIX")},
    "overlay": {"path": str(overlay), "marker": str(overlay / ".case9-tokenizers-overlay.json")},
    "forbidden_packages": packages,
    "acl": {"importable": False},
    "tokenizers": {"importable": False},
    "status": "failed",
}
try:
    import acl  # type: ignore  # noqa: F401
    result["acl"] = {"importable": True, "origin": getattr(acl, "__file__", None)}
    import tokenizers
    origin = Path(tokenizers.__file__).resolve()
    origin.relative_to(overlay)
    result["tokenizers"] = {"importable": True, "origin": str(origin), "version": getattr(tokenizers, "__version__", None)}
    result["status"] = "passed"
except Exception as exc:
    result["error"] = {"type": type(exc).__name__, "message": str(exc)}
temporary = destination.with_name(destination.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, destination)
print(json.dumps({"status": result["status"], "report": str(destination)}, ensure_ascii=False))
raise SystemExit(0 if result["status"] == "passed" else 2)
PY
}

validate_campaign_lock() {
  local destination="$RUN_DIR/campaign-lock-admission.json"
  "$PYTHON" - "$CAMPAIGN_LOCK" "$OM" "$CONTRACT" "$destination" "$MODEL_ID" "$TARGET_SOC" <<'PY'
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

lock_path = Path(sys.argv[1])
om_path = Path(sys.argv[2])
contract_path = Path(sys.argv[3])
destination = Path(sys.argv[4])
expected_model = sys.argv[5]
expected_soc = sys.argv[6]


def digest(path: Path) -> str:
    state = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            state.update(block)
    return state.hexdigest()


result = {
    "schema_version": 1,
    "kind": "case9_qwen25_b1_campaign_lock_admission",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "lock": str(lock_path),
    "om": str(om_path),
    "runtime_contract": str(contract_path),
    "expected_model": expected_model,
    "expected_soc": expected_soc,
    "status": "failed",
}
try:
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("campaign lock root must be an object")
    expected = {
        "schema_version": 2,
        "kind": "case9_qwen25_b1_campaign_om_lock",
        "model_id": expected_model,
        "soc_version": expected_soc,
        "om_path": "artifacts/qwen25-b1/qwen25-static-kv-1024-b1.om",
        "runtime_contract_path": "artifacts/qwen25-b1/qwen25-static-kv-1024-v2-om-contract.json",
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ValueError("campaign lock %s does not match %r" % (key, value))
    for key in ("sha256", "runtime_contract_sha256", "contract_sha256", "controller_contract_sha256"):
        value = document.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("campaign lock %s is not a lowercase SHA-256" % key)
    actual_om_bytes = om_path.stat().st_size
    actual_om_sha = digest(om_path)
    actual_contract_sha = digest(contract_path)
    if document.get("bytes") != actual_om_bytes:
        raise ValueError("campaign lock OM byte count does not match the artifact")
    if document.get("sha256") != actual_om_sha:
        raise ValueError("campaign lock OM SHA-256 does not match the artifact")
    if document.get("runtime_contract_sha256") != actual_contract_sha:
        raise ValueError("campaign lock runtime contract SHA-256 does not match the artifact")
    result["lock_digest"] = digest(lock_path)
    result["om_artifact"] = {"bytes": actual_om_bytes, "sha256": actual_om_sha}
    result["runtime_contract_digest"] = actual_contract_sha
    result["status"] = "passed"
except Exception as exc:
    result["error"] = {"type": type(exc).__name__, "message": str(exc)}
temporary = destination.with_name(destination.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, destination)
print(json.dumps({"status": result["status"], "report": str(destination)}, ensure_ascii=False))
raise SystemExit(0 if result["status"] == "passed" else 2)
PY
}

write_service_record() {
  local destination="$RUN_DIR/service-process.json"
  "$PYTHON" - "$destination" "$SERVICE_PID" "$SERVICE_PGID" "$SERVICE_SETSID_PID" "$PORT" "$RUN_DIR/service-command.sh" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

destination = Path(sys.argv[1])
result = {
    "schema_version": 1,
    "kind": "case9_qwen25_b1_native_service_process",
    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    "pid": int(sys.argv[2]),
    "pgid": int(sys.argv[3]),
    "setsid_launcher_pid": int(sys.argv[4]),
    "host": "127.0.0.1",
    "port": int(sys.argv[5]),
    "command_record": sys.argv[6],
    "ownership": "created_by_this_launcher",
    "cleanup_policy": "only the recorded session-leading process group may be signaled",
}
temporary = destination.with_name(destination.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, destination)
PY
}

write_run_summary() {
  local exit_code="$1"
  local destination="$RUN_DIR/run-summary.json"
  "$PYTHON" - "$destination" "$exit_code" "$RUN_PHASE" "$ACCEPTANCE_EXIT" "$CLEANUP_STATUS" "$SERVICE_PID" "$SERVICE_PGID" "$PORT" "$BOARD_IP" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

destination = Path(sys.argv[1])
exit_code = int(sys.argv[2])
phase = sys.argv[3]
acceptance_exit = sys.argv[4]
cleanup_status = sys.argv[5]
service_pid = sys.argv[6] or None
service_pgid = sys.argv[7] or None
result = {
    "schema_version": 1,
    "kind": "case9_qwen25_b1_native_acceptance_run",
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    "classification": "experimental_dirty_base",
    "target_soc": "Ascend310B1",
    "board_ip": sys.argv[9],
    "candidate_endpoint": "http://127.0.0.1:%s" % sys.argv[8],
    "phase": phase,
    "exit_code": exit_code,
    "acceptance_exit_code": acceptance_exit,
    "service": {"pid": int(service_pid) if service_pid else None, "pgid": int(service_pgid) if service_pgid else None},
    "cleanup_status": cleanup_status,
    "status": "passed" if exit_code == 0 and acceptance_exit == "0" and cleanup_status in {"stopped", "already-exited"} else "failed",
    "boundary": "No ATC, conversion, installation, or shared-service mutation was performed.",
}
temporary = destination.with_name(destination.name + ".part")
temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, destination)
PY
}

service_alive() {
  [[ -n "$SERVICE_PID" ]] && kill -0 "$SERVICE_PID" 2>/dev/null
}

cleanup_service() {
  if [[ -z "$SERVICE_PID" ]]; then
    CLEANUP_STATUS="not-started"
    return 0
  fi
  if ! service_alive; then
    CLEANUP_STATUS="already-exited"
    return 0
  fi
  local observed_pgid
  observed_pgid="$(ps -o pgid= -p "$SERVICE_PID" 2>/dev/null | tr -d '[:space:]')"
  if [[ ! "$SERVICE_PID" =~ ^[1-9][0-9]*$ || ! "$SERVICE_PGID" =~ ^[1-9][0-9]*$ || "$SERVICE_PGID" != "$SERVICE_PID" || "$observed_pgid" != "$SERVICE_PGID" ]]; then
    CLEANUP_STATUS="refused-unverified-process-group"
    return 1
  fi
  kill -TERM -- "-$SERVICE_PGID" 2>/dev/null || true
  local deadline=$((SECONDS + 30))
  while service_alive && (( SECONDS < deadline )); do
    sleep 1
  done
  if service_alive; then
    kill -KILL -- "-$SERVICE_PGID" 2>/dev/null || true
    deadline=$((SECONDS + 10))
    while service_alive && (( SECONDS < deadline )); do
      sleep 1
    done
  fi
  if service_alive; then
    CLEANUP_STATUS="failed-process-group-still-alive"
    return 1
  fi
  CLEANUP_STATUS="stopped"
  return 0
}

finalize() {
  local exit_code="$?"
  local cleanup_exit=0
  trap - EXIT INT TERM HUP
  cleanup_service || cleanup_exit="$?"
  if (( cleanup_exit != 0 && exit_code == 0 )); then
    exit_code=1
    RUN_PHASE="cleanup-failed"
  fi
  if [[ -n "$RUN_DIR" && -d "$RUN_DIR" ]]; then
    snapshot_npu "after-cleanup"
    write_run_summary "$exit_code" || true
  fi
  exit "$exit_code"
}

on_signal() {
  local signal_name="$1"
  RUN_PHASE="interrupted-${signal_name}"
  exit 130
}

while (($#)); do
  case "$1" in
    --root) [[ $# -ge 2 ]] || die "--root requires a value"; ROOT="$2"; shift 2 ;;
    --source-root) [[ $# -ge 2 ]] || die "--source-root requires a value"; SOURCE_ROOT="$2"; shift 2 ;;
    --campaign-lock) [[ $# -ge 2 ]] || die "--campaign-lock requires a value"; CAMPAIGN_LOCK_REL="$2"; shift 2 ;;
    --allow-dirty-base) ALLOW_DIRTY_BASE=1; shift ;;
    --run-id) [[ $# -ge 2 ]] || die "--run-id requires a value"; RUN_ID="$2"; shift 2 ;;
    --port) [[ $# -ge 2 ]] || die "--port requires a value"; PORT="$2"; shift 2 ;;
    --board-ip) [[ $# -ge 2 ]] || die "--board-ip requires a value"; BOARD_IP="$2"; shift 2 ;;
    --startup-timeout) [[ $# -ge 2 ]] || die "--startup-timeout requires a value"; STARTUP_TIMEOUT="$2"; shift 2 ;;
    --request-timeout) [[ $# -ge 2 ]] || die "--request-timeout requires a value"; REQUEST_TIMEOUT="$2"; shift 2 ;;
    --service-max-tokens) [[ $# -ge 2 ]] || die "--service-max-tokens requires a value"; SERVICE_MAX_TOKENS="$2"; shift 2 ;;
    --smoke-max-tokens) [[ $# -ge 2 ]] || die "--smoke-max-tokens requires a value"; SMOKE_MAX_TOKENS="$2"; shift 2 ;;
    --long-budgets) [[ $# -ge 2 ]] || die "--long-budgets requires a value"; LONG_BUDGETS="$2"; shift 2 ;;
    --stability-loops) [[ $# -ge 2 ]] || die "--stability-loops requires a value"; STABILITY_LOOPS="$2"; shift 2 ;;
    --stability-max-tokens) [[ $# -ge 2 ]] || die "--stability-max-tokens requires a value"; STABILITY_MAX_TOKENS="$2"; shift 2 ;;
    --probe-max-tokens) [[ $# -ge 2 ]] || die "--probe-max-tokens requires a value"; PROBE_MAX_TOKENS="$2"; shift 2 ;;
    --perf-warmup) [[ $# -ge 2 ]] || die "--perf-warmup requires a value"; PERF_WARMUP="$2"; shift 2 ;;
    --perf-loops) [[ $# -ge 2 ]] || die "--perf-loops requires a value"; PERF_LOOPS="$2"; shift 2 ;;
    --perf-max-tokens) [[ $# -ge 2 ]] || die "--perf-max-tokens requires a value"; PERF_MAX_TOKENS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$ALLOW_DIRTY_BASE" == "1" ]] || die "--allow-dirty-base is required for the shared 20T base environment"
[[ -n "$CAMPAIGN_LOCK_REL" ]] || die "--campaign-lock is required and must name a separately generated B1 campaign lock"
is_safe_id "$RUN_ID" || die "--run-id is unsafe"
is_relative_file "$CAMPAIGN_LOCK_REL" || die "--campaign-lock must be a safe path relative to --root"
validate_number "--port" "$PORT" 1024 65535
validate_number "--startup-timeout" "$STARTUP_TIMEOUT" 1 600
validate_number "--request-timeout" "$REQUEST_TIMEOUT" 1 600
validate_number "--service-max-tokens" "$SERVICE_MAX_TOKENS" 1 80
validate_number "--smoke-max-tokens" "$SMOKE_MAX_TOKENS" 1 80
validate_number "--stability-loops" "$STABILITY_LOOPS" 1 1000
validate_number "--stability-max-tokens" "$STABILITY_MAX_TOKENS" 1 80
validate_number "--probe-max-tokens" "$PROBE_MAX_TOKENS" 1 80
validate_number "--perf-warmup" "$PERF_WARMUP" 0 1000
validate_number "--perf-loops" "$PERF_LOOPS" 1 1000
validate_number "--perf-max-tokens" "$PERF_MAX_TOKENS" 1 80
validate_long_budgets
MAX_REQUEST_BUDGET="$SMOKE_MAX_TOKENS"
for value in "$LONG_BUDGET_MAX" "$STABILITY_MAX_TOKENS" "$PROBE_MAX_TOKENS" "$PERF_MAX_TOKENS"; do
  (( value > MAX_REQUEST_BUDGET )) && MAX_REQUEST_BUDGET="$value"
done
(( SERVICE_MAX_TOKENS >= MAX_REQUEST_BUDGET )) || die "--service-max-tokens is below an acceptance request budget"

[[ -d "$ROOT" ]] || die "campaign root does not exist: $ROOT"
ROOT="$(canonical_directory "$ROOT")"
case "$ROOT" in
  /home/HwHiAiUser/case9-om-20t-*) ;;
  *) die "--root must resolve to /home/HwHiAiUser/case9-om-20t-*" ;;
esac
if [[ -z "$SOURCE_ROOT" ]]; then
  SOURCE_ROOT="$ROOT/src"
fi
[[ -d "$SOURCE_ROOT" ]] || die "source root does not exist: $SOURCE_ROOT"
SOURCE_ROOT="$(canonical_directory "$SOURCE_ROOT")"
case "$SOURCE_ROOT" in
  "$ROOT"/src|"$ROOT"/src/*) ;;
  *) die "--source-root must remain below $ROOT/src" ;;
esac
CAMPAIGN_LOCK="$ROOT/$CAMPAIGN_LOCK_REL"
[[ -f "$CAMPAIGN_LOCK" && ! -L "$CAMPAIGN_LOCK" ]] || die "campaign lock is missing or a symlink: $CAMPAIGN_LOCK"
[[ -f "$CONDA_SH" ]] || die "conda activation script is missing: $CONDA_SH"
[[ -f "$CANN_ENV" ]] || die "CANN environment script is missing: $CANN_ENV"
command -v setsid >/dev/null 2>&1 || die "setsid is required to own the candidate service process group"
command -v ps >/dev/null 2>&1 || die "ps is required to verify the candidate service process group"
command -v npu-smi >/dev/null 2>&1 || die "npu-smi is required for acceptance evidence"

OM="$ROOT/artifacts/qwen25-b1/qwen25-static-kv-1024-b1.om"
TOKENIZER="$ROOT/artifacts/qwen25-b1/tokenizer.json"
TOKENIZER_CONFIG="$ROOT/artifacts/qwen25-b1/tokenizer_config.json"
CONTRACT="$ROOT/artifacts/qwen25-b1/qwen25-static-kv-1024-v2-om-contract.json"
TOKENIZER_LOCK="$ROOT/artifacts/qwen25-b1/tokenizer.json.lock.json"
for file in "$OM" "$TOKENIZER" "$TOKENIZER_CONFIG" "$CONTRACT" "$TOKENIZER_LOCK"; do
  [[ -f "$file" && ! -L "$file" ]] || die "required immutable campaign artifact is missing or a symlink: $file"
done
for file in \
  "$SOURCE_ROOT/scripts/serve_qwen25_kv_acl.py" \
  "$SOURCE_ROOT/scripts/qwen25_acceptance.py" \
  "$SOURCE_ROOT/scripts/provision_case9_om20t_overlay.sh" \
  "$SOURCE_ROOT/tests/fixtures/qwen25_chinese_probe.json"; do
  [[ -f "$file" && ! -L "$file" ]] || die "required synced source file is missing or a symlink: $file"
done

RUN_DIR="$ROOT/reports/qwen25-b1-native-acceptance/$RUN_ID"
[[ ! -e "$RUN_DIR" ]] || die "refusing to reuse existing evidence directory: $RUN_DIR"
mkdir -p "$RUN_DIR"
trap finalize EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

write_command_file "$RUN_DIR/launcher-command.sh" "$0" "${ORIGINAL_ARGS[@]}"
snapshot_npu "before-service"

set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
set -u
conda activate base || die "could not activate conda base"
[[ "${CONDA_DEFAULT_ENV:-}" == "base" ]] || die "active conda environment is not base"
[[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]] || die "base does not expose a usable Python interpreter"
BASE_PREFIX="$CONDA_PREFIX"
set +u
# shellcheck disable=SC1090
source "$CANN_ENV"
set -u
[[ "${CONDA_PREFIX:-}" == "$BASE_PREFIX" ]] || die "sourcing CANN changed the selected conda base environment"
export PATH="$BASE_PREFIX/bin:$PATH"
hash -r 2>/dev/null || true
export PYTHONNOUSERSITE=1
export PYTHONPATH="$ROOT/overlay:$SOURCE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="$BASE_PREFIX/bin/python"
[[ -x "$PYTHON" ]] || die "activated base Python is unavailable: $PYTHON"

# This is a verification-only call.  It refuses an absent or substituted
# overlay and cannot install, upgrade, uninstall, or download anything.
bash "$SOURCE_ROOT/scripts/provision_case9_om20t_overlay.sh" --root "$ROOT" --conda-env base --check \
  >"$RUN_DIR/overlay-check.stdout.log" 2>"$RUN_DIR/overlay-check.stderr.log" || die "campaign overlay verification failed"
write_environment_report || die "base/CANN/overlay environment admission failed"
validate_campaign_lock || die "campaign lock admission failed"
port_preflight || die "loopback port $PORT is already occupied or cannot be reserved"

SERVICE_COMMAND=(
  "$PYTHON" "$SOURCE_ROOT/scripts/serve_qwen25_kv_acl.py"
  --host 127.0.0.1 --port "$PORT" --root "$ROOT"
  --om "$OM" --tokenizer "$TOKENIZER" --tokenizer-config "$TOKENIZER_CONFIG"
  --contract "$CONTRACT" --lock "$CAMPAIGN_LOCK" --tokenizer-lock "$TOKENIZER_LOCK"
  --soc-version "$TARGET_SOC" --max-tokens "$SERVICE_MAX_TOKENS"
)
write_command_file "$RUN_DIR/service-command.sh" "${SERVICE_COMMAND[@]}"
SERVICE_PID_FILE="$RUN_DIR/service.pid"
RUN_PHASE="starting-service"
CASE9_QWEN25_KV_ALLOW_DIRTY_BASE=1 setsid bash -c '
set -Eeuo pipefail
pid_file="$1"
shift
temporary="${pid_file}.part"
umask 077
printf "%s\\n" "$$" >"$temporary"
mv -f -- "$temporary" "$pid_file"
exec "$@"
' case9-qwen25-b1-native-service "$SERVICE_PID_FILE" "${SERVICE_COMMAND[@]}" \
  >"$RUN_DIR/service.stdout.log" 2>"$RUN_DIR/service.stderr.log" &
SERVICE_SETSID_PID="$!"

deadline=$((SECONDS + STARTUP_TIMEOUT))
while (( SECONDS < deadline )); do
  if [[ -f "$SERVICE_PID_FILE" ]]; then
    SERVICE_PID="$(tr -d '[:space:]' < "$SERVICE_PID_FILE")"
    if [[ "$SERVICE_PID" =~ ^[1-9][0-9]*$ ]]; then
      SERVICE_PGID="$(ps -o pgid= -p "$SERVICE_PID" 2>/dev/null | tr -d '[:space:]')"
      if [[ "$SERVICE_PGID" == "$SERVICE_PID" ]] && service_alive; then
        if "$PYTHON" - "$PORT" "$MODEL_ID" >"$RUN_DIR/startup-health.json.part" <<'PY'
import json
import sys
from urllib.request import urlopen

port, expected_model = int(sys.argv[1]), sys.argv[2]
with urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as response:
    payload = json.loads(response.read().decode("utf-8"))
if response.status != 200 or payload.get("ready") is not True or payload.get("healthy") is not True or payload.get("model") != expected_model:
    raise SystemExit("health payload did not admit the expected native B1 service")
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
        then
          mv -f -- "$RUN_DIR/startup-health.json.part" "$RUN_DIR/startup-health.json"
          break
        fi
        rm -f -- "$RUN_DIR/startup-health.json.part"
      elif ! service_alive; then
        RUN_PHASE="service-exited-before-ready"
        die "candidate service exited before readiness; inspect $RUN_DIR/service.stderr.log"
      fi
    fi
  fi
  sleep 1
done
[[ -f "$RUN_DIR/startup-health.json" ]] || { RUN_PHASE="service-startup-timeout"; die "candidate service did not become healthy within $STARTUP_TIMEOUT seconds"; }
write_service_record
snapshot_npu "during-service-ready"

ACCEPTANCE_COMMAND=(
  "$PYTHON" "$SOURCE_ROOT/scripts/qwen25_acceptance.py"
  --endpoint "http://127.0.0.1:$PORT/v1/chat/completions"
  --health-url "http://127.0.0.1:$PORT/health"
  --models-url "http://127.0.0.1:$PORT/v1/models"
  --model "$MODEL_ID" --board-ip "$BOARD_IP" --board-tier board20t
  --om "$OM" --lock "$CAMPAIGN_LOCK" --tokenizer-lock "$TOKENIZER_LOCK"
  --contract "$CONTRACT" --tokenizer "$TOKENIZER"
  --probe-file "$SOURCE_ROOT/tests/fixtures/qwen25_chinese_probe.json"
  --output "$RUN_DIR/acceptance.json" --allow-dirty-base
  --timeout "$REQUEST_TIMEOUT" --max-tokens "$SMOKE_MAX_TOKENS"
  --long-budgets "$LONG_BUDGETS" --stability-loops "$STABILITY_LOOPS"
  --stability-max-tokens "$STABILITY_MAX_TOKENS" --probe-max-tokens "$PROBE_MAX_TOKENS"
  --perf-warmup "$PERF_WARMUP" --perf-loops "$PERF_LOOPS" --perf-max-tokens "$PERF_MAX_TOKENS"
)
write_command_file "$RUN_DIR/acceptance-command.sh" "${ACCEPTANCE_COMMAND[@]}"
RUN_PHASE="running-acceptance"
set +e
"${ACCEPTANCE_COMMAND[@]}" >"$RUN_DIR/acceptance.stdout.log" 2>"$RUN_DIR/acceptance.stderr.log"
ACCEPTANCE_EXIT="$?"
set -e
atomic_number "$RUN_DIR/acceptance.exit-code" "$ACCEPTANCE_EXIT"
snapshot_npu "during-after-acceptance"
if [[ "$ACCEPTANCE_EXIT" != "0" ]]; then
  RUN_PHASE="acceptance-failed"
  exit "$ACCEPTANCE_EXIT"
fi
RUN_PHASE="acceptance-passed"
echo "Qwen2.5 B1 native 20T acceptance passed; evidence: $RUN_DIR/acceptance.json"
