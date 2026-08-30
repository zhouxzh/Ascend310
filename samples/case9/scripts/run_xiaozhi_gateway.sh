#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${CASE9_GATEWAY_CONDA_ENV:-case9-local-chat}"
cd "${script_dir}"
export PYTHONNOUSERSITE=1

if [[ -n "${PYTHON_BIN:-}" ]]; then
  exec "$PYTHON_BIN" app.py "$@"
fi
if [[ ! -r "$conda_profile" ]]; then
  echo "Conda profile not found: $conda_profile" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "$conda_profile"
conda activate "$conda_env"
exec python app.py "$@"
