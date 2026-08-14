#!/usr/bin/env bash
set -eo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The CANN setup script reads LD_LIBRARY_PATH before assigning it. Keep nounset
# disabled for that setup step, then enable strict checks for the application.
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
set -u
conda activate base
cd "${PROJECT_ROOT}"
exec python scripts/start_dashboard.py "$@"
