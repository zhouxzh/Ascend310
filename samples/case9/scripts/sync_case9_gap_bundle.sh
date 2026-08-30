#!/usr/bin/env bash
# Synchronize the explicit Case9 dual-board gap evidence set.
#
# The default mode is a non-mutating dry run. Execute mode only copies files
# named by the allowlists below (or by --extra-*), never recurses through a
# home directory, never passes rsync --delete, and verifies every transfer
# with byte counts and SHA-256 before an atomic rename.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE=""
BOARD8_HOST="192.168.1.90"
BOARD20_HOST="192.168.1.95"
REMOTE_USER="HwHiAiUser"
BOARD8_ROOT="/home/HwHiAiUser/case9-mindspore-chat"
BOARD20_ROOT="/home/HwHiAiUser/case9-mindspore-chat"
BOARD8_OM_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024"
BOARD20_OM_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024-20t"
BOARD8_DEEPSEEK_ROOT="/home/HwHiAiUser/case9-deepseek-8t-experiment"
BOARD20_DEEPSEEK_ROOT="/home/HwHiAiUser/case9-deepseek-20t-experiment"
BOARD_SELECTION="both"
SYNC_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_RUN_ID=""
DRY_RUN=1
NO_SOURCE=0
NO_MODELS=0
NO_REPORTS=0
SKIP_BOARD20=0
SOURCE_REGISTRY="${REPO_DIR}/configs/chat_model_profiles.json"
PROBE_FILE="${REPO_DIR}/tests/fixtures/case9_dual_board_probe.json"

# Exact paths only. New timestamped files can be supplied with --extra-*.
SOURCE_FILES=(
  README.md app.py case9_model_profiles.py config.py local_session.py
  local_model_manifest.json
  mindspore_chat_providers.py mindspore_chat_service.py
  qwen25_kv_acl_contract.py qwen25_kv_acl_runtime.py qwen25_kv_acl_service.py
  text_chat_app.py upstream.py
  scripts/check_mindspore_chat_environment.py scripts/mindspore_chat_acceptance.py
  scripts/qwen25_acceptance.py scripts/run_mindspore_chat_acceptance.sh
  scripts/run_mindspore_chat_service.sh scripts/run_qwen25_kv_acl_service.sh
  scripts/verify_mindspore_profile_artifacts.py scripts/verify_qwen25_repro_bundle.py
  scripts/run_case9_gap_acceptance.sh scripts/sync_case9_gap_bundle.sh
  scripts/verify_case9_completion.py scripts/run_qwen25_dual_board_acceptance.sh
  scripts/sync_qwen25_repro_bundle.sh
  tests/test_case9_gap_acceptance.py tests/test_qwen25_acceptance_tools.py
  tests/test_qwen25_kv_acl_service.py
  tests/fixtures/qwen25_chinese_probe.json
  docs/00-case9-current-runbook.md docs/01-qwen25-dual-board-validation.md
  docs/02-qwen25-reproducibility-and-sync.md docs/03-case9-history-and-boundaries.md
  docs/12-case9-evidence-index.md docs/27-case9-dual-board-gap-completion-plan.md
  docs/28-case9-dual-board-gap-validation-record.md
)
COMMON_BOARD_FILES=(
  artifacts/models/qwen1.5-0.5b-chat/model.safetensors
  artifacts/models/qwen1.5-0.5b-chat/tokenizer.json
  artifacts/models/qwen1.5-0.5b-chat/tokenizer_config.json
  artifacts/models/qwen1.5-0.5b-chat/config.json
  artifacts/models/qwen1.5-0.5b-chat/generation_config.json
  artifacts/models/tinyllama-1.1b-chat/model.safetensors
  artifacts/models/tinyllama-1.1b-chat/tokenizer.json
  artifacts/models/tinyllama-1.1b-chat/tokenizer_config.json
  artifacts/models/tinyllama-1.1b-chat/config.json
  artifacts/models/tinyllama-1.1b-chat/generation_config.json
  artifacts/models/tinyllama-1.1b-chat/special_tokens_map.json
  artifacts/models/tinyllama-1.1b-chat/tokenizer.model
  reports/mindspore-chat/environment/board-system.txt
  reports/mindspore-chat/environment/board-npu.txt
  reports/mindspore-chat/environment/board-python.txt
  reports/mindspore-chat/environment/board-pip-freeze.txt
)
OM_COMMON_FILES=(
  artifacts/qwen25-static-kv-1024-v2.onnx
  artifacts/tokenizer.json artifacts/tokenizer.json.lock.json
)
OM_BOARD8_FILES=(
  artifacts/qwen25-static-kv-1024-v2.om
  artifacts/qwen25-static-kv-1024-v2.om.lock.json
  contracts/qwen25-static-kv-1024-v2-om-contract.json
)
OM_BOARD20_FILES=(
  artifacts/qwen25-static-kv-1024-b1.om
  artifacts/qwen25-static-kv-1024-b1.om.lock.json
  contracts/qwen25-static-kv-1024-b1-om-contract.json
)
REPORT_BASENAMES=(
  acceptance.json metadata.json command.json health.json models.json
  json-smoke.json sse-smoke.json long-output.json stability.json
  performance.json errors.json protocol.json quality.json snapshots.json
  README.txt
)
REPORT_PROFILES=(
  qwen1.5-0.5b-mindspore
  tinyllama-1.1b-mindspore
  deepseek-r1-qwen-1.5b-mindspore
)
declare -a EXTRA_BOARD8=()
declare -a EXTRA_BOARD20=()
declare -a REPORT_SPECS=()
declare -a REPORT_OM_SPECS=()
declare -a LOCAL_REPORT_SPECS=()
declare -a LOCAL_ARTIFACT_SPECS=()
declare -a INGESTED_REPORTS=()

usage() {
  cat <<'EOF'
Usage: bash scripts/sync_case9_gap_bundle.sh [options]

Default mode is dry-run and does not contact a board or create a bundle.
Execute mode copies only the explicit allowlist and writes checksummed files.

  --execute                 transfer files and write the manifest
  --dry-run                 print the plan (default)
  --board 8t|20t|both       board selection (default both)
  --board8-host HOST        default 192.168.1.90
  --board20-host HOST       default 192.168.1.95
  --user NAME               default HwHiAiUser
  --bundle DIR              destination reproducibility directory
  --sync-run-id ID          provenance id
  --report-run-id ID        remote gap acceptance id (default: sync-run-id)
  --board8-root PATH        MindSpore root on 8T
  --board20-root PATH       MindSpore root on 20T
  --board8-om-root PATH     Qwen2.5 OM root on 8T
  --board20-om-root PATH    Qwen2.5 OM root on 20T
  --board8-deepseek-root P  DeepSeek root on 8T
  --board20-deepseek-root P DeepSeek root on 20T
  --source-registry FILE    checked-in registry to copy
  --probe-file FILE         fixed probe fixture to copy
  --extra-board8 REL        add one explicit board8 relative path (repeatable)
  --extra-board20 REL       add one explicit board20 relative path (repeatable)
  --report BOARD MODEL REL  explicitly ingest one report (repeatable; REL is
                            relative to that board's MindSpore root)
  --report-om BOARD MODEL REL explicitly ingest an OM report (REL is relative
                             to that board's OM root)
  --local-report BOARD MODEL PATH ingest a local acceptance.json or report
                             directory (fixed sidecars only; repeatable)
  --local-artifact DEST_REL PATH copy one local model artifact into the bundle
                              (repeatable; destination must be an artifact tree)
  --no-source               do not copy controller source files
  --no-models               do not copy model/OM files
  --no-reports              do not copy default report files
  --skip-board20            do not contact the 20T board
  -h, --help                show this help
EOF
}
die() { echo "sync-case9-gap: $*" >&2; exit 2; }
is_word() { [[ "${1:-}" =~ ^[A-Za-z0-9._-]+$ ]]; }
is_host() { [[ "${1:-}" =~ ^[A-Za-z0-9._:-]+$ ]]; }
is_id() { [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$ ]]; }
is_rel() {
  [[ -n "${1:-}" && "${1}" != /* && "${1}" != *".."* &&
    "${1}" != *" "* && "${1}" != *$'\t'* && "${1}" != *$'\n'* &&
    "${1}" != *'\\'* && "${1}" != *$'\r'* ]]
}
is_root() {
  [[ "${1:-}" =~ ^/home/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+$ ]] &&
    [[ "${1}" != *".."* ]]
}
board_selected() {
  [[ "$1" == board20t && "$SKIP_BOARD20" == 1 ]] && return 1
  [[ "$BOARD_SELECTION" == both ||
     ("$BOARD_SELECTION" == 8t && "$1" == board8t) ||
     ("$BOARD_SELECTION" == 20t && "$1" == board20t) ]]
}
abs_path() {
  if command -v realpath >/dev/null 2>&1; then
    realpath -m -- "$1"
    return
  fi
  # GNU realpath is present in the supported WSL/controller environment. The
  # fallback keeps a lexical absolute path if a minimal shell lacks it.
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$PWD" "$1" ;;
  esac
}

while (($#)); do
  case "$1" in
    --execute) DRY_RUN=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --board) BOARD_SELECTION="${2:?missing --board value}"; shift 2 ;;
    --board8-host) BOARD8_HOST="${2:?missing value}"; shift 2 ;;
    --board20-host) BOARD20_HOST="${2:?missing value}"; shift 2 ;;
    --user) REMOTE_USER="${2:?missing value}"; shift 2 ;;
    --bundle) BUNDLE="${2:?missing value}"; shift 2 ;;
    --sync-run-id|--run-id) SYNC_RUN_ID="${2:?missing value}"; shift 2 ;;
    --report-run-id) REPORT_RUN_ID="${2:?missing value}"; shift 2 ;;
    --board8-root) BOARD8_ROOT="${2:?missing value}"; shift 2 ;;
    --board20-root) BOARD20_ROOT="${2:?missing value}"; shift 2 ;;
    --board8-om-root) BOARD8_OM_ROOT="${2:?missing value}"; shift 2 ;;
    --board20-om-root) BOARD20_OM_ROOT="${2:?missing value}"; shift 2 ;;
    --board8-deepseek-root) BOARD8_DEEPSEEK_ROOT="${2:?missing value}"; shift 2 ;;
    --board20-deepseek-root) BOARD20_DEEPSEEK_ROOT="${2:?missing value}"; shift 2 ;;
    --source-registry) SOURCE_REGISTRY="${2:?missing value}"; shift 2 ;;
    --probe-file) PROBE_FILE="${2:?missing value}"; shift 2 ;;
    --extra-board8) EXTRA_BOARD8+=("${2:?missing value}"); shift 2 ;;
    --extra-board20) EXTRA_BOARD20+=("${2:?missing value}"); shift 2 ;;
    --report) REPORT_SPECS+=("${2:?missing board}"$'\t'"${3:?missing model}"$'\t'"${4:?missing report path}"); shift 4 ;;
    --report-om) REPORT_OM_SPECS+=("${2:?missing board}"$'\t'"${3:?missing model}"$'\t'"${4:?missing report path}"); shift 4 ;;
    --local-report) LOCAL_REPORT_SPECS+=("${2:?missing board}"$'\t'"${3:?missing model}"$'\t'"${4:?missing local report path}"); shift 4 ;;
    --local-artifact) LOCAL_ARTIFACT_SPECS+=("${2:?missing destination}"$'\t'"${3:?missing local artifact path}"); shift 3 ;;
    --no-source) NO_SOURCE=1; shift ;;
    --no-models) NO_MODELS=1; shift ;;
    --no-reports) NO_REPORTS=1; shift ;;
    --skip-board20) SKIP_BOARD20=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$BOARD_SELECTION" == 8t || "$BOARD_SELECTION" == 20t || "$BOARD_SELECTION" == both ]] || die "--board must be 8t, 20t, or both"
is_host "$BOARD8_HOST" || die "unsafe board8 host"
is_host "$BOARD20_HOST" || die "unsafe board20 host"
is_word "$REMOTE_USER" || die "unsafe SSH user"
is_id "$SYNC_RUN_ID" || die "unsafe sync run id"
if [[ -z "$REPORT_RUN_ID" ]]; then REPORT_RUN_ID="$SYNC_RUN_ID"; fi
is_id "$REPORT_RUN_ID" || die "unsafe report run id"
for root in "$BOARD8_ROOT" "$BOARD20_ROOT" "$BOARD8_OM_ROOT" "$BOARD20_OM_ROOT" "$BOARD8_DEEPSEEK_ROOT" "$BOARD20_DEEPSEEK_ROOT"; do
  is_root "$root" || die "unsafe remote root: $root"
done
for rel in "${SOURCE_FILES[@]}" "${COMMON_BOARD_FILES[@]}" "${OM_COMMON_FILES[@]}" "${OM_BOARD8_FILES[@]}" "${OM_BOARD20_FILES[@]}" "${EXTRA_BOARD8[@]}" "${EXTRA_BOARD20[@]}"; do
  is_rel "$rel" || die "unsafe relative path: $rel"
done
for spec in "${REPORT_SPECS[@]}"; do
  IFS=$'\t' read -r report_board report_model report_rel <<< "$spec"
  [[ "$report_board" == board8t || "$report_board" == board20t ]] || die "unsafe report board: $report_board"
  if ! board_selected "$report_board"; then
    printf 'skip explicit report outside board selection: %s/%s\n' "$report_board" "$report_model"
    continue
  fi
  is_id "$report_model" || die "unsafe report model: $report_model"
  is_rel "$report_rel" || die "unsafe report path: $report_rel"
done
for spec in "${REPORT_OM_SPECS[@]}"; do
  IFS=$'\t' read -r report_board report_model report_rel <<< "$spec"
  [[ "$report_board" == board8t || "$report_board" == board20t ]] || die "unsafe OM report board: $report_board"
  if ! board_selected "$report_board"; then
    printf 'skip explicit OM report outside board selection: %s/%s\n' "$report_board" "$report_model"
    continue
  fi
  is_id "$report_model" || die "unsafe OM report model: $report_model"
  is_rel "$report_rel" || die "unsafe OM report path: $report_rel"
done
for spec in "${LOCAL_REPORT_SPECS[@]}"; do
  IFS=$'\t' read -r report_board report_model report_path <<< "$spec"
  [[ "$report_board" == board8t || "$report_board" == board20t ]] || die "unsafe local report board: $report_board"
  is_id "$report_model" || die "unsafe local report model: $report_model"
  [[ -n "$report_path" && "$report_path" != *$'\t'* && "$report_path" != *$'\n'* ]] || die "unsafe local report path"
done
for spec in "${LOCAL_ARTIFACT_SPECS[@]}"; do
  IFS=$'\t' read -r artifact_dest artifact_path <<< "$spec"
  is_rel "$artifact_dest" || die "unsafe local artifact destination: $artifact_dest"
  case "$artifact_dest" in
    artifacts/*|source-model/*|contracts/*|environment/*|boards/*|reports/*) ;;
    *) die "local artifact destination must be under artifacts/, source-model/, contracts/, environment/, boards/, or reports/" ;;
  esac
  case "$artifact_dest" in
    bundle-manifest.json|SHA256SUMS.txt|*.part|*/bundle-manifest.json|*/SHA256SUMS.txt) die "reserved local artifact destination: $artifact_dest" ;;
  esac
  [[ -n "$artifact_path" && "$artifact_path" != *$'\t'* && "$artifact_path" != *$'\n'* ]] || die "unsafe local artifact path"
done
if [[ -z "$BUNDLE" ]]; then BUNDLE="$REPO_DIR/repro/case9-dual-board-gap-$SYNC_RUN_ID"; fi
if [[ "$BUNDLE" != /* ]]; then BUNDLE="$REPO_DIR/$BUNDLE"; fi
BUNDLE="$(abs_path "$BUNDLE")"
[[ "$BUNDLE" != "$REPO_DIR" && "$BUNDLE" != "$REPO_DIR/" ]] || die "bundle must be a child path"
[[ ! -L "$BUNDLE" ]] || die "refusing symlink bundle: $BUNDLE"
if (( NO_SOURCE == 0 )); then
  [[ -f "$SOURCE_REGISTRY" && ! -L "$SOURCE_REGISTRY" ]] || die "source registry is not a regular file: $SOURCE_REGISTRY"
  [[ -f "$PROBE_FILE" && ! -L "$PROBE_FILE" ]] || die "probe file is not a regular file: $PROBE_FILE"
fi

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=2)
if (( DRY_RUN == 0 )); then
  for command in ssh rsync sha256sum python3; do command -v "$command" >/dev/null 2>&1 || die "$command is required in execute mode"; done
  mkdir -p "$BUNDLE"
fi
echo "Case9 gap bundle: $BUNDLE"
echo "boards: selection=$BOARD_SELECTION board8=$BOARD8_HOST board20=$BOARD20_HOST"
echo "policy: explicit allowlist; rsync --partial --append-verify; no recursive copy; no --delete"

copy_local_file() {
  local source="$1" dest_rel="$2" dest="$BUNDLE/$2" part
  if (( DRY_RUN )); then printf 'DRY-RUN source %s <- %s\n' "$dest" "$source"; return; fi
  [[ -f "$source" && ! -L "$source" ]] || die "local source is not a regular file: $source"
  mkdir -p "$(dirname "$dest")"
  [[ ! -L "$dest" && ! -L "${dest}.part" ]] || die "refusing symlink destination: $dest"
  part="${dest}.part"; rm -f -- "$part"
  cp -- "$source" "$part"
  local expected_size actual_size expected_sha actual_sha
  expected_size="$(wc -c < "$source" | tr -d '[:space:]')"
  expected_sha="$(sha256sum "$source" | cut -d ' ' -f1)"
  actual_size="$(wc -c < "$part" | tr -d '[:space:]')"
  actual_sha="$(sha256sum "$part" | cut -d ' ' -f1)"
  [[ "$actual_size" == "$expected_size" && "$actual_sha" == "$expected_sha" ]] || die "local source verification failed: $dest_rel"
  mv -f -- "$part" "$dest"
}

sync_local() {
  local source="$1" rel="$2"
  copy_local_file "$source" "source/$rel"
}

sync_local_report() {
  local board="$1" model="$2" source_path="$3" basename dest_rel
  if (( DRY_RUN )); then
    printf 'DRY-RUN local report %s/%s <- %s\n' "$board" "$model" "$source_path"
    return
  fi
  if [[ -d "$source_path" && ! -L "$source_path" ]]; then
    for basename in "${REPORT_BASENAMES[@]}"; do
      [[ -f "$source_path/$basename" && ! -L "$source_path/$basename" ]] || continue
      dest_rel="reports/$board/$model/$REPORT_RUN_ID/$basename"
      copy_local_file "$source_path/$basename" "$dest_rel"
      [[ "$basename" == acceptance.json ]] && INGESTED_REPORTS+=("$board"$'\t'"$model"$'\t'"$dest_rel")
    done
    [[ -f "$source_path/acceptance.json" ]] || printf '[%s] local report directory has no acceptance.json: %s\n' "$board" "$source_path"
  elif [[ -f "$source_path" && ! -L "$source_path" ]]; then
    dest_rel="reports/$board/$model/$REPORT_RUN_ID/$(basename "$source_path")"
    copy_local_file "$source_path" "$dest_rel"
    [[ "$(basename "$source_path")" == acceptance.json ]] && INGESTED_REPORTS+=("$board"$'\t'"$model"$'\t'"$dest_rel")
  else
    die "local report must be a regular file or directory: $source_path"
  fi
}

sync_remote() {
  local board="$1" host="$2" remote_root="$3" rel="$4" dest_rel="$5"
  local remote="${remote_root%/}/$rel" dest="$BUNDLE/$dest_rel" part expected_size expected_sha actual_size actual_sha
  if (( DRY_RUN )); then printf 'DRY-RUN %s <- %s@%s:%s\n' "$dest" "$REMOTE_USER" "$host" "$remote"; return; fi
  mkdir -p "$(dirname "$dest")"
  [[ ! -L "$dest" && ! -L "${dest}.part" ]] || die "refusing symlink destination: $dest"
  expected_size="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "test -f '$remote' && stat -c '%s' '$remote'")" || die "remote file unavailable: $host:$remote"
  expected_sha="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "sha256sum '$remote' | cut -d ' ' -f1")" || die "remote hash failed: $host:$remote"
  [[ "$expected_size" =~ ^[0-9]+$ && "$expected_sha" =~ ^[[:xdigit:]]{64}$ ]] || die "invalid remote metadata: $host:$remote"
  if [[ -f "$dest" && ! -L "$dest" ]]; then
    actual_size="$(wc -c < "$dest" | tr -d '[:space:]')"; actual_sha="$(sha256sum "$dest" | cut -d ' ' -f1)"
    if [[ "$actual_size" == "$expected_size" && "$actual_sha" == "$expected_sha" ]]; then return; fi
  fi
  part="${dest}.part"; rm -f -- "$part"
  rsync -a --partial --append-verify -e "ssh ${SSH_OPTS[*]}" "$REMOTE_USER@$host:$remote" "$part" || die "rsync failed: $host:$remote"
  actual_size="$(wc -c < "$part" | tr -d '[:space:]')"; actual_sha="$(sha256sum "$part" | cut -d ' ' -f1)"
  [[ "$actual_size" == "$expected_size" ]] || die "size mismatch: $dest_rel"
  [[ "$actual_sha" == "$expected_sha" ]] || die "SHA-256 mismatch: $dest_rel"
  mv -f -- "$part" "$dest"
}

remote_exists() {
  local host="$1" remote_root="$2" rel="$3"
  local remote="${remote_root%/}/$rel"
  ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "test -f '$remote'"
}

sync_remote_optional() {
  local board="$1" host="$2" remote_root="$3" rel="$4" dest_rel="$5"
  if remote_exists "$host" "$remote_root" "$rel"; then
    sync_remote "$board" "$host" "$remote_root" "$rel" "$dest_rel"
  else
    printf '[%s] optional report not present: %s@%s:%s\n' "$board" "$REMOTE_USER" "$host" "${remote_root%/}/$rel"
  fi
}

sync_report_file() {
  local board="$1" host="$2" remote_root="$3" model="$4" rel="$5" dest_rel="$6"
  sync_remote "$board" "$host" "$remote_root" "$rel" "$dest_rel"
  INGESTED_REPORTS+=("$board"$'\t'"$model"$'\t'"$dest_rel")
}

sync_default_reports() {
  local board="$1" host="$2" remote_root="$3" om_root="$4" profile base rel dest basename
  # Both acceptance helpers write their report directory below the deployment
  # root's repository-local reports/mindspore-chat tree.  The run/ tree only
  # contains temporary registry/probe state and must never be used as evidence.
  base="reports/mindspore-chat/case9-gap/$REPORT_RUN_ID"
  # The acceptance helpers write acceptance.json plus a fixed set of
  # sidecars. acceptance.json is optional here so a partial campaign can be
  # synchronized and represented as not-run; explicit --report is strict.
  for profile in "${REPORT_PROFILES[@]}"; do
    rel="$base/$profile/acceptance.json"
    dest="reports/$board/$profile/$REPORT_RUN_ID/acceptance.json"
    if (( DRY_RUN )); then
      printf 'DRY-RUN default report %s/%s <- %s@%s:%s\n' "$board" "$profile" "$REMOTE_USER" "$host" "${remote_root%/}/$rel"
      continue
    fi
    if remote_exists "$host" "$remote_root" "$rel"; then
      sync_report_file "$board" "$host" "$remote_root" "$profile" "$rel" "$dest"
    else
      printf '[%s] acceptance report not present (kept not-run): %s\n' "$board" "$rel"
      continue
    fi
    for basename in "${REPORT_BASENAMES[@]}"; do
      [[ "$basename" == acceptance.json ]] && continue
      rel="$base/$profile/$basename"
      dest="reports/$board/$profile/$REPORT_RUN_ID/$basename"
      sync_remote_optional "$board" "$host" "$remote_root" "$rel" "$dest"
    done
  done
  rel="$base/qwen25-onnx-om/acceptance.json"
  dest="reports/$board/qwen25-onnx-om/$REPORT_RUN_ID/acceptance.json"
  if (( DRY_RUN )); then
    printf 'DRY-RUN default OM report %s/qwen25-onnx-om <- %s@%s:%s\n' "$board" "$REMOTE_USER" "$host" "${om_root%/}/$rel"
    return
  fi
  if remote_exists "$host" "$om_root" "$rel"; then
    sync_report_file "$board" "$host" "$om_root" "qwen25-onnx-om" "$rel" "$dest"
    for basename in "${REPORT_BASENAMES[@]}"; do
      [[ "$basename" == acceptance.json ]] && continue
      rel="$base/qwen25-onnx-om/$basename"
      dest="reports/$board/qwen25-onnx-om/$REPORT_RUN_ID/$basename"
      sync_remote_optional "$board" "$host" "$om_root" "$rel" "$dest"
    done
  else
    printf '[%s] OM acceptance report not present (kept not-run): %s\n' "$board" "${om_root%/}/$rel"
  fi
}

if (( NO_SOURCE == 0 )); then
  for rel in "${SOURCE_FILES[@]}"; do sync_local "$REPO_DIR/$rel" "$rel"; done
  sync_local "$SOURCE_REGISTRY" "configs/chat_model_profiles.json"
  sync_local "$PROBE_FILE" "tests/fixtures/case9_dual_board_probe.json"
fi

for spec in "${LOCAL_REPORT_SPECS[@]}"; do
  IFS=$'\t' read -r report_board report_model report_path <<< "$spec"
  if ! board_selected "$report_board"; then
    printf 'skip local report outside board selection: %s/%s\n' "$report_board" "$report_model"
    continue
  fi
  if (( DRY_RUN )); then
    printf 'DRY-RUN local report %s/%s <- %s\n' "$report_board" "$report_model" "$report_path"
  else
    sync_local_report "$report_board" "$report_model" "$report_path"
  fi
done

for spec in "${LOCAL_ARTIFACT_SPECS[@]}"; do
  IFS=$'\t' read -r artifact_dest artifact_path <<< "$spec"
  copy_local_file "$artifact_path" "$artifact_dest"
done

sync_board() {
  local board="$1" host="$2" ms_root="$3" om_root="$4" deepseek_root="$5" rel soc
  soc="Ascend310B1"; [[ "$board" == board8t ]] && soc="Ascend310B4"
  if (( NO_MODELS == 0 )); then
    for rel in "${COMMON_BOARD_FILES[@]}"; do sync_remote "$board" "$host" "$ms_root" "$rel" "boards/$board/$rel"; done
    for rel in "${OM_COMMON_FILES[@]}"; do sync_remote "$board" "$host" "$om_root" "$rel" "boards/$board/artifacts/om/$soc/${rel##*/}"; done
    if [[ "$board" == board8t ]]; then
      for rel in "${OM_BOARD8_FILES[@]}"; do sync_remote "$board" "$host" "$om_root" "$rel" "boards/$board/artifacts/om/$soc/${rel##*/}"; done
    else
      for rel in "${OM_BOARD20_FILES[@]}"; do sync_remote "$board" "$host" "$om_root" "$rel" "boards/$board/artifacts/om/$soc/${rel##*/}"; done
    fi
    sync_remote "$board" "$host" "$deepseek_root" "model/model.safetensors" "boards/$board/source-model/deepseek/model.safetensors"
    sync_remote "$board" "$host" "$deepseek_root" "model/tokenizer.json" "boards/$board/source-model/deepseek/tokenizer.json"
    sync_remote "$board" "$host" "$deepseek_root" "model/config.json" "boards/$board/source-model/deepseek/config.json"
  fi
  if (( NO_REPORTS == 0 )); then
    sync_default_reports "$board" "$host" "$ms_root" "$om_root"
  fi
  if [[ "$board" == board8t ]]; then
    for rel in "${EXTRA_BOARD8[@]}"; do sync_remote "$board" "$host" "$ms_root" "$rel" "reports/$board/extra/${rel##*/}"; done
  else
    for rel in "${EXTRA_BOARD20[@]}"; do sync_remote "$board" "$host" "$ms_root" "$rel" "reports/$board/extra/${rel##*/}"; done
  fi
}
for spec in "${REPORT_SPECS[@]}"; do
  IFS=$'\t' read -r report_board report_model report_rel <<< "$spec"
  if ! board_selected "$report_board"; then
    printf 'skip explicit report outside board selection: %s/%s\n' "$report_board" "$report_model"
    continue
  fi
  if [[ "$report_board" == board8t ]]; then
    report_host="$BOARD8_HOST"; report_root="$BOARD8_ROOT"
  else
    report_host="$BOARD20_HOST"; report_root="$BOARD20_ROOT"
  fi
  report_dest="reports/$report_board/$report_model/$REPORT_RUN_ID/$(basename "$report_rel")"
  if (( DRY_RUN )); then
    printf 'DRY-RUN explicit report %s/%s <- %s@%s:%s\n' "$report_board" "$report_model" "$REMOTE_USER" "$report_host" "${report_root%/}/$report_rel"
  else
    sync_report_file "$report_board" "$report_host" "$report_root" "$report_model" "$report_rel" "$report_dest"
  fi
done
for spec in "${REPORT_OM_SPECS[@]}"; do
  IFS=$'\t' read -r report_board report_model report_rel <<< "$spec"
  if ! board_selected "$report_board"; then
    printf 'skip explicit OM report outside board selection: %s/%s\n' "$report_board" "$report_model"
    continue
  fi
  if [[ "$report_board" == board8t ]]; then
    report_host="$BOARD8_HOST"; report_root="$BOARD8_OM_ROOT"
  else
    report_host="$BOARD20_HOST"; report_root="$BOARD20_OM_ROOT"
  fi
  report_dest="reports/$report_board/$report_model/$REPORT_RUN_ID/$(basename "$report_rel")"
  if (( DRY_RUN )); then
    printf 'DRY-RUN explicit OM report %s/%s <- %s@%s:%s\n' "$report_board" "$report_model" "$REMOTE_USER" "$report_host" "${report_root%/}/$report_rel"
  else
    sync_report_file "$report_board" "$report_host" "$report_root" "$report_model" "$report_rel" "$report_dest"
  fi
done
if [[ "$BOARD_SELECTION" == 8t || "$BOARD_SELECTION" == both ]]; then sync_board board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$BOARD8_OM_ROOT" "$BOARD8_DEEPSEEK_ROOT"; fi
if [[ "$SKIP_BOARD20" == 1 ]]; then echo "board20 skipped by request"; elif [[ "$BOARD_SELECTION" == 20t || "$BOARD_SELECTION" == both ]]; then sync_board board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$BOARD20_OM_ROOT" "$BOARD20_DEEPSEEK_ROOT"; fi
if (( DRY_RUN )); then echo "dry-run complete; no SSH, files, or manifests were changed"; exit 0; fi

# Build a deterministic manifest after all transfers pass. Existing files are
# retained and checksummed; no deletion is performed.
python3 - "$BUNDLE" "$SYNC_RUN_ID" "$BOARD8_HOST" "$BOARD20_HOST" "$BOARD_SELECTION" "$SKIP_BOARD20" "$REPORT_RUN_ID" "$BOARD8_ROOT" "$BOARD20_ROOT" "$BOARD8_OM_ROOT" "$BOARD20_OM_ROOT" "${INGESTED_REPORTS[@]}" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
root = Path(sys.argv[1]).resolve()
run_id, board8, board20, selection, skipped, report_run_id = sys.argv[2:8]
board8_root, board20_root, board8_om_root, board20_om_root = sys.argv[8:12]
ingested_specs = sys.argv[12:]
excluded = {"bundle-manifest.json", "SHA256SUMS.txt", "bundle-manifest.json.part", "SHA256SUMS.txt.part"}
files = []
for path in sorted(item for item in root.rglob("*") if item.is_file() and not item.is_symlink() and not item.name.endswith(".part")):
    relative = path.relative_to(root).as_posix()
    if relative in excluded:
        continue
    state = hashlib.sha256(); size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            state.update(block); size += len(block)
    files.append({"path": relative, "bytes": size, "sha256": state.hexdigest()})
models = ("qwen25-onnx-om", "qwen1.5-0.5b-mindspore", "tinyllama-1.1b-mindspore", "deepseek-r1-qwen-1.5b-mindspore")
matrix_by_key = {
    (board, model): {
        "board": board,
        "model": model,
        "status": "not-run",
        "reason": "gap acceptance report must be attached explicitly",
    }
    for board in ("board8t", "board20t")
    for model in models
}
aliases = {
    "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om": "qwen25-onnx-om",
    "qwen25-static-kv-1024": "qwen25-onnx-om",
}
# Preserve statuses from an earlier sync of the same bundle so separate
# board/model campaigns can be accumulated without rewriting prior evidence.
previous_matrix = {}
previous_ingested = []
previous_manifest = root / "bundle-manifest.json"
if previous_manifest.is_file() and not previous_manifest.is_symlink():
    try:
        old = json.loads(previous_manifest.read_text(encoding="utf-8"))
        if isinstance(old, dict):
            for item in old.get("matrix", []):
                if not isinstance(item, dict):
                    continue
                board = item.get("board")
                model = aliases.get(item.get("model"), item.get("model"))
                if (board, model) in matrix_by_key:
                    previous_matrix[(board, model)] = dict(item)
            if isinstance(old.get("ingested_reports"), list):
                previous_ingested = [item for item in old["ingested_reports"] if isinstance(item, dict)]
    except Exception:
        # A malformed prior manifest must not prevent a fresh bundle from
        # being generated; the new manifest will be checked by the verifier.
        previous_matrix = {}
        previous_ingested = []
for key, item in previous_matrix.items():
    matrix_by_key[key] = item
ingested = []
for spec in ingested_specs:
    parts = spec.split("\t", 2)
    if len(parts) != 3:
        continue
    board, model, relative = parts
    model = aliases.get(model, model)
    key = (board, model)
    if key not in matrix_by_key:
        continue
    path = root / relative
    status = "failed"
    reason = "report could not be parsed"
    report_status = None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        report_status = payload.get("status") if isinstance(payload, dict) else None
        if report_status == "passed":
            status = "passed"
            reason = None
        elif report_status == "blocked":
            status = "blocked"
            reason = str(payload.get("reason") or payload.get("error") or "report marked blocked")
        else:
            status = "failed"
            reason = str(payload.get("error") or payload.get("reason") or "acceptance report status: %s" % report_status)
    except Exception as exc:
        reason = "report parse error: %s" % exc
    record = {
        "board": board,
        "model": model,
        "status": status,
        "report": relative,
        "source_report_status": report_status,
    }
    if reason:
        record["reason"] = reason
    matrix_by_key[key] = record
    ingested.append({"board": board, "model": model, "report": relative, "status": status})
matrix = [matrix_by_key[(board, model)] for board in ("board8t", "board20t") for model in models]
all_ingested = []
seen_ingested = set()
for item in previous_ingested + ingested:
    key = (item.get("board"), item.get("model"), item.get("report"))
    if key in seen_ingested:
        continue
    seen_ingested.add(key)
    all_ingested.append(item)
manifest = {
    "schema_version": 1, "bundle": "case9-dual-board-gap", "sync_run_id": run_id,
    "report_run_id": report_run_id,
    "remote_roots": {
        "board8t": {"host": board8, "mindspore": board8_root, "om": board8_om_root},
        "board20t": {"host": board20, "mindspore": board20_root, "om": board20_om_root},
    },
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "allowlist_policy": "explicit paths only; rsync --partial --append-verify; no recursive home copy; no --delete; .part size SHA-256 atomic rename",
    "boards": {
        "board8t": {"host": board8, "soc": "Ascend310B4", "tier": "8T", "status": "selected" if selection in ("8t", "both") else "not-selected"},
        "board20t": {"host": board20, "soc": "Ascend310B1", "tier": "20T", "status": "skipped" if skipped == "1" or selection == "8t" else "selected"},
    },
    "matrix": matrix, "required_files": files, "ingested_reports": all_ingested,
}
(root / "SHA256SUMS.txt.part").write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in files), encoding="ascii")
(root / "SHA256SUMS.txt.part").replace(root / "SHA256SUMS.txt")
(root / "bundle-manifest.json.part").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
(root / "bundle-manifest.json.part").replace(root / "bundle-manifest.json")
print("wrote %d checksummed files and %d matrix entries" % (len(files), len(matrix)))
PY
echo "gap bundle synchronization complete: $BUNDLE"
