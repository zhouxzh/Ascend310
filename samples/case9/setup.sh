#!/usr/bin/env bash
# Install gateway dependencies only. Model conversion belongs to a board-side,
# model-specific acceptance campaign and is intentionally not part of setup.
#
# This entry point is deliberately fail-closed: it must run in an explicit,
# non-base virtual environment and requires an operator opt-in before pip can
# change anything. It never upgrades pip or installs into the board's
# system/base interpreter.
set -euo pipefail

if [[ "${CASE9_ALLOW_GATEWAY_INSTALL:-}" != "1" ]]; then
  echo "Refusing implicit dependency installation. Activate a dedicated conda/venv and set CASE9_ALLOW_GATEWAY_INSTALL=1." >&2
  exit 2
fi

python_bin="${PYTHON_BIN:-python3}"
export PYTHONNOUSERSITE=1

"${python_bin}" - <<'PY'
import importlib.util
import os
import sys

prefix = os.path.realpath(sys.prefix)
base_prefix = os.path.realpath(getattr(sys, "base_prefix", sys.prefix))
conda_prefix = os.path.realpath(os.environ.get("CONDA_PREFIX", ""))
conda_name = os.environ.get("CONDA_DEFAULT_ENV", "")
if prefix == "/usr" or (prefix == base_prefix and not conda_prefix):
    raise SystemExit("activate a dedicated virtualenv or conda environment first")
if conda_name.lower() in {"base", ""} and conda_prefix:
    raise SystemExit("the base conda environment is not an admitted install target")
blocked = {
    "torch", "torch_npu", "torchaudio", "torchvision", "torchtext",
    "mindtorch", "mindspore", "transformers", "vllm", "mindie",
}
present = sorted(name for name in blocked if importlib.util.find_spec(name) is not None)
if present:
    raise SystemExit("forbidden inference packages are present: " + ", ".join(present))
print("install_target", sys.executable)
PY

"${python_bin}" -m pip install --disable-pip-version-check --no-input -r requirements.txt

"${python_bin}" - <<'PY'
import importlib.util

blocked = {
    "torch", "torch_npu", "torchaudio", "torchvision", "torchtext",
    "mindtorch", "mindspore", "transformers", "vllm", "mindie",
}
present = sorted(name for name in blocked if importlib.util.find_spec(name) is not None)
if present:
    raise SystemExit("dependency installation introduced forbidden packages: " + ", ".join(present))
print("forbidden inference packages: none")
PY

if [[ -f .env ]]; then
  "${python_bin}" app.py --check-config
else
  echo "Dependencies installed. Copy .env.example to .env and configure it before starting."
fi
