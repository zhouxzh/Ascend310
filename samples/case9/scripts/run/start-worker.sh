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

conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:?set ASCEND_EDGE_CHAT_CONDA_ENV to the validated MindSpore environment}"
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"

[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
[[ -r "${cann_env}" ]] || { echo "CANN environment script not found: ${cann_env}" >&2; exit 2; }
source "${conda_profile}"
conda activate "${conda_env}"
set +u
source "${cann_env}"
set -u
export PYTHONNOUSERSITE=1
export ASCEND_EDGE_CHAT_ALLOW_PREVIEW="${ASCEND_EDGE_CHAT_ALLOW_PREVIEW:-1}"
exec python -m ascend_edge_chat worker "$@"
