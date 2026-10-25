#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:?set ASCEND_EDGE_CHAT_CONDA_ENV to the validated MindSpore environment}"
wheelhouse="${ASCEND_EDGE_CHAT_WHEELHOUSE:?set ASCEND_EDGE_CHAT_WHEELHOUSE to the reviewed local wheel directory}"
requirements="${ASCEND_EDGE_CHAT_REQUIREMENTS_FILE:-${root_dir}/configs/release-service-requirements.txt}"

[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
[[ -f "${requirements}" && ! -L "${requirements}" ]] || { echo "Requirements file must be a regular file: ${requirements}" >&2; exit 2; }
[[ -d "${wheelhouse}" && ! -L "${wheelhouse}" ]] || { echo "Wheelhouse must be a regular directory: ${wheelhouse}" >&2; exit 2; }
source "${conda_profile}"
conda activate "${conda_env}"
export PYTHONNOUSERSITE=1

python -m pip install --disable-pip-version-check --no-index --no-deps \
  --only-binary=:all: --require-hashes --find-links "${wheelhouse}" -r "${requirements}"

python - <<'PY'
import importlib
import importlib.metadata

for name in ("fastapi", "pydantic", "uvicorn", "httpx", "dotenv"):
    module = importlib.import_module(name)
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        version = "unversioned"
    print("%s=%s (%s)" % (name, version, module.__file__))
PY
