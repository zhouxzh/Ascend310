#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"
source "${root_dir}/.env"
report_root="${ASCEND_EDGE_CHAT_REPORT_DIR:-${root_dir}/run/reports}"
run_id="release-$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="${report_root}/${run_id}"
mkdir -p "${run_dir}"

for port in 7861 7865 8090; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    echo "release acceptance requires unused port ${port}" >&2
    exit 2
  fi
done

cleanup() {
  if [[ -n "${monitor_pid:-}" ]] && kill -0 "${monitor_pid}" 2>/dev/null; then
    kill "${monitor_pid}" 2>/dev/null || true
    wait "${monitor_pid}" 2>/dev/null || true
  fi
  for pid in "${ui_pid:-}" "${gateway_pid:-}" "${worker_pid:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

capture_processes() {
  local phase="$1"
  {
    printf 'timestamp_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for item in "worker:${worker_pid:-}" "gateway:${gateway_pid:-}" "ui:${ui_pid:-}"; do
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

bash scripts/verify/release-preflight.sh >"${run_dir}/preflight.log" 2>&1
npu-smi info >"${run_dir}/npu-before.log" 2>&1 || true
bash scripts/run/start-worker.sh >"${run_dir}/worker.log" 2>&1 & worker_pid=$!
bash scripts/run/start-gateway.sh >"${run_dir}/gateway.log" 2>&1 & gateway_pid=$!
bash scripts/run/start-ui.sh >"${run_dir}/ui.log" 2>&1 & ui_pid=$!

for _ in $(seq 1 60); do
  if curl --fail --silent --show-error http://127.0.0.1:8090/health >"${run_dir}/worker-health.json" \
    && curl --fail --silent --show-error http://127.0.0.1:7861/health >"${run_dir}/gateway-health.json" \
    && curl --fail --silent --show-error http://127.0.0.1:7865/ >"${run_dir}/ui.html"; then
    break
  fi
  sleep 2
done
test -s "${run_dir}/worker-health.json"
test -s "${run_dir}/gateway-health.json"
test -s "${run_dir}/ui.html"
capture_processes before
(
  while kill -0 "${worker_pid}" 2>/dev/null; do
    capture_processes during-"$(date -u +%H%M%S)"
    npu-smi info >>"${run_dir}/npu-during.log" 2>&1 || true
    sleep 5
  done
) & monitor_pid=$!

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate "${ASCEND_EDGE_CHAT_CONDA_ENV}"
export PYTHONNOUSERSITE=1
python scripts/verify/release_acceptance.py \
  --token "${ASCEND_EDGE_CHAT_GATEWAY_API_KEY}" \
  --output "${run_dir}/acceptance.json" \
  >"${run_dir}/acceptance-path.log" 2>&1
capture_processes after
npu-smi info >"${run_dir}/npu-after.log" 2>&1 || true
printf '%s\n' "${run_dir}"
