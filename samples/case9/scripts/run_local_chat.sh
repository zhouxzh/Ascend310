#!/usr/bin/env bash
# Launch the unauthenticated, board-local chat service in its dedicated conda
# environment.  This script does not create or modify environments; provision
# the environment and speech runtimes explicitly before using it.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${LOCAL_CHAT_CONDA_ENV:-case9-local-chat}"

if [[ ! -r "${conda_profile}" ]]; then
  echo "Conda profile not found: ${conda_profile}" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "${conda_profile}"
conda activate "${conda_env}"
cd "${script_dir}"

# Keep the gateway token server-side. The browser only receives the WebSocket
# warning and device labels, never this value.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
local_env_file="${LOCAL_CHAT_ENV_FILE:-.env.local}"
if [[ -f "${local_env_file}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${local_env_file}"
  set +a
fi
# Re-assert this after loading operator-controlled env files.  The audio
# runtime must not inherit packages from ~/.local or from another interpreter.
export PYTHONNOUSERSITE=1
python_bin="${PYTHON_BIN:-python}"
"${python_bin}" -c 'import os, pathlib, sys; expected=os.environ.get("CONDA_PREFIX"); actual=pathlib.Path(sys.executable).resolve(); expected_bin=(pathlib.Path(expected) / "bin" / "python").resolve() if expected else actual; sys.exit("python is outside the activated conda environment") if expected and actual != expected_bin else None' \
  || { echo "Python is not the activated ${conda_env} interpreter; refusing to start" >&2; exit 2; }
"${python_bin}" -c 'import fastapi, httpx, sherpa_onnx, uvicorn; print("local-chat runtime:", fastapi.__version__, httpx.__version__, uvicorn.__version__, sherpa_onnx.__file__)' \
  || { echo "FastAPI/httpx/sherpa_onnx/uvicorn are unavailable in ${conda_env}; refusing to start" >&2; exit 2; }
if ! "${python_bin}" - <<'PY'
import importlib.metadata
import importlib.util
import sys

blocked = {
    "torch", "torch_npu", "torchaudio", "torchvision", "torchtext",
    "mindtorch", "mindspore", "transformers", "vllm", "mindie",
    "qwen_ascend_llm", "onnxruntime", "xformers",
}
found = set()
for distribution in importlib.metadata.distributions():
    name = (distribution.metadata.get("Name") or "").lower().replace("-", "_")
    if name in blocked:
        found.add(name)
for name in blocked:
    try:
        if importlib.util.find_spec(name) is not None:
            found.add(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
if found:
    raise SystemExit("forbidden inference packages are present: " + ", ".join(sorted(found)))
print("forbidden inference packages: none")
PY
then
  echo "Refusing to start: remove forbidden inference packages from ${conda_env}" >&2
  exit 2
fi
# Report pre-existing user-site packages without allowing them into this
# process. This is an audit signal, not a request to delete user files.
env -u PYTHONNOUSERSITE "${python_bin}" - <<'PY'
import site
from pathlib import Path

blocked = {"torch", "torch_npu", "torchaudio", "torchvision", "torchtext", "mindtorch",
           "mindspore", "transformers", "vllm", "mindie", "qwen_ascend_llm",
           "onnxruntime", "xformers"}
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
local_home="${CASE9_LOCAL_CHAT_HOME:-$HOME/case9-local-chat}"
export LOCAL_GATEWAY_API_KEY="${LOCAL_GATEWAY_API_KEY:-${GATEWAY_API_KEY:-}}"
export SHERPA_MODEL_DIR="${SHERPA_MODEL_DIR:-$local_home/artifacts/asr}"
export SHERPA_TTS_MODEL_DIR="${SHERPA_TTS_MODEL_DIR:-$local_home/artifacts/tts}"
export LOCAL_CHAT_PULSE_SOURCE="${LOCAL_CHAT_PULSE_SOURCE:-alsa_input.usb-046d_C922_Pro_Stream_Webcam_B7E0139F-02.analog-stereo}"
export LOCAL_CHAT_PULSE_SINK="${LOCAL_CHAT_PULSE_SINK:-alsa_output.usb-Jieli_Technology_UACDemoV1.0_4150344637353804-00.analog-stereo}"
export AUDIO_MAX_DURATION_SECONDS="${AUDIO_MAX_DURATION_SECONDS:-30}"

cat >&2 <<'WARNING'
WARNING: case9 local chat has no API authentication. A same-LAN client can
control the board microphone and USB speaker. Use it only on a trusted
experimental network.
WARNING

exec "${python_bin}" local_app.py "$@"
