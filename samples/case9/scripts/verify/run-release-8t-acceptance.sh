#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"
source "${root_dir}/.env"

[[ "${ASCEND_EDGE_CHAT_BACKEND:-}" == "acl-static-kv" ]] || {
  echo "this acceptance script only runs the acl-static-kv backend" >&2
  exit 2
}
: "${ASCEND_EDGE_CHAT_MODEL_SOURCE:?set ASCEND_EDGE_CHAT_MODEL_SOURCE to hf or offline-bundle}"

cleanup_timeout_seconds="${ASCEND_EDGE_CHAT_CLEANUP_TIMEOUT_SECONDS:-30}"
report_root="${ASCEND_EDGE_CHAT_REPORT_DIR:-${root_dir}/run/reports}"
run_id="release-8t-$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="${report_root}/${run_id}"
mkdir -p "${run_dir}"

stop_pid() {
  local label="$1"
  local pid="$2"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 0
  kill -0 "${pid}" 2>/dev/null || return 0
  echo "stopping ${label} pid=${pid}" >&2
  kill -TERM "${pid}" 2>/dev/null || true
  local deadline=$((SECONDS + cleanup_timeout_seconds))
  while kill -0 "${pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "${label} pid=${pid} did not exit after ${cleanup_timeout_seconds}s; sending KILL" >&2
      kill -KILL "${pid}" 2>/dev/null || true
      break
    fi
    sleep 0.2
  done
  wait "${pid}" 2>/dev/null || true
}

cleanup() {
  stop_pid monitor "${monitor_pid:-}"
  stop_pid ui "${ui_pid:-}"
  stop_pid gateway "${gateway_pid:-}"
  stop_pid acl-worker "${acl_pid:-}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for port in 8084 7861 7865; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    echo "8T release acceptance requires unused port ${port}" >&2
    exit 2
  fi
done

capture_processes() {
  local phase="$1"
  {
    printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for item in "acl:${acl_pid:-}" "gateway:${gateway_pid:-}" "ui:${ui_pid:-}"; do
      local label="${item%%:*}"
      local pid="${item#*:}"
      if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        printf '[%s]\n' "${label}"
        ps -o pid=,rss=,etimes=,args= -p "${pid}" || true
        if [[ -d "/proc/${pid}/fd" ]]; then
          printf 'fd_count='
          find "/proc/${pid}/fd" -mindepth 1 -maxdepth 1 -type l 2>/dev/null | wc -l
        fi
      fi
    done
  } >"${run_dir}/process-${phase}.log"
}

bash scripts/verify/release-8t-preflight.sh >"${run_dir}/preflight.log" 2>&1
npu-smi info >"${run_dir}/npu-before.log" 2>&1 || true
bash scripts/run/start-acl-worker.sh >"${run_dir}/acl-worker.log" 2>&1 & acl_pid=$!
bash scripts/run/start-gateway.sh >"${run_dir}/gateway.log" 2>&1 & gateway_pid=$!
bash scripts/run/start-ui.sh >"${run_dir}/ui.log" 2>&1 & ui_pid=$!

ready=0
for _ in $(seq 1 90); do
  if curl --fail --silent --show-error http://127.0.0.1:8084/health >"${run_dir}/acl-health.json" \
    && curl --fail --silent --show-error http://127.0.0.1:7861/health >"${run_dir}/gateway-health.json" \
    && curl --fail --silent --show-error http://127.0.0.1:7865/ >"${run_dir}/ui.html"; then
    ready=1
    break
  fi
  sleep 2
done
(( ready == 1 )) || {
  echo "8T release services did not become ready" >&2
  exit 1
}
capture_processes before
(
  while kill -0 "${acl_pid}" 2>/dev/null; do
    capture_processes "during-$(date -u +%H%M%S)"
    npu-smi info >>"${run_dir}/npu-during.log" 2>&1 || true
    sleep 5
  done
) & monitor_pid=$!

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate "${ASCEND_EDGE_CHAT_CONDA_ENV:-ascend}"
export PYTHONNOUSERSITE=1
python scripts/verify/release_acceptance.py \
  --token "${ASCEND_EDGE_CHAT_GATEWAY_API_KEY}" \
  --model-source "${ASCEND_EDGE_CHAT_MODEL_SOURCE}" \
  --output "${run_dir}/acceptance.json" \
  >"${run_dir}/acceptance-path.log" 2>&1
capture_processes after
npu-smi info >"${run_dir}/npu-after.log" 2>&1 || true
printf '%s\n' "${run_dir}"
