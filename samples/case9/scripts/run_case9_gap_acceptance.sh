#!/usr/bin/env bash
# Run bounded Case9 gap acceptance against already-running board services.
# Default is dry-run; this wrapper installs no packages and never manages a
# remote process.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BOARD8_HOST="192.168.1.90"
BOARD20_HOST="192.168.1.95"
REMOTE_USER="HwHiAiUser"
BOARD8_ROOT="/home/HwHiAiUser/case9-mindspore-chat"
BOARD20_ROOT="/home/HwHiAiUser/case9-mindspore-chat"
BOARD8_OM_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024"
BOARD20_OM_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024-20t"
CONDA_SH="/usr/local/miniconda3/etc/profile.d/conda.sh"
CANN_SH="/usr/local/Ascend/ascend-toolkit/set_env.sh"
BOARD_SELECTION="both"
KIND="all"
PROFILES="qwen1.5-0.5b-mindspore,tinyllama-1.1b-mindspore,deepseek-r1-qwen-1.5b-mindspore"
PROFILE_OPTION_SEEN=0
REGISTRY="${REPO_DIR}/configs/chat_model_profiles.json"
PROBE_FILE="${REPO_DIR}/tests/fixtures/case9_dual_board_probe.json"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT=""
MIND_PORT=8090
OM_PORT=8084
TIMEOUT=300
DRY_RUN=1
PULL=1
ALLOW_BLOCKED=0
SKIP_QUALITY=0
SKIP_SNAPSHOTS=0

usage() {
  cat <<'EOF'
Usage: bash scripts/run_case9_gap_acceptance.sh [options]

The candidate service must already be running on each selected board. This
command performs read-only HTTP acceptance requests and pulls reports.

  --execute                 run the campaign (default is dry-run)
  --dry-run                 print commands without SSH or report writes
  --board 8t|20t|both       board selection (default both)
  --kind all|mindspore|om   model family (default all)
  --profiles LIST           comma-separated MindSpore profile ids
  --profile ID              add one MindSpore profile (repeatable)
  --registry FILE            explicit temporary/isolated registry source
  --probe-file FILE          probe fixture
  --board8-host HOST        default 192.168.1.90
  --board20-host HOST       default 192.168.1.95
  --user NAME                default HwHiAiUser
  --board8-root PATH         MindSpore deployment root
  --board20-root PATH        MindSpore deployment root
  --board8-om-root PATH      Qwen2.5 OM root
  --board20-om-root PATH     Qwen2.5 OM root
  --output DIR               local report root
  --run-id ID                report campaign id
  --mind-port N              must be 8090
  --om-port N                must be 8084
  --timeout SEC              per-request timeout
  --no-pull                  leave reports on boards
  --allow-blocked             permit pinned blocked profiles in a temporary registry
  --skip-quality              skip quality probes
  --skip-snapshots            skip npu-smi snapshots
  -h, --help                 show this help
EOF
}
die() { echo "case9-gap-acceptance: $*" >&2; exit 2; }
is_host() { [[ "${1:-}" =~ ^[A-Za-z0-9._:-]+$ ]]; }
is_word() { [[ "${1:-}" =~ ^[A-Za-z0-9._-]+$ ]]; }
is_id() { [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$ ]]; }
is_root() { [[ "${1:-}" =~ ^/home/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+$ ]] && [[ "$1" != *".."* ]]; }
is_uint() { [[ "${1:-}" =~ ^[0-9]+$ ]]; }
is_profile() { [[ "${1:-}" =~ ^[a-z0-9][a-z0-9._-]{2,63}$ ]]; }

while (($#)); do
  case "$1" in
    --execute|--run) DRY_RUN=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --board) BOARD_SELECTION="${2:?missing --board value}"; shift 2 ;;
    --kind) KIND="${2:?missing --kind value}"; shift 2 ;;
    --profiles) PROFILES="${2:?missing --profiles value}"; PROFILE_OPTION_SEEN=1; shift 2 ;;
    --profile) if (( PROFILE_OPTION_SEEN == 0 )); then PROFILES=""; PROFILE_OPTION_SEEN=1; fi; if [[ -z "$PROFILES" ]]; then PROFILES="$2"; else PROFILES="$PROFILES,$2"; fi; shift 2 ;;
    --registry) REGISTRY="${2:?missing registry value}"; shift 2 ;;
    --probe-file) PROBE_FILE="${2:?missing probe value}"; shift 2 ;;
    --board8-host) BOARD8_HOST="${2:?missing value}"; shift 2 ;;
    --board20-host) BOARD20_HOST="${2:?missing value}"; shift 2 ;;
    --user) REMOTE_USER="${2:?missing value}"; shift 2 ;;
    --board8-root) BOARD8_ROOT="${2:?missing value}"; shift 2 ;;
    --board20-root) BOARD20_ROOT="${2:?missing value}"; shift 2 ;;
    --board8-om-root) BOARD8_OM_ROOT="${2:?missing value}"; shift 2 ;;
    --board20-om-root) BOARD20_OM_ROOT="${2:?missing value}"; shift 2 ;;
    --output) OUTPUT="${2:?missing output value}"; shift 2 ;;
    --run-id) RUN_ID="${2:?missing run id}"; shift 2 ;;
    --mind-port) MIND_PORT="${2:?missing port}"; shift 2 ;;
    --om-port) OM_PORT="${2:?missing port}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?missing timeout}"; shift 2 ;;
    --no-pull) PULL=0; shift ;;
    --allow-blocked) ALLOW_BLOCKED=1; shift ;;
    --skip-quality) SKIP_QUALITY=1; shift ;;
    --skip-snapshots) SKIP_SNAPSHOTS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$BOARD_SELECTION" == 8t || "$BOARD_SELECTION" == 20t || "$BOARD_SELECTION" == both ]] || die "--board must be 8t, 20t, or both"
[[ "$KIND" == all || "$KIND" == mindspore || "$KIND" == om ]] || die "--kind must be all, mindspore, or om"
is_host "$BOARD8_HOST" || die "unsafe board8 host"
is_host "$BOARD20_HOST" || die "unsafe board20 host"
is_word "$REMOTE_USER" || die "unsafe SSH user"
is_id "$RUN_ID" || die "unsafe run id"
is_uint "$MIND_PORT" && ((MIND_PORT == 8090)) || die "MindSpore candidate port must be 8090"
is_uint "$OM_PORT" && ((OM_PORT == 8084)) || die "OM candidate port must be 8084"
is_uint "$TIMEOUT" && ((TIMEOUT > 0 && TIMEOUT <= 600)) || die "timeout must be 1-600 seconds"
is_root "$BOARD8_ROOT" || die "unsafe board8 root"
is_root "$BOARD20_ROOT" || die "unsafe board20 root"
is_root "$BOARD8_OM_ROOT" || die "unsafe board8 OM root"
is_root "$BOARD20_OM_ROOT" || die "unsafe board20 OM root"
for profile in ${PROFILES//,/ }; do is_profile "$profile" || die "unsafe profile id: $profile"; done
[[ -f "$REGISTRY" && ! -L "$REGISTRY" ]] || die "registry is not a regular file: $REGISTRY"
[[ -f "$PROBE_FILE" && ! -L "$PROBE_FILE" ]] || die "probe file is not a regular file: $PROBE_FILE"
if [[ -z "$OUTPUT" ]]; then OUTPUT="$REPO_DIR/repro/case9-dual-board-gap-$RUN_ID/reports"; fi
if [[ "$OUTPUT" != /* ]]; then OUTPUT="$REPO_DIR/$OUTPUT"; fi
if (( DRY_RUN == 0 )); then mkdir -p "$OUTPUT"; fi

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=2)
MIND_HELPER="$SCRIPT_DIR/mindspore_chat_acceptance.py"
OM_HELPER="$SCRIPT_DIR/qwen25_acceptance.py"
[[ -r "$MIND_HELPER" && -r "$OM_HELPER" ]] || die "acceptance helper is missing"
echo "Case9 gap acceptance: board=$BOARD_SELECTION kind=$KIND run_id=$RUN_ID"
echo "targets: board8=$BOARD8_HOST board20=$BOARD20_HOST mindspore=127.0.0.1:$MIND_PORT om=127.0.0.1:$OM_PORT"
echo "policy: service prestarted; no package installation; no remote process management"

remote_copy_atomic() {
  local host="$1" local_file="$2" remote_file="$3" label="$4"
  local expected_size expected_sha actual_size actual_sha
  if (( DRY_RUN )); then
    printf 'DRY-RUN registry/probe %s -> %s@%s:%s.part -> %s\n' "$label" "$REMOTE_USER" "$host" "$remote_file" "$remote_file"
    return
  fi
  expected_size="$(wc -c < "$local_file" | tr -d '[:space:]')"
  expected_sha="$(sha256sum "$local_file" | cut -d ' ' -f1)"
  ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "mkdir -p '$(dirname "$remote_file")' && rm -f '$remote_file.part'" || die "remote staging failed: $label"
  scp "${SSH_OPTS[@]}" "$local_file" "$REMOTE_USER@$host:$remote_file.part" >/dev/null || die "transfer failed: $label"
  actual_size="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "stat -c '%s' '$remote_file.part'")" || die "remote size failed: $label"
  actual_sha="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "sha256sum '$remote_file.part' | cut -d ' ' -f1")" || die "remote hash failed: $label"
  [[ "$actual_size" == "$expected_size" && "$actual_sha" == "$expected_sha" ]] || die "remote checksum mismatch: $label"
  ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "mv -f '$remote_file.part' '$remote_file'" || die "atomic rename failed: $label"
}

pull_report() {
  local host="$1" remote_file="$2" local_file="$3" label="$4"
  local metadata remote_size remote_sha actual_size actual_sha part
  if (( PULL == 0 )); then
    printf '[%s] report retained on board: %s\n' "$label" "$remote_file"
    return
  fi
  metadata="$(ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "test -f '$remote_file' && stat -c '%s' '$remote_file' && sha256sum '$remote_file'")" || die "report metadata failed: $label"
  remote_size="$(printf '%s\n' "$metadata" | sed -n '1p')"
  remote_sha="$(printf '%s\n' "$metadata" | sed -n '2p' | cut -d ' ' -f1)"
  [[ "$remote_size" =~ ^[0-9]+$ && "$remote_sha" =~ ^[[:xdigit:]]{64}$ ]] || die "invalid report metadata: $label"
  mkdir -p "$(dirname "$local_file")"
  part="${local_file}.part"
  rm -f -- "$part"
  scp "${SSH_OPTS[@]}" "$REMOTE_USER@$host:$remote_file" "$part" >/dev/null || die "report pull failed: $label"
  actual_size="$(wc -c < "$part" | tr -d '[:space:]')"
  actual_sha="$(sha256sum "$part" | cut -d ' ' -f1)"
  [[ "$actual_size" == "$remote_size" && "$actual_sha" == "$remote_sha" ]] || die "report checksum mismatch: $label"
  mv -f -- "$part" "$local_file"
  printf '[%s] report verified bytes=%s sha256=%s local=%s\n' "$label" "$actual_size" "$actual_sha" "$local_file"
}

make_temp_registry() {
  local source="$1" profile="$2" temporary="$3" board="$4" board_host="$5" board_soc="$6" board_tier="$7"
  python3 - "$source" "$profile" "$temporary" "$ALLOW_BLOCKED" "$board_host" "$board_soc" "$board_tier" <<'PY'
import json
import sys
from pathlib import Path
source, profile, target, allow_blocked, board_host, board_soc, board_tier = sys.argv[1:]
document = json.loads(Path(source).read_text(encoding="utf-8"))
items = document.get("profiles") if isinstance(document, dict) else None
if not isinstance(items, list):
    raise SystemExit("registry.profiles is not a list")
selected = [item for item in items if isinstance(item, dict) and item.get("id") == profile]
if len(selected) != 1:
    raise SystemExit("profile is not present exactly once: %s" % profile)
item = selected[0]
# A gap campaign may intentionally run a profile on the other board. Bind the
# temporary registry to the actual target so the health identity gate remains
# meaningful; the checked-in registry is never changed.
item["board"] = {"host": board_host, "soc": board_soc, "tier": board_tier}
if item.get("status") in {"blocked", "not-run"}:
    if allow_blocked != "1":
        raise SystemExit("profile is %s; pass --allow-blocked for a temporary registry" % item.get("status"))
    if not item.get("revision_pinned"):
        raise SystemExit("blocked mutable revision cannot be used for a temporary registry")
    item["status"] = "experimental_dirty_base"
    item["admission"] = {"eligible": False, "reason": "Temporary gap acceptance only; never activate or promote."}
Path(target).write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY
}

run_mindspore() {
  local board="$1" host="$2" root="$3" profile="$4" env_name="$5"
  # mindspore_chat_acceptance.py confines reports to its repository-local
  # reports/mindspore-chat root. Keep the campaign staging directory there;
  # the separate run/ tree is reserved for launch state.
  local remote_run="$root/reports/mindspore-chat/case9-gap/$RUN_ID/$profile"
  local remote_registry="$root/run/case9-gap/$RUN_ID/registry-$profile.json"
  local remote_probe="$root/run/case9-gap/$RUN_ID/probe.json"
  local remote_report="$remote_run/acceptance.json"
  local local_report="$OUTPUT/$board/$profile/$RUN_ID/acceptance.json"
  local temporary command skip_quality_arg="" skip_snapshots_arg=""
  if (( SKIP_QUALITY )); then skip_quality_arg="--skip-quality"; fi
  if (( SKIP_SNAPSHOTS )); then skip_snapshots_arg="--skip-snapshots"; fi
  if (( DRY_RUN )); then
    printf '[dry-run] %s/%s: ssh %s@%s python mindspore_chat_acceptance.py --registry %s --execute --output %s\n' "$board" "$profile" "$REMOTE_USER" "$host" "$remote_registry" "$remote_run"
    printf '[dry-run] temp registry: %s -> %s\n' "$REGISTRY" "$remote_registry"
    return
  fi
  temporary="$(mktemp "${TMPDIR:-/tmp}/case9-gap-registry.XXXXXX.json")"
  local board_soc board_tier
  board_soc="Ascend310B4"; board_tier="8T"
  [[ "$board" == board20t ]] && board_soc="Ascend310B1" && board_tier="20T"
  make_temp_registry "$REGISTRY" "$profile" "$temporary" "$board" "$host" "$board_soc" "$board_tier"
  remote_copy_atomic "$host" "$temporary" "$remote_registry" "$board/$profile registry"
  remote_copy_atomic "$host" "$PROBE_FILE" "$remote_probe" "$board/$profile probe"
  rm -f -- "$temporary"
  ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "mkdir -p '$remote_run'" || die "cannot create remote report directory"
  # mindspore_chat_acceptance.py treats --output as a directory and writes
  # acceptance.json beneath it. Keep the file path used for pulling separate.
  # MindNLP is installed in the board image's user site.  Hiding that site
  # makes the acceptance helper fail before it can issue a request, so keep
  # the environment behavior identical to the service launcher (unset by
  # default; callers may still set CASE9_PYTHONNOUSERSITE explicitly in a
  # manually constructed command).
  printf -v command 'set -Eeuo pipefail; cd %q; if test -r %q; then set +u; source %q; conda activate %q; set -u; else exit 2; fi; if test -r %q; then set +u; source %q; set -u; else exit 2; fi; export PYTHONPATH=%q; unset PYTHONNOUSERSITE; python - --profile %q --registry %q --host 127.0.0.1 --port 8090 --output %q --run-id %q --probe-file %q --timeout %q --execute %s %s' "$root" "$CONDA_SH" "$CONDA_SH" "$env_name" "$CANN_SH" "$CANN_SH" "$root" "$profile" "$remote_registry" "$remote_run" "$RUN_ID" "$remote_probe" "$TIMEOUT" "$skip_quality_arg" "$skip_snapshots_arg"
  echo "[$board/$profile] running read-only acceptance on $host"
  # Preserve the report even when a gate fails.  The helper's non-zero status
  # is reported only after the atomic pull has had a chance to retain evidence.
  local acceptance_rc=0 pull_rc=0
  if ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "$command" < "$MIND_HELPER"; then
    acceptance_rc=0
  else
    acceptance_rc=$?
  fi
  if pull_report "$host" "$remote_report" "$local_report" "$board/$profile"; then
    pull_rc=0
  else
    pull_rc=$?
  fi
  (( pull_rc == 0 )) || die "MindSpore report pull failed: $board/$profile (acceptance rc=$acceptance_rc)"
  (( acceptance_rc == 0 )) || die "MindSpore acceptance failed: $board/$profile (report retained)"
}

run_om() {
  local board="$1" host="$2" root="$3" om_root="$4" soc="$5"
  local om_env="case9-acl-om"
  [[ "$board" == board20t ]] && om_env="base"
  local remote_report="$root/reports/mindspore-chat/case9-gap/$RUN_ID/qwen25-onnx-om/acceptance.json"
  local local_report="$OUTPUT/$board/qwen25-onnx-om/$RUN_ID/acceptance.json"
  local om_name="qwen25-static-kv-1024-v2.om"
  local lock_name="qwen25-static-kv-1024-v2.om.lock.json"
  local contract_name="qwen25-static-kv-1024-v2-om-contract.json"
  if [[ "$board" == board20t ]]; then
    om_name="qwen25-static-kv-1024-b1.om"
    lock_name="qwen25-static-kv-1024-b1.om.lock.json"
    contract_name="qwen25-static-kv-1024-b1-om-contract.json"
  fi
  local om="$om_root/artifacts/$om_name"
  local lock="$om_root/artifacts/$lock_name"
  local contract="$om_root/contracts/$contract_name"
  local tokenizer="$om_root/artifacts/tokenizer.json"
  local tokenizer_lock="$om_root/artifacts/tokenizer.json.lock.json"
  local command dirty_arg=""
  if (( DRY_RUN )); then
    printf '[dry-run] %s/qwen25-onnx-om: ssh %s@%s python qwen25_acceptance.py --endpoint http://127.0.0.1:8084/v1/chat/completions --om %s --lock %s --contract %s\n' "$board" "$REMOTE_USER" "$host" "$om" "$lock" "$contract"
    return
  fi
  ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "mkdir -p '$(dirname "$remote_report")'" || die "cannot create OM report directory"
  [[ "$board" == board20t ]] && dirty_arg="--allow-dirty-base"
  printf -v command 'set -Eeuo pipefail; cd %q; if test -r %q; then set +u; source %q; conda activate %q; set -u; else exit 2; fi; if test -r %q; then set +u; source %q; set -u; else exit 2; fi; export PYTHONPATH=%q; export PYTHONNOUSERSITE=1; python - --endpoint %q --health-url %q --models-url %q --model qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om --board-ip %q --board-tier %q --om %q --lock %q --tokenizer-lock %q --contract %q --tokenizer %q --output %q --timeout %q --max-tokens 2 --long-budgets 8,16,24,32,48,64,80 --stability-loops 10 --perf-warmup 2 --perf-loops 30 --perf-max-tokens 2 %s' "$root" "$CONDA_SH" "$CONDA_SH" "$om_env" "$CANN_SH" "$CANN_SH" "$root" "http://127.0.0.1:8084/v1/chat/completions" "http://127.0.0.1:8084/health" "http://127.0.0.1:8084/v1/models" "$host" "$([[ "$soc" == Ascend310B4 ]] && printf '8T' || printf '20T')" "$om" "$lock" "$tokenizer_lock" "$contract" "$tokenizer" "$remote_report" "$TIMEOUT" "$dirty_arg"
  echo "[$board/qwen25-onnx-om] running read-only acceptance on $host"
  local acceptance_rc=0 pull_rc=0
  if ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$host" "$command" < "$OM_HELPER"; then
    acceptance_rc=0
  else
    acceptance_rc=$?
  fi
  if pull_report "$host" "$remote_report" "$local_report" "$board/qwen25-onnx-om"; then
    pull_rc=0
  else
    pull_rc=$?
  fi
  (( pull_rc == 0 )) || die "OM report pull failed: $board (acceptance rc=$acceptance_rc)"
  (( acceptance_rc == 0 )) || die "OM acceptance failed: $board (report retained)"
}

run_board() {
  local board="$1" host="$2" root="$3" om_root="$4" env_name="$5" soc="$6" profile
  if [[ "$KIND" == om || "$KIND" == all ]]; then
    run_om "$board" "$host" "$root" "$om_root" "$soc"
  fi
  [[ "$KIND" == mindspore || "$KIND" == all ]] || return 0
  for profile in ${PROFILES//,/ }; do
    run_mindspore "$board" "$host" "$root" "$profile" "$env_name"
  done
}

if [[ "$BOARD_SELECTION" == 8t || "$BOARD_SELECTION" == both ]]; then
  run_board board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$BOARD8_OM_ROOT" base Ascend310B4
fi
if [[ "$BOARD_SELECTION" == 20t || "$BOARD_SELECTION" == both ]]; then
  run_board board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$BOARD20_OM_ROOT" base Ascend310B1
fi
echo "gap acceptance plan complete: run_id=$RUN_ID output=$OUTPUT dry_run=$DRY_RUN"
