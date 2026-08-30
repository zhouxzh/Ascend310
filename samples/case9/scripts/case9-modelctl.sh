#!/usr/bin/env bash
# Operate the single active MindSpore chat worker. Only PIDs started by this
# script are eligible for termination; formal ACL/gateway processes are never
# touched.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${script_dir}"
registry="${CASE9_MODEL_PROFILES:-${script_dir}/configs/chat_model_profiles.json}"
state_dir="${CASE9_MODELCTL_STATE_DIR:-${script_dir}/run/mindspore-chat}"
state_file="${CASE9_MODELCTL_STATE_FILE:-${state_dir}/active-model.json}"
pid_file="${state_dir}/worker.pid"
pgid_file="${state_dir}/worker.pgid"
journal_file="${CASE9_MODELCTL_STARTING_FILE:-${state_dir}/starting.json}"
log_dir="${CASE9_MODELCTL_LOG_DIR:-${state_dir}/logs}"
lock_file="${state_dir}/modelctl.lock"
health_port="${MINDSPORE_CHAT_PORT:-8090}"
conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
conda_env="${CASE9_MINDSPORE_CONDA_ENV:-base}"

die() { echo "case9-modelctl: $*" >&2; exit 2; }

[[ "${health_port}" =~ ^[0-9]+$ ]] && (( health_port >= 1 && health_port <= 65535 )) || \
  die "MINDSPORE_CHAT_PORT must be an integer between 1 and 65535"

# A worker may create multiprocessing children.  Every worker launched by this
# controller must therefore own an isolated process group so a switch can
# terminate the complete group without touching the controller or other
# services.  Refuse to launch when the platform cannot provide ``setsid``.
command -v setsid >/dev/null 2>&1 || die "setsid is required for worker process-group isolation"

# Resolve Python in this process. Non-interactive SSH shells do not load
# conda's shell hook, so an inherited `python` may be missing or wrong.  The
# board's verified MindSpore/MindNLP wheels live in the base user site; hiding
# that site selects an incompatible system copy.  Keep it visible by default
# and allow an operator to opt into the isolated mode explicitly.
case "${CASE9_PYTHONNOUSERSITE:-}" in
  1) export PYTHONNOUSERSITE=1 ;;
  0|"") unset PYTHONNOUSERSITE ;;
  *) die "CASE9_PYTHONNOUSERSITE must be 0 or 1" ;;
esac
export PYTHONPATH="${script_dir}${PYTHONPATH:+:${PYTHONPATH}}"
python_bin=""
resolve_python() {
  local explicit="${PYTHON_BIN:-}" candidate="" prefix="" candidate_real="" prefix_real=""
  local allow_external="${CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON:-0}"
  [[ -r "${conda_profile}" ]] || {
    if [[ "${allow_external}" == "1" && "${explicit}" == /* && -x "${explicit}" ]]; then
      candidate="${explicit}"
      "${candidate}" -c 'import case9_model_profiles' >/dev/null 2>&1 || die "Python cannot import case9_model_profiles: ${candidate}"
      python_bin="${candidate}"
      return 0
    fi
    die "Conda profile not found: ${conda_profile}; refusing system-Python fallback"
  }
  # The vendor conda hook is not nounset-clean on every board image.
  set +u
  # shellcheck disable=SC1090
  source "${conda_profile}"
  set -u
  command -v conda >/dev/null 2>&1 || die "conda command is unavailable after sourcing ${conda_profile}"
  conda activate "${conda_env}" >/dev/null 2>&1 || die "Conda environment not found: ${conda_env}"
  prefix="${CONDA_PREFIX:-}"
  [[ -n "${prefix}" && -d "${prefix}" ]] || die "Conda activation did not set CONDA_PREFIX"
  prefix_real="$(readlink -f "${prefix}" 2>/dev/null || printf '%s' "${prefix}")"
  if [[ -n "${explicit}" ]]; then
    if [[ "${explicit}" == */* ]]; then
      [[ -x "${explicit}" ]] || die "PYTHON_BIN is not executable: ${explicit}"
      candidate="${explicit}"
    else
      candidate="$(command -v "${explicit}" 2>/dev/null || true)"
      [[ -n "${candidate}" ]] || die "PYTHON_BIN was not found: ${explicit}"
    fi
  else
    candidate="$(command -v python 2>/dev/null || true)"
    [[ -n "${candidate}" ]] || die "activated conda environment has no python executable"
  fi
  candidate_real="$(readlink -f "${candidate}" 2>/dev/null || printf '%s' "${candidate}")"
  if [[ "${candidate_real}" != "${prefix_real}"/* ]]; then
    if [[ "${allow_external}" != "1" || "${explicit}" != /* ]]; then
      die "PYTHON_BIN is outside activated CONDA_PREFIX (${prefix_real}); set an explicit absolute path and CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON=1 only for an audited exception"
    fi
    echo "WARNING: using explicitly approved external Python ${candidate_real}" >&2
  fi
  "${candidate}" -c 'import case9_model_profiles' >/dev/null 2>&1 || die "Python cannot import case9_model_profiles: ${candidate}"
  python_bin="${candidate}"
}
resolve_python

mkdir -p "${state_dir}" "${log_dir}"

if command -v flock >/dev/null 2>&1; then
  exec 9>"${lock_file}"
  flock -n 9 || die "another model operation is in progress"
fi

profile_exists() {
  local requested="$1"
  "${python_bin}" - "${registry}" "${requested}" <<'PY'
import os
import sys
from case9_model_profiles import ProfileError, load_profiles
profiles = load_profiles(sys.argv[1])
try:
    profile = profiles.get(sys.argv[2])
except ProfileError as exc:
    # Keep the CLI contract stable for an unknown id while preserving other
    # registry/schema errors as hard failures with their original detail.
    if str(exc).startswith("unknown profile:"):
        raise SystemExit("profile is not present in registry: %s" % sys.argv[2])
    raise
if profile is None:
    raise SystemExit("profile is not present in registry: %s" % sys.argv[2])
if profile.status == "blocked":
    raise SystemExit("profile is blocked: %s" % profile.id)
if profile.status == "not-run":
    raise SystemExit("profile has not passed its load gate: %s" % profile.id)
if profile.status == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
    raise SystemExit(
        "profile is experimental_dirty_base; set CASE9_ALLOW_EXPERIMENTAL=1 "
        "for an explicit candidate switch"
    )
if profile.status not in {"admitted", "experimental_dirty_base"}:
    raise SystemExit("profile status is not activatable: %s" % profile.status)
PY
}

profile_list() {
  "${python_bin}" case9_model_profiles.py list --registry "${registry}" --state "${state_file}"
}

# The PID/PGID files are recovery pointers, not disposable caches.  Treat any
# filesystem object at either path as occupied and validate it before a
# mutating operation.  In particular, never follow a symlink or silently
# replace a pointer whose owner cannot be established.
tracking_sidecars_present=0
tracking_sidecar_status="absent"
tracking_sidecar_pid=""
tracking_sidecar_pgid=""
tracking_sidecar_error=""

tracking_path_present() {
  [[ -e "$1" || -L "$1" ]]
}

tracking_sidecar_temp_present() {
  # ``compgen`` returns a non-zero status when the glob has no matches; the
  # caller always invokes this in an ``if``/``||`` context under ``set -e``.
  compgen -G "$1.part.*" >/dev/null 2>&1
}

read_tracking_sidecar() {
  local path="$1" value
  [[ -L "${path}" ]] && return 2
  [[ -e "${path}" ]] || return 1
  [[ -f "${path}" ]] || return 2
  # Read through a no-follow descriptor and accept exactly one positive
  # decimal PID, with at most one Unix newline.  This rejects values such as
  # ``12 34`` and multi-line concatenation that ``tr -d whitespace`` would
  # accidentally turn into a different PID.
  if ! value="$("${python_bin}" - "${path}" <<'PY'
import os
import re
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except OSError:
    raise SystemExit(2)
try:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(2)
    data = os.read(descriptor, 257)
finally:
    os.close(descriptor)
if len(data) > 256 or re.fullmatch(rb"[1-9][0-9]*(?:\n|\r\n)?", data) is None:
    raise SystemExit(2)
sys.stdout.write(data.rstrip(b"\r\n").decode("ascii"))
PY
)"; then
    return 2
  fi
  printf '%s\n' "${value}"
}

inspect_tracking_sidecars() {
  local pid_present=0 pgid_present=0 value
  tracking_sidecars_present=0
  tracking_sidecar_status="absent"
  tracking_sidecar_pid=""
  tracking_sidecar_pgid=""
  tracking_sidecar_error=""

  if tracking_path_present "${pid_file}"; then
    pid_present=1
  fi
  if tracking_path_present "${pgid_file}"; then
    pgid_present=1
  fi
  if tracking_sidecar_temp_present "${pid_file}" || tracking_sidecar_temp_present "${pgid_file}"; then
    tracking_sidecars_present=1
    tracking_sidecar_status="partial"
    tracking_sidecar_error="temporary sidecar write remains"
    return 1
  fi
  if (( pid_present == 0 && pgid_present == 0 )); then
    return 0
  fi

  tracking_sidecars_present=1
  if (( pid_present != 0 )); then
    if ! value="$(read_tracking_sidecar "${pid_file}")"; then
      tracking_sidecar_status="invalid"
      tracking_sidecar_error="worker.pid is not a regular file containing one positive integer"
      return 1
    fi
    tracking_sidecar_pid="${value}"
  fi
  if (( pgid_present != 0 )); then
    if ! value="$(read_tracking_sidecar "${pgid_file}")"; then
      tracking_sidecar_status="invalid"
      tracking_sidecar_error="worker.pgid is not a regular file containing one positive integer"
      tracking_sidecar_pid=""
      return 1
    fi
    tracking_sidecar_pgid="${value}"
  fi
  if (( pid_present != pgid_present )); then
    tracking_sidecar_status="partial"
    tracking_sidecar_error="worker.pid and worker.pgid must be present together"
    return 1
  fi
  if [[ "${tracking_sidecar_pid}" != "${tracking_sidecar_pgid}" ]]; then
    tracking_sidecar_status="mismatch"
    tracking_sidecar_error="worker.pid and worker.pgid differ"
    return 1
  fi
  tracking_sidecar_status="complete"
  return 0
}

clear_consistent_sidecars() {
  local expected_pid="${1:-}" expected_pgid="${2:-}"
  local inspect_status=0
  inspect_tracking_sidecars || inspect_status=$?
  if (( inspect_status != 0 )); then
    echo "case9-modelctl: refusing to clear worker sidecars (${tracking_sidecar_error})" >&2
    return 1
  fi
  if (( tracking_sidecars_present == 0 )); then
    return 0
  fi
  if [[ -n "${expected_pid}" && "${tracking_sidecar_pid}" != "${expected_pid}" ]] || \
     [[ -n "${expected_pgid}" && "${tracking_sidecar_pgid}" != "${expected_pgid}" ]]; then
    echo "case9-modelctl: worker sidecars do not match the stopped worker; retaining them" >&2
    return 1
  fi
  if ! rm -f -- "${pid_file}" "${pgid_file}"; then
    echo "case9-modelctl: could not clear worker sidecars" >&2
    return 1
  fi
  return 0
}

validate_state_path() {
  if tracking_path_present "${state_file}" && { [[ -L "${state_file}" ]] || [[ ! -f "${state_file}" ]]; }; then
    echo "case9-modelctl: active state path is not a regular file; refusing mutation" >&2
    return 1
  fi
  return 0
}

preflight_tracking() {
  local inspect_status=0 pid_status=0 group_status=0 metadata_present=0
  validate_state_path || return 1
  inspect_tracking_sidecars || inspect_status=$?
  if (( inspect_status != 0 )); then
    # A journal/state record can safely anchor a single sidecar written during
    # the documented mirror sequence.  Without either trusted record there is
    # no profile identity, so every partial pointer remains a hard stop.
    if tracking_path_present "${state_file}" || tracking_path_present "${journal_file}"; then
      metadata_present=1
    fi
    if [[ "${tracking_sidecar_status}" != "partial" || "${metadata_present}" -eq 0 ]]; then
      echo "case9-modelctl: unreconciled worker sidecars (${tracking_sidecar_error}); refusing mutation" >&2
      return 1
    fi
  fi
  if (( tracking_sidecars_present == 0 )); then
    return 0
  fi

  # A state file or starting journal supplies the profile identity used by
  # stop_pid().  Their cross-checks happen in load_state_values() and
  # recover_starting_journal(); do not infer identity from arbitrary `ps`
  # text when both metadata records are absent.
  if tracking_path_present "${state_file}" || tracking_path_present "${journal_file}"; then
    return 0
  fi

  # No trusted profile metadata remains.  A live PID must be retained rather
  # than guessed at; only a demonstrably dead PID and empty matching process
  # group can be safely garbage-collected.
  if worker_pid_is_live "${tracking_sidecar_pid}"; then
    echo "case9-modelctl: live worker sidecars have no state/journal identity; refusing to signal" >&2
    return 1
  else
    pid_status=$?
  fi
  if (( pid_status != 1 )); then
    echo "case9-modelctl: could not inspect orphan worker PID; retaining sidecars" >&2
    return 1
  fi
  if worker_group_alive "${tracking_sidecar_pgid}"; then
    echo "case9-modelctl: orphan worker PGID still has live processes; retaining sidecars" >&2
    return 1
  else
    group_status=$?
  fi
  if (( group_status != 1 )); then
    echo "case9-modelctl: could not prove orphan worker group is empty; retaining sidecars" >&2
    return 1
  fi
  clear_consistent_sidecars "${tracking_sidecar_pid}" "${tracking_sidecar_pgid}" || return 1
  # The pair was proven stale and removed.  Refresh the in-memory snapshot so
  # launch_profile() can proceed without mistaking the just-recovered files
  # for a new tracked worker.
  inspect_tracking_sidecars || return 1
  echo "case9-modelctl: cleared sidecars for an exited worker with an empty group" >&2
  return 0
}

read_state() {
  local payload
  payload="$("${python_bin}" case9_model_profiles.py status --registry "${registry}" --state "${state_file}")"
  # Status must expose an interrupted tracking write instead of presenting an
  # apparently idle controller.  This inspection is read-only; mutations use
  # preflight_tracking() below and fail closed on the same condition.
  inspect_tracking_sidecars || true
  local sidecars_present=false
  (( tracking_sidecars_present != 0 )) && sidecars_present=true
  if [[ ! -f "${state_file}" ]]; then
    local journal_present=false
    # Treat any existing filesystem object (including a directory or dangling
    # symlink) as an unreconciled journal.  A directory at this path must not
    # be mistaken for an absent journal and allow a new worker to launch.
    [[ -e "${journal_file}" || -L "${journal_file}" ]] && journal_present=true
    "${python_bin}" - "${payload}" "${journal_present}" "${sidecars_present}" \
      "${tracking_sidecar_status}" "${tracking_sidecar_error}" <<'PY'
import json
import sys
body = json.loads(sys.argv[1])
body["runtime"] = {
    "state_status": "none",
    "worker_pid": None,
    "worker_pgid": None,
    "pid_alive": False,
    "identity_match": False,
    "group_isolated": False,
    "group_alive": False,
    "health_ok": False,
    "stale": sys.argv[3] == "true",
    "starting_journal_present": sys.argv[2] == "true",
    "sidecars_present": sys.argv[3] == "true",
    "sidecar_status": sys.argv[4],
    "orphan_recovery_required": sys.argv[3] == "true",
    "tracking_error": sys.argv[5] or None,
}
print(json.dumps(body, ensure_ascii=False, sort_keys=True))
PY
    return 0
  fi
  local journal_present=false
  [[ -e "${journal_file}" || -L "${journal_file}" ]] && journal_present=true

  # A valid state file is required before probing.  This helper fails closed
  # on malformed/incomplete state and never guesses which process is ours.
  load_state_values
  local pid_alive=false identity_match=false group_isolated=false group_alive=false health_ok=false
  if worker_pid_is_live "${previous_pid}"; then
    pid_alive=true
    if worker_group_isolated "${previous_pid}" "${previous_pgid}"; then
      group_isolated=true
      if worker_group_alive "${previous_pgid}"; then
        group_alive=true
      fi
    fi
    if pid_matches_worker "${previous_pid}" "${previous_profile}"; then
      identity_match=true
      if health_ready "${previous_profile}" "${previous_pid}"; then
        health_ok=true
      fi
    fi
  fi
  "${python_bin}" - "${payload}" "${previous_status}" "${previous_pid}" "${previous_pgid}" \
    "${pid_alive}" "${identity_match}" "${group_isolated}" "${group_alive}" "${health_ok}" \
    "${journal_present}" "${sidecars_present}" "${tracking_sidecar_status}" \
    "${tracking_sidecar_error}" <<'PY'
import json
import sys
body = json.loads(sys.argv[1])
status = sys.argv[2]
raw_pid = sys.argv[3]
pid = int(raw_pid) if raw_pid.isdigit() else None
raw_pgid = sys.argv[4]
pgid = int(raw_pgid) if raw_pgid.isdigit() else None
pid_alive = sys.argv[5] == "true"
identity_match = sys.argv[6] == "true"
group_isolated = sys.argv[7] == "true"
group_alive = sys.argv[8] == "true"
health_ok = sys.argv[9] == "true"
journal_present = sys.argv[10] == "true"
sidecars_present = sys.argv[11] == "true"
sidecar_status = sys.argv[12]
tracking_error = sys.argv[13] or None
body["runtime"] = {
    "state_status": status,
    "worker_pid": pid,
    "worker_pgid": pgid,
    "pid_alive": pid_alive,
    "identity_match": identity_match,
    "group_isolated": group_isolated,
    "group_alive": group_alive,
    "health_ok": health_ok,
    "starting_journal_present": journal_present,
    "sidecars_present": sidecars_present,
    "sidecar_status": sidecar_status,
    "orphan_recovery_required": sidecars_present and sidecar_status != "complete",
    "tracking_error": tracking_error,
    # A failed/switching state is deliberately stale until an operator has
    # verified and cleaned the tracked worker.  Keeping this bit true prevents
    # the UI/status reader from presenting an abandoned candidate as healthy.
    "stale": (sidecars_present and sidecar_status != "complete") or status == "failed" or (
        status in {"starting", "running", "rollback", "switching"}
        and not (pid_alive and identity_match and group_isolated and group_alive and health_ok)
    ),
}
print(json.dumps(body, ensure_ascii=False, sort_keys=True))
PY
}

write_state() {
  local profile="$1" pid="${2:-}" status="$3" cache_cleared="${4:-true}"
  [[ "${CASE9_MODELCTL_TEST_FAIL_STATE_WRITE:-0}" == "1" ]] && return 1
  "${python_bin}" - "${state_file}" "${registry}" "${profile}" "${pid}" "${status}" "${cache_cleared}" <<'PY'
import sys
from case9_model_profiles import load_profiles, write_active_state
profile_id = sys.argv[3]
worker_pid = int(sys.argv[4]) if sys.argv[4] else None
cache_cleared = sys.argv[6].lower() == "true"
write_active_state(
    sys.argv[1], profile_id, status=sys.argv[5], worker_pid=worker_pid,
    cache_cleared=cache_cleared, registry=load_profiles(sys.argv[2]),
)
PY
}

# Record a candidate before waiting for HTTP readiness.  ``active-model.json``
# is also written as ``starting`` below, while this journal carries the PGID
# needed to stop the candidate if the controller exits in that window.
write_starting_journal() {
  local profile="$1" pid="$2" pgid="$3" log_path="$4"
  [[ "${CASE9_MODELCTL_TEST_FAIL_JOURNAL_WRITE:-0}" == "1" ]] && return 1
  "${python_bin}" - "${journal_file}" "${registry}" "${profile}" "${pid}" "${pgid}" \
    "${health_port}" "${log_path}" "${log_dir}" <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

from case9_model_profiles import load_profiles

journal_path = Path(sys.argv[1])
registry_path = sys.argv[2]
profile_id = sys.argv[3]
pid = int(sys.argv[4])
pgid = int(sys.argv[5])
health_port = int(sys.argv[6])
log_path = Path(sys.argv[7])
log_root = Path(sys.argv[8]).resolve()
if pid < 1 or pgid < 1 or pid != pgid:
    raise SystemExit("starting journal requires PID == PGID")
if health_port < 1 or health_port > 65535:
    raise SystemExit("starting journal health port is invalid")
load_profiles(registry_path).get(profile_id)
if journal_path.exists() or journal_path.is_symlink():
    raise SystemExit("starting journal target already exists")
resolved_log = log_path.resolve()
try:
    resolved_log.relative_to(log_root)
except ValueError as exc:
    raise SystemExit("starting journal log path escapes log directory") from exc
if not profile_id or any(char in profile_id for char in "\n\r"):
    raise SystemExit("starting journal profile_id is invalid")
if any(char in str(log_path) for char in "\n\r"):
    raise SystemExit("starting journal log_path is invalid")
payload = {
    "schema_version": 1,
    "status": "starting",
    "profile_id": profile_id,
    "worker_pid": pid,
    "worker_pgid": pgid,
    "health_port": health_port,
    "log_path": str(log_path),
    "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
journal_path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(
    prefix=".%s." % journal_path.name,
    suffix=".part",
    dir=str(journal_path.parent),
)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_name, journal_path)
except Exception:
    try:
        os.unlink(temporary_name)
    except OSError:
        pass
    raise
PY
}

clear_starting_journal() {
  if [[ -L "${journal_file}" ]]; then
    echo "case9-modelctl: starting journal is a symlink; refusing to remove it" >&2
    return 1
  fi
  rm -f -- "${journal_file}"
}

read_starting_journal() {
  # Return 1 when absent, 2 when malformed/unsafe, and 0 with globals set.
  journal_profile=""
  journal_pid=""
  journal_pgid=""
  journal_port=""
  journal_log=""
  if [[ -L "${journal_file}" ]]; then
    echo "case9-modelctl: starting journal is a symlink; refusing to use it" >&2
    return 2
  fi
  if [[ ! -e "${journal_file}" && ! -L "${journal_file}" ]]; then
    return 1
  fi
  [[ -f "${journal_file}" ]] || {
    echo "case9-modelctl: starting journal path is not a regular file" >&2
    return 2
  }
  local values
  if ! values="$("${python_bin}" - "${journal_file}" "${registry}" "${health_port}" "${log_dir}" <<'PY'
import json
from pathlib import Path
import sys

from case9_model_profiles import load_profiles

path = Path(sys.argv[1])
try:
    registry = load_profiles(sys.argv[2])
    expected_port = int(sys.argv[3])
    log_root = Path(sys.argv[4]).resolve()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate starting journal key: %s" % key)
            result[key] = value
        return result
    document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(document, dict):
        raise ValueError("journal must be an object")
    required = {
        "schema_version", "status", "profile_id", "worker_pid", "worker_pgid",
        "health_port", "log_path", "created_at",
    }
    if set(document) != required or document["schema_version"] != 1 or document["status"] != "starting":
        raise ValueError("journal schema or status is invalid")
    profile_id = document["profile_id"]
    if not isinstance(profile_id, str) or not profile_id or any(char in profile_id for char in "\n\r"):
        raise ValueError("journal profile_id is invalid")
    registry.get(profile_id)
    pid = document["worker_pid"]
    pgid = document["worker_pgid"]
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        raise ValueError("journal worker_pid is invalid")
    if isinstance(pgid, bool) or not isinstance(pgid, int) or pgid < 1 or pgid != pid:
        raise ValueError("journal worker_pgid is invalid")
    if document["health_port"] != expected_port:
        raise ValueError("journal health port does not match controller")
    log_path = document["log_path"]
    created_at = document["created_at"]
    if not isinstance(log_path, str) or not log_path or any(char in log_path for char in "\n\r"):
        raise ValueError("journal log_path is invalid")
    if not isinstance(created_at, str) or not created_at or any(char in created_at for char in "\n\r"):
        raise ValueError("journal created_at is invalid")
    try:
        Path(log_path).resolve().relative_to(log_root)
    except ValueError as exc:
        raise ValueError("journal log_path escapes log directory") from exc
    print(profile_id)
    print(pid)
    print(pgid)
    print(document["health_port"])
    print(log_path)
except Exception as exc:
    raise SystemExit("invalid starting journal: %s" % exc)
PY
)"; then
    echo "case9-modelctl: starting journal validation failed" >&2
    return 2
  fi
  mapfile -t journal_values <<<"${values}"
  journal_profile="${journal_values[0]:-}"
  journal_pid="${journal_values[1]:-}"
  journal_pgid="${journal_values[2]:-}"
  journal_port="${journal_values[3]:-}"
  journal_log="${journal_values[4]:-}"
  [[ -n "${journal_profile}" && "${journal_pid}" =~ ^[1-9][0-9]*$ && \
    "${journal_pgid}" == "${journal_pid}" && "${journal_port}" == "${health_port}" ]] || {
    echo "case9-modelctl: starting journal values are invalid" >&2
    return 2
  }
  return 0
}

write_pid_file() {
  local pid="$1" pid_tmp="${pid_file}.part.$$"
  [[ "${CASE9_MODELCTL_TEST_FAIL_PID_WRITE:-0}" == "1" ]] && return 1
  if tracking_path_present "${pid_file}" && { [[ -L "${pid_file}" ]] || [[ ! -f "${pid_file}" ]]; }; then
    return 1
  fi
  printf '%s\n' "${pid}" > "${pid_tmp}" || return 1
  mv -f -- "${pid_tmp}" "${pid_file}" || {
    rm -f -- "${pid_tmp}"
    return 1
  }
}

preserve_failed_worker() {
  local profile="$1" pid="$2"
  # State is the authoritative recovery pointer.  Write it first, with the
  # cache explicitly marked uncleared, then mirror the PID sidecar.  If the
  # sidecar write fails the state still lets a later `stop` attempt cleanup.
  if ! write_state "${profile}" "${pid}" "failed" "false"; then
    echo "case9-modelctl: could not preserve failed worker state profile=${profile} pid=${pid}" >&2
    return 1
  fi
  if ! write_pid_file "${pid}"; then
    echo "case9-modelctl: failed worker state saved but PID sidecar update failed" >&2
    return 1
  fi
  local pgid
  pgid="$(worker_group_id "${pid}" 2>/dev/null || true)"
  # A failed launch may still be observed in the controller's transient
  # process group.  Never persist that value: it would make later recovery
  # metadata look authoritative even though the isolation contract was not
  # established.  The PID/state pointer remains for a fail-closed diagnosis;
  # a later status/stop invocation derives a PGID read-only and rechecks it.
  if [[ -n "${pgid}" ]] && worker_group_isolated "${pid}" "${pgid}"; then
    if ! write_pgid_file "${pid}" "${pgid}"; then
      echo "case9-modelctl: failed worker state saved but PGID sidecar update failed" >&2
      return 1
    fi
  else
    # Do not erase an existing PGID pointer merely because isolation could not
    # be proven.  A partial/invalid pointer intentionally blocks later launch
    # until an operator has inspected it.
    if tracking_path_present "${pgid_file}"; then
      echo "case9-modelctl: retaining existing PGID sidecar because isolation was not proven" >&2
      return 1
    fi
  fi
  echo "case9-modelctl: preserved failed worker pointer profile=${profile} pid=${pid}" >&2
  return 0
}

clear_state() {
  "${python_bin}" - "${state_file}" <<'PY'
import sys
from case9_model_profiles import clear_active_state
clear_active_state(sys.argv[1])
PY
}

load_state_values() {
  previous_profile=""
  previous_pid=""
  previous_pgid=""
  previous_status=""
  state_present=0
  [[ -f "${state_file}" ]] || return 0
  state_present=1
  local values
  if ! values="$("${python_bin}" - "${state_file}" "${registry}" <<'PY'
import sys
from case9_model_profiles import load_profiles, read_active_state
value = read_active_state(sys.argv[1], registry=load_profiles(sys.argv[2]))
if value is not None:
    print(value.profile_id)
    print(value.worker_pid or "")
    print(value.status)
PY
)"; then
    die "active state is invalid; refusing to mutate it"
  fi
  mapfile -t state_values <<<"${values}"
  previous_profile="${state_values[0]:-}"
  previous_pid="${state_values[1]:-}"
  previous_status="${state_values[2]:-}"
  if [[ -z "${previous_profile}" || -z "${previous_status}" ]]; then
    die "active state is incomplete; refusing to mutate it"
  fi
  # A state without a PID cannot be safely recovered: clearing it could strand
  # an unknown worker. Keep the file for operator diagnosis.
  if [[ -z "${previous_pid}" ]]; then
    die "active state has no worker PID; refusing to mutate it"
  fi
  if tracking_path_present "${pgid_file}"; then
    if ! previous_pgid="$(read_tracking_sidecar "${pgid_file}")"; then
      die "worker PGID sidecar is invalid; refusing to mutate it"
    fi
  else
    # Older state files predate the PGID sidecar.  Derive it read-only so a
    # legacy worker can still be stopped when it is demonstrably isolated;
    # stop_pid will fail closed if the observed session is not self-owned.
    previous_pgid="$(worker_group_id "${previous_pid}" 2>/dev/null || true)"
  fi
}

validate_state_sidecar_consistency() {
  local inspect_status=0
  (( state_present != 0 )) || return 0
  inspect_tracking_sidecars || inspect_status=$?
  if (( inspect_status != 0 )) && [[ "${tracking_sidecar_status}" != "partial" ]]; then
    echo "case9-modelctl: active state has unreconciled worker sidecars (${tracking_sidecar_error})" >&2
    return 1
  fi
  # Older active states do not carry sidecars.  Keep their read-only PGID
  # derivation path, but when sidecars do exist they must be a complete,
  # setsid-owned mirror of the same worker rather than a pointer to a newer
  # or unrelated process.
  if (( tracking_sidecars_present == 0 )); then
    return 0
  fi
  if [[ -n "${tracking_sidecar_pid}" && "${tracking_sidecar_pid}" != "${previous_pid}" ]] || \
     [[ -n "${tracking_sidecar_pgid}" && "${tracking_sidecar_pgid}" != "${previous_pid}" ]]; then
    echo "case9-modelctl: active state and worker sidecars disagree; refusing mutation" >&2
    return 1
  fi
  [[ "${tracking_sidecar_status}" == "complete" ]] && previous_pgid="${tracking_sidecar_pgid}"
  return 0
}

validate_journal_sidecar_consistency() {
  local inspect_status=0
  inspect_tracking_sidecars || inspect_status=$?
  if (( inspect_status != 0 )) && [[ "${tracking_sidecar_status}" != "partial" ]]; then
    echo "case9-modelctl: starting journal has unreconciled worker sidecars (${tracking_sidecar_error})" >&2
    return 1
  fi
  # A journal is written before its mirror files, so no sidecars is a valid
  # crash boundary.  Once either sidecar exists both must match the journal.
  if (( tracking_sidecars_present == 0 )); then
    return 0
  fi
  if [[ -n "${tracking_sidecar_pid}" && "${tracking_sidecar_pid}" != "${journal_pid}" ]] || \
     [[ -n "${tracking_sidecar_pgid}" && "${tracking_sidecar_pgid}" != "${journal_pgid}" ]]; then
    echo "case9-modelctl: starting journal and worker sidecars disagree; refusing mutation" >&2
    return 1
  fi
  return 0
}

validate_state_journal_consistency() {
  (( state_present != 0 )) || return 0
  if [[ "${previous_profile}" != "${journal_profile}" || \
        "${previous_pid}" != "${journal_pid}" || \
        "${previous_pgid}" != "${journal_pgid}" ]]; then
    echo "case9-modelctl: active state and starting journal disagree; refusing mutation" >&2
    return 1
  fi
  return 0
}

pid_matches_worker() {
  local pid="$1" expected_profile="$2"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  local args
  args="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ "${args}" == *"mindspore_chat_service.py"* ]] || return 1
  [[ "${args}" == *"--profile ${expected_profile}"* ]] || return 1
  [[ "${args}" == *"--port ${health_port}"* ]]
}

worker_group_id() {
  local pid="$1" value
  value="$(ps -p "${pid}" -o pgid= 2>/dev/null | awk 'NR==1 {gsub(/[[:space:]]/, ""); print; exit}' || true)"
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "${value}"
}

worker_session_id() {
  local pid="$1" value
  value="$(ps -p "${pid}" -o sid= 2>/dev/null | awk 'NR==1 {gsub(/[[:space:]]/, ""); print; exit}' || true)"
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "${value}"
}

worker_group_isolated() {
  local pid="$1" pgid="$2" actual_pgid actual_sid
  [[ "${pid}" =~ ^[1-9][0-9]*$ && "${pgid}" =~ ^[1-9][0-9]*$ ]] || return 1
  actual_pgid="$(worker_group_id "${pid}")" || return 1
  actual_sid="$(worker_session_id "${pid}")" || return 1
  # setsid makes the worker the session and process-group leader.  Requiring
  # both IDs to equal the tracked PID prevents a stale/reused PGID from being
  # signalled accidentally.
  [[ "${actual_pgid}" == "${pgid}" && "${actual_pgid}" == "${pid}" && "${actual_sid}" == "${pid}" ]]
}

wait_for_isolated_group() {
  local pid="$1" attempts="${CASE9_MODELCTL_GROUP_WAIT_ATTEMPTS:-200}"
  local delay="${CASE9_MODELCTL_GROUP_WAIT_DELAY_SECONDS:-0.01}"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "${attempts}" =~ ^[1-9][0-9]*$ ]] || return 1
  # The background setsid command can be observed briefly before it has
  # exec'd the launcher.  Never persist that transient parent PGID: a stale
  # sidecar would make a later switch signal the wrong process group.
  for ((attempt=0; attempt<attempts; attempt+=1)); do
    local pgid sid
    pgid="$(worker_group_id "${pid}" 2>/dev/null || true)"
    sid="$(worker_session_id "${pid}" 2>/dev/null || true)"
    if [[ "${pgid}" == "${pid}" && "${sid}" == "${pid}" ]]; then
      printf '%s\n' "${pgid}"
      return 0
    fi
    # A process that has already exited cannot become isolated later.  Avoid
    # extending the wait and leave the caller to retain a failed pointer.
    worker_pid_is_live "${pid}" || return 1
    sleep "${delay}"
  done
  return 1
}

worker_group_alive() {
  local pgid="$1" members live_count
  [[ "${pgid}" =~ ^[1-9][0-9]*$ ]] || return 2
  # Return 0 when at least one non-zombie process remains, 1 when the group is
  # empty, and 2 when the process table cannot be read (fail closed).
  members="$(ps -eo pid=,pgid=,stat= 2>/dev/null)" || return 2
  live_count="$(printf '%s\n' "${members}" | awk -v target="${pgid}" '$2 == target && $3 !~ /^Z/ { count++ } END { print count + 0 }')" || return 2
  [[ "${live_count}" =~ ^[0-9]+$ ]] || return 2
  (( live_count > 0 )) && return 0
  return 1
}

worker_group_owned() {
  local pgid="$1" rows summary found bad
  [[ "${pgid}" =~ ^[1-9][0-9]*$ ]] || return 2
  rows="$(ps -eo pid=,pgid=,sid=,stat= 2>/dev/null)" || return 2
  # Descendants keep the leader's PGID/SID after the leader exits.  Require
  # every live member to retain both IDs before signalling a group whose
  # leader is no longer available for a fresh command-line check.
  summary="$(printf '%s\n' "${rows}" | awk -v target="${pgid}" '
    $2 == target && $4 !~ /^Z/ { found=1; if ($3 != target) { bad=1 } }
    END { print (found ? 1 : 0), (bad ? 1 : 0) }
  ')" || return 2
  read -r found bad <<<"${summary}"
  [[ "${found:-0}" == "1" ]] || return 1
  [[ "${bad:-1}" == "0" ]] || return 2
  return 0
}

worker_pid_is_live() {
  local pid="$1" stat
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  if ! stat="$(ps -p "${pid}" -o stat= 2>/dev/null)"; then
    return 2
  fi
  stat="$(printf '%s\n' "${stat}" | awk 'NR==1 {gsub(/[[:space:]]/, ""); print; exit}')"
  [[ -n "${stat}" ]] || return 2
  # ``kill -0`` succeeds for zombies; they no longer execute and must not
  # block group cleanup or trigger a command-line identity mismatch.
  [[ "${stat}" != Z* ]]
}

write_pgid_file() {
  local pid="$1" pgid="$2" pgid_tmp="${pgid_file}.part.$$"
  [[ "${CASE9_MODELCTL_TEST_FAIL_PGID_WRITE:-0}" == "1" ]] && return 1
  [[ "${pid}" =~ ^[1-9][0-9]*$ && "${pgid}" =~ ^[1-9][0-9]*$ && "${pid}" == "${pgid}" ]] || return 1
  if tracking_path_present "${pgid_file}" && { [[ -L "${pgid_file}" ]] || [[ ! -f "${pgid_file}" ]]; }; then
    return 1
  fi
  printf '%s\n' "${pgid}" > "${pgid_tmp}" || return 1
  mv -f -- "${pgid_tmp}" "${pgid_file}" || {
    rm -f -- "${pgid_tmp}"
    return 1
  }
}

stop_pid() {
  local pid="$1" profile="$2" requested_pgid="${3:-}" stop_seconds="${CASE9_MODELCTL_STOP_SECONDS:-30}"
  [[ "${stop_seconds}" =~ ^[0-9]+$ ]] || {
    echo "case9-modelctl: invalid CASE9_MODELCTL_STOP_SECONDS=${stop_seconds}" >&2
    return 1
  }
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || {
    echo "case9-modelctl: invalid worker PID" >&2
    return 1
  }
  local pgid="${requested_pgid}"
  if [[ -z "${pgid}" ]]; then
    pgid="$(worker_group_id "${pid}" 2>/dev/null || true)"
  fi
  [[ "${pgid}" =~ ^[1-9][0-9]*$ ]] || {
    echo "case9-modelctl: worker PGID is unavailable; refusing to stop PID ${pid}" >&2
    return 1
  }

  local group_status
  # A dead leader is only considered stopped when its isolated group is also
  # empty.  If descendants remain, retain state rather than guessing which
  # process owns an untracked group.
  if worker_pid_is_live "${pid}"; then
    :
  else
    local pid_status=$?
    if [[ "${pid_status}" -eq 2 ]]; then
      echo "case9-modelctl: could not inspect worker PID ${pid}" >&2
      return 1
    fi
    if worker_group_alive "${pgid}"; then
      echo "case9-modelctl: worker leader ${pid} is gone but PGID ${pgid} still has live children" >&2
      return 1
    else
      group_status=$?
      [[ "${group_status}" -eq 1 ]] && return 0
      echo "case9-modelctl: could not inspect worker PGID ${pgid}" >&2
      return 1
    fi
  fi

  # Always verify the leader command/profile before signalling anything.  The
  # isolation check proves this PID is both the session and process-group
  # leader created by setsid, so a group signal cannot reach the controller.
  if ! pid_matches_worker "${pid}" "${profile}"; then
    echo "case9-modelctl: refusing to stop PID ${pid}; command identity mismatch" >&2
    return 1
  fi
  if ! worker_group_isolated "${pid}" "${pgid}"; then
    echo "case9-modelctl: refusing to stop PID ${pid}; process group is not isolated" >&2
    return 1
  fi
  if worker_group_alive "${pgid}"; then
    :
  else
    group_status=$?
    [[ "${group_status}" -eq 1 ]] && return 0
    echo "case9-modelctl: could not inspect worker PGID ${pgid}" >&2
    return 1
  fi

  if kill -TERM -- "-${pgid}" 2>/dev/null; then
    :
  else
    if worker_group_alive "${pgid}"; then
      echo "case9-modelctl: TERM failed for worker PGID ${pgid}" >&2
      return 1
    else
      group_status=$?
      [[ "${group_status}" -eq 1 ]] && return 0
      echo "case9-modelctl: could not inspect worker PGID ${pgid} after TERM failure" >&2
      return 1
    fi
  fi

  for ((seconds=0; seconds<stop_seconds; seconds+=1)); do
    if worker_group_alive "${pgid}"; then
      sleep 1
      continue
    else
      group_status=$?
      [[ "${group_status}" -eq 1 ]] && return 0
      echo "case9-modelctl: could not inspect worker PGID ${pgid} while stopping" >&2
      return 1
    fi
  done

  # KILL is sent only after rechecking the leader or, if it exited while
  # handling TERM, every remaining member's PGID/SID ownership.
  if worker_pid_is_live "${pid}"; then
    if pid_matches_worker "${pid}" "${profile}" && worker_group_isolated "${pid}" "${pgid}"; then
      :
    else
      echo "case9-modelctl: worker PID ${pid} changed identity before KILL" >&2
      return 1
    fi
  else
    local pid_status=$?
    if [[ "${pid_status}" -eq 2 ]]; then
      echo "case9-modelctl: could not inspect worker PID ${pid} before KILL" >&2
      return 1
    fi
    if worker_group_owned "${pgid}"; then
      :
    else
      group_status=$?
      [[ "${group_status}" -eq 1 ]] && return 0
      echo "case9-modelctl: worker PGID ${pgid} ownership changed before KILL" >&2
      return 1
    fi
  fi

  if kill -KILL -- "-${pgid}" 2>/dev/null; then
    :
  else
    if worker_group_alive "${pgid}"; then
      echo "case9-modelctl: KILL failed for worker PGID ${pgid}" >&2
      return 1
    else
      group_status=$?
      [[ "${group_status}" -eq 1 ]] && return 0
      echo "case9-modelctl: could not inspect worker PGID ${pgid} after KILL failure" >&2
      return 1
    fi
  fi

  # KILL is asynchronous. Recheck the complete process group before callers
  # clear state or launch a replacement.
  for ((seconds=0; seconds<5; seconds+=1)); do
    if worker_group_alive "${pgid}"; then
      sleep 1
      continue
    else
      group_status=$?
      [[ "${group_status}" -eq 1 ]] && return 0
      echo "case9-modelctl: could not inspect worker PGID ${pgid} after KILL" >&2
      return 1
    fi
  done
  echo "case9-modelctl: worker PID ${pid} is still alive after KILL (PGID ${pgid} still has live processes)" >&2
  return 1
}

abort_launched_worker() {
  local profile="$1" pid="$2" pgid="${3:-}" reason="${4:-launch persistence failure}"
  echo "case9-modelctl: ${reason}; refusing to continue with an untracked worker" >&2
  # The launch path has just verified the command identity and isolated group.
  # Reuse the same guarded stop routine; never fall back to a broad signal.
  if stop_pid "${pid}" "${profile}" "${pgid}"; then
    clear_stopped_tracking "${pid}" "${pgid}" || {
      echo "case9-modelctl: worker stopped but recovery metadata could not be cleared" >&2
      return 2
    }
    echo "case9-modelctl: launch worker stopped after persistence failure" >&2
    return 1
  fi
  # If stopping cannot be proven safe, preserve whatever metadata can be
  # written.  This intentionally returns a distinct failure so callers do not
  # attempt a replacement/rollback over a possibly live candidate.
  preserve_failed_worker "${profile}" "${pid}" || true
  echo "case9-modelctl: launch worker could not be stopped; failed worker state retained" >&2
  return 2
}

health_ready() {
  local expected_profile="$1" expected_pid="$2"
  local expected_pgid
  expected_pgid="$(worker_group_id "${expected_pid}" 2>/dev/null || true)"
  [[ -n "${expected_pgid}" ]] || return 1
  # A healthy HTTP response is not enough to authorize the active-profile
  # short-circuit.  Reconfirm that the tracked PID owns an isolated session/
  # process group and that the complete group is still present.
  worker_group_isolated "${expected_pid}" "${expected_pgid}" || return 1
  worker_group_alive "${expected_pgid}" || return 1
  pid_matches_worker "${expected_pid}" "${expected_profile}" || return 1
  local response_file http_status response
  response_file="$(mktemp "${state_dir}/.health.XXXXXX")" || return 1
  http_status="$(curl --silent --show-error --max-time 5 \
    --output "${response_file}" --write-out '%{http_code}' \
    "http://127.0.0.1:${health_port}/health" 2>/dev/null || true)"
  response="$(<"${response_file}")"
  rm -f "${response_file}"
  [[ "${http_status}" == "200" ]] || return 1
  "${python_bin}" - "${response}" "${expected_profile}" "${expected_pid}" "${registry}" <<'PY'
import json
import re
import sys
from case9_model_profiles import load_profiles
try:
    body = json.loads(sys.argv[1])
    expected_profile = sys.argv[2]
    expected_pid = int(sys.argv[3])
    registry = load_profiles(sys.argv[4])
    expected_soc = registry.get(expected_profile).board_soc
except (ValueError, TypeError, json.JSONDecodeError, IndexError):
    raise SystemExit(1)
if not isinstance(body, dict):
    raise SystemExit(1)
observed_profile = body.get("profile") or body.get("profile_id")
worker_pid = body.get("worker_pid")
observed_npu = body.get("npu_model")
fingerprint = body.get("environment_fingerprint")
if (
    body.get("ready") is not True
    or body.get("healthy") is not True
    or body.get("cache_cleared") is not True
    or str(body.get("device_target", "")).lower() != "ascend"
    or observed_profile != expected_profile
    or isinstance(worker_pid, bool)
    or not isinstance(worker_pid, int)
    or worker_pid != expected_pid
    or observed_npu != expected_soc
    or not isinstance(fingerprint, str)
    or re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint) is None
):
    raise SystemExit(1)
raise SystemExit(0)
PY
}

recover_starting_journal() {
  # A controller can disappear after launch_profile() has returned a PID but
  # before wait_ready() publishes the running state.  Reconcile that journal
  # before any new switch/stop operation.  Every signal path still goes
  # through stop_pid(), which requires command identity and an isolated group.
  if ! preflight_tracking; then
    return 1
  fi
  local journal_status
  if read_starting_journal; then
    journal_status=0
  else
    journal_status=$?
  fi
  if [[ "${journal_status}" -eq 1 ]]; then
    return 0
  fi
  if [[ "${journal_status}" -ne 0 ]]; then
    echo "case9-modelctl: refusing operation while starting journal is invalid" >&2
    return 1
  fi
  if ! validate_journal_sidecar_consistency; then
    return 1
  fi
  if tracking_path_present "${state_file}"; then
    load_state_values
    if ! validate_state_sidecar_consistency || ! validate_state_journal_consistency; then
      return 1
    fi
  fi

  local pid_status=0 group_status
  # Keep the helper's nonzero exit status explicitly.  A bare ``if`` does
  # not preserve it reliably after its compound branch has completed.
  worker_pid_is_live "${journal_pid}" || pid_status=$?
  if [[ "${pid_status}" -eq 0 ]]; then
    if ! worker_group_isolated "${journal_pid}" "${journal_pgid}"; then
      echo "case9-modelctl: starting journal worker process group is not isolated" >&2
      return 1
    fi
    if ! pid_matches_worker "${journal_pid}" "${journal_profile}"; then
      echo "case9-modelctl: starting journal worker identity mismatch" >&2
      return 1
    fi
    # If the worker completed while the controller was down, make it the
    # authoritative running state instead of needlessly restarting it.
    if health_ready "${journal_profile}" "${journal_pid}"; then
      write_pgid_file "${journal_pid}" "${journal_pgid}" || return 1
      write_pid_file "${journal_pid}" || return 1
      write_state "${journal_profile}" "${journal_pid}" "running" "true" || return 1
      clear_starting_journal || return 1
      echo "case9-modelctl: recovered ready starting worker profile=${journal_profile} pid=${journal_pid}" >&2
      return 0
    fi
    # An explicitly requested switch/stop may safely clean a worker that is
    # still loading, because identity and process-group ownership were just
    # rechecked above.
    if stop_pid "${journal_pid}" "${journal_profile}" "${journal_pgid}"; then
      clear_starting_journal || return 1
      echo "case9-modelctl: stopped incomplete starting worker profile=${journal_profile} pid=${journal_pid}" >&2
      return 0
    fi
    echo "case9-modelctl: could not safely stop starting worker; journal retained" >&2
    return 1
  fi
  if [[ "${pid_status}" -eq 2 ]]; then
    echo "case9-modelctl: could not inspect starting worker PID ${journal_pid}; journal retained" >&2
    return 1
  fi
  # A dead leader with surviving descendants cannot be attributed safely
  # after PID reuse. Leave the journal for operator diagnosis rather than
  # signaling a group whose ownership is no longer anchored by its leader.
  if worker_group_alive "${journal_pgid}"; then
    echo "case9-modelctl: starting worker leader is gone but PGID ${journal_pgid} still has live descendants; journal retained" >&2
    return 1
  else
    group_status=$?
    if [[ "${group_status}" -eq 2 ]]; then
      echo "case9-modelctl: could not inspect starting worker PGID ${journal_pgid}; journal retained" >&2
      return 1
    fi
    clear_starting_journal || return 1
    echo "case9-modelctl: cleared starting journal for exited worker PID ${journal_pid}" >&2
    return 0
  fi
}

wait_ready() {
  local expected_profile="$1" expected_pid="$2" wait_seconds="${CASE9_MODELCTL_WAIT_SECONDS:-180}"
  [[ "${wait_seconds}" =~ ^[0-9]+$ ]] || return 1
  for ((attempt=0; attempt<=wait_seconds; attempt+=1)); do
    kill -0 "${expected_pid}" 2>/dev/null || return 1
    local expected_pgid
    expected_pgid="$(worker_group_id "${expected_pid}" 2>/dev/null || true)"
    worker_group_isolated "${expected_pid}" "${expected_pgid}" || return 1
    health_ready "${expected_profile}" "${expected_pid}" && return 0
    (( attempt < wait_seconds )) && sleep 1
  done
  return 1
}

launch_profile() {
  local profile="$1" stamp log_path pid pgid
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}"
  log_path="${log_dir}/${profile}-${stamp}.log"
  if ! preflight_tracking; then
    return 1
  fi
  if [[ -L "${journal_file}" || -e "${journal_file}" ]]; then
    echo "case9-modelctl: a starting journal already exists; recover it before launching another worker" >&2
    return 1
  fi
  if tracking_path_present "${state_file}" || (( tracking_sidecars_present != 0 )); then
    echo "case9-modelctl: tracked worker metadata remains; refusing to launch a replacement" >&2
    return 1
  fi
  # The new PID/PGID pair is written below only after this launch has been
  # issued; the starting journal and state are written before readiness is
  # checked.  No old sidecar is removed here: preflight must have proved that
  # the tracking directory is clean.
  CASE9_ACTIVE_PROFILE="${profile}" CASE9_MODEL_PROFILES="${registry}" \
  CASE9_PYTHON_BIN="${python_bin}" \
  CASE9_PROCESS_GROUP_READY="1" \
    MINDSPORE_CHAT_PORT="${health_port}" nohup setsid bash "${script_dir}/scripts/run_mindspore_chat_service.sh" \
    >"${log_path}" 2>&1 < /dev/null 9>&- &
  pid=$!
  # Wait for setsid to establish the worker-owned session before persisting
  # its PGID.  Reading immediately after ``&`` can capture the controller's
  # transient PGID and later cause a safe stop to be rejected or, worse, to
  # target an unrelated group.  A missing sidecar is safe: status/stop derive
  # the value read-only and still require the full isolation check.
  pgid="$(wait_for_isolated_group "${pid}" 2>/dev/null || true)"
  if [[ -z "${pgid}" ]]; then
    abort_launched_worker "${profile}" "${pid}" "" "worker PGID was not observed" || return $?
    return 1
  fi
  # Persist the journal and its mirrors as hard gates.  A write failure must
  # stop the known worker (or leave a failed pointer) before this function can
  # return; continuing would create an untracked process that owns 8090/NPU.
  if ! write_starting_journal "${profile}" "${pid}" "${pgid}" "${log_path}"; then
    abort_launched_worker "${profile}" "${pid}" "${pgid}" "could not write starting worker journal" || return $?
  fi
  if ! write_pgid_file "${pid}" "${pgid}"; then
    abort_launched_worker "${profile}" "${pid}" "${pgid}" "could not update worker PGID sidecar" || return $?
  fi
  if ! write_pid_file "${pid}"; then
    abort_launched_worker "${profile}" "${pid}" "${pgid}" "could not update worker PID sidecar" || return $?
  fi
  if ! write_state "${profile}" "${pid}" "starting" "false"; then
    abort_launched_worker "${profile}" "${pid}" "${pgid}" "could not write starting worker state" || return $?
  fi
  printf '%s\n' "${pid}:${log_path}"
}

clear_pid_file() {
  clear_consistent_sidecars
}

clear_pgid_file() {
  # Kept as a compatibility wrapper for callers outside this script.  The
  # pair must be cleared atomically from the controller's point of view.
  clear_consistent_sidecars
}

clear_stopped_tracking() {
  local expected_pid="${1:-}" expected_pgid="${2:-}"
  # Do not delete the active state until its corresponding sidecars have been
  # checked and removed.  If an I/O error leaves a pointer behind, the state
  # still explains which worker it belonged to and a future invocation fails
  # closed instead of launching a replacement.
  clear_consistent_sidecars "${expected_pid}" "${expected_pgid}" || return 1
  clear_state || return 1
  clear_starting_journal || return 1
}

rollback_profile() {
  local profile="$1" launch_info rollback_pid rollback_log rollback_pgid
  echo "attempting rollback to ${profile}" >&2
  if ! launch_info="$(launch_profile "${profile}")"; then
    echo "case9-modelctl: rollback launch failed" >&2
    return 1
  fi
  rollback_pid="${launch_info%%:*}"
  rollback_log="${launch_info#*:}"
  rollback_pgid="$(worker_group_id "${rollback_pid}" 2>/dev/null || true)"
  if wait_ready "${profile}" "${rollback_pid}"; then
    if write_state "${profile}" "${rollback_pid}" "rollback"; then
      clear_starting_journal || return 1
      return 0
    fi
    echo "case9-modelctl: rollback became ready but state write failed" >&2
  else
    echo "case9-modelctl: rollback worker did not become ready (log ${rollback_log})" >&2
  fi
  if ! stop_pid "${rollback_pid}" "${profile}" "${rollback_pgid}"; then
    # The rollback worker is now the only process that may need cleanup. Do
    # not leave active-model.json pointing at the old worker while worker.pid
    # points at this candidate: that split makes a later `stop` unsafe.
    preserve_failed_worker "${profile}" "${rollback_pid}" || true
    echo "case9-modelctl: rollback worker could not be stopped; failed worker state retained" >&2
    return 2
  fi
  clear_starting_journal || true
  return 1
}

command_name="${1:-status}"
case "${command_name}" in
  list)
    profile_list
    ;;
  status)
    read_state
    ;;
  stop)
    if ! recover_starting_journal; then
      die "starting worker recovery failed; journal retained"
    fi
    load_state_values
    if ! validate_state_sidecar_consistency; then
      die "active state and worker sidecars are inconsistent; refusing to mutate"
    fi
    if [[ -n "${previous_pid}" && -n "${previous_profile}" ]]; then
      if ! stop_pid "${previous_pid}" "${previous_profile}" "${previous_pgid}"; then
        die "worker did not stop; active state retained"
      fi
    fi
    clear_stopped_tracking "${previous_pid}" "${previous_pgid}" || \
      die "could not clear stopped worker tracking; state retained"
    echo "MindSpore chat worker stopped"
    ;;
  switch)
    requested="${2:-}"
    [[ -n "${requested}" ]] || die "usage: $0 switch PROFILE"
    # profile_exists emits the specific admission failure (blocked, missing,
    # dirty-base, or unsupported status). Keep that diagnostic intact and do
    # not replace it with a misleading generic "not present" message.
    if ! profile_exists "${requested}"; then
      die "profile validation failed: ${requested}"
    fi
    if ! recover_starting_journal; then
      die "starting worker recovery failed; journal retained"
    fi
    load_state_values
    if ! validate_state_sidecar_consistency; then
      die "active state and worker sidecars are inconsistent; refusing to mutate"
    fi
    if [[ "${previous_profile}" == "${requested}" && ( "${previous_status}" == "running" || "${previous_status}" == "rollback" ) ]] && health_ready "${requested}" "${previous_pid}"; then
      echo "profile already active: ${requested}"
      exit 0
    fi
    if [[ -n "${previous_pid}" || -n "${previous_profile}" ]]; then
      # Publish a transition generation before touching the old worker. The
      # UI drops its conversation immediately, while retaining the old PID so
      # a failed stop remains diagnosable and cannot be silently orphaned.
      if ! write_state "${previous_profile}" "${previous_pid}" "switching"; then
        die "could not record model switch; active state retained"
      fi
      if ! stop_pid "${previous_pid}" "${previous_profile}" "${previous_pgid}"; then
        die "previous worker did not stop; active state retained"
      fi
      if ! clear_stopped_tracking "${previous_pid}" "${previous_pgid}"; then
        die "previous worker stopped but tracking cleanup failed; refusing replacement"
      fi
    fi
    if ! launch_info="$(launch_profile "${requested}")"; then
      echo "case9-modelctl: could not launch profile ${requested}" >&2
      if [[ -n "${previous_profile}" ]]; then
        rollback_status=1
        rollback_profile "${previous_profile}" || rollback_status=$?
        if [[ "${rollback_status}" -eq 0 ]]; then
          die "switch launch failed; rolled back to ${previous_profile}"
        fi
        if [[ "${rollback_status}" -eq 2 ]]; then
          die "switch launch failed; rollback worker could not be stopped; active state retained"
        fi
      fi
      # launch_profile() already stopped and cleaned a candidate when a
      # persistence write failed.  Never clear an unrelated pointer here.
      if [[ -z "${previous_profile}" ]]; then
        clear_stopped_tracking || true
      fi
      die "switch launch failed and rollback was not ready; service is fail-closed"
    fi
    new_pid="${launch_info%%:*}"
    new_log="${launch_info#*:}"
    if wait_ready "${requested}" "${new_pid}"; then
      if write_state "${requested}" "${new_pid}" "running"; then
        clear_starting_journal || die "could not clear starting journal; state retained"
        echo "active profile: ${requested} (pid ${new_pid})"
        exit 0
      fi
      echo "case9-modelctl: state write failed after candidate became ready" >&2
    else
      echo "profile failed to become ready: ${requested} (log ${new_log})" >&2
    fi
    new_pgid="$(worker_group_id "${new_pid}" 2>/dev/null || true)"
    if ! stop_pid "${new_pid}" "${requested}" "${new_pgid}"; then
      preserve_failed_worker "${requested}" "${new_pid}" || true
      die "candidate worker could not be stopped; failed worker state retained"
    fi
    if ! clear_stopped_tracking "${new_pid}" "${new_pgid}"; then
      die "candidate stopped but tracking cleanup failed; service is fail-closed"
    fi
    if [[ -n "${previous_profile}" ]]; then
      rollback_status=1
      rollback_profile "${previous_profile}" || rollback_status=$?
      if [[ "${rollback_status}" -eq 0 ]]; then
        die "switch failed; rolled back to ${previous_profile}"
      fi
      if [[ "${rollback_status}" -eq 2 ]]; then
        die "switch failed; rollback worker could not be stopped; active state retained"
      fi
    fi
    clear_stopped_tracking || true
    die "switch failed and rollback was not ready; service is fail-closed"
    ;;
  *)
    die "usage: $0 {list|status|stop|switch PROFILE}"
    ;;
esac
