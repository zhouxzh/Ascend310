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
BOARD20_HOST="192.168.8.210"
REMOTE_USER="HwHiAiUser"
REMOTE_ROOT="/home/HwHiAiUser/case9-mindspore-chat"
SOURCE_ALLOWLIST="${CASE9_MINDSPORE_SOURCE_ALLOWLIST:-$REPO_DIR/configs/mindspore_chat_source_allowlist.txt}"
SKIP_BOARD20=0
DRY_RUN=0
SYNC_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

usage() {
  cat <<'EOF'
Usage: bash scripts/sync_mindspore_chat_repro_bundle.sh [options]

Synchronize an explicit allowlist from the 8T and 20T MindSpore chat boards.
The destination is a Git-ignored reproducibility bundle.

  --bundle DIR          destination bundle (default: repro/mindspore-chat-20260829)
  --board8-host HOST    8T board (default: 192.168.1.90)
  --board20-host HOST   20T board (default: 192.168.8.210)
  --user NAME           SSH user (default: HwHiAiUser)
  --remote-root PATH    board project root (default: /home/HwHiAiUser/case9-mindspore-chat)
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
    --source-allowlist) [[ $# -ge 2 ]] || die "--source-allowlist requires a value"; SOURCE_ALLOWLIST="$2"; shift 2 ;;
    --sync-run-id) [[ $# -ge 2 ]] || die "--sync-run-id requires a value"; SYNC_RUN_ID="$2"; shift 2 ;;
    --skip-board20) SKIP_BOARD20=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
is_host "$BOARD8_HOST" || die "unsafe board8 host"
is_host "$BOARD20_HOST" || die "unsafe board20 host"
is_word "$REMOTE_USER" || die "unsafe SSH user"
is_root "$REMOTE_ROOT" || die "unsafe remote root"
is_word "$SYNC_RUN_ID" || die "unsafe sync run id"
if [[ "$SOURCE_ALLOWLIST" != /* ]]; then
  SOURCE_ALLOWLIST="$REPO_DIR/$SOURCE_ALLOWLIST"
fi
[[ -f "$SOURCE_ALLOWLIST" && ! -L "$SOURCE_ALLOWLIST" ]] || die "source allowlist is not a regular file: $SOURCE_ALLOWLIST"

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

# Every entry below is an exact relative path. Keep this list intentionally
# boring and reviewable; adding a wildcard or a recursive copy is prohibited.
BOARD8_FILES=(
  artifacts/models/qwen1.5-0.5b-chat/model.safetensors
  artifacts/models/qwen1.5-0.5b-chat/tokenizer.json
  artifacts/models/qwen1.5-0.5b-chat/tokenizer_config.json
  artifacts/models/qwen1.5-0.5b-chat/config.json
  artifacts/models/qwen1.5-0.5b-chat/generation_config.json
  artifacts/models/qwen1.5-0.5b-chat/vocab.json
  artifacts/models/qwen1.5-0.5b-chat/merges.txt
  artifacts/models/tinyllama-1.1b-chat/model.safetensors
  artifacts/models/tinyllama-1.1b-chat/tokenizer.json
  artifacts/models/tinyllama-1.1b-chat/tokenizer_config.json
  artifacts/models/tinyllama-1.1b-chat/config.json
  artifacts/models/tinyllama-1.1b-chat/generation_config.json
  artifacts/models/tinyllama-1.1b-chat/special_tokens_map.json
  artifacts/models/tinyllama-1.1b-chat/tokenizer.model
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

# The 20T is currently unreachable. Its allowlist is deliberately reports and
# environment evidence only; model weights are not copied from that board.
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
}

transfer_one() {
  local host="$1" board="$2" rel="$3"
  local remote="${REMOTE_ROOT%/}/$rel"
  local dest="$BUNDLE/$board/$rel"
  local part="${dest}.part"
  local expected_bytes expected_sha actual_bytes actual_sha
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
      RECORDS+=("$board|$rel|$expected_bytes|$expected_sha|$host|$remote")
      return
    fi
  fi
  rsync -a --partial --append-verify -e "ssh ${SSH_OPTS[*]}" "${REMOTE_USER}@${host}:${remote}" "$part" || die "rsync failed: $host:$rel"
  actual_bytes="$(stat -c '%s' "$part" 2>/dev/null || wc -c < "$part")"
  actual_sha="$(sha256sum "$part" | cut -d ' ' -f1)"
  [[ "$actual_bytes" == "$expected_bytes" ]] || die "size mismatch: $board/$rel"
  [[ "$actual_sha" == "$expected_sha" ]] || die "SHA-256 mismatch: $board/$rel"
  mv -f -- "$part" "$dest"
  RECORDS+=("$board|$rel|$expected_bytes|$expected_sha|$host|$remote")
}

echo "MindSpore chat bundle: $BUNDLE"
echo "Allowlist: source=${#SOURCE_FILES[@]} files, board8=${#BOARD8_FILES[@]} files, board20=${#BOARD20_FILES[@]} files"
for rel in "${SOURCE_FILES[@]}"; do sync_local_source "$rel"; done
for rel in "${BOARD8_FILES[@]}"; do transfer_one "$BOARD8_HOST" board8t "$rel"; done
if ((SKIP_BOARD20)); then
  echo "Skipping board20 ($BOARD20_HOST) by request"
else
  for rel in "${BOARD20_FILES[@]}"; do transfer_one "$BOARD20_HOST" board20t "$rel"; done
fi

if ((DRY_RUN)); then
  echo "Dry run complete; no files or manifests were changed."
  exit 0
fi

# Build the exact set managed by this run. Older bundle layouts are retained
# rather than deleted, but the manifest distinguishes them from this explicit
# source/board snapshot so they cannot be mistaken for current inputs.
MANAGED_LIST="$(mktemp "${TMPDIR:-/tmp}/case9-mindspore-managed.XXXXXX")"
trap 'rm -f -- "$MANAGED_LIST"' EXIT
{
  for rel in "${SOURCE_FILES[@]}"; do printf 'source/%s\n' "$rel"; done
  for rel in "${BOARD8_FILES[@]}"; do printf 'board8t/%s\n' "$rel"; done
  if ((SKIP_BOARD20 == 0)); then
    for rel in "${BOARD20_FILES[@]}"; do printf 'board20t/%s\n' "$rel"; done
  fi
} > "$MANAGED_LIST"

# Build deterministic checksums and a machine-readable manifest only after all
# transfers have passed. Existing historical files remain untouched and are
# still checksummed, but are marked legacy_retained rather than current input.
python3 - "$BUNDLE" "$SYNC_RUN_ID" "$BOARD8_HOST" "$BOARD20_HOST" "$SKIP_BOARD20" "$MANAGED_LIST" <<'PY'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
run_id, board8, board20, skipped, managed_list_path = sys.argv[2:]
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
    if source_host and rel in managed:
        entry.update({
            "source_board": board,
            "source_host": source_host,
            "source_root": "/home/HwHiAiUser/case9-mindspore-chat",
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
    "scope": {
        "current_managed_entries": len(managed_paths),
        "current_source_entries": current_counts["source"],
        "current_board8t_entries": current_counts["board8t"],
        "current_board20t_entries": current_counts["board20t"],
        "legacy_retained_entries": legacy_count,
        "legacy_policy": "Retained historical bundle files are SHA-256 checked but are not current allowlist inputs.",
    },
    "sources": {
        "board8t": {"host": board8, "remote_root": "/home/HwHiAiUser/case9-mindspore-chat", "status": "synced"},
        "board20t": {"host": board20, "remote_root": "/home/HwHiAiUser/case9-mindspore-chat", "status": "skipped" if skipped == "1" else "attempted"},
    },
    "files": files,
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
