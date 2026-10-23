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
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:?set ASCEND_EDGE_CHAT_CONDA_ENV to the validated Python environment}"

[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
source "${conda_profile}"
conda activate "${conda_env}"
export PYTHONNOUSERSITE=1
cat >&2 <<'WARNING'
WARNING: Ascend Edge Chat's browser UI has no login. Every host on the same
trusted LAN can send messages to this board. Do not expose port 7865 to the
internet. The gateway token remains server-side.
WARNING
exec python -m ascend_edge_chat ui "$@"
