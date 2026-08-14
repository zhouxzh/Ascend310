#!/usr/bin/env bash
set -eo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /usr/local/miniconda3/etc/profile.d/conda.sh
set -u
conda activate base
cd "${PROJECT_ROOT}"
exec python -m time_frequency_dashboard.acquisition.usb_diagnostics "$@"
