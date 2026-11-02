#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"
[[ -r "${env_file}" ]] || { echo "Supervisor environment file is not readable: ${env_file}" >&2; exit 2; }
set -a
source "${env_file}"
set +a

conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:-ascend}"
[[ "${conda_env}" == "ascend" ]] || { echo "backend supervisor requires conda env ascend" >&2; exit 2; }
[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
: "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR:?set ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR}"
source "${conda_profile}"
conda activate "${conda_env}"
export PYTHONNOUSERSITE=1
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
[[ -r "${cann_env}" ]] || { echo "CANN environment script not found: ${cann_env}" >&2; exit 2; }
set +u
source "${cann_env}"
set -u
exec python -m ascend_edge_chat backend-supervisor \
  --host 127.0.0.1 \
  --port "${ASCEND_EDGE_CHAT_BACKEND_SUPERVISOR_PORT:-7863}" \
  --candidate-data-dir "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR}" \
  --catalog "${ASCEND_EDGE_CHAT_ACL_CANDIDATE_CATALOG:-${root_dir}/configs/20t-backend-catalog.json}" \
  --mindspore-catalog "${ASCEND_EDGE_CHAT_MODEL_CATALOG:-${root_dir}/configs/release-model-catalog.json}" \
  "$@"
