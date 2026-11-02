#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"
[[ -r "${env_file}" ]] || { echo "Chat environment file is not readable: ${env_file}" >&2; exit 2; }
set -a
source "${env_file}"
set +a

[[ "${ASCEND_EDGE_CHAT_BACKEND:-mindspore}" == "mindspore" ]] || {
  echo "The shared chat launcher defaults to MindSpore; choose optional backends in the UI after startup" >&2
  exit 2
}

conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:-ascend}"
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
supervisor_port="${ASCEND_EDGE_CHAT_BACKEND_SUPERVISOR_PORT:-7863}"
supervisor_url="${ASCEND_EDGE_CHAT_BACKEND_SUPERVISOR_URL:-http://127.0.0.1:${supervisor_port}}"
default_model="${ASCEND_EDGE_CHAT_PROFILE:-qwen1.5-0.5b-chat}"
report_root="${ASCEND_EDGE_CHAT_REPORT_DIR:-${root_dir}/run/reports}"
cleanup_timeout_seconds="${ASCEND_EDGE_CHAT_CLEANUP_TIMEOUT_SECONDS:-30}"

[[ "${conda_env}" == "ascend" ]] || { echo "chat processes require conda env ascend" >&2; exit 2; }
[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
[[ -r "${cann_env}" ]] || { echo "CANN environment script not found: ${cann_env}" >&2; exit 2; }
[[ "${supervisor_url}" == "http://127.0.0.1:${supervisor_port}" ]] || {
  echo "backend supervisor must use its fixed loopback URL" >&2
  exit 2
}
[[ "${cleanup_timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
  echo "ASCEND_EDGE_CHAT_CLEANUP_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}
: "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR:?set ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR}"
: "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_CATALOG:?set ASCEND_EDGE_CHAT_ACL_CANDIDATE_CATALOG}"
: "${ASCEND_EDGE_CHAT_MODEL_CATALOG:?set ASCEND_EDGE_CHAT_MODEL_CATALOG}"

source "${conda_profile}"
conda activate "${conda_env}"
set +u
source "${cann_env}"
set -u
export PYTHONNOUSERSITE=1

for port in 7861 "${supervisor_port}" 7865 8084 8090; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    echo "chat startup requires unused port ${port}" >&2
    exit 2
  fi
done

run_dir="${report_root}/chat-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${run_dir}"
supervisor_pid=""
ui_pid=""
stop_pid() {
  local label="$1" pid="$2"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 0
  kill -0 "${pid}" 2>/dev/null || return 0
  kill -TERM "${pid}" 2>/dev/null || true
  local deadline=$((SECONDS + cleanup_timeout_seconds))
  while kill -0 "${pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "${label} pid=${pid} did not stop after ${cleanup_timeout_seconds}s; sending KILL" >&2
      kill -KILL "${pid}" 2>/dev/null || true
      break
    fi
    sleep 0.2
  done
  wait "${pid}" 2>/dev/null || true
}
cleanup() {
  stop_pid ui "${ui_pid:-}"
  stop_pid backend-supervisor "${supervisor_pid:-}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

bash scripts/verify/release-preflight.sh >"${run_dir}/preflight.log" 2>&1
bash scripts/run/start-backend-supervisor.sh >"${run_dir}/supervisor.log" 2>&1 & supervisor_pid=$!
supervisor_ready=0
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error "${supervisor_url}/api/status" >"${run_dir}/supervisor-status.json"; then
    supervisor_ready=1
    break
  fi
  kill -0 "${supervisor_pid}" 2>/dev/null || { echo "backend supervisor exited; see ${run_dir}" >&2; exit 1; }
  sleep 1
done
(( supervisor_ready == 1 )) || { echo "backend supervisor did not become ready; see ${run_dir}" >&2; exit 1; }

curl --fail --silent --show-error --max-time 240 \
  -H 'Content-Type: application/json' \
  -d "{\"backend\":\"mindspore\",\"model\":\"${default_model}\"}" \
  "${supervisor_url}/api/select" >"${run_dir}/backend-selection.json"
bash scripts/run/start-ui.sh >"${run_dir}/ui.log" 2>&1 & ui_pid=$!
ui_ready=0
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:7865/api/config >"${run_dir}/ui-config.json"; then
    ui_ready=1
    break
  fi
  kill -0 "${ui_pid}" 2>/dev/null || { echo "UI exited; see ${run_dir}" >&2; exit 1; }
  sleep 1
done
(( ui_ready == 1 )) || { echo "UI did not become ready; see ${run_dir}" >&2; exit 1; }
python - "${run_dir}/ui-config.json" <<'PY'
import json
import sys
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
worker = config.get("worker")
if not isinstance(worker, dict) or worker.get("healthy") is not True or worker.get("ready") is not True:
    raise SystemExit("default MindSpore worker is not ready; see UI and worker logs")
if config.get("active_backend") != "mindspore":
    raise SystemExit("shared chat startup must select MindSpore by default")
PY

printf 'run_dir=%s\nui_url=http://<board-ip>:7865/\ndefault_backend=mindspore\ndefault_model=%s\n' \
  "${run_dir}" "${default_model}"
while true; do
  for item in "backend-supervisor:${supervisor_pid}" "ui:${ui_pid}"; do
    label="${item%%:*}"
    pid="${item#*:}"
    kill -0 "${pid}" 2>/dev/null || { echo "${label} exited; see ${run_dir}" >&2; exit 1; }
  done
  sleep 2
done
