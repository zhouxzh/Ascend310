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

: "${ASCEND_EDGE_CHAT_DATA_DIR:?set an absolute ASCEND_EDGE_CHAT_DATA_DIR}"
conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:?set ASCEND_EDGE_CHAT_CONDA_ENV to the validated MindSpore environment}"
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"

[[ "${conda_env}" == "ascend" ]] || {
  echo "release requires the unified conda environment named ascend (got ${conda_env})" >&2
  exit 2
}
[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
[[ -r "${cann_env}" ]] || { echo "CANN environment script not found: ${cann_env}" >&2; exit 2; }
source "${conda_profile}"
conda activate "${conda_env}"
set +u
source "${cann_env}"
set -u
export PYTHONNOUSERSITE=1

# The worker and release preflight share this fail-closed lock verifier. It
# rejects non-ascend envs, user-site imports, Python/ABI/package/CANN drift,
# and a missing PyACL import before any model or gateway work begins.
python - <<'PY'
import importlib
import sys

from ascend_edge_chat.backends.runtime_preflight import validate_runtime_lock

report = validate_runtime_lock()
print("runtime_lock=%s" % report["status"])
for name in ("acl", "mindspore", "mindnlp", "fastapi", "pydantic", "httpx", "uvicorn", "dotenv"):
    module = importlib.import_module(name)
    print("%s=%s" % (name, getattr(module, "__file__", "<built-in>")))
print("python=%s" % sys.executable)
print("python_abi=%s" % sys.implementation.cache_tag)
PY
npu-smi info
python -m ascend_edge_chat verify --data-dir "${ASCEND_EDGE_CHAT_DATA_DIR}"
python -m ascend_edge_chat gateway --check-config
