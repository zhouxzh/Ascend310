#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
cd "$project_root"

source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base

export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1

python -m time_frequency_dashboard.model.prepare_volk_benchmarks
exec python -m time_frequency_dashboard.benchmark_volk_npu "$@"
