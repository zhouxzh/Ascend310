#!/bin/bash
# Case 6: Smart Car Perception - unified Ascend environment
set -euo pipefail

CONDA_SH="${CONDA_SH:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
if [[ ! -r "${CONDA_SH}" ]]; then
    echo "conda activation script not found: ${CONDA_SH}" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "${CONDA_SH}"
if ! conda activate ascend; then
    echo "required conda environment 'ascend' is unavailable" >&2
    exit 2
fi
export PYTHONNOUSERSITE=1

if [[ -r /usr/local/Ascend/ascend-toolkit/set_env.sh ]]; then
    # shellcheck disable=SC1091
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
fi

if [[ "$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.9" ]]; then
    echo "case6 requires Python 3.9 from conda environment 'ascend'" >&2
    exit 2
fi

echo "=== Case 6: Smart Car Perception - unified environment ==="
echo "[1/2] Installing Python packages into ascend ..."
python -m pip install -r requirements.txt

echo "[2/2] Preparing ResNet18 scene classifier (ONNX + ATC where available) ..."
python prepare_models.py

echo ""
echo "=== setup complete ==="
echo "Start: python app.py"
echo "Recover model: python prepare_models.py --force"
