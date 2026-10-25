#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"
if [[ -r "${env_file}" ]]; then
  set -a
  source "${env_file}"
  set +a
fi

: "${ASCEND_EDGE_CHAT_DATA_DIR:?set an absolute ASCEND_EDGE_CHAT_DATA_DIR}"
: "${ASCEND_EDGE_CHAT_ACL_LOCK:?set ASCEND_EDGE_CHAT_ACL_LOCK}"
conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:-ascend}"
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"

[[ "${conda_env}" == "ascend" ]] || {
  echo "8T static-KV release requires the unified ascend environment" >&2
  exit 2
}
[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
[[ -r "${cann_env}" ]] || { echo "CANN environment script not found: ${cann_env}" >&2; exit 2; }

source "${conda_profile}"
conda activate "${conda_env}"
set +u
source "${cann_env}"
set -u
export PYTHONNOUSERSITE=1

exec python -m ascend_edge_chat acl-worker \
  --data-dir "${ASCEND_EDGE_CHAT_DATA_DIR}" \
  --lock "${ASCEND_EDGE_CHAT_ACL_LOCK}" \
  --host "${ASCEND_EDGE_CHAT_ACL_HOST:-127.0.0.1}" \
  --port "${ASCEND_EDGE_CHAT_ACL_PORT:-8084}" \
  --max-tokens "${ASCEND_EDGE_CHAT_ACL_MAX_TOKENS:-80}" \
  --execution-timeout "${ASCEND_EDGE_CHAT_ACL_EXECUTION_TIMEOUT_SECONDS:-240}" \
  "$@"
