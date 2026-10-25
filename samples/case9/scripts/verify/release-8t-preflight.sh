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
: "${ASCEND_EDGE_CHAT_ACL_LOCK:?set ASCEND_EDGE_CHAT_ACL_LOCK}"
conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${ASCEND_EDGE_CHAT_CONDA_ENV:-ascend}"
cann_env="${ASCEND_EDGE_CHAT_CANN_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"

[[ "${ASCEND_EDGE_CHAT_BACKEND:-}" == "acl-static-kv" ]] || {
  echo "ASCEND_EDGE_CHAT_BACKEND must be acl-static-kv" >&2
  exit 2
}
[[ "${conda_env}" == "ascend" ]] || {
  echo "release requires the unified conda environment named ascend" >&2
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

python - <<'PY'
import importlib
import platform
import site
import sys

if sys.version_info[:3] != (3, 9, 25):
    raise SystemExit("release requires Python 3.9.25, got %s" % platform.python_version())
if site.ENABLE_USER_SITE:
    raise SystemExit("release requires user-site isolation")
for name in ("acl", "numpy", "tokenizers", "fastapi", "pydantic", "httpx", "uvicorn", "dotenv"):
    module = importlib.import_module(name)
    print("%s=%s" % (name, getattr(module, "__file__", "<built-in>")))
print("python=%s" % sys.executable)
print("python_abi=%s" % sys.implementation.cache_tag)
PY

npu_report="$(mktemp "${TMPDIR:-/tmp}/ascend-edge-chat-8t-npu.XXXXXX")"
trap 'rm -f "${npu_report}"' EXIT
npu-smi info | tee "${npu_report}"
grep -Eq '310B4|Ascend310B4' "${npu_report}"
python -m ascend_edge_chat acl verify --data-dir "${ASCEND_EDGE_CHAT_DATA_DIR}" --lock "${ASCEND_EDGE_CHAT_ACL_LOCK}"
python -m ascend_edge_chat gateway --check-config
python - <<'PY'
from pathlib import Path

from ascend_edge_chat.model_catalog import catalog_by_id
from ascend_edge_chat.ui.web import TextChatSettings

settings = TextChatSettings.from_environ()
catalog = catalog_by_id(Path(settings.catalog_path))
if settings.active_profile not in catalog:
    raise SystemExit("active profile is not present in the 8T release catalog")
if settings.worker_mode != "external" or settings.worker_health_url != "http://127.0.0.1:8084/health":
    raise SystemExit("8T release requires the fixed external ACL worker health endpoint")
print("ui_profile=%s worker_mode=%s health=%s" % (
    settings.active_profile, settings.worker_mode, settings.worker_health_url
))
PY
