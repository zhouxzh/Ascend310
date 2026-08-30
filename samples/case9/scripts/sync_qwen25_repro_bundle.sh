#!/usr/bin/env bash
# Synchronize an explicit Qwen2.5 artifact allowlist from both boards.
# Every remote file uses metadata -> .part transfer -> size/SHA verification
# -> atomic rename. No recursive copy or --delete is performed.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUNDLE="$REPO_DIR/repro/qwen25-kv1024-dual-board-20260827"
USER_NAME="HwHiAiUser"
BOARD8_HOST="192.168.1.90"
BOARD20_HOST="192.168.8.210"
# These defaults are for the archived historical layout.  The current
# candidate roots are intentionally passed explicitly by the canonical
# runbook, because a historical campaign stores 20T files below run/replacement.
BOARD8_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024-20260825"
BOARD20_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024-20260827-20t"
BOARD20_RUN_ID="20260827T045500Z"
SOURCE_MODEL_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024-20260825"
LAYOUT="historical"
DRY_RUN=0
NO_SOURCE=0
NO_SOURCE_MODEL=0
NO_REPORTS=0
OFFLINE_BOARD8=0
OFFLINE_BOARD20=0
SYNC_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
CAMPAIGN_RUN_ID=""
BOARD8_EVIDENCE_REL=()
BOARD20_EVIDENCE_REL=()
LOCAL_EVIDENCE_REL=()
QWEN_MODEL_ID="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"

usage() {
  cat <<'EOF'
Usage: bash scripts/sync_qwen25_repro_bundle.sh [options]

The allowlist contains common ONNX/checkpoint/tokenizer files, native B4 and
B1 OM/contract/lock files, selected board reports, and local source files.
No recursive copy or --delete is used.

  --bundle DIR              destination bundle (default: repro/...)
  --user NAME               default HwHiAiUser
  --board8-host IP          default 192.168.1.90
  --board20-host IP         default 192.168.8.210
  --board8-root PATH        source root below /home/NAME (pass current candidate root explicitly)
  --board20-root PATH       source root below /home/NAME (pass current candidate root explicitly)
  --layout NAME             historical or candidate (default: historical)
  --source-model-root PATH  board8 source checkpoint root for candidate layout
  --board20-run-id ID       historical 20T report run directory, default 20260827T045500Z
  --sync-run-id ID          provenance batch identifier (default: current UTC)
  --campaign-run-id ID      acceptance campaign report ID (alias: --run-id)
  --board8-evidence-rel P   optional explicit reports/... or logs/... file; repeatable
  --board20-evidence-rel P  optional explicit reports/... or logs/... file; repeatable
  --local-evidence-rel P    manifest-track an existing local reports/... or logs/... file; repeatable
  --offline-board8          skip 8T SSH transfers; require and preserve locally verified B4 entries
  --offline-board20         skip 20T SSH transfers; require and preserve locally verified B1 entries
  --no-source-model         skip board source-checkpoint transfers; preserve existing local entries
  --no-source               do not copy local source files
  --no-reports              do not copy selected campaign reports
  --dry-run                 print the allowlist and transfer commands
EOF
}

die() { echo "sync: $*" >&2; exit 2; }
is_word() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }
is_host() { [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; }
is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }
is_rel() {
  [[ "$1" =~ ^[A-Za-z0-9._/-]+$ && "$1" != /* && "$1" != *".."* ]]
}
is_root() {
  [[ "$1" =~ ^/home/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+$ ]] &&
    [[ "$1" != *".."* ]]
}
is_evidence_rel() {
  is_rel "$1" && { [[ "$1" == reports/* || "$1" == logs/* ]]; }
}
assert_plain_destination() {
  local path="$1"
  [[ ! -L "$path" ]] || die "refusing to overwrite symlink destination: $path"
}

while (($#)); do
  case "$1" in
    --bundle) BUNDLE="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --board8-host) BOARD8_HOST="$2"; shift 2 ;;
    --board20-host) BOARD20_HOST="$2"; shift 2 ;;
    --board8-root) BOARD8_ROOT="$2"; shift 2 ;;
    --board20-root) BOARD20_ROOT="$2"; shift 2 ;;
    --layout) LAYOUT="$2"; shift 2 ;;
    --source-model-root) SOURCE_MODEL_ROOT="$2"; shift 2 ;;
    --board20-run-id) BOARD20_RUN_ID="$2"; shift 2 ;;
    --sync-run-id) SYNC_RUN_ID="$2"; shift 2 ;;
    --campaign-run-id|--run-id) CAMPAIGN_RUN_ID="$2"; shift 2 ;;
    --board8-evidence-rel) BOARD8_EVIDENCE_REL+=("$2"); shift 2 ;;
    --board20-evidence-rel) BOARD20_EVIDENCE_REL+=("$2"); shift 2 ;;
    --local-evidence-rel) LOCAL_EVIDENCE_REL+=("$2"); shift 2 ;;
    --offline-board8) OFFLINE_BOARD8=1; shift ;;
    --offline-board20) OFFLINE_BOARD20=1; shift ;;
    --no-source-model) NO_SOURCE_MODEL=1; shift ;;
    --no-source) NO_SOURCE=1; shift ;;
    --no-reports) NO_REPORTS=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
is_word "$USER_NAME" || die "unsafe user"
is_host "$BOARD8_HOST" || die "unsafe board8 host"
is_host "$BOARD20_HOST" || die "unsafe board20 host"
is_word "$BOARD20_RUN_ID" || die "unsafe board20 run id"
is_word "$SYNC_RUN_ID" || die "unsafe sync run id"
if [[ -n "$CAMPAIGN_RUN_ID" ]]; then
  is_word "$CAMPAIGN_RUN_ID" || die "unsafe campaign run id"
fi
is_root "$BOARD8_ROOT" || die "unsafe board8 root"
is_root "$BOARD20_ROOT" || die "unsafe board20 root"
is_root "$SOURCE_MODEL_ROOT" || die "unsafe source model root"
[[ "$LAYOUT" == historical || "$LAYOUT" == candidate ]] || die "--layout must be historical or candidate"
for rel in "${BOARD8_EVIDENCE_REL[@]}"; do
  is_evidence_rel "$rel" || die "board8 evidence must be an explicit reports/... or logs/... path"
done
for rel in "${BOARD20_EVIDENCE_REL[@]}"; do
  is_evidence_rel "$rel" || die "board20 evidence must be an explicit reports/... or logs/... path"
done
for rel in "${LOCAL_EVIDENCE_REL[@]}"; do
  is_evidence_rel "$rel" || die "local evidence must be an explicit reports/... or logs/... path"
done
for command in ssh scp rsync sha256sum python3; do command -v "$command" >/dev/null || die "$command is required"; done
# Fixed options are intentionally kept as one constant; there is no user
# supplied shell fragment in this value.
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=6"

COMMON_REMOTE='artifacts/qwen25-static-kv-1024-v2.onnx
artifacts/tokenizer.json
artifacts/tokenizer_config.json'
SOURCE_MODEL_REMOTE='source-model/model.safetensors
source-model/config.json
source-model/generation_config.json
source-model/tokenizer.json
source-model/tokenizer_config.json
source-model/LICENSE'
CONTRACT8_REMOTE='contracts/qwen25-static-kv-1024-fp32-contract.json
contracts/qwen25-static-kv-1024-v2-controller-contract.json
contracts/qwen25-static-kv-1024-v2-om-contract.json'
REPORT8_REMOTE='reports/qwen25-static-kv-1024-fp32-inspect.json'
BOARD8_REMOTE='artifacts/qwen25-static-kv-1024-v2.om
artifacts/qwen25-static-kv-1024-v2.om.lock.json'
# The 20T run is stored below run/replacement.  Public common artifacts are
# sourced from board8t once and cross-checked in the generated manifest.
BOARD20_RUN_REMOTE='artifacts/qwen25-static-kv-1024-b1.om
artifacts/qwen25-static-kv-1024-b1.om.lock.json'
BOARD20_CONTRACT_REMOTE='contracts/qwen25-static-kv-1024-v2-om-contract.json
contracts/qwen25-static-kv-1024-fp32-contract.json'
BOARD20_REPORT_REMOTE='reports/20260827T054614Z-acl-smoke.txt
reports/20260827T054614Z-smoke-after.txt
reports/20260827T054614Z-smoke-before.txt
reports/20260827T054614Z-smoke-during.txt
reports/base-overlay-check.txt
reports/benchmark-json-1warmup-5.json
reports/benchmark-json-after.txt
reports/benchmark-json-before.txt
reports/benchmark-json-during.txt
reports/benchmark-sse-1warmup-5.json
reports/benchmark-sse-after.txt
reports/benchmark-sse-before.txt
reports/benchmark-sse-during.txt
reports/qwen25-static-kv-1024-fp32-inspect.json
reports/service-postcheck.txt
reports/system-20260827T052949Z.txt'
BOARD20_LOG_REMOTE='logs/atc-20260827T053058Z.log
logs/service-8084.log
logs/service-launcher.log'
LOCAL_SOURCE='app.py
config.py
upstream.py
retrieval.py
text_chat_app.py
local_session.py
local_app.py
audio_io.py
qwen25_kv_acl_runtime.py
qwen25_kv_acl_service.py
qwen25_kv_acl_contract.py
qwen25_kv_tokenizer.py
scripts/serve_qwen25_kv_acl.py
scripts/run_qwen25_kv_acl_service.sh
scripts/provision_qwen25_kv102_board.sh
scripts/qwen25_acceptance.py
scripts/run_qwen25_dual_board_acceptance.sh
scripts/sync_qwen25_repro_bundle.sh
scripts/prepare_qwen25_dual_board_run.sh
scripts/run_qwen25_kv102_gateway.sh
scripts/run_qwen25_kv102_text_chat.sh
scripts/run_xiaozhi_gateway.sh
scripts/run_text_chat.sh
scripts/verify_qwen25_repro_bundle.py
tests/fixtures/qwen25_chinese_probe.json
tests/test_gateway.py
tests/test_text_chat_app.py
tests/test_qwen25_acceptance_tools.py
tests/test_qwen25_kv_acl_service.py
tests/test_qwen25_static_export.py
tests/test_qwen25_static_kv_compare.py
tests/test_qwen25_static_kv_graph.py
tools/export_qwen25_static_onnx.py
tools/inspect_qwen25_static_onnx.py
tools/compare_qwen25_static_kv.py
requirements.txt
requirements-acl-om.txt
requirements-local-chat.txt
requirements-qwen25-export-sci-agent.txt
pytest.ini
.env.example
.env.local.example
local_model_manifest.json
frontend/index.html
frontend/package.json
frontend/package-lock.json
frontend/src/App.tsx
frontend/src/main.tsx
frontend/src/protocol.ts
frontend/src/styles.css
frontend/test/ui.test.mjs
frontend/tsconfig.app.json
frontend/tsconfig.json
frontend/tsconfig.node.json
frontend/vite.config.ts
frontend/dist/index.html
frontend/dist/assets/index-B8E1yCVM.js
frontend/dist/assets/index-Ckdv_CFc.css
README.md
docs/00-case9-current-runbook.md
docs/01-qwen25-dual-board-validation.md
docs/02-qwen25-reproducibility-and-sync.md
docs/03-case9-history-and-boundaries.md
docs/12-case9-evidence-index.md'

# Candidate roots are isolated from the historical deployment roots.  These
# lists are intentionally literal: a candidate sync never discovers files by
# walking a remote directory and therefore cannot copy an unexpected model.
CANDIDATE_BOARD8_REMOTE='artifacts/qwen25-static-kv-1024-v2.om
artifacts/qwen25-static-kv-1024-v2.om.lock.json
contracts/qwen25-static-kv-1024-v2-om-contract.json'
CANDIDATE_BOARD20_REMOTE='artifacts/qwen25-static-kv-1024-b1.om
artifacts/qwen25-static-kv-1024-b1.om.lock.json
contracts/qwen25-static-kv-1024-b1-om-contract.json'
# Tokenizer locks are derived from the common tokenizer lock when a candidate
# staging tree does not contain a board-local copy.  If a board-local copy is
# present, it is still synchronized through this explicit optional allowlist.
CANDIDATE_BOARD8_OPTIONAL='artifacts/tokenizer.json.lock.json'
CANDIDATE_BOARD20_OPTIONAL='artifacts/tokenizer.json.lock.json'
# These are the setup/restart evidence files created by the current campaign.
# Future or additional files must be passed explicitly with
# --board{8,20}-evidence-rel; no wildcard or recursive copy is permitted.
CANDIDATE_BOARD8_EVIDENCE='reports/20260827T102500Z/preflight.json
reports/20260827T102500Z/invalid-preflight-locks/om-lock-symlink-stat.json
reports/20260827T102500Z/invalid-preflight-locks/tokenizer-lock-symlink-stat.json
reports/20260827T103052Z-acl-smoke.txt
reports/20260827T103052Z-smoke-before.txt
reports/20260827T103052Z-smoke-during.txt
reports/20260827T103052Z-smoke-after.txt
reports/npu-before-20260827T101916Z.txt
reports/system-20260827T101916Z.txt
reports/restart-20260827T111500Z-b4/npu-before.txt
reports/restart-20260827T111500Z-b4/npu-after-start.txt
reports/restart-20260827T111500Z-b4/old-cmdline.txt
reports/restart-20260827T111500Z-b4/old-process.txt
logs/service-8084-b4.log
logs/service-8084-r2-b4.log'
CANDIDATE_BOARD20_EVIDENCE='reports/20260827T102500Z/preflight.json
reports/20260827T102500Z/invalid-preflight-locks/om-lock-symlink-stat.json
reports/20260827T102500Z/invalid-preflight-locks/tokenizer-lock-symlink-stat.json
reports/20260827T103052Z-acl-smoke.txt
reports/20260827T103052Z-smoke-before.txt
reports/20260827T103052Z-smoke-during.txt
reports/20260827T103052Z-smoke-after.txt
reports/npu-before-20260827T101916Z.txt
reports/system-20260827T101916Z.txt
reports/restart-20260827T111500Z-b1/npu-before.txt
reports/restart-20260827T111500Z-b1/npu-after-start.txt
reports/restart-20260827T111500Z-b1/old-cmdline.txt
reports/restart-20260827T111500Z-b1/old-process.txt
logs/service-8084-b1.log
logs/service-8084-r2-b1.log'

# These snapshots were collected during the original campaign.  They are
# copied into an explicitly labelled historical subtree; they are not claimed
# to be a fresh environment capture from the current IP address.
LOCAL_ENVIRONMENT_SOURCE='repro/qwen25-kv1024-20260825/environment/board-case9-local-chat-snapshot-20260825.txt
repro/qwen25-kv1024-20260825/environment/board-snapshot-20260825.txt
repro/qwen25-kv1024-20260825/environment/case9-acl-om-conda-explicit.txt
repro/qwen25-kv1024-20260825/environment/case9-acl-om-pip-freeze.txt
repro/qwen25-kv1024-20260825/environment/case9-local-chat-conda-explicit.txt
repro/qwen25-kv1024-20260825/environment/case9-local-chat-pip-freeze.txt
repro/qwen25-kv1024-20260825/environment/controller-sci-agent-conda-explicit.txt
repro/qwen25-kv1024-20260825/environment/controller-sci-agent-pip-freeze.txt
repro/qwen25-kv1024-20260825/environment/controller-sci-agent-python.txt'

common_destination() {
  case "$1" in
    artifacts/*) printf 'artifacts/common/%s' "${1#artifacts/}" ;;
    source-model/*) printf 'source-model/%s' "${1#source-model/}" ;;
    contracts/*) printf 'contracts/%s' "${1#contracts/}" ;;
    reports/*) printf 'reports/board8t/%s' "${1#reports/}" ;;
    *) printf 'metadata/common/%s' "$1" ;;
  esac
}

transfer_remote() {
  local board="$1" host="$2" remote_root="$3" remote_rel="$4" local_rel="$5"
  local remote_file="$remote_root/$remote_rel" destination="$BUNDLE/$local_rel"
  local metadata remote_size remote_sha part actual_size actual_sha
  is_rel "$remote_rel" || die "unsafe internal remote path: $remote_rel"
  is_rel "$local_rel" || die "unsafe internal destination path: $local_rel"
  if (( DRY_RUN )); then
    printf '[dry-run] %s %s:%s -> %s\n' "$board" "$host" "$remote_file" "$destination"
    return 0
  fi
  # Do not let ssh or rsync consume the here-string that drives the explicit
  # allowlist loop below.  Without this redirection only the first list entry
  # may be synchronized when a transport implementation reads stdin.
  # Candidate artifacts are symlinks into an immutable board-side artifact
  # store.  Metadata must describe the linked file, matching rsync -L below.
  metadata="$(ssh $SSH_OPTS "$USER_NAME@$host" "test -f '$remote_file' && stat -Lc '%s' '$remote_file' && sha256sum '$remote_file'" </dev/null)" ||
    die "cannot inspect $board:$remote_rel"
  remote_size="$(printf '%s\n' "$metadata" | sed -n '1p')"
  remote_sha="$(printf '%s\n' "$metadata" | sed -n '2p' | awk '{print $1}')"
  is_uint "$remote_size" || die "bad remote size for $board:$remote_rel"
  [[ "$remote_sha" =~ ^[0-9a-fA-F]{64}$ ]] || die "bad remote sha for $board:$remote_rel"
  mkdir -p "$(dirname "$destination")"
  assert_plain_destination "$destination"
  if [[ -f "$destination" ]]; then
    actual_size="$(wc -c < "$destination" | tr -d '[:space:]')"
    actual_sha="$(sha256sum "$destination" | awk '{print $1}')"
    if [[ "$actual_size" == "$remote_size" && "$actual_sha" == "$remote_sha" ]]; then
      # A previously interrupted transfer can leave a stale sibling .part.
      # The final destination has just been re-verified against the remote
      # file, so removing only that explicit temporary path is safe and keeps
      # the bundle verifier from treating the bundle as incomplete.
      [[ ! -e "$destination.part" ]] || rm -f -- "$destination.part"
      printf '%s\t%s\t%s\t%s\n' "$local_rel" "$actual_size" "$actual_sha" "$board:$remote_file" >> "$BUNDLE/.entries.tsv.part"
      echo "skip (verified) $local_rel"
      return 0
    fi
  fi
  part="$destination.part"
  # Keep an interrupted .part file so rsync can resume it with
  # --partial/--append-verify.  The final size and digest checks below remain
  # mandatory before the atomic rename.
  # Candidate roots contain symlinks to the verified shared artifacts.  -L
  # copies their bytes, never a symlink into the local bundle.
  rsync -aL --partial --append-verify --human-readable --info=progress2 \
    -e "ssh $SSH_OPTS" "$USER_NAME@$host:$remote_file" "$part" </dev/null ||
    die "transfer failed $board:$remote_rel"
  actual_size="$(wc -c < "$part" | tr -d '[:space:]')"
  actual_sha="$(sha256sum "$part" | awk '{print $1}')"
  [[ "$actual_size" == "$remote_size" && "$actual_sha" == "$remote_sha" ]] ||
    die "checksum mismatch $board:$remote_rel"
  mv -f -- "$part" "$destination"
  printf '%s\t%s\t%s\t%s\n' "$local_rel" "$actual_size" "$actual_sha" "$board:$remote_file" >> "$BUNDLE/.entries.tsv.part"
  echo "synced $local_rel bytes=$actual_size sha256=$actual_sha"
}

transfer_local() {
  local source_rel="$1"
  local destination_rel="$2"
  local source="$REPO_DIR/$source_rel"
  local destination="$BUNDLE/$destination_rel"
  local source_size source_sha part actual_size actual_sha
  is_rel "$source_rel" || die "unsafe internal local path"
  is_rel "$destination_rel" || die "unsafe internal destination path"
  [[ -f "$source" ]] || die "local source is missing: $source"
  source_size="$(wc -c < "$source" | tr -d '[:space:]')"
  source_sha="$(sha256sum "$source" | awk '{print $1}')"
  if (( DRY_RUN )); then
    printf '[dry-run] local %s -> %s bytes=%s sha256=%s\n' "$source" "$destination" "$source_size" "$source_sha"
    return 0
  fi
  mkdir -p "$(dirname "$destination")"
  assert_plain_destination "$destination"
  part="$destination.part"
  rm -f -- "$part"
  cp -- "$source" "$part"
  actual_size="$(wc -c < "$part" | tr -d '[:space:]')"
  actual_sha="$(sha256sum "$part" | awk '{print $1}')"
  [[ "$actual_size" == "$source_size" && "$actual_sha" == "$source_sha" ]] || die "local checksum mismatch: $source_rel"
  mv -f -- "$part" "$destination"
  printf '%s\t%s\t%s\t%s\n' "$destination_rel" "$actual_size" "$actual_sha" "local:$source" >> "$BUNDLE/.entries.tsv.part"
  echo "copied $destination_rel bytes=$actual_size sha256=$actual_sha"
}

register_existing_local_evidence() {
  local relative="$1" path size sha
  is_evidence_rel "$relative" || die "unsafe local evidence path: $relative"
  path="$BUNDLE/$relative"
  [[ ! -L "$path" ]] || die "local evidence must not be a symlink: $relative"
  [[ -f "$path" ]] || die "local evidence is missing: $relative"
  if (( DRY_RUN )); then
    printf '[dry-run] manifest-track existing local evidence %s\n' "$relative"
    return 0
  fi
  size="$(wc -c < "$path" | tr -d '[:space:]')"
  sha="$(sha256sum "$path" | awk '{print $1}')"
  printf '%s\t%s\t%s\t%s\n' "$relative" "$size" "$sha" "local-verified:$relative" >> "$BUNDLE/.entries.tsv.part"
  echo "manifest-tracked local evidence $relative bytes=$size sha256=$sha"
}

write_tokenizer_lock() {
  local tokenizer="$BUNDLE/artifacts/common/tokenizer.json"
  local lock="$BUNDLE/artifacts/common/tokenizer.json.lock.json"
  local size sha part lock_size lock_sha
  if (( DRY_RUN )); then
    echo "[dry-run] generate tokenizer lock $lock from $tokenizer"
    return 0
  fi
  [[ -f "$tokenizer" ]] || die "tokenizer was not synchronized: $tokenizer"
  size="$(wc -c < "$tokenizer" | tr -d '[:space:]')"
  sha="$(sha256sum "$tokenizer" | awk '{print $1}')"
  part="$lock.part"
  python3 - "$part" "$size" "$sha" <<'PY'
import datetime
import json
import re
from pathlib import Path
import sys

target = Path(sys.argv[1])
target.write_text(json.dumps({
    "schema_version": 1,
    "artifact": "tokenizer.json",
    "bytes": int(sys.argv[2]),
    "sha256": sys.argv[3],
    "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source": "sync_qwen25_repro_bundle.sh",
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  mv -f -- "$part" "$lock"
  lock_size="$(wc -c < "$lock" | tr -d '[:space:]')"
  lock_sha="$(sha256sum "$lock" | awk '{print $1}')"
  printf '%s\t%s\t%s\t%s\n' "artifacts/common/tokenizer.json.lock.json" "$lock_size" "$lock_sha" "generated:$tokenizer" >> "$BUNDLE/.entries.tsv.part"
  echo "generated tokenizer lock bytes=$size sha256=$sha"
}

sync_local_environment_sources() {
  local source_rel destination_rel
  while IFS= read -r source_rel; do
    [[ -z "$source_rel" ]] && continue
    if [[ ! -f "$REPO_DIR/$source_rel" ]]; then
      echo "skip (optional historical environment source absent) $source_rel"
      continue
    fi
    destination_rel="environment/historical/$(basename "$source_rel")"
    transfer_local "$source_rel" "$destination_rel"
  done <<< "$LOCAL_ENVIRONMENT_SOURCE"
}

write_environment_snapshot() {
  local entries_tmp
  entries_tmp="$BUNDLE/.environment-entries.tmp"
  if (( DRY_RUN )); then
    echo "[dry-run] generate environment provenance files under $BUNDLE/environment"
    return 0
  fi
  rm -f -- "$entries_tmp"
  python3 - "$BUNDLE" "$REPO_DIR" "$SYNC_RUN_ID" "$LAYOUT" "$BOARD8_HOST" "$BOARD20_HOST" "$BOARD8_ROOT" "$BOARD20_ROOT" "$OFFLINE_BOARD8" "$OFFLINE_BOARD20" > "$entries_tmp" <<'PY'
import datetime
import hashlib
from pathlib import Path
import platform
import sys

bundle, repo, sync_id, layout, board8, board20, root8, root20, offline8, offline20 = map(str, sys.argv[1:])
root = Path(bundle).resolve()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
files = {
    "environment/bundle-sync-provenance.txt": f"""schema=3
sync_run_id={sync_id}
layout={layout}
recorded_at_utc={now}
board8_host={board8}
board8_root={root8}
board8_sync_status={'unreachable' if offline8 == '1' else 'remote_verified'}
board20_host={board20}
board20_root={root20}
board20_sync_status={'unreachable' if offline20 == '1' else 'remote_verified'}
allowlist=explicit; recursive_copy=false; rsync_delete=false
secrets_included=false
system_cann_included=false
""",
    "environment/controller-runtime.txt": f"""recorded_at_utc={now}
python_executable={sys.executable}
python_version={sys.version.split()[0]}
platform={platform.platform()}
machine={platform.machine()}
repository_root={Path(repo).resolve()}
scope=controller-side provenance only; CANN/ACL/ATC are board-only
""",
    "environment/board8t-provenance.txt": f"""board_id=board8t
soc=Ascend310B4
current_ip={board8}
report_collection_ip=192.168.8.178
sync_run_id={sync_id}
sync_status={'unreachable' if offline8 == '1' else 'remote_verified'}
note=The current .90 address is an alias for the same physical board previously measured at .178; an IP-only change does not imply a new inference run.
candidate_evidence=Explicit historical 7867/7868 files are tracked separately when present; the original 130500Z chain directory is not asserted by this snapshot.
""",
    "environment/board20t-provenance.txt": f"""board_id=board20t
soc=Ascend310B1
current_ip={board20}
sync_run_id={sync_id}
sync_status={'unreachable' if offline20 == '1' else 'remote_verified'}
environment_label=base+base-overlay; dirty-base experimental
candidate_evidence=Raw candidate chain files remain pending while SSH is unavailable; historical full/usage reports are retained separately.
""",
    "environment/reproduction-commands.txt": """# Board shell preflight (do not modify shell startup files)
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
sha256sum -c SHA256SUMS.txt
bash scripts/provision_qwen25_kv102_board.sh check
bash scripts/provision_qwen25_kv102_board.sh inspect
bash scripts/provision_qwen25_kv102_board.sh smoke
# The board ACL runtime must not install or import torch, torch_npu, torchaudio,
# transformers, onnxruntime, mindspore, vllm, mindie, or unreviewed OPP.
""",
}
for relative, content in files.items():
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(content, encoding="utf-8")
    part.replace(path)
    data = path.read_bytes()
    print(f"{relative}\t{len(data)}\t{hashlib.sha256(data).hexdigest()}\tgenerated:environment-provenance")
PY
  while IFS=$'\t' read -r relative size sha source; do
    [[ -z "$relative" ]] && continue
    printf '%s\t%s\t%s\t%s\n' "$relative" "$size" "$sha" "$source" >> "$BUNDLE/.entries.tsv.part"
  done < "$entries_tmp"
  rm -f -- "$entries_tmp"
  echo "generated environment provenance files"
}

transfer_optional_remote() {
  local board="$1" host="$2" remote_root="$3" remote_rel="$4" local_rel="$5"
  is_rel "$remote_rel" || die "unsafe optional remote path: $remote_rel"
  is_rel "$local_rel" || die "unsafe optional destination path: $local_rel"
  if (( DRY_RUN )); then
    transfer_remote "$board" "$host" "$remote_root" "$remote_rel" "$local_rel"
    return 0
  fi
  if ssh $SSH_OPTS "$USER_NAME@$host" "test -f '$remote_root/$remote_rel'" </dev/null >/dev/null 2>&1; then
    transfer_remote "$board" "$host" "$remote_root" "$remote_rel" "$local_rel"
  else
    echo "skip (optional file absent) $board:$remote_rel"
  fi
}

board8_destination() {
  case "$1" in
    artifacts/*) printf 'artifacts/om/Ascend310B4/%s' "${1#artifacts/}" ;;
    contracts/*) printf 'contracts/Ascend310B4/%s' "${1#contracts/}" ;;
    reports/*) printf 'reports/board8t/%s' "${1#reports/}" ;;
    *) printf 'metadata/board8t/%s' "$1" ;;
  esac
}

board20_destination() {
  case "$1" in
    artifacts/*) printf 'artifacts/om/Ascend310B1/%s' "${1#artifacts/}" ;;
    contracts/*) printf 'contracts/Ascend310B1/%s' "${1#contracts/}" ;;
    reports/*) printf 'reports/board20t/%s/%s' "$BOARD20_RUN_ID" "${1#reports/}" ;;
    logs/*) printf 'reports/board20t/%s/logs/%s' "$BOARD20_RUN_ID" "${1#logs/}" ;;
    *) printf 'metadata/board20t/%s' "$1" ;;
  esac
}

candidate_evidence_destination() {
  local board="$1" rel="$2"
  is_evidence_rel "$rel" || die "candidate evidence path is not reports/... or logs/...: $rel"
  case "$board" in
    board8t)
      case "$rel" in
        reports/*) printf 'reports/board8t/candidate/%s' "${rel#reports/}" ;;
        logs/*) printf 'reports/board8t/candidate/logs/%s' "${rel#logs/}" ;;
      esac ;;
    board20t)
      case "$rel" in
        reports/*) printf 'reports/board20t/candidate/%s' "${rel#reports/}" ;;
        logs/*) printf 'reports/board20t/candidate/logs/%s' "${rel#logs/}" ;;
      esac ;;
    *) die "unknown candidate board: $board" ;;
  esac
}

candidate_artifact_destination() {
  local board="$1" rel="$2" soc
  is_rel "$rel" || die "unsafe candidate artifact path: $rel"
  case "$board" in
    board8t) soc="Ascend310B4" ;;
    board20t) soc="Ascend310B1" ;;
    *) die "unknown candidate board: $board" ;;
  esac
  case "$rel" in
    artifacts/*.om|artifacts/*.om.lock.json)
      printf 'artifacts/om/%s/%s' "$soc" "${rel#artifacts/}" ;;
    artifacts/tokenizer.json.lock.json)
      printf 'locks/%s/tokenizer.json.lock.json' "$soc" ;;
    contracts/*)
      printf 'contracts/%s/%s' "$soc" "${rel#contracts/}" ;;
    *) die "candidate artifact is outside the explicit model/contract allowlist: $rel" ;;
  esac
}

sync_candidate_evidence() {
  local board="$1" host="$2" root="$3" defaults="$4"
  local rel
  while IFS= read -r rel; do
    [[ -z "$rel" ]] || transfer_optional_remote "$board" "$host" "$root" "$rel" "$(candidate_evidence_destination "$board" "$rel")"
  done <<< "$defaults"
  if [[ "$board" == board8t ]]; then
    for rel in "${BOARD8_EVIDENCE_REL[@]}"; do
      transfer_optional_remote "$board" "$host" "$root" "$rel" "$(candidate_evidence_destination "$board" "$rel")"
    done
  else
    for rel in "${BOARD20_EVIDENCE_REL[@]}"; do
      transfer_optional_remote "$board" "$host" "$root" "$rel" "$(candidate_evidence_destination "$board" "$rel")"
    done
  fi
}

assert_offline_board20() {
  (( OFFLINE_BOARD20 )) || return 0
  [[ -f "$BUNDLE/bundle-manifest.json" ]] ||
    die "--offline-board20 requires an existing bundle-manifest.json with local B1 evidence"
  python3 - "$BUNDLE" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
manifest_path = root / "bundle-manifest.json"
document = json.loads(manifest_path.read_text(encoding="utf-8"))
entries = {item.get("path"): item for item in document.get("required_files", []) if isinstance(item, dict)}
required = [
    "artifacts/om/Ascend310B1/qwen25-static-kv-1024-b1.om",
    "artifacts/om/Ascend310B1/qwen25-static-kv-1024-b1.om.lock.json",
    "contracts/Ascend310B1/qwen25-static-kv-1024-v2-om-contract.json",
]
for relative in required:
    path = root / relative
    item = entries.get(relative)
    if not path.is_file() or not isinstance(item, dict):
        raise SystemExit(f"offline board20 missing local verified entry: {relative}")
    state = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            state.update(block)
    if size != item.get("bytes") or state.hexdigest().lower() != str(item.get("sha256", "")).lower():
        raise SystemExit(f"offline board20 local entry failed manifest hash: {relative}")
campaign = list((root / "reports" / "full-campaign" / "board20t").glob("*/acceptance.json"))
perf = list((root / "reports" / "usage-perf" / "board20t").glob("*/acceptance.json"))
if not campaign:
    raise SystemExit("offline board20 requires a local board20 full-campaign acceptance report")
if not perf:
    raise SystemExit("offline board20 requires a local board20 usage-perf acceptance report")
print(f"offline board20 local verification passed: artifacts={len(required)} campaign={len(campaign)} perf={len(perf)}")
PY
}

assert_existing_report_entry() {
  local board="$1" relative="$2" label="$3"
  if [[ "$board" == "8" ]]; then
    (( OFFLINE_BOARD8 )) || return 0
  elif [[ "$board" == "20" ]]; then
    (( OFFLINE_BOARD20 )) || return 0
  else
    die "unknown offline report board: $board"
  fi
  python3 - "$BUNDLE" "$relative" "$label" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
relative, label = sys.argv[2:]
document = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
entries = {item.get("path"): item for item in document.get("required_files", []) if isinstance(item, dict)}
item = entries.get(relative)
path = root / relative
if not path.is_file() or not isinstance(item, dict):
    raise SystemExit(f"offline {label} requires manifest-tracked report: {relative}")
state = hashlib.sha256()
size = 0
with path.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        state.update(block)
if size != item.get("bytes") or state.hexdigest().lower() != str(item.get("sha256", "")).lower():
    raise SystemExit(f"offline {label} report failed manifest hash: {relative}")
print(f"offline {label} manifest report verified: {relative}")
PY
}

assert_offline_board8() {
  (( OFFLINE_BOARD8 )) || return 0
  [[ -f "$BUNDLE/bundle-manifest.json" ]] ||
    die "--offline-board8 requires an existing bundle-manifest.json with local B4 evidence"
  python3 - "$BUNDLE" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
document = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
entries = {item.get("path"): item for item in document.get("required_files", []) if isinstance(item, dict)}
required = [
    "artifacts/om/Ascend310B4/qwen25-static-kv-1024-v2.om",
    "artifacts/om/Ascend310B4/qwen25-static-kv-1024-v2.om.lock.json",
    "contracts/qwen25-static-kv-1024-v2-om-contract.json",
]
for relative in required:
    path = root / relative
    item = entries.get(relative)
    if not path.is_file() or not isinstance(item, dict):
        raise SystemExit(f"offline board8 missing local verified entry: {relative}")
    state = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            state.update(block)
    if size != item.get("bytes") or state.hexdigest().lower() != str(item.get("sha256", "")).lower():
        raise SystemExit(f"offline board8 local entry failed manifest hash: {relative}")
campaign = list((root / "reports" / "full-campaign" / "board8t").glob("*/acceptance.json"))
perf = list((root / "reports" / "usage-perf" / "board8t").glob("*/acceptance.json"))
if not campaign:
    raise SystemExit("offline board8 requires a local board8 full-campaign acceptance report")
if not perf:
    raise SystemExit("offline board8 requires a local board8 usage-perf acceptance report")
print(f"offline board8 local verification passed: artifacts={len(required)} campaign={len(campaign)} perf={len(perf)}")
PY
}

if (( ! DRY_RUN )); then
  mkdir -p "$BUNDLE"
  : > "$BUNDLE/.entries.tsv.part"
fi
assert_offline_board20
assert_offline_board8
for rel in "${LOCAL_EVIDENCE_REL[@]}"; do
  register_existing_local_evidence "$rel"
done

if [[ "$LAYOUT" == historical ]]; then
  if (( ! OFFLINE_BOARD8 )); then
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "$(common_destination "$rel")"
    done <<< "$COMMON_REMOTE"
    if (( ! NO_SOURCE_MODEL )); then
      while IFS= read -r rel; do
        [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "source-model/${rel#source-model/}"
      done <<< "$SOURCE_MODEL_REMOTE"
    else
      echo "source-model: skipped remote source-checkpoint transfers"
    fi
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "contracts/${rel#contracts/}"
    done <<< "$CONTRACT8_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "reports/board8t/${rel#reports/}"
    done <<< "$REPORT8_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "$(board8_destination "$rel")"
    done <<< "$BOARD8_REMOTE"
  else
    echo "offline board8: skipped historical 8T transfers"
  fi
  if (( ! OFFLINE_BOARD20 )); then
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT/run/replacement/$BOARD20_HOST/$BOARD20_RUN_ID" "$rel" "$(board20_destination "$rel")"
    done <<< "$BOARD20_RUN_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT/run/replacement/$BOARD20_HOST/$BOARD20_RUN_ID" "$rel" "$(board20_destination "$rel")"
    done <<< "$BOARD20_CONTRACT_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT/run/replacement/$BOARD20_HOST/$BOARD20_RUN_ID" "$rel" "$(board20_destination "$rel")"
    done <<< "$BOARD20_REPORT_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT/run/replacement/$BOARD20_HOST/$BOARD20_RUN_ID" "$rel" "$(board20_destination "$rel")"
    done <<< "$BOARD20_LOG_REMOTE"
  else
    echo "offline board20: skipped historical 20T transfers"
  fi
else
  # Common model/tokenizer files come from the B4 candidate root.  The source
  # checkpoint is kept as a separate explicit root so a fresh candidate tree
  # does not need to duplicate the multi-gigabyte source files.
  if (( ! OFFLINE_BOARD8 )); then
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "$(common_destination "$rel")"
    done <<< "$COMMON_REMOTE"
    if (( ! NO_SOURCE_MODEL )); then
      while IFS= read -r rel; do
        [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$SOURCE_MODEL_ROOT" "$rel" "source-model/${rel#source-model/}"
      done <<< "$SOURCE_MODEL_REMOTE"
    else
      echo "source-model: skipped remote source-checkpoint transfers"
    fi
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "$(candidate_artifact_destination board8t "$rel")"
    done <<< "$CANDIDATE_BOARD8_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_optional_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$rel" "$(candidate_artifact_destination board8t "$rel")"
    done <<< "$CANDIDATE_BOARD8_OPTIONAL"
  else
    echo "offline board8: skipped candidate 8T transfers"
  fi
  if (( ! OFFLINE_BOARD20 )); then
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$rel" "$(candidate_artifact_destination board20t "$rel")"
    done <<< "$CANDIDATE_BOARD20_REMOTE"
    while IFS= read -r rel; do
      [[ -z "$rel" ]] || transfer_optional_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$rel" "$(candidate_artifact_destination board20t "$rel")"
    done <<< "$CANDIDATE_BOARD20_OPTIONAL"
  else
    echo "offline board20: skipped all 20T remote transfers; local B1 entries retained"
  fi
  if (( ! NO_REPORTS )); then
    if (( ! OFFLINE_BOARD8 )); then
      sync_candidate_evidence board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$CANDIDATE_BOARD8_EVIDENCE"
    else
      echo "offline board8: skipped candidate evidence transfers"
    fi
    if (( ! OFFLINE_BOARD20 )); then
      sync_candidate_evidence board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$CANDIDATE_BOARD20_EVIDENCE"
    else
      echo "offline board20: skipped candidate evidence transfers"
    fi
  fi
fi
if (( ! NO_REPORTS )) && [[ -n "$CAMPAIGN_RUN_ID" ]]; then
  # Pull only the explicitly named campaign report.  The previous version
  # accidentally appended reports/ twice, so keep the path rooted at the
  # deployment root here.
  if (( ! OFFLINE_BOARD8 )); then
    transfer_optional_remote board8t "$BOARD8_HOST" "$BOARD8_ROOT" \
      "reports/dual-board-acceptance/$CAMPAIGN_RUN_ID/board8t/acceptance.json" \
      "reports/board8t/$CAMPAIGN_RUN_ID/acceptance.json"
  else
    echo "offline board8: skipped campaign report transfer"
  fi
  if (( ! OFFLINE_BOARD20 )); then
    transfer_optional_remote board20t "$BOARD20_HOST" "$BOARD20_ROOT" \
      "reports/dual-board-acceptance/$CAMPAIGN_RUN_ID/board20t/acceptance.json" \
      "reports/board20t/$CAMPAIGN_RUN_ID/acceptance.json"
  else
    echo "offline board20: skipped campaign report transfer"
  fi
fi
if (( ! NO_SOURCE )); then
  while IFS= read -r rel; do
    [[ -z "$rel" ]] || transfer_local "$rel" "source/$rel"
  done <<< "$LOCAL_SOURCE"
  sync_local_environment_sources
fi
write_tokenizer_lock
write_environment_snapshot
if (( DRY_RUN )); then
  echo "dry-run complete; no files or remote directories were changed"
  exit 0
fi

python3 - "$BUNDLE" "$BUNDLE/.entries.tsv.part" "$SYNC_RUN_ID" "$CAMPAIGN_RUN_ID" "$BOARD8_HOST" "$BOARD20_HOST" "$BOARD8_ROOT" "$BOARD20_ROOT" "$LAYOUT" "$SOURCE_MODEL_ROOT" "$OFFLINE_BOARD8" "$OFFLINE_BOARD20" <<'PY'
import datetime
import json
import re
from pathlib import Path
import sys

bundle = Path(sys.argv[1])
entries_path = Path(sys.argv[2])
sync_run_id, campaign_run_id, board8, board20, root8, root20, layout, source_model_root, offline_board8, offline_board20 = sys.argv[3:]

# Merge with an existing manifest.  A candidate-only sync intentionally does
# not re-copy the source checkpoint and historical evidence, but those entries
# must remain part of the reproducibility bundle.  Invalid existing metadata
# is rejected rather than silently replaced.
manifest_path = bundle / "bundle-manifest.json"
previous = {}
if manifest_path.is_file():
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"sync: existing manifest is invalid: {exc}")
    if not isinstance(previous, dict):
        raise SystemExit("sync: existing manifest must be an object")

entries_by_path = {}
for item in previous.get("required_files", []):
    if not isinstance(item, dict):
        raise SystemExit("sync: existing manifest has a malformed required_files entry")
    path = item.get("path")
    if (
        not isinstance(path, str)
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", path)
        or path.startswith("/")
        or ".." in Path(path).parts
        or "." in Path(path).parts
    ):
        raise SystemExit(f"sync: existing manifest has an unsafe path: {path!r}")
    if not isinstance(item.get("bytes"), int) or item["bytes"] < 0:
        raise SystemExit(f"sync: existing manifest has an invalid byte count: {path}")
    if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]):
        raise SystemExit(f"sync: existing manifest has an invalid SHA-256: {path}")
    entries_by_path[path] = item

for line in entries_path.read_text(encoding="utf-8").splitlines():
    if not line:
        continue
    rel, size, sha, source = line.split("\t", 3)
    entries_by_path[rel] = {"path": rel, "bytes": int(size), "sha256": sha, "source": source}
entries = list(entries_by_path.values())
entries.sort(key=lambda item: item["path"])

campaigns = []
for value in previous.get("campaign_run_ids", []):
    if isinstance(value, str) and value not in campaigns:
        campaigns.append(value)
if isinstance(previous.get("campaign_run_id"), str) and previous["campaign_run_id"] not in campaigns:
    campaigns.append(previous["campaign_run_id"])
if campaign_run_id and campaign_run_id not in campaigns:
    campaigns.append(campaign_run_id)

manifest = dict(previous)
manifest.update({
    "schema_version": 3,
    "bundle_id": "qwen25-kv1024-dual-board-20260827",
    "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "sync_run_id": sync_run_id,
    "campaign_run_id": campaign_run_id or (previous.get("campaign_run_id") if isinstance(previous.get("campaign_run_id"), str) else None),
    "campaign_run_ids": campaigns,
    "sync_layout": layout,
    "board8_sync_status": "unreachable" if offline_board8 == "1" else "remote_verified",
    "board20_sync_status": "unreachable" if offline_board20 == "1" else "remote_verified",
    "board8_last_verified_local": datetime.datetime.now(datetime.timezone.utc).isoformat() if offline_board8 == "1" else previous.get("board8_last_verified_local"),
    "board20_last_verified_local": datetime.datetime.now(datetime.timezone.utc).isoformat() if offline_board20 == "1" else previous.get("board20_last_verified_local"),
    "model_id": "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om",
    "boards": {
        "board8t": {"ip": board8, "soc": "Ascend310B4", "root": root8, "sync_status": "unreachable" if offline_board8 == "1" else "remote_verified"},
        "board20t": {"ip": board20, "soc": "Ascend310B1", "root": root20, "environment": "base+base-overlay (dirty-base experimental)", "sync_status": "unreachable" if offline_board20 == "1" else "remote_verified"},
    },
    "source_model_root": source_model_root,
    "allowlist_policy": "explicit paths only; rsync --partial --append-verify; .part, size, sha256, atomic rename",
    "required_files": entries,
})
manifest_part = bundle / "bundle-manifest.json.part"
manifest_part.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
manifest_part.replace(bundle / "bundle-manifest.json")
sums_part = bundle / "SHA256SUMS.txt.part"
sums_part.write_text("".join(f"{item['sha256']}  ./{item['path']}\n" for item in entries), encoding="utf-8")
sums_part.replace(bundle / "SHA256SUMS.txt")
entries_path.unlink(missing_ok=True)
print(json.dumps({"bundle": str(bundle), "files": len(entries)}, ensure_ascii=False))
PY
echo "bundle synchronization complete: $BUNDLE"
