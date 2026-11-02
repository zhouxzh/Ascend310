#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"
env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"
[[ -r "${env_file}" ]] || { echo "Static-KV environment file is not readable: ${env_file}" >&2; exit 2; }
set -a
source "${env_file}"
set +a
[[ "${ASCEND_EDGE_CHAT_BACKEND:-}" == "acl-static-kv" ]] || { echo "standalone static-KV mode requires acl-static-kv" >&2; exit 2; }

report_root="${ASCEND_EDGE_CHAT_RUN_DIR:-${root_dir}/run/static-kv-standalone}"
run_dir="${report_root}/$(date -u +%Y%m%dT%H%M%SZ)"
cleanup_timeout="${ASCEND_EDGE_CHAT_CLEANUP_TIMEOUT_SECONDS:-30}"
[[ "${cleanup_timeout}" =~ ^[1-9][0-9]*$ ]] || { echo "cleanup timeout must be a positive integer" >&2; exit 2; }
for port in 8084 7861 7865; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then echo "static-KV run requires unused port ${port}" >&2; exit 2; fi
done
mkdir -p "${run_dir}"
acl_pid=""; gateway_pid=""; ui_pid=""
stop_pid() {
  local label="$1" pid="$2"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 0
  kill -0 "${pid}" 2>/dev/null || return 0
  kill -TERM "${pid}" 2>/dev/null || true
  local deadline=$((SECONDS + cleanup_timeout))
  while kill -0 "${pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then kill -KILL "${pid}" 2>/dev/null || true; break; fi
    sleep 0.2
  done
  wait "${pid}" 2>/dev/null || true
}
cleanup() {
  stop_pid ui "${ui_pid:-}"
  stop_pid gateway "${gateway_pid:-}"
  stop_pid acl-worker "${acl_pid:-}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

bash scripts/verify/release-8t-preflight.sh >"${run_dir}/preflight.log" 2>&1
bash scripts/run/start-acl-worker.sh >"${run_dir}/acl-worker.log" 2>&1 & acl_pid=$!
bash scripts/run/start-gateway.sh >"${run_dir}/gateway.log" 2>&1 & gateway_pid=$!
bash scripts/run/start-ui.sh >"${run_dir}/ui.log" 2>&1 & ui_pid=$!
ready=0
for _ in $(seq 1 90); do
  if curl --fail --silent --show-error http://127.0.0.1:8084/health >"${run_dir}/acl-health.json" \
    && curl --fail --silent --show-error http://127.0.0.1:7861/health >"${run_dir}/gateway-health.json" \
    && curl --fail --silent --show-error http://127.0.0.1:7865/ >/dev/null; then ready=1; break; fi
  for pid in "${acl_pid}" "${gateway_pid}" "${ui_pid}"; do
    kill -0 "${pid}" 2>/dev/null || { echo "a static-KV process exited; see ${run_dir}" >&2; exit 1; }
  done
  sleep 2
done
(( ready == 1 )) || { echo "static-KV services did not become ready; see ${run_dir}" >&2; exit 1; }
printf 'mode=optional-static-kv\nrun_dir=%s\nui_url=http://<board-ip>:7865/\n' "${run_dir}"
while true; do
  for pid in "${acl_pid}" "${gateway_pid}" "${ui_pid}"; do
    kill -0 "${pid}" 2>/dev/null || { echo "a static-KV process exited; stopping remaining processes" >&2; exit 1; }
  done
  sleep 2
done
