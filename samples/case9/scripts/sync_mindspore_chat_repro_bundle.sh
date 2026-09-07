#!/usr/bin/env bash
# Synchronize the explicit MindSpore chat reproducibility allowlist.
#
# This script deliberately does not recurse through a board home directory.
# The rsync invocation uses partial/append verification; no deletion flag is
# passed. Each remote file is copied to a local .part path, checked for size
# and SHA-256, then atomically renamed into the bundle.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$REPO_DIR/../.." && pwd)"
BUNDLE="$REPO_DIR/repro/mindspore-chat-20260829"
BOARD8_HOST="192.168.1.90"
BOARD20_HOST="192.168.1.95"
REMOTE_USER="HwHiAiUser"
REMOTE_ROOT="/home/HwHiAiUser/case9-mindspore-chat"
SOURCE_ALLOWLIST="${CASE9_MINDSPORE_SOURCE_ALLOWLIST:-$REPO_DIR/configs/mindspore_chat_source_allowlist.txt}"
REGISTRY="$REPO_DIR/configs/chat_model_profiles.json"
BOARD_SELECTION="both"
PROFILE_FILTER_RAW=""
SKIP_BOARD20=0
SKIP_BOARD20_EXPLICIT=0
DRY_RUN=0
SYNC_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

usage() {
  cat <<'EOF'
Usage: bash scripts/sync_mindspore_chat_repro_bundle.sh [options]

Synchronize an explicit allowlist from the 8T and 20T MindSpore chat boards.
The destination is a Git-ignored reproducibility bundle.

  --bundle DIR          destination bundle (default: repro/mindspore-chat-20260829)
  --board8-host HOST    8T board (default: 192.168.1.90)
  --board20-host HOST   20T board (default: 192.168.1.95)
  --user NAME           SSH user (default: HwHiAiUser)
  --remote-root PATH    board project root (default: /home/HwHiAiUser/case9-mindspore-chat)
  --registry FILE       schema-v2 profile registry (default: configs/chat_model_profiles.json)
  --board 8t|20t|both   board selection (default: both)
  --profile ID[,ID...]  native profile filter (repeatable; default: all 8 native profiles)
  --source-allowlist FILE
                        local source file list (default: configs/mindspore_chat_source_allowlist.txt)
  --sync-run-id ID      provenance batch id (default: current UTC)
  --skip-board20        do not contact the 20T board
  --dry-run             print the allowlist and commands without transferring
  -h, --help            show this help
EOF
}

die() { echo "sync-mindspore: $*" >&2; exit 2; }
is_word() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }
is_host() { [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; }
is_rel() {
  [[ "$1" =~ ^[A-Za-z0-9._/-]+$ ]] && [[ "$1" != /* ]] && [[ "$1" != *".."* ]];
}
is_root() {
  [[ "$1" =~ ^/home/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+$ ]] && [[ "$1" != *".."* ]];
}

while (($#)); do
  case "$1" in
    --bundle) [[ $# -ge 2 ]] || die "--bundle requires a value"; BUNDLE="$2"; shift 2 ;;
    --board8-host) [[ $# -ge 2 ]] || die "--board8-host requires a value"; BOARD8_HOST="$2"; shift 2 ;;
    --board20-host) [[ $# -ge 2 ]] || die "--board20-host requires a value"; BOARD20_HOST="$2"; shift 2 ;;
    --user) [[ $# -ge 2 ]] || die "--user requires a value"; REMOTE_USER="$2"; shift 2 ;;
    --remote-root) [[ $# -ge 2 ]] || die "--remote-root requires a value"; REMOTE_ROOT="$2"; shift 2 ;;
    --registry) [[ $# -ge 2 ]] || die "--registry requires a value"; REGISTRY="$2"; shift 2 ;;
    --board) [[ $# -ge 2 ]] || die "--board requires a value"; BOARD_SELECTION="$2"; shift 2 ;;
    --profile) [[ $# -ge 2 && -n "$2" ]] || die "--profile requires a non-empty value"; PROFILE_FILTER_RAW="${PROFILE_FILTER_RAW:+$PROFILE_FILTER_RAW,}$2"; shift 2 ;;
    --source-allowlist) [[ $# -ge 2 ]] || die "--source-allowlist requires a value"; SOURCE_ALLOWLIST="$2"; shift 2 ;;
    --sync-run-id) [[ $# -ge 2 ]] || die "--sync-run-id requires a value"; SYNC_RUN_ID="$2"; shift 2 ;;
    --skip-board20) SKIP_BOARD20=1; SKIP_BOARD20_EXPLICIT=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
[[ "$BOARD_SELECTION" == "8t" || "$BOARD_SELECTION" == "20t" || "$BOARD_SELECTION" == "both" ]] \
  || die "--board must be 8t, 20t, or both"
if [[ "$BOARD_SELECTION" == "20t" ]]; then
  ((SKIP_BOARD20_EXPLICIT)) || SKIP_BOARD20=0
elif [[ "$BOARD_SELECTION" == "8t" ]]; then
  SKIP_BOARD20=1
fi
is_host "$BOARD8_HOST" || die "unsafe board8 host"
is_host "$BOARD20_HOST" || die "unsafe board20 host"
is_word "$REMOTE_USER" || die "unsafe SSH user"
is_root "$REMOTE_ROOT" || die "unsafe remote root"
is_word "$SYNC_RUN_ID" || die "unsafe sync run id"
if [[ "$SOURCE_ALLOWLIST" != /* ]]; then
  SOURCE_ALLOWLIST="$REPO_DIR/$SOURCE_ALLOWLIST"
fi
[[ -f "$SOURCE_ALLOWLIST" && ! -L "$SOURCE_ALLOWLIST" ]] || die "source allowlist is not a regular file: $SOURCE_ALLOWLIST"
if [[ "$REGISTRY" != /* ]]; then
  REGISTRY="$REPO_DIR/$REGISTRY"
fi
[[ -f "$REGISTRY" && ! -L "$REGISTRY" ]] || die "registry is not a regular file: $REGISTRY"

# Run the repository's canonical parser before the shell-side plan builder.
# The planner below extracts only transfer fields, but it must never become a
# weaker second interpretation of schema v2 (especially for admitted quality
# evidence, per-SoC validation, and path metadata).  Loading the validator by
# explicit file path also keeps custom fixture registries usable in tests.
if ! python3 - "$REGISTRY" "$REPO_DIR" <<'PY'
import importlib.util
import sys

registry_path, sample_dir = sys.argv[1:]
validator_path = sample_dir + "/case9_model_profiles.py"
spec = importlib.util.spec_from_file_location("case9_sync_registry_validator", validator_path)
if spec is None or spec.loader is None:
    raise SystemExit("canonical profile validator is unavailable")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
    module.load_profiles(registry_path)
except Exception as exc:
    raise SystemExit("invalid schema-v2 registry: %s" % exc)
finally:
    sys.modules.pop(spec.name, None)
PY
then
  die "canonical schema-v2 registry validation failed"
fi

declare -a PROFILE_FILTERS=()
if [[ -n "$PROFILE_FILTER_RAW" ]]; then
  IFS=',' read -r -a PROFILE_FILTERS <<< "$PROFILE_FILTER_RAW"
  for profile_id in "${PROFILE_FILTERS[@]}"; do
    is_word "$profile_id" || die "unsafe profile id: $profile_id"
  done
fi

# The source snapshot is local controller material, but it is still explicit:
# no recursive copy of the checkout is allowed.  Paths beginning with src/ are
# resolved from the repository root; all other paths are resolved from this
# sample directory.
declare -a SOURCE_FILES=()
declare -A SOURCE_SEEN=()
while IFS= read -r source_rel || [[ -n "$source_rel" ]]; do
  source_rel="${source_rel%$'\r'}"
  [[ -z "$source_rel" || "${source_rel:0:1}" == "#" ]] && continue
  is_rel "$source_rel" || die "unsafe source allowlist path: $source_rel"
  [[ -z "${SOURCE_SEEN[$source_rel]+present}" ]] || die "duplicate source allowlist path: $source_rel"
  SOURCE_SEEN["$source_rel"]=1
  SOURCE_FILES+=("$source_rel")
done < "$SOURCE_ALLOWLIST"
(( ${#SOURCE_FILES[@]} > 0 )) || die "source allowlist is empty"

# Build a plan from the schema-v2 registry instead of maintaining a second,
# drifting model list in this shell script.  The helper emits only exact
# relative paths for native MindSpore profiles.  Conditional profiles are
# deliberately excluded from synchronization and cannot be selected with
# --profile.
PROFILE_PLAN="$(mktemp "${TMPDIR:-/tmp}/case9-mindspore-profile-plan.XXXXXX")"
MISSING_PLAN="$(mktemp "${TMPDIR:-/tmp}/case9-mindspore-missing-plan.XXXXXX")"
cleanup_plan_files() {
  rm -f -- "$PROFILE_PLAN" "$MISSING_PLAN" "${MANAGED_LIST:-}" "${MISSING_LIST:-}" 2>/dev/null || true
}
trap cleanup_plan_files EXIT
if ! python3 - "$REGISTRY" "$PROFILE_FILTER_RAW" > "$PROFILE_PLAN" <<'PY'
import json
import re
import sys
from pathlib import PurePosixPath

registry_path, filter_raw = sys.argv[1:]
try:
    document = json.load(open(registry_path, encoding="utf-8"))
except Exception as exc:
    raise SystemExit("cannot parse registry: %s" % exc)
if not isinstance(document, dict) or document.get("schema_version") != 2:
    raise SystemExit("registry must use schema_version=2")
profiles = document.get("profiles")
if not isinstance(profiles, list):
    raise SystemExit("registry profiles must be an array")
safe_id = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
safe_rel = re.compile(r"^[A-Za-z0-9._/-]+$")
requested_parts = filter_raw.split(",") if filter_raw else []
if any(not item for item in requested_parts):
    raise SystemExit("--profile contains an empty selector")
requested = requested_parts
if len(requested) != len(set(requested)):
    raise SystemExit("duplicate --profile selector")
for item in requested:
    if not safe_id.fullmatch(item):
        raise SystemExit("unsafe profile selector: %s" % item)

native = []
all_ids = set()
for profile in profiles:
    if not isinstance(profile, dict):
        raise SystemExit("profile entry must be an object")
    profile_id = profile.get("id")
    if not isinstance(profile_id, str) or not safe_id.fullmatch(profile_id):
        raise SystemExit("invalid profile id")
    if profile_id in all_ids:
        raise SystemExit("duplicate profile id: %s" % profile_id)
    all_ids.add(profile_id)
    if profile.get("candidate_kind") != "native_mindspore":
        continue
    native.append(profile)
native_ids = {item["id"] for item in native}
unknown = sorted(set(requested) - native_ids)
if unknown:
    raise SystemExit("--profile is not a native_mindspore profile: %s" % ", ".join(unknown))
selected = [item for item in native if not requested or item["id"] in requested]
if not selected:
    raise SystemExit("registry contains no selected native_mindspore profile")
cache_dirs = set()
for profile in selected:
    cache_dir_value = profile.get("cache_dir")
    if not isinstance(cache_dir_value, str) or cache_dir_value in cache_dirs:
        raise SystemExit("selected native profiles must have unique cache_dir values")
    cache_dirs.add(cache_dir_value)

def safe_path(value, label):
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in PurePosixPath(value).parts:
        raise SystemExit("unsafe %s path" % label)
    if not safe_rel.fullmatch(value):
        raise SystemExit("unsafe %s path: %s" % (label, value))
    return value

def report_candidates(raw, soc):
    raw = safe_path(raw, "report")
    candidates = []
    # Registry paths commonly point into a local repro bundle.  Convert only
    # known board-relative prefixes; no remote directory search is performed.
    parts = raw.split("/")
    board_key = "board8t" if soc == "Ascend310B4" else "board20t"
    if parts and parts[0] == "repro" and board_key in parts:
        board_index = parts.index(board_key)
        tail = parts[board_index + 1:]
        if tail:
            tail_path = "/".join(tail)
            if tail_path.startswith("reports/mindspore-chat/") or tail_path.startswith("run/"):
                candidates.append(tail_path)
            # A report may be recorded either as
            # ``.../board8t/reports/mindspore-chat/...`` or as
            # ``.../reports/board8t/...``.  The latter has no ``reports/``
            # component left in ``tail_path``; inspect the component before
            # the board key and map it to the canonical remote prefix.
            elif board_index > 0 and parts[board_index - 1] == "reports":
                candidates.append("reports/mindspore-chat/" + tail_path)
                candidates.append("reports/" + board_key + "/" + tail_path)
                candidates.append("reports/" + tail_path)
    if raw.startswith("reports/") or raw.startswith("run/"):
        direct_parts = raw.split("/")
        if direct_parts[0] == "reports" and len(direct_parts) > 1 and direct_parts[1] == board_key:
            # Normalize a direct ``reports/<board>/...`` registry path in the
            # same way as its repro-bundle spelling while retaining the raw
            # path as a final compatibility candidate.
            tail = "/".join(direct_parts[2:])
            if tail:
                candidates.append("reports/mindspore-chat/" + tail)
                candidates.append("reports/" + board_key + "/" + tail)
                candidates.append("reports/" + tail)
        candidates.append(raw)
    if not candidates:
        candidates.append(raw)
    # Stable de-duplication keeps the generated plan reviewable.
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            yield candidate

def canonical_report_destination(profile_id, soc, raw):
    """Return one stable bundle-relative destination for a registry report.

    Registry paths are provenance paths and may include a local ``repro``
    bundle name, a board component, or a ``reports`` wrapper.  Strip those
    transport/provenance prefixes only; the remaining report hierarchy and
    filename are retained under a fixed board/profile-reports/SoC namespace.
    The original path remains in the R plan and manifest for traceability.
    """
    raw = safe_path(raw, "report")
    board_key = "board8t" if soc == "Ascend310B4" else "board20t"
    suffix = raw.split("/")
    # ``repro/<bundle>/`` is a local provenance wrapper, never part of the
    # remote report identity.  Require a non-empty remainder so malformed
    # registry paths fail before any SSH operation.
    if suffix and suffix[0] == "repro":
        suffix = suffix[2:]
    if not suffix:
        raise SystemExit("report path has no canonical suffix: %s" % raw)

    # Remove the board wrapper wherever it is used by the historical layouts:
    # ``board8t/...``, ``reports/board8t/...`` and
    # ``reports/mindspore-chat/board8t/...``.  A path naming the other board is
    # rejected instead of being silently attributed to this SoC.
    other_board = "board20t" if board_key == "board8t" else "board8t"
    if other_board in suffix:
        raise SystemExit("report path board does not match %s: %s" % (soc, raw))
    if board_key in suffix:
        suffix = suffix[suffix.index(board_key) + 1:]

    # Keep the remaining relative hierarchy exactly as recorded.  For example,
    # ``board8t/reports/mindspore-chat/...`` retains ``reports/mindspore-chat``
    # while ``reports/board8t/...`` loses only the outer board wrapper.  This
    # avoids silently merging otherwise distinct run/evidence paths.
    if not suffix:
        raise SystemExit("report path has no canonical suffix: %s" % raw)
    suffix_path = safe_path("/".join(suffix), "canonical report")
    destination = "%s/profile-reports/%s/%s/%s" % (
        board_key, profile_id, soc, suffix_path
    )
    return safe_path(destination, "report destination")

destination_paths = set()
report_destinations = set()
for profile in selected:
    profile_id = profile["id"]
    cache_dir = safe_path(profile.get("cache_dir"), "cache_dir")
    artifacts = profile.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SystemExit("profile %s has no artifacts" % profile_id)
    targets = profile.get("board_targets")
    if not isinstance(targets, list) or not targets:
        raise SystemExit("profile %s has no board_targets" % profile_id)
    target_socs = set()
    for target in targets:
        if not isinstance(target, dict):
            raise SystemExit("profile %s has malformed board target" % profile_id)
        target_soc = target.get("soc")
        if target_soc not in ("Ascend310B4", "Ascend310B1"):
            raise SystemExit("profile %s has unsupported board target SoC" % profile_id)
        target_socs.add(target_soc)
    if target_socs != {"Ascend310B4", "Ascend310B1"}:
        raise SystemExit("profile %s must declare both board target SoCs" % profile_id)
    validation = profile.get("validation")
    if not isinstance(validation, dict):
        raise SystemExit("profile %s has no validation map" % profile_id)
    for required_soc in ("Ascend310B4", "Ascend310B1"):
        if required_soc not in validation:
            raise SystemExit("profile %s is missing validation for %s" % (profile_id, required_soc))
        if not isinstance(validation[required_soc], dict):
            raise SystemExit("profile %s %s validation must be an object" % (profile_id, required_soc))
    artifact_paths = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise SystemExit("profile %s has malformed artifact" % profile_id)
        filename = safe_path(artifact.get("filename"), "artifact")
        if filename in artifact_paths:
            raise SystemExit("profile %s has duplicate artifact filename" % profile_id)
        artifact_paths.add(filename)
        destination = "%s/%s" % (cache_dir, filename)
        if destination in destination_paths:
            raise SystemExit("selected profiles have colliding artifact destination: %s" % destination)
        destination_paths.add(destination)
        kind = artifact.get("kind")
        if not isinstance(kind, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,31}", kind):
            raise SystemExit("profile %s has invalid artifact kind" % profile_id)
        # A = profile id, cache-relative artifact path, kind, expected bytes,
        # and expected SHA.  Empty metadata is retained as an explicit value;
        # the board-side hash is still recorded when the file exists.
        expected_bytes = artifact.get("expected_bytes")
        expected_sha = artifact.get("sha256")
        if expected_bytes is not None and (
            isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0
        ):
            raise SystemExit("profile %s has invalid artifact size" % profile_id)
        if expected_sha is not None and (not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha)):
            raise SystemExit("profile %s has invalid artifact SHA" % profile_id)
        print("A\t%s\t%s/%s\t%s\t%s\t%s" % (
            profile_id, cache_dir, filename, kind,
            "" if expected_bytes is None else expected_bytes,
            "" if expected_sha is None else expected_sha.lower(),
        ))
    for soc in ("Ascend310B4", "Ascend310B1"):
        report_paths = validation[soc].get("report_paths")
        if not isinstance(report_paths, list):
            raise SystemExit("profile %s %s report_paths must be an array" % (profile_id, soc))
        for raw in report_paths:
            candidates = list(report_candidates(raw, soc))
            destination = canonical_report_destination(profile_id, soc, raw)
            if destination in report_destinations:
                raise SystemExit("selected profiles have colliding report destination: %s" % destination)
            report_destinations.add(destination)
            # Commas are excluded by safe_path, so a comma-separated candidate
            # field remains unambiguous and lets the shell try known prefixes
            # without performing a remote directory search.  R columns are:
            # profile, SoC, remote candidates, registry path, destination.
            print("R\t%s\t%s\t%s\t%s\t%s" % (
                profile_id, soc, ",".join(candidates), raw, destination
            ))
PY
then
  die "registry profile plan generation failed"
fi
[[ -s "$PROFILE_PLAN" ]] || die "registry produced an empty profile plan"

# Every entry below is an exact relative path. Keep this list intentionally
# boring and reviewable; adding a wildcard or a recursive copy is prohibited.
BOARD8_FILES=(
  # The root-level registry is an older board snapshot. The launchers only
  # use configs/chat_model_profiles.json, so retain any previously copied root
  # file as legacy evidence instead of treating it as current input.
  configs/chat_model_profiles.json
  case9_model_profiles.py
  mindspore_chat_providers.py
  mindspore_chat_service.py
  local_session.py
  text_chat_app.py
  app.py
  config.py
  upstream.py
  retrieval.py
  scripts/case9-modelctl.sh
  scripts/check_mindspore_chat_environment.py
  scripts/mindspore_chat_acceptance.py
  scripts/run_mindspore_chat_acceptance.sh
  scripts/run_mindspore_chat_service.sh
  scripts/run_mindspore_chat_gateway.sh
  scripts/run_mindspore_chat_text.sh
  scripts/run_text_chat.sh
  scripts/verify_mindspore_profile_artifacts.py
  tests/fixtures/mindspore_chat_probe.json
  frontend/dist/index.html
  frontend/dist/assets/index-B8E1yCVM.js
  frontend/dist/assets/index-Ckdv_CFc.css
  reports/mindspore-chat/tinyllama-download-20260829/artifact-verification.json
  reports/mindspore-chat/tinyllama-download-20260829/source.txt
  reports/mindspore-chat/tinyllama-download-20260829/SHA256SUMS.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/README.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/acceptance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/command.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/errors.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/health.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/json-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/long-output.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/metadata.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/models.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/performance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/protocol.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/quality.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/sse-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-health-gate-20260829c/stability.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/README.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/acceptance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/command.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/errors.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/health.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/json-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/long-output.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/metadata.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/models.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/performance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/protocol.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/quality.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/snapshots.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/sse-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/stability.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/README.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/acceptance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/command.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/errors.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/health.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/json-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/long-output.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/metadata.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/models.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/performance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/protocol.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/quality.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/snapshots.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/sse-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/stability.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/README.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/acceptance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/command.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/errors.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/health.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/json-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/long-output.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/metadata.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/models.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/performance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/protocol.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/quality.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/sse-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-code-20260829k/stability.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/README.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/acceptance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/command.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/errors.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/health.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/json-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/long-output.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/metadata.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/models.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/performance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/protocol.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/quality.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/sse-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/stability.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/README.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/acceptance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/command.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/errors.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/health-final.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/health.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/json-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/long-output.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/lpm-dmesg-and-processes.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/metadata.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/modelctl-status.txt
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/models.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/performance.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/protocol.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/quality.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/snapshots.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/sse-smoke.json
  reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/stability.json
  run/mindspore-chat/candidate-recovery-20260829r/README.txt
  run/mindspore-chat/candidate-recovery-20260829r/chain-summary-final.json
  run/mindspore-chat/candidate-recovery-20260829r/chain-summary.json
  run/mindspore-chat/candidate-recovery-20260829r/gateway.log
  run/mindspore-chat/candidate-recovery-20260829r/gateway.pid
  run/mindspore-chat/candidate-recovery-20260829r/text-ui.log
  run/mindspore-chat/candidate-recovery-20260829r/text-ui.pid
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T143924Z-15861-23648.log
  run/mindspore-chat/active-model.json
  run/mindspore-chat/worker.pid
  run/mindspore-chat/worker.pgid
  run/mindspore-chat/process-migration-20260829T105131Z.log
  run/mindspore-chat/legacy-migration-20260829T105229Z/active-model.json
  run/mindspore-chat/legacy-migration-20260829T105229Z/worker.pid
  run/mindspore-chat/artifact-verification/qwen1.5-0.5b-mindspore-20260829T080625Z.json
  run/mindspore-chat/artifact-verification/tinyllama-latest.json
  run/mindspore-chat/artifact-verification/qwen1.5-0.5b-mindspore-20260829T111023Z.json
  run/mindspore-chat/artifact-verification/qwen1.5-0.5b-mindspore-20260829T115505Z.json
  run/mindspore-chat/artifact-verification/tinyllama-1.1b-mindspore-20260829T110012Z.json
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T080625Z.log
  run/mindspore-chat/logs/tinyllama-1.1b-mindspore-20260829T071501Z.log
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T105244Z-47997-14444.log
  run/mindspore-chat/logs/tinyllama-1.1b-mindspore-20260829T105946Z-51165-31067.log
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T110958Z-57319-24854.log
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T110324Z-53961-26438.log
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T115438Z-68706-30033.log
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T125047Z-76390-5174.log
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T132104Z-90436-6247.log
  run/mindspore-chat/logs/candidate-gateway.log
  run/mindspore-chat/logs/candidate-text.log
  run/mindspore-chat/gateway-20260829T073550Z.log
  run/mindspore-chat/text-20260829T073550Z.log
  run/mindspore-chat/environment-preflight/qwen1.5-0.5b-mindspore-20260829T110324Z.json
  run/mindspore-chat/environment-preflight/qwen1.5-0.5b-mindspore-20260829T110959Z.json
  run/mindspore-chat/environment-preflight/qwen1.5-0.5b-mindspore-20260829T115438Z.json
  run/mindspore-chat/environment-preflight/tinyllama-1.1b-mindspore-20260829T105947Z.json
  reports/mindspore-chat/environment/board8t-system-20260829.txt
  reports/mindspore-chat/environment/board8t-npu-20260829.txt
  reports/mindspore-chat/environment/board8t-python-20260829.txt
  reports/mindspore-chat/environment/board8t-pip-freeze-20260829.txt
)

# The 20T list remains deliberately explicit. It contains only the board-side
# evidence named below; no home-directory recursion or implicit model copy is
# performed.
BOARD20_FILES=(
  environment/board20t-connectivity-20260829.txt
  run/mindspore-chat/active-model.json
  run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T080625Z.log
  run/mindspore-chat/logs/tinyllama-1.1b-mindspore-20260829T071501Z.log
)

for rel in "${BOARD8_FILES[@]}" "${BOARD20_FILES[@]}"; do is_rel "$rel" || die "unsafe allowlist path: $rel"; done

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=12 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
if ((DRY_RUN == 0)); then
  for cmd in ssh rsync sha256sum python3; do command -v "$cmd" >/dev/null || die "$cmd is required"; done
fi

if [[ -e "$BUNDLE" && -L "$BUNDLE" ]]; then
  die "refusing symlink bundle: $BUNDLE"
fi
if ((DRY_RUN == 0)); then
  mkdir -p "$BUNDLE"
fi

declare -a RECORDS=()
declare -a MISSING_RECORDS=()
source_path_for() {
  local rel="$1"
  if [[ "$rel" == src/* ]]; then
    printf '%s/%s\n' "$REPO_ROOT" "$rel"
  else
    printf '%s/%s\n' "$REPO_DIR" "$rel"
  fi
}

sync_local_source() {
  local rel="$1"
  local source dest part expected_bytes actual_bytes expected_sha actual_sha
  source="$(source_path_for "$rel")"
  dest="$BUNDLE/source/$rel"
  part="${dest}.part"
  [[ -f "$source" && ! -L "$source" ]] || die "local source is not a regular file: $source"
  if ((DRY_RUN)); then
    printf 'DRY-RUN %s <- %s\n' "$dest" "$source"
    return
  fi
  # Reject symlinked source components and ensure the resolved file remains in
  # either the sample or repository root before it is copied.
  local source_real sample_real repo_real
  source_real="$(readlink -f -- "$source" 2>/dev/null || true)"
  sample_real="$(readlink -f -- "$REPO_DIR" 2>/dev/null || true)"
  repo_real="$(readlink -f -- "$REPO_ROOT" 2>/dev/null || true)"
  [[ -n "$source_real" && ( "$source_real" == "$sample_real"/* || "$source_real" == "$repo_real"/* ) ]] \
    || die "local source escapes repository roots: $rel"
  mkdir -p "$(dirname "$dest")"
  [[ ! -L "$dest" && ! -L "$part" ]] || die "refusing symlink source destination: $dest"
  expected_bytes="$(stat -c '%s' "$source" 2>/dev/null || wc -c < "$source")"
  expected_sha="$(sha256sum "$source" | cut -d ' ' -f1)"
  if [[ -f "$dest" ]]; then
    actual_bytes="$(stat -c '%s' "$dest" 2>/dev/null || wc -c < "$dest")"
    actual_sha="$(sha256sum "$dest" | cut -d ' ' -f1)"
    if [[ "$actual_bytes" == "$expected_bytes" && "$actual_sha" == "$expected_sha" ]]; then
      RECORDS+=("source|$rel|$expected_bytes|$expected_sha|controller|$source")
      return
    fi
  fi
  [[ ! -e "$part" ]] || die "unexpected existing source part file: $part"
  cp -- "$source" "$part"
  actual_bytes="$(stat -c '%s' "$part" 2>/dev/null || wc -c < "$part")"
  actual_sha="$(sha256sum "$part" | cut -d ' ' -f1)"
  [[ "$actual_bytes" == "$expected_bytes" ]] || die "local source size mismatch: $rel"
  [[ "$actual_sha" == "$expected_sha" ]] || die "local source SHA-256 mismatch: $rel"
  mv -f -- "$part" "$dest"
  RECORDS+=("source|$rel|$expected_bytes|$expected_sha|controller|$source")
}

transfer_one() {
  local host="$1" board="$2" rel="$3" dest_rel="${4:-$rel}"
  local remote="${REMOTE_ROOT%/}/$rel"
  local dest="$BUNDLE/$dest_rel"
  local part="${dest}.part"
  local expected_bytes expected_sha actual_bytes actual_sha record_rel
  if ((DRY_RUN == 0)); then
    mkdir -p "$(dirname "$dest")"
  fi
  [[ ! -L "$dest" && ! -L "$part" ]] || die "refusing symlink destination: $dest"
  if ((DRY_RUN)); then
    printf 'DRY-RUN %s <- %s:%s\n' "$dest" "$REMOTE_USER@$host" "$remote"
    return
  fi
  expected_bytes="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "test -f '$remote' && stat -c '%s' '$remote'")" || die "remote file unavailable: $host:$rel"
  # Use ``cut`` instead of an embedded awk ``$1`` expression.  The latter is
  # expanded by the local shell inside the double-quoted SSH command and can
  # silently turn the host's positional parameters into the hash filter.
  expected_sha="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "sha256sum '$remote' | cut -d ' ' -f1")" || die "remote hash failed: $host:$rel"
  [[ "$expected_bytes" =~ ^[0-9]+$ && "$expected_sha" =~ ^[[:xdigit:]]{64}$ ]] || die "invalid remote metadata: $host:$rel"
  # A verified destination is already reproducible. Reuse it on subsequent
  # runs so a routine manifest refresh does not retransmit multi-gigabyte
  # checkpoints; a changed or incomplete destination still goes through the
  # .part/rsync/integrity path below.
  if [[ -f "$dest" && ! -L "$dest" ]]; then
    actual_bytes="$(stat -c '%s' "$dest" 2>/dev/null || wc -c < "$dest")"
    actual_sha="$(sha256sum "$dest" | cut -d ' ' -f1)"
    if [[ "$actual_bytes" == "$expected_bytes" && "$actual_sha" == "$expected_sha" ]]; then
      record_rel="$dest_rel"
      [[ "$record_rel" == "$board/"* ]] && record_rel="${record_rel#"$board/"}"
      RECORDS+=("$board|$record_rel|$expected_bytes|$expected_sha|$host|$remote")
      return
    fi
  fi
  rsync -a --partial --append-verify -e "ssh ${SSH_OPTS[*]}" "${REMOTE_USER}@${host}:${remote}" "$part" || die "rsync failed: $host:$rel"
  actual_bytes="$(stat -c '%s' "$part" 2>/dev/null || wc -c < "$part")"
  actual_sha="$(sha256sum "$part" | cut -d ' ' -f1)"
  [[ "$actual_bytes" == "$expected_bytes" ]] || die "size mismatch: $board/$rel"
  [[ "$actual_sha" == "$expected_sha" ]] || die "SHA-256 mismatch: $board/$rel"
  mv -f -- "$part" "$dest"
  record_rel="$dest_rel"
  [[ "$record_rel" == "$board/"* ]] && record_rel="${record_rel#"$board/"}"
  RECORDS+=("$board|$record_rel|$expected_bytes|$expected_sha|$host|$remote")
}

# Optional board files are intentionally represented in the manifest instead
# of aborting a whole synchronization.  This is needed for the eight native
# profiles: a profile may be registered before its weights or reports have
# been installed on a particular board.  The remote path is still exact and
# validated; no directory listing or recursive search is attempted.
transfer_optional() {
  local host="$1" board="$2" rel="$3" dest_rel="$4" kind="$5" profile="${6:-}" soc="${7:-}"
  local expected_bytes="${8:-}" expected_sha="${9:-}" actual_bytes actual_sha remote_status
  local remote="${REMOTE_ROOT%/}/$rel"
  if ((DRY_RUN)); then
    printf 'DRY-RUN optional %s <- %s:%s\n' "$BUNDLE/$dest_rel" "$REMOTE_USER@$host" "$remote"
    return
  fi
  # Distinguish a successful SSH command whose test returns false (the
  # optional file is absent) from an SSH transport/authentication failure.
  # Treating both as ``missing`` would silently produce a partial manifest
  # when a board is offline, which is unsafe for reproducibility.
  if ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "test -f '$remote'" >/dev/null 2>&1; then
    remote_status=0
  else
    remote_status=$?
  fi
  if ((remote_status != 0)); then
    if ((remote_status == 1)); then
      printf '[%s] missing optional %s: %s\n' "$board" "$kind" "$remote"
      # Keep a ninth field for report provenance.  It is empty for ordinary
      # artifacts/legacy files and populated by transfer_report_optional below.
      MISSING_RECORDS+=("$board|$profile|$soc|$kind|$rel|$dest_rel|$host|$remote|")
      return 0
    fi
    die "SSH probe failed (exit $remote_status): $host:$rel"
  fi
  if [[ "$kind" == artifact:* && ( -n "$expected_bytes" || -n "$expected_sha" ) ]]; then
    actual_bytes="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "stat -c '%s' '$remote'")" || die "remote size failed: $host:$rel"
    actual_sha="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "sha256sum '$remote' | cut -d ' ' -f1")" || die "remote hash failed: $host:$rel"
    if [[ ( -n "$expected_bytes" && "$actual_bytes" != "$expected_bytes" ) || ( -n "$expected_sha" && "${actual_sha,,}" != "${expected_sha,,}" ) ]]; then
      printf '[%s] artifact metadata mismatch: %s\n' "$board" "$remote"
      MISSING_RECORDS+=("$board|$profile|$soc|artifact-metadata-mismatch|$rel|$dest_rel|$host|$remote|")
      return 0
    fi
  fi
  transfer_one "$host" "$board" "$rel" "$dest_rel"
}

transfer_report_optional() {
  local host="$1" board="$2" profile="$3" soc="$4" candidates_csv="$5" raw="$6" destination="$7"
  local candidate remote dest_rel found=0 remote_status
  [[ "$destination" == "$board/"* ]] || die "unsafe report destination: $destination"
  if ((DRY_RUN)); then
    printf 'DRY-RUN optional report %s <- %s:%s\n' "$BUNDLE/$destination" "$REMOTE_USER@$host" "$candidates_csv"
    return
  fi
  IFS=',' read -r -a candidates <<< "$candidates_csv"
  for candidate in "${candidates[@]}"; do
    remote="${REMOTE_ROOT%/}/$candidate"
    if ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "test -f '$remote'" >/dev/null 2>&1; then
      remote_status=0
    else
      remote_status=$?
    fi
    if ((remote_status == 0)); then
      # The canonical destination was generated with the registry plan and is
      # independent of whichever remote candidate happened to exist.
      transfer_one "$host" "$board" "$candidate" "$destination"
      found=1
      break
    elif ((remote_status != 1)); then
      die "SSH probe failed (exit $remote_status): $host:$candidate"
    fi
  done
  if ((found == 0)); then
    printf '[%s] missing optional report: %s\n' "$board" "$raw"
    # For reports, remote_rel is the exact comma-separated candidate list and
    # the final field preserves the registry path independently of whichever
    # candidate is eventually found.
    MISSING_RECORDS+=("$board|$profile|$soc|report|$candidates_csv|$destination|$host|${REMOTE_ROOT%/}|$raw")
  fi
}

sync_profile_plan() {
  local host="$1" board="$2" soc="$3" type profile cache_rel kind expected_bytes expected_sha
  local report_soc candidates_csv raw report_destination
  while IFS=$'\t' read -r type profile cache_rel kind expected_bytes expected_sha; do
    [[ "$type" == "A" ]] || continue
    # Registry artifact metadata is provenance; transfer integrity is always
    # checked against the board's live size/SHA.  Expected values are retained
    # in the missing/manifest records but do not make an absent file fatal.
    dest_rel="$board/$cache_rel"
    transfer_optional "$host" "$board" "$cache_rel" "$dest_rel" "artifact:$kind" "$profile" "$soc" "$expected_bytes" "$expected_sha"
  done < "$PROFILE_PLAN"
  while IFS=$'\t' read -r type profile report_soc candidates_csv raw report_destination; do
    [[ "$type" == "R" && "$report_soc" == "$soc" ]] || continue
    transfer_report_optional "$host" "$board" "$profile" "$soc" "$candidates_csv" "$raw" "$report_destination"
  done < "$PROFILE_PLAN"
}

echo "MindSpore chat bundle: $BUNDLE"
PROFILE_ARTIFACT_COUNT="$(awk -F '\t' '$1 == "A" { count++ } END { print count + 0 }' "$PROFILE_PLAN")"
PROFILE_REPORT_COUNT="$(awk -F '\t' '$1 == "R" { count++ } END { print count + 0 }' "$PROFILE_PLAN")"
PROFILE_COUNT="$(awk -F '\t' '$1 == "A" { seen[$2] = 1 } END { for (id in seen) count++; print count + 0 }' "$PROFILE_PLAN")"
echo "Allowlist: source=${#SOURCE_FILES[@]} files, board8=${#BOARD8_FILES[@]} legacy files, board20=${#BOARD20_FILES[@]} legacy files"
echo "Native profile plan: profiles=$PROFILE_COUNT artifacts=$PROFILE_ARTIFACT_COUNT report_paths=$PROFILE_REPORT_COUNT"
for rel in "${SOURCE_FILES[@]}"; do sync_local_source "$rel"; done
if [[ "$BOARD_SELECTION" == "8t" || "$BOARD_SELECTION" == "both" ]]; then
  for rel in "${BOARD8_FILES[@]}"; do
    transfer_optional "$BOARD8_HOST" board8t "$rel" "board8t/$rel" "legacy" "" "Ascend310B4"
  done
  sync_profile_plan "$BOARD8_HOST" board8t Ascend310B4
fi
if ((SKIP_BOARD20)); then
  echo "Skipping board20 ($BOARD20_HOST) by request"
else
  if [[ "$BOARD_SELECTION" == "20t" || "$BOARD_SELECTION" == "both" ]]; then
    for rel in "${BOARD20_FILES[@]}"; do
      transfer_optional "$BOARD20_HOST" board20t "$rel" "board20t/$rel" "legacy" "" "Ascend310B1"
    done
    sync_profile_plan "$BOARD20_HOST" board20t Ascend310B1
  fi
fi

if ((DRY_RUN)); then
  echo "Dry run complete; no files or manifests were changed."
  exit 0
fi

# Build the exact set managed by this run from successful transfers. Optional
# board artifacts/reports that are absent are intentionally excluded here and
# listed in ``missing`` below; requiring them in this list would turn a
# partially provisioned but valid registry into a fatal synchronization error.
MANAGED_LIST="$(mktemp "${TMPDIR:-/tmp}/case9-mindspore-managed.XXXXXX")"
{
  declare -A managed_seen=()
  for record in "${RECORDS[@]}"; do
    IFS='|' read -r record_board record_rel _record_bytes _record_sha _record_host _record_remote <<< "$record"
    managed_key="$record_board/$record_rel"
    [[ -z "${managed_seen[$managed_key]+present}" ]] || continue
    managed_seen["$managed_key"]=1
    printf '%s\n' "$managed_key"
  done
} > "$MANAGED_LIST"

MISSING_LIST="$MISSING_PLAN"
for record in "${MISSING_RECORDS[@]}"; do printf '%s\n' "$record"; done > "$MISSING_LIST"

# Build deterministic checksums and a machine-readable manifest only after all
# transfers have passed. Existing historical files remain untouched and are
# still checksummed, but are marked legacy_retained rather than current input.
python3 - "$BUNDLE" "$SYNC_RUN_ID" "$BOARD8_HOST" "$BOARD20_HOST" "$SKIP_BOARD20" "$SKIP_BOARD20_EXPLICIT" "$BOARD_SELECTION" "$REMOTE_ROOT" "$MANAGED_LIST" "$MISSING_LIST" "$PROFILE_PLAN" "$REGISTRY" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
run_id, board8, board20, skipped, skipped_explicit, board_selection, remote_root, managed_list_path, missing_list_path, plan_path, registry_path = sys.argv[2:]
excluded = {"bundle-manifest.json", "SHA256SUMS.txt", "bundle-manifest.json.part", "SHA256SUMS.txt.part"}
managed_list = Path(managed_list_path)
managed_paths = [line.strip() for line in managed_list.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(managed_paths) != len(set(managed_paths)):
    raise SystemExit("managed path list contains duplicates")
for relative in managed_paths:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"managed path is missing or unsafe: {relative}")
managed = set(managed_paths)
missing = []
missing_path = Path(missing_list_path)
if missing_path.is_file():
    for line in missing_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split("|", 8)
        if len(fields) != 9:
            raise SystemExit("malformed missing record")
        board, profile, soc, kind, remote_rel, dest_rel, source_host, remote_root, registry_path_value = fields
        item = {
            "board": board,
            "profile": profile or None,
            "soc": soc or None,
            "kind": kind,
            "remote_rel": remote_rel,
            "destination": dest_rel,
            "source_host": source_host,
            "source_root": remote_root,
            "status": "missing",
        }
        if kind == "report":
            item.update({
                "registry_path": registry_path_value or None,
                "remote_candidates": [value for value in remote_rel.split(",") if value],
            })
        missing.append(item)

missing_counts = {"board8t": 0, "board20t": 0}
for item in missing:
    if item["board"] in missing_counts:
        missing_counts[item["board"]] += 1

plan_artifacts = []
plan_reports = []
plan_profiles = set()
for line in Path(plan_path).read_text(encoding="utf-8").splitlines():
    fields = line.split("\t")
    if not fields:
        continue
    if fields[0] == "A" and len(fields) == 6:
        _, profile, relative, kind, expected_bytes, expected_sha = fields
        plan_profiles.add(profile)
        plan_artifacts.append({
            "profile": profile,
            "relative": relative,
            "kind": kind,
            "expected_bytes": int(expected_bytes) if expected_bytes else None,
            "expected_sha256": expected_sha or None,
        })
    elif fields[0] == "R" and len(fields) == 6:
        _, profile, soc, candidates, registry_path_value, destination = fields
        plan_profiles.add(profile)
        plan_reports.append({
            "profile": profile,
            "soc": soc,
            "remote_candidates": candidates.split(","),
            "registry_path": registry_path_value,
            "destination": destination,
        })
    else:
        raise SystemExit("malformed profile plan line")
report_by_destination = {item["destination"]: item for item in plan_reports}
missing_by_destination = {
    item["destination"]: item
    for item in missing
    if item.get("kind") == "report" and item.get("destination")
}
files = []
current_counts = {"source": 0, "board8t": 0, "board20t": 0}
legacy_count = 0
for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".part")):
    rel = path.relative_to(root).as_posix()
    if rel in excluded:
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    board, source_rel = (rel.split("/", 1) + [""])[:2]
    source_host = board8 if board == "board8t" else board20 if board == "board20t" else None
    if rel in managed:
        classification = "current_source" if board == "source" else f"current_{board}"
        if board in current_counts:
            current_counts[board] += 1
    else:
        classification = "legacy_retained"
        legacy_count += 1
    entry = {
        "path": rel,
        "bytes": path.stat().st_size,
        "sha256": digest,
        "classification": classification,
    }
    report_provenance = report_by_destination.get(rel)
    if report_provenance is not None:
        # Keep provenance on the file entry as well as in profile_plan.reports;
        # consumers that only inspect SHA256SUMS-adjacent file records still
        # get the registry path, exact candidate list and canonical target.
        entry.update({
            "registry_path": report_provenance["registry_path"],
            "remote_candidates": report_provenance["remote_candidates"],
            "destination": report_provenance["destination"],
            "report_status": "synced" if rel in managed else "legacy_retained",
        })
    if source_host and rel in managed:
        entry.update({
            "source_board": board,
            "source_host": source_host,
            "source_soc": "Ascend310B4" if board == "board8t" else "Ascend310B1",
            "source_root": remote_root,
            "source_rel": source_rel,
        })
    elif board == "source" and rel in managed:
        entry.update({"source_controller": True, "source_rel": source_rel})
    files.append(entry)

sums = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
sums_part = root / "SHA256SUMS.txt.part"
sums_part.write_text(sums, encoding="ascii")
sums_part.replace(root / "SHA256SUMS.txt")
manifest = {
    "schema_version": 1,
    "bundle": "mindspore-chat-20260829",
    "sync_run_id": run_id,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "allowlist_policy": "explicit paths only; rsync partial/append verification; no recursive home copy; no deletion flag",
    "registry": {
        "path": str(Path(registry_path).name),
        "schema_version": 2,
        "selected_native_profiles": sorted(plan_profiles),
        "native_profile_selection": "all native_mindspore profiles by default; --profile narrows this set",
    },
    "profile_plan": {
        "artifact_count": len(plan_artifacts),
        "report_path_count": len(plan_reports),
        "artifacts": plan_artifacts,
        "reports": [
            dict(
                item,
                status=(
                    "synced" if item["destination"] in managed
                    else "missing" if item["destination"] in missing_by_destination
                    else "not-selected"
                ),
            )
            for item in plan_reports
        ],
    },
    "scope": {
        "current_managed_entries": len(managed_paths),
        "current_source_entries": current_counts["source"],
        "current_board8t_entries": current_counts["board8t"],
        "current_board20t_entries": current_counts["board20t"],
        "legacy_retained_entries": legacy_count,
        "legacy_policy": "Retained historical bundle files are SHA-256 checked but are not current allowlist inputs.",
        "board_selection": board_selection,
        "missing_optional_entries": len(missing),
    },
    "sources": {
        "board8t": {
            "host": board8,
            "remote_root": remote_root,
            "selected": board_selection in {"8t", "both"},
            "status": (
                "not-selected" if board_selection == "20t"
                else "synced" if missing_counts["board8t"] == 0
                else "partial"
            ),
            "missing_optional_entries": missing_counts["board8t"],
        },
        "board20t": {
            "host": board20,
            "remote_root": remote_root,
            "selected": board_selection in {"20t", "both"} and skipped != "1",
            "status": (
                "skipped" if skipped_explicit == "1"
                else "not-selected" if board_selection == "8t"
                else "synced" if missing_counts["board20t"] == 0
                else "partial" if board_selection in {"20t", "both"}
                else "not-selected"
            ),
            "missing_optional_entries": missing_counts["board20t"],
        },
    },
    "files": files,
    "missing": missing,
}
manifest_part = root / "bundle-manifest.json.part"
manifest_part.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
manifest_part.replace(root / "bundle-manifest.json")
print(
    "Wrote "
    f"{len(files)} checksummed files "
    f"(current={len(managed_paths)}, legacy={legacy_count}) "
    "to SHA256SUMS.txt and bundle-manifest.json"
)
PY

echo "MindSpore chat bundle synchronization complete."
