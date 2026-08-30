#!/usr/bin/env bash
# Start the lightweight text-only browser UI. No audio or model packages are
# installed by this wrapper; it only activates an existing Python environment.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${script_dir}"

# Load configuration before selecting the interpreter so TEXT_CHAT_CONDA_ENV
# and CONDA_PROFILE can be kept in the board-local env file.  The files are
# operator-controlled in this experiment and are intentionally sourced, just
# as the gateway launcher does.
load_env_file() {
  local env_path="$1"
  if [[ -f "${env_path}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_path}"
    set +a
  fi
}

load_env_file "${TEXT_CHAT_ENV_ROOT_FILE:-.env}"
local_env_file="${TEXT_CHAT_ENV_FILE:-.env.local}"
load_env_file "${local_env_file}"

conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${TEXT_CHAT_CONDA_ENV:-case9-local-chat}"

if [[ ! -r "${conda_profile}" ]]; then
  echo "Conda profile not found: ${conda_profile}" >&2
  exit 2
fi

# Activate the dedicated runtime even when the caller did not activate conda
# first. This service does not install packages or fall back to base Python.
# shellcheck disable=SC1090
source "${conda_profile}"
conda activate "${conda_env}" || {
  echo "Conda environment not found: ${conda_env}" >&2
  exit 2
}
export PYTHONNOUSERSITE=1
export TEXT_CHAT_GATEWAY_API_KEY="${TEXT_CHAT_GATEWAY_API_KEY:-${GATEWAY_API_KEY:-}}"

# A dedicated environment remains the default and the only admission path for
# formal runs.  The 20T board currently has a user-authorized, pre-existing
# `base` environment that contains inference packages; allow the text-only UI
# to be exercised there only with an explicit, auditable opt-in.  This does not
# import or install any of those packages and never permits the setting for a
# non-base environment.
allow_dirty_base="${TEXT_CHAT_ALLOW_DIRTY_BASE:-0}"
if [[ "$allow_dirty_base" != "0" && "$allow_dirty_base" != "1" ]]; then
  echo "TEXT_CHAT_ALLOW_DIRTY_BASE must be 0 or 1" >&2
  exit 2
fi

python_bin="${TEXT_CHAT_PYTHON_BIN:-python}"
"${python_bin}" -c 'import os, pathlib, sys; expected=os.environ.get("CONDA_PREFIX"); actual=pathlib.Path(sys.executable).resolve(); expected_bin=(pathlib.Path(expected) / "bin" / "python").resolve() if expected else actual; sys.exit("python is outside the activated conda environment") if expected and actual != expected_bin else None' \
  || { echo "Python is not the activated ${conda_env} interpreter; refusing to start" >&2; exit 2; }
"${python_bin}" -c 'import fastapi, httpx, uvicorn; print("text-chat runtime:", fastapi.__version__, httpx.__version__, uvicorn.__version__)' \
  || { echo "FastAPI/httpx/uvicorn are unavailable in ${conda_env}; refusing to start" >&2; exit 2; }
if ! "${python_bin}" - "${allow_dirty_base}" "${conda_env}" <<'PY'
import importlib.metadata
import importlib.util
import sys
import os

allow_dirty_base = sys.argv[1] == "1"
conda_env = sys.argv[2]
blocked = {
    "torch",
    "torch_npu",
    "torchaudio",
    "torchvision",
    "torchtext",
    "mindtorch",
    "mindspore",
    "transformers",
    "vllm",
    "mindie",
    "qwen_ascend_llm",
    "onnxruntime",
}
installed = set()
for distribution in importlib.metadata.distributions():
    name = (distribution.metadata.get("Name") or "").lower().replace("-", "_")
    if name in blocked:
        installed.add(name)
for name in blocked:
    try:
        if importlib.util.find_spec(name) is not None:
            installed.add(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
if installed:
    # The override is deliberately narrow: only the known 20T `base` test
    # environment may continue, and the package list is still reported.
    if not (
        allow_dirty_base
        and conda_env == "base"
        and os.environ.get("CONDA_DEFAULT_ENV") == "base"
    ):
        print(
            "Forbidden inference packages are present in the activated environment: "
            + ", ".join(sorted(installed)),
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(
        "WARNING: explicit dirty-base text-UI test override; forbidden packages "
        "remain importable: " + ", ".join(sorted(installed)),
        file=sys.stderr,
    )
    print("forbidden inference packages: allowed by explicit dirty-base override")
else:
    print("forbidden inference packages: none")
PY
then
  echo "Refusing to start: use dedicated ${conda_env} without forbidden inference packages" >&2
  exit 2
fi

# The dedicated environment disables user-site imports. Report pre-existing
# board-local inference packages without deleting them or allowing them into
# this process.
env -u PYTHONNOUSERSITE "${python_bin}" - <<'PY'
from pathlib import Path
import site

blocked = {
    "torch", "torch_npu", "torchaudio", "mindtorch", "torchvision", "torchtext",
    "mindspore", "transformers", "vllm", "mindie", "qwen_ascend_llm", "onnxruntime",
}
user_site = Path(site.getusersitepackages()).resolve()
found = set()
if user_site.is_dir():
    for entry in user_site.iterdir():
        stem = entry.name.lower()
        if stem.endswith(".dist-info") or stem.endswith(".egg-info"):
            stem = stem.rsplit("-", 1)[0]
        stem = stem.replace("-", "_")
        if stem in blocked:
            found.add(stem)
        if entry.is_dir() and entry.name.replace("-", "_").lower() in blocked:
            found.add(entry.name.replace("-", "_").lower())
print("user_site=" + str(user_site))
print("user_site_preexisting=" + (", ".join(sorted(found)) if found else "none"))
print("user_site_packages_are_not_loaded_when_PYTHONNOUSERSITE=1")
PY

cat >&2 <<'WARNING'
WARNING: the text chat UI has no browser authentication. Any same-LAN client
can submit text to the board. Use it only on a trusted experimental network.
The gateway token remains server-side.
If TEXT_CHAT_ALLOW_DIRTY_BASE=1 was used, this is an experimental dirty-base
run and must not be promoted to a formal deployment.
WARNING

exec "${python_bin}" text_chat_app.py "$@"
