#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"
source "${root_dir}/.env"

[[ "${ASCEND_EDGE_CHAT_BACKEND:-}" == "acl-static-kv" ]] || {
  echo "ASCEND_EDGE_CHAT_BACKEND must be acl-static-kv" >&2
  exit 2
}

for port in 8084 7861 7865; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    echo "release requires unused port ${port}" >&2
    exit 2
  fi
done

run_root="${ASCEND_EDGE_CHAT_RUN_DIR:-${root_dir}/run/formal-8t}"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="${run_root}/${run_id}"
mkdir -p "${run_dir}"

cleanup_timeout_seconds="${ASCEND_EDGE_CHAT_CLEANUP_TIMEOUT_SECONDS:-30}"
stop_pid() {
  local label="$1"
  local pid="$2"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 0
  kill -0 "${pid}" 2>/dev/null || return 0
  kill -TERM "${pid}" 2>/dev/null || true
  local deadline=$((SECONDS + cleanup_timeout_seconds))
  while kill -0 "${pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      kill -KILL "${pid}" 2>/dev/null || true
      break
    fi
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
  if curl --fail --silent --show-error http://127.0.0.1:8084/health >/dev/null \
    && curl --fail --silent --show-error http://127.0.0.1:7861/health >/dev/null \
    && curl --fail --silent --show-error http://127.0.0.1:7865/ >/dev/null; then
    ready=1
    break
  fi
  for pid in "${acl_pid}" "${gateway_pid}" "${ui_pid}"; do
    kill -0 "${pid}" 2>/dev/null || {
      echo "a release process exited before readiness; see ${run_dir}" >&2
      exit 1
    }
  done
  sleep 2
done
(( ready == 1 )) || {
  echo "release services did not become ready; see ${run_dir}" >&2
  exit 1
}

printf 'release_dir=%s\nacl_pid=%s\ngateway_pid=%s\nui_pid=%s\nui_url=http://<board-ip>:%s/\n' \
  "${run_dir}" "${acl_pid}" "${gateway_pid}" "${ui_pid}" "${ASCEND_EDGE_CHAT_UI_PORT:-7865}"

while true; do
  for pid in "${acl_pid}" "${gateway_pid}" "${ui_pid}"; do
    kill -0 "${pid}" 2>/dev/null || {
      echo "a release process exited; stopping the remaining processes" >&2
      exit 1
    }
  done
  sleep 2
done
