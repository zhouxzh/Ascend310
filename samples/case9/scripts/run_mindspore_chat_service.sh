#!/usr/bin/env bash
# Start one profile-specific MindSpore chat worker on the candidate loopback
# port. This wrapper only activates the already-installed board environment;
# it never installs packages or changes shell startup files.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${script_dir}"

# ``case9-modelctl`` wraps this launcher with ``setsid``.  Direct invocations
# are re-execed through the same boundary so a MindSpore multiprocessing child
# cannot remain in the caller's process group.  The marker prevents recursion
# when the wrapper has already established the session.
if [[ "${CASE9_PROCESS_GROUP_READY:-0}" != "1" ]]; then
  command -v setsid >/dev/null 2>&1 || {
    echo "setsid is required for worker process-group isolation" >&2
    exit 2
  }
  export CASE9_PROCESS_GROUP_READY="1"
  exec setsid "${BASH_SOURCE[0]}" "$@"
fi
command -v setsid >/dev/null 2>&1 || {
  echo "setsid is required for worker process-group isolation" >&2
  exit 2
}
command -v ps >/dev/null 2>&1 || {
  echo "ps is required to verify worker process-group isolation" >&2
  exit 2
}
worker_pgid="$(ps -p "$$" -o pgid= 2>/dev/null | awk 'NR==1 {gsub(/[[:space:]]/, ""); print; exit}' || true)"
worker_sid="$(ps -p "$$" -o sid= 2>/dev/null | awk 'NR==1 {gsub(/[[:space:]]/, ""); print; exit}' || true)"
if [[ "${worker_pgid}" != "$$" || "${worker_sid}" != "$$" ]]; then
  echo "worker must run as a setsid session/process-group leader" >&2
  exit 2
fi

profile="${CASE9_ACTIVE_PROFILE:-}"
host="${MINDSPORE_CHAT_HOST:-127.0.0.1}"
port="${MINDSPORE_CHAT_PORT:-8090}"
registry="${CASE9_MODEL_PROFILES:-${script_dir}/configs/chat_model_profiles.json}"
conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${CASE9_MINDSPORE_CONDA_ENV:-base}"

if [[ "${host}" != "127.0.0.1" ]]; then
  echo "MindSpore chat service is loopback-only" >&2
  exit 2
fi
if [[ "${port}" != "8090" ]]; then
  echo "MindSpore chat service must use candidate port 8090" >&2
  exit 2
fi
if [[ -z "${profile}" ]]; then
  echo "CASE9_ACTIVE_PROFILE is required; select a profile with case9-modelctl" >&2
  exit 2
fi
if [[ ! -r "${conda_profile}" ]]; then
  echo "Conda profile not found: ${conda_profile}" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "${conda_profile}"
conda activate "${conda_env}" || {
  echo "Conda environment not found: ${conda_env}" >&2
  exit 2
}

# MindSpore 2.4.10 and MindNLP 0.4.1 are installed in this board image's
# base user site.  Do not hide that site by default: doing so selects the
# incompatible conda MindSpore copy and makes MindNLP unavailable.  The
# exception is explicit and remains useful for diagnosing a clean environment.
case "${CASE9_PYTHONNOUSERSITE:-}" in
  1) export PYTHONNOUSERSITE=1 ;;
  0|"") unset PYTHONNOUSERSITE ;;
  *) echo "CASE9_PYTHONNOUSERSITE must be 0 or 1" >&2; exit 2 ;;
esac

# Do not rely on an interactive shell's PATH.  ``modelctl`` and operators may
# invoke this wrapper through a non-interactive SSH command, where ``python``
# is otherwise absent.  The fallback is restricted to the activated conda
# prefix; a system Python is never accepted.
python_bin="${CASE9_PYTHON_BIN:-$(command -v python || true)}"
if [[ -z "${python_bin}" && -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
  python_bin="${CONDA_PREFIX}/bin/python"
fi
if [[ -z "${python_bin}" || ! -x "${python_bin}" ]]; then
  echo "Activated conda environment does not provide an executable Python" >&2
  exit 2
fi
python_real="$(readlink -f "${python_bin}" 2>/dev/null || printf '%s' "${python_bin}")"
prefix_real="$(readlink -f "${CONDA_PREFIX:-}" 2>/dev/null || printf '%s' "${CONDA_PREFIX:-}")"
if [[ -z "${prefix_real}" || "${python_real}" != "${prefix_real}"/* ]]; then
  if [[ "${CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON:-0}" != "1" || "${CASE9_PYTHON_BIN:-}" != /* ]]; then
    echo "Python executable is outside activated CONDA_PREFIX; refusing fallback" >&2
    exit 2
  fi
  echo "WARNING: using explicitly approved external Python ${python_real}" >&2
fi
export CASE9_PYTHON_BIN="${python_bin}"

cann_env="${CANN_ENV_SCRIPT:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
if [[ ! -r "${cann_env}" ]]; then
  echo "CANN environment script not found: ${cann_env}" >&2
  exit 2
fi
# shellcheck disable=SC1090
# CANN's vendor script is not nounset-clean on all 8.0 installations (it may
# read LD_LIBRARY_PATH before defining it). Keep the wrapper strict while
# allowing that vendor script to initialize its documented environment.
set +u
source "${cann_env}"
set -u

export PYTHONPATH="${script_dir}${PYTHONPATH:+:${PYTHONPATH}}"
export CASE9_ACTIVE_PROFILE="${profile}"
export CASE9_MODEL_PROFILES="${registry}"
export MINDSPORE_CHAT_HOST="${host}"
export MINDSPORE_CHAT_PORT="${port}"
# Keep profile caches below the deployment root.  The provider validates the
# relative cache path from the registry and uses this root for both local
# artifacts and MindNLP's optional cache_dir argument.
export CASE9_MODEL_ROOT="${CASE9_MODEL_ROOT:-${script_dir}}"
# A generation watchdog must terminate this worker if a MindSpore call blocks
# beyond its deadline.  modelctl can then roll back or leave the candidate
# chain fail-closed; formal ACL services are unaffected by this flag.
export CASE9_PROCESS_WATCHDOG="${CASE9_PROCESS_WATCHDOG:-1}"
export CASE9_WORKER_MAIN="1"

# Keep the direct launcher subject to the same admission boundary as
# case9-modelctl.  A caller must explicitly acknowledge the shared dirty-base
# environment; blocked/not-run profiles can never be started by bypassing the
# controller.
if ! "${python_bin}" - "${registry}" "${profile}" <<'PY'
import os
import sys
from case9_model_profiles import load_profiles

profiles = load_profiles(sys.argv[1])
profile = profiles.get(sys.argv[2])
if profile is None:
    raise SystemExit("unknown profile: %s" % sys.argv[2])
status = str(profile.status).strip().lower()
if status in {"blocked", "not-run"}:
    raise SystemExit("profile is %s: %s" % (status, profile.id))
if status == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
    raise SystemExit(
        "profile is experimental_dirty_base; set CASE9_ALLOW_EXPERIMENTAL=1 "
        "for an explicit candidate start"
    )
if status not in {"admitted", "experimental_dirty_base"}:
    raise SystemExit("profile status is not activatable: %s" % status)
PY
then
  echo "Profile admission gate failed; refusing to start profile=${profile}" >&2
  exit 2
fi

# A read-only hardware/environment gate runs before any model import.  It
# proves that the selected profile matches the visible 310B chip and that
# MindSpore selects Ascend.  Existing forbidden packages in the shared base
# are reported as dirty-base evidence; they are not removed or used by this
# adapter.
environment_checker="${script_dir}/scripts/check_mindspore_chat_environment.py"
if [[ ! -f "${environment_checker}" ]]; then
  echo "Environment checker not found: ${environment_checker}" >&2
  exit 2
fi
environment_report_dir="${script_dir}/run/mindspore-chat/environment-preflight"
mkdir -p "${environment_report_dir}"
environment_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
environment_report="${environment_report_dir}/${profile}-${environment_stamp}.json"
if ! "${python_bin}" "${environment_checker}" \
    --profile "${profile}" \
    --registry "${registry:-${script_dir}/configs/chat_model_profiles.json}" \
    --root "${script_dir}" --json >"${environment_report}"; then
  echo "MindSpore/Ascend environment preflight failed; refusing to start profile=${profile}" >&2
  echo "Environment report: ${environment_report}" >&2
  exit 2
fi
echo "Verified MindSpore/Ascend environment: ${environment_report}" >&2

# Verify every declared model file before importing MindSpore or loading a
# worker.  This is intentionally a hard gate: a missing or changed artifact
# must never result in a service that appears healthy with an unverified model.
artifact_verifier="${script_dir}/scripts/verify_mindspore_profile_artifacts.py"
if [[ ! -f "${artifact_verifier}" ]]; then
  echo "Artifact verifier not found: ${artifact_verifier}" >&2
  exit 2
fi
verification_dir="${script_dir}/run/mindspore-chat/artifact-verification"
mkdir -p "${verification_dir}"
verification_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
verification_report="${verification_dir}/${profile}-${verification_stamp}.json"
if ! "${python_bin}" "${artifact_verifier}" \
    --profile "${profile}" \
    --root "${CASE9_MODEL_ROOT}" \
    --output "${verification_report}"; then
  echo "Model artifact verification failed; refusing to start profile=${profile}" >&2
  echo "Verification report: ${verification_report}" >&2
  exit 2
fi
echo "Verified model artifacts: ${verification_report}" >&2

"${python_bin}" - <<'PY'
import importlib
import importlib.metadata
import os
import site
import sys

print("mindspore-chat python:", sys.executable)
print("python_user_site:", site.getusersitepackages())
print("user_site_enabled:", bool(site.ENABLE_USER_SITE))
print("PYTHONNOUSERSITE:", os.environ.get("PYTHONNOUSERSITE", "<unset>"))
for name in ("mindspore", "mindnlp"):
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        raise SystemExit("required %s import failed: %s" % (name, exc))
    print("%s_module:" % name, getattr(module, "__file__", "<built-in>"))

for distribution in ("mindspore", "mindnlp", "pytest"):
    try:
        print("%s=%s" % (distribution, importlib.metadata.version(distribution)))
    except importlib.metadata.PackageNotFoundError:
        print("%s=missing" % distribution)
PY

echo "WARNING: MindSpore chat uses the shared dirty-base environment (${conda_env})." >&2
echo "WARNING: Existing torch/torch_npu/torchaudio packages are retained; the adapter does not import them." >&2
echo "Starting profile=${profile} on ${host}:${port}" >&2
export CASE9_LAUNCHER_VERIFIED="1"
exec "${python_bin}" mindspore_chat_service.py --profile "${profile}" --host "${host}" --port "${port}"
