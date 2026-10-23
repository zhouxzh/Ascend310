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
voice_env="${ASCEND_EDGE_CHAT_VOICE_CONDA_ENV:?set ASCEND_EDGE_CHAT_VOICE_CONDA_ENV to the isolated voice Python environment}"

[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
source "${conda_profile}"
conda activate "${voice_env}"
export PYTHONNOUSERSITE=1

# The agent is loopback-only and must own board ALSA devices. It does not source
# CANN or load the NPU worker; ASR/TTS readiness is checked independently.
exec python -m ascend_edge_chat voice "$@"
