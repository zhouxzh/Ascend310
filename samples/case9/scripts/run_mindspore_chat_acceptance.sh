#!/usr/bin/env bash
# Run read-only acceptance checks for one already-running MindSpore profile.
# The default is dry-run. No package manager, service launcher, or process
# manager is invoked by this wrapper.
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"
python_bin="${PYTHON_BIN:-python3}"
mode="--dry-run"
forward=()

# The registry lives at the repository root.  Keep direct script execution
# deterministic even if an old same-named helper exists below scripts/.
export PYTHONPATH="${repo_dir}${PYTHONPATH:+:${PYTHONPATH}}"

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      cd "${repo_dir}"
      exec "$python_bin" -m scripts.mindspore_chat_acceptance --help
      ;;
    --execute|--run) mode="--execute" ;;
    --dry-run) mode="--dry-run" ;;
    *) forward+=("$arg") ;;
  esac
done

cd "${repo_dir}"
exec "$python_bin" -m scripts.mindspore_chat_acceptance "$mode" "${forward[@]}"
