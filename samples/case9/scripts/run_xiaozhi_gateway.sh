#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${CASE9_GATEWAY_CONDA_ENV:-case9-local-chat}"
cd "${script_dir}"
# Formal gateway launches remain isolated.  The candidate MindSpore chain can
# explicitly use the board image's existing user-site web dependencies without
# installing or upgrading packages in the shared base environment.
case "${CASE9_GATEWAY_PYTHONNOUSERSITE:-1}" in
  1) export PYTHONNOUSERSITE=1 ;;
  0)
    if [[ "${CASE9_GATEWAY_ALLOW_DIRTY_BASE:-0}" != "1" || "${conda_env}" != "base" ]]; then
      echo "CASE9_GATEWAY_PYTHONNOUSERSITE=0 is only allowed with CASE9_GATEWAY_ALLOW_DIRTY_BASE=1 in base" >&2
      exit 2
    fi
    unset PYTHONNOUSERSITE
    ;;
  *) echo "CASE9_GATEWAY_PYTHONNOUSERSITE must be 0 or 1" >&2; exit 2 ;;
esac

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
