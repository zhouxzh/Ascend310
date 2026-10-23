#!/usr/bin/env bash
# Read-only prerequisite gate for the optional board-owned voice agent.
# It never records PCM, installs packages, changes groups, or starts capture.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${root_dir}"

env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"
if [[ -r "${env_file}" ]]; then
  set -a
  source "${env_file}"
  set +a
fi

conda_profile="${ASCEND_EDGE_CHAT_CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
voice_env="${ASCEND_EDGE_CHAT_VOICE_CONDA_ENV:?set ASCEND_EDGE_CHAT_VOICE_CONDA_ENV to the isolated voice Python environment}"

[[ -r "${conda_profile}" ]] || { echo "Conda profile not found: ${conda_profile}" >&2; exit 2; }
source "${conda_profile}"
conda activate "${voice_env}"
export PYTHONNOUSERSITE=1

command -v arecord >/dev/null || { echo "arecord is unavailable" >&2; exit 2; }
command -v aplay >/dev/null || { echo "aplay is unavailable" >&2; exit 2; }
[[ -r /proc/asound/cards && -r /proc/asound/pcm ]] || { echo "ALSA proc nodes are unavailable" >&2; exit 2; }

if ! id -nG | tr ' ' '\n' | grep -qx audio; then
  echo "The account is not in the audio group; checking ACL/direct node access instead." >&2
fi

capture_count="$(grep -c 'capture' /proc/asound/pcm || true)"
playback_count="$(grep -c 'playback' /proc/asound/pcm || true)"
[[ "${capture_count}" -gt 0 ]] || { echo "No ALSA capture PCM device is enumerated" >&2; exit 4; }
[[ "${playback_count}" -gt 0 ]] || { echo "No ALSA playback PCM device is enumerated" >&2; exit 4; }

capture_access=0
for node in /dev/snd/pcmC*D*c; do
  [[ -e "${node}" ]] || continue
  if [[ -r "${node}" ]]; then
    capture_access=1
    break
  fi
done
[[ "${capture_access}" -eq 1 ]] || {
  echo "No enumerated ALSA capture node is readable by the current account" >&2
  exit 3
}

playback_access=0
for node in /dev/snd/pcmC*D*p; do
  [[ -e "${node}" ]] || continue
  if [[ -w "${node}" ]]; then
    playback_access=1
    break
  fi
done
[[ "${playback_access}" -eq 1 ]] || {
  echo "No enumerated ALSA playback node is writable by the current account" >&2
  exit 3
}

python - <<'PY'
import importlib
import sys
for module in ("fastapi", "httpx", "pydantic", "uvicorn"):
    value = importlib.import_module(module)
    print(f"{module}={getattr(value, '__file__', '<built-in>')}")
print(f"python={sys.executable}")
PY
python -m ascend_edge_chat voice --check-config

echo "Voice preflight passed: ALSA devices are enumerated and the account has audio access."
echo "ASR/TTS model readiness, recording, playback, and speech quality remain separate gates."
