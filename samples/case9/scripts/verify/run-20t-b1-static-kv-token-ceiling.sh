#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"
[[ -r "${env_file}" ]] || { echo "Environment file is not readable: ${env_file}" >&2; exit 2; }
set -a
source "${env_file}"
set +a

: "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR:?set ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR}"
conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
source "${conda_profile}"
experiment_conda_env="${ASCEND_EDGE_CHAT_TOKEN_CEILING_CONDA_ENV:-ascend}"
conda activate "${experiment_conda_env}"
export PYTHONNOUSERSITE=1
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
[[ -r "${cann_env}" ]] || { echo "CANN environment script not found: ${cann_env}" >&2; exit 2; }
set +u
source "${cann_env}"
set -u

port=8086
candidate_id="${ASCEND_EDGE_CHAT_TOKEN_CEILING_CANDIDATE_ID:-qwen2.5-0.5b-instruct-static-kv-1024-b1-offline}"
candidate_catalog="${ASCEND_EDGE_CHAT_TOKEN_CEILING_CATALOG:-${root_dir}/configs/20t-backend-catalog.json}"
[[ -r "${candidate_catalog}" ]] || { echo "candidate catalog is not readable: ${candidate_catalog}" >&2; exit 2; }
worker_max_tokens="${ASCEND_EDGE_CHAT_TOKEN_CEILING_WORKER_MAX_TOKENS:-80}"
execution_timeout="${ASCEND_EDGE_CHAT_TOKEN_CEILING_EXECUTION_TIMEOUT_SECONDS:-240}"
force_continue="${ASCEND_EDGE_CHAT_TOKEN_CEILING_FORCE_CONTINUE:-0}"
allow_legacy_environment="${ASCEND_EDGE_CHAT_TOKEN_CEILING_ALLOW_LEGACY_ENVIRONMENT:-0}"
[[ "${worker_max_tokens}" =~ ^[1-9][0-9]*$ ]] && (( worker_max_tokens <= 1024 )) || {
  echo "ASCEND_EDGE_CHAT_TOKEN_CEILING_WORKER_MAX_TOKENS must be 1..1024" >&2
  exit 2
}
[[ "${execution_timeout}" =~ ^[1-9][0-9]*$ ]] && (( execution_timeout <= 14400 )) || {
  echo "ASCEND_EDGE_CHAT_TOKEN_CEILING_EXECUTION_TIMEOUT_SECONDS must be 1..14400" >&2
  exit 2
}
[[ "${force_continue}" =~ ^[01]$ ]] || {
  echo "ASCEND_EDGE_CHAT_TOKEN_CEILING_FORCE_CONTINUE must be 0 or 1" >&2
  exit 2
}
[[ "${allow_legacy_environment}" =~ ^[01]$ ]] || {
  echo "ASCEND_EDGE_CHAT_TOKEN_CEILING_ALLOW_LEGACY_ENVIRONMENT must be 0 or 1" >&2
  exit 2
}
if ss -ltn "sport = :${port}" | grep -q LISTEN; then
  echo "token-ceiling experiment requires unused port ${port}" >&2
  exit 2
fi

report_dir="${ASCEND_EDGE_CHAT_TOKEN_CEILING_REPORT_DIR:-${root_dir}/run/reports/20t-b1-static-kv-token-ceiling-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "${report_dir}"
worker_log="${report_dir}/acl-worker.log"
report_path="${report_dir}/results.json"

cleanup() {
  if [[ -n "${worker_pid:-}" ]] && kill -0 "${worker_pid}" 2>/dev/null; then
    kill -TERM "${worker_pid}" 2>/dev/null || true
    for _ in $(seq 1 80); do
      kill -0 "${worker_pid}" 2>/dev/null || break
      sleep 0.25
    done
    kill -KILL "${worker_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

worker_args=(
  -m ascend_edge_chat acl-candidate-worker
  --candidate "${candidate_id}"
  --data-dir "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR}"
  --catalog "${candidate_catalog}"
  --host 127.0.0.1
  --port "${port}"
  --max-tokens "${worker_max_tokens}"
  --execution-timeout "${execution_timeout}"
)
if [[ "${force_continue}" == "1" ]]; then
  worker_args+=(--force-continue)
else
  worker_args+=(--disable-sentence-stop)
fi
if [[ "${allow_legacy_environment}" == "1" ]]; then
  worker_args+=(--allow-legacy-environment)
fi
nohup setsid python "${worker_args[@]}" >"${worker_log}" 2>&1 < /dev/null &
worker_pid=$!

for _ in $(seq 1 180); do
  if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:${port}/health" >"${report_dir}/initial-health.json"; then
    break
  fi
  kill -0 "${worker_pid}" 2>/dev/null || { tail -n 120 "${worker_log}" >&2; exit 1; }
  sleep 1
done
curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null

measure_args=(
  --url "http://127.0.0.1:${port}"
  --out "${report_path}"
  --kind "${ASCEND_EDGE_CHAT_TOKEN_CEILING_REPORT_KIND:-20t-b1-static-kv-token-ceiling}"
)
if [[ "${force_continue}" == "1" ]]; then
  measure_args+=(--require-force-continue)
fi
python scripts/verify/measure-b1-static-kv-token-ceiling.py \
  "${measure_args[@]}" \
  "$@"

printf 'report_dir=%s\nreport=%s\n' "${report_dir}" "${report_path}"
