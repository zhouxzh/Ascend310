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
conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:-ascend}"

[[ "${conda_env}" == "ascend" ]] || {
  echo "8T static-KV release requires the unified ascend environment" >&2
  exit 2
}
[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }

source "${conda_profile}"
conda activate "${conda_env}"
export PYTHONNOUSERSITE=1

python -m ascend_edge_chat acl download \
  --data-dir "${ASCEND_EDGE_CHAT_DATA_DIR}" \
  --mirror "${HF_ENDPOINT:-https://hf-mirror.com}"
