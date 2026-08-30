#!/usr/bin/env bash
# Run a bounded Qwen2.5 Static-KV acceptance campaign on one or both boards.
# The service is never started or stopped. Reports are pulled with size/SHA
# verification and an atomic rename.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HELPER="$SCRIPT_DIR/qwen25_acceptance.py"
BOARD8_HOST="192.168.1.90"
BOARD20_HOST="192.168.8.210"
SSH_USER="HwHiAiUser"
# Candidate service roots used by the current runbook.  Historical campaign
# roots must be supplied explicitly when replaying an archived report.
BOARD8_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024"
BOARD20_ROOT="/home/HwHiAiUser/case9-qwen25-kv1024-20t"
BOARD8_OM_REL="artifacts/qwen25-static-kv-1024-v2.om"
BOARD8_LOCK_REL="artifacts/qwen25-static-kv-1024-v2.om.lock.json"
BOARD8_CONTRACT_REL="contracts/qwen25-static-kv-1024-v2-om-contract.json"
BOARD20_OM_REL="artifacts/qwen25-static-kv-1024-b1.om"
BOARD20_LOCK_REL="artifacts/qwen25-static-kv-1024-b1.om.lock.json"
BOARD20_CONTRACT_REL="contracts/qwen25-static-kv-1024-b1-om-contract.json"
TOKENIZER_REL="artifacts/tokenizer.json"
TOKENIZER_LOCK_REL="artifacts/tokenizer.json.lock.json"
PROBE_FILE_REL=""
SERVICE_PORT=8084
BOARD_SELECTION="both"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOCAL_OUTPUT="$REPO_DIR/repro/qwen25-kv1024-dual-board-20260827/reports"
PULL_REPORTS=1
DRY_RUN=0
TIMEOUT=300
MAX_TOKENS=2
LONG_BUDGETS="8,16,24,32,48,64,80"
STABILITY_LOOPS=10
STABILITY_MAX_TOKENS=2
PROBE_MAX_TOKENS=8
PERF_WARMUP=2
PERF_LOOPS=30
PERF_MAX_TOKENS=2
ENV_NAME_8="case9-acl-om"
ENV_NAME_20="base"
CONDA_SH="/usr/local/miniconda3/etc/profile.d/conda.sh"
CANN_ENV="/usr/local/Ascend/ascend-toolkit/set_env.sh"
QWEN_MODEL_ID="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"

usage() {
  cat <<'EOF'
Usage: bash scripts/run_qwen25_dual_board_acceptance.sh [options]

The candidate service must already listen on 127.0.0.1:8084 on each board.
This command never starts/stops a service and installs no packages.

  --board 8t|20t|both       target board(s), default both
  --board8-host IP          default 192.168.1.90
  --board20-host IP         default 192.168.8.210
  --user NAME               default HwHiAiUser
  --board8-root PATH        8T deployment root below /home/NAME
  --board20-root PATH       20T deployment root below /home/NAME
  --board8-om-rel PATH      8T OM path relative to root
  --board20-om-rel PATH     20T OM path relative to root
  --board8-lock-rel PATH    8T lock path relative to root
  --board20-lock-rel PATH   20T lock path relative to root
  --board8-contract-rel P   8T descriptor contract relative path
  --board20-contract-rel P  20T descriptor contract relative path
  --tokenizer-lock-rel P    tokenizer lock relative path (default: artifacts/tokenizer.json.lock.json)
  --probe-file-rel PATH     optional board-local JSON probe fixture relative to root
  --port N                  must remain 8084
  --output DIR              local report directory
  --run-id ID               safe report identifier
  --timeout SEC              per-request timeout, default 300
  --long-budgets LIST        default 8,16,24,32,48,64,80
  --stability-loops N        default 10
  --perf-warmup N            default 2
  --perf-loops N             default 30
  --perf-max-tokens N        default 2
  --no-pull                  keep reports on boards only
  --dry-run                  print SSH commands without side effects
EOF
}

die() { echo "acceptance: $*" >&2; exit 2; }
is_word() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }
is_host() { [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; }
is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }
is_rel() {
  [[ "$1" != /* && -n "$1" && "$1" != *".."* && "$1" != *" "* &&
    "$1" != *$'\t'* && "$1" != *$'\n'* ]]
}
is_root() {
  [[ "$1" =~ ^/home/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+$ ]] &&
    [[ "$1" != *".."* ]]
}

while (($#)); do
  case "$1" in
    --board) BOARD_SELECTION="${2:?missing --board value}"; shift 2 ;;
    --board8-host) BOARD8_HOST="${2:?missing value}"; shift 2 ;;
    --board20-host) BOARD20_HOST="${2:?missing value}"; shift 2 ;;
    --user) SSH_USER="${2:?missing value}"; shift 2 ;;
    --board8-root) BOARD8_ROOT="${2:?missing value}"; shift 2 ;;
    --board20-root) BOARD20_ROOT="${2:?missing value}"; shift 2 ;;
    --board8-om-rel) BOARD8_OM_REL="${2:?missing value}"; shift 2 ;;
    --board20-om-rel) BOARD20_OM_REL="${2:?missing value}"; shift 2 ;;
    --board8-lock-rel) BOARD8_LOCK_REL="${2:?missing value}"; shift 2 ;;
    --board20-lock-rel) BOARD20_LOCK_REL="${2:?missing value}"; shift 2 ;;
    --board8-contract-rel) BOARD8_CONTRACT_REL="${2:?missing value}"; shift 2 ;;
    --board20-contract-rel) BOARD20_CONTRACT_REL="${2:?missing value}"; shift 2 ;;
    --tokenizer-lock-rel) TOKENIZER_LOCK_REL="${2:?missing value}"; shift 2 ;;
    --probe-file-rel) PROBE_FILE_REL="${2:?missing value}"; shift 2 ;;
    --port) SERVICE_PORT="${2:?missing value}"; shift 2 ;;
    --output) LOCAL_OUTPUT="${2:?missing value}"; shift 2 ;;
    --run-id) RUN_ID="${2:?missing value}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?missing value}"; shift 2 ;;
    --max-tokens) MAX_TOKENS="${2:?missing value}"; shift 2 ;;
    --long-budgets) LONG_BUDGETS="${2:?missing value}"; shift 2 ;;
    --stability-loops) STABILITY_LOOPS="${2:?missing value}"; shift 2 ;;
    --stability-max-tokens) STABILITY_MAX_TOKENS="${2:?missing value}"; shift 2 ;;
    --probe-max-tokens) PROBE_MAX_TOKENS="${2:?missing value}"; shift 2 ;;
    --perf-warmup) PERF_WARMUP="${2:?missing value}"; shift 2 ;;
    --perf-loops) PERF_LOOPS="${2:?missing value}"; shift 2 ;;
    --perf-max-tokens) PERF_MAX_TOKENS="${2:?missing value}"; shift 2 ;;
    --no-pull) PULL_REPORTS=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$BOARD_SELECTION" == 8t || "$BOARD_SELECTION" == 20t || "$BOARD_SELECTION" == both ]] ||
  die "--board must be 8t, 20t, or both"
is_host "$BOARD8_HOST" || die "unsafe board8 host"
is_host "$BOARD20_HOST" || die "unsafe board20 host"
is_word "$SSH_USER" || die "unsafe SSH user"
is_word "$RUN_ID" || die "unsafe run id"
is_uint "$SERVICE_PORT" && (( SERVICE_PORT == 8084 )) || die "candidate port must be 8084"
for value in "$TIMEOUT" "$MAX_TOKENS" "$STABILITY_LOOPS" "$STABILITY_MAX_TOKENS" "$PROBE_MAX_TOKENS" "$PERF_WARMUP" "$PERF_LOOPS" "$PERF_MAX_TOKENS"; do
  is_uint "$value" || die "numeric option is invalid: $value"
done
(( TIMEOUT > 0 && MAX_TOKENS > 0 && MAX_TOKENS <= 80 &&
   STABILITY_LOOPS > 0 && STABILITY_MAX_TOKENS > 0 && STABILITY_MAX_TOKENS <= 80 &&
   PROBE_MAX_TOKENS > 0 && PROBE_MAX_TOKENS <= 80 &&
   PERF_LOOPS > 0 && PERF_MAX_TOKENS > 0 && PERF_MAX_TOKENS <= 80 )) ||
  die "numeric option is outside accepted bounds"
is_root "$BOARD8_ROOT" || die "unsafe board8 root"
is_root "$BOARD20_ROOT" || die "unsafe board20 root"
for value in "$BOARD8_OM_REL" "$BOARD8_LOCK_REL" "$BOARD8_CONTRACT_REL" "$BOARD20_OM_REL" "$BOARD20_LOCK_REL" "$BOARD20_CONTRACT_REL" "$TOKENIZER_REL" "$TOKENIZER_LOCK_REL"; do
  is_rel "$value" || die "unsafe relative path: $value"
done
if [[ -n "$PROBE_FILE_REL" ]]; then
  is_rel "$PROBE_FILE_REL" || die "unsafe probe fixture path: $PROBE_FILE_REL"
fi
[[ -r "$HELPER" ]] || die "missing helper: $HELPER"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=12 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)

pull_report() {
  local label="$1" host="$2" remote_file="$3" local_file="$4"
  local metadata remote_size remote_sha actual_size actual_sha part
  metadata="$(ssh "${SSH_OPTS[@]}" "$SSH_USER@$host" "test -f '$remote_file' && stat -c '%s' '$remote_file' && sha256sum '$remote_file'")" ||
    die "cannot read remote report metadata: $label"
  remote_size="$(printf '%s\n' "$metadata" | sed -n '1p')"
  remote_sha="$(printf '%s\n' "$metadata" | sed -n '2p' | awk '{print $1}')"
  is_uint "$remote_size" || die "invalid remote report size: $label"
  [[ "$remote_sha" =~ ^[0-9a-fA-F]{64}$ ]] || die "invalid remote report sha256: $label"
  mkdir -p "$(dirname "$local_file")"
  part="$local_file.part"
  rm -f -- "$part"
  scp "${SSH_OPTS[@]}" "$SSH_USER@$host:$remote_file" "$part" >/dev/null ||
    die "report transfer failed: $label"
  actual_size="$(wc -c < "$part" | tr -d '[:space:]')"
  actual_sha="$(sha256sum "$part" | awk '{print $1}')"
  [[ "$actual_size" == "$remote_size" && "$actual_sha" == "$remote_sha" ]] ||
    die "report checksum mismatch: $label"
  mv -f -- "$part" "$local_file"
  echo "[$label] report sha256=$remote_sha local=$local_file"
}

run_one_board() {
  local label="$1" host="$2" root="$3" om_rel="$4" lock_rel="$5" contract_rel="$6" env_name="$7" dirty="$8"
  local endpoint="http://127.0.0.1:$SERVICE_PORT/v1/chat/completions"
  local report_dir="$root/reports/dual-board-acceptance/$RUN_ID/$label"
  local remote_report="$report_dir/acceptance.json"
  local local_report="$LOCAL_OUTPUT/$label/$RUN_ID/acceptance.json"
  local om="$root/$om_rel" lock="$root/$lock_rel" contract="$root/$contract_rel" tokenizer="$root/$TOKENIZER_REL" tokenizer_lock="$root/$TOKENIZER_LOCK_REL"
  local dirty_arg="" probe_arg="" probe_check=""
  [[ "$dirty" == 1 ]] && dirty_arg="--allow-dirty-base"
  if [[ -n "$PROBE_FILE_REL" ]]; then
    local probe_file="$root/$PROBE_FILE_REL"
    printf -v probe_arg '%q %q' '--probe-file' "$probe_file"
    printf -v probe_check 'test -r %q; ' "$probe_file"
  fi
  local command
  printf -v command 'set -Eeuo pipefail; for file in %q %q %q %q %q; do test -r "$file"; done; %s mkdir -p %q; if test -r %q; then set +u; source %q; conda activate %q; set -u; fi; test -n "${CONDA_PREFIX:-}" && test -x "$CONDA_PREFIX/bin/python"; if test -r %q; then set +u; source %q; set -u; fi; export PATH="$CONDA_PREFIX/bin:$PATH"; hash -r 2>/dev/null || true; export PYTHONNOUSERSITE=1; python - --endpoint %q --health-url %q --models-url %q --model %q --board-ip %q --board-tier %q --om %q --lock %q --tokenizer-lock %q --contract %q --tokenizer %q --output %q --timeout %q --max-tokens %q --long-budgets %q --stability-loops %q --stability-max-tokens %q --probe-max-tokens %q --perf-warmup %q --perf-loops %q --perf-max-tokens %q %s %s' \
    "$om" "$lock" "$contract" "$tokenizer" "$tokenizer_lock" "$probe_check" "$report_dir" "$CONDA_SH" "$CONDA_SH" "$env_name" "$CANN_ENV" "$CANN_ENV" \
    "$endpoint" "http://127.0.0.1:$SERVICE_PORT/health" "http://127.0.0.1:$SERVICE_PORT/v1/models" "$QWEN_MODEL_ID" "$host" "$label" "$om" "$lock" "$tokenizer_lock" "$contract" "$tokenizer" "$remote_report" "$TIMEOUT" "$MAX_TOKENS" "$LONG_BUDGETS" "$STABILITY_LOOPS" "$STABILITY_MAX_TOKENS" "$PROBE_MAX_TOKENS" "$PERF_WARMUP" "$PERF_LOOPS" "$PERF_MAX_TOKENS" "$dirty_arg" "$probe_arg"
  if (( DRY_RUN )); then
    printf '[dry-run] %s: ssh %s@%s %s < %s\n' "$label" "$SSH_USER" "$host" "$command" "$HELPER"
    printf '[dry-run] report: %s -> %s\n' "$remote_report" "$local_report"
    return 0
  fi
  echo "[$label] running on $host (candidate port $SERVICE_PORT)"
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$host" "$command" < "$HELPER" || die "acceptance failed on $label"
  (( PULL_REPORTS )) && pull_report "$label" "$host" "$remote_report" "$local_report"
}

(( DRY_RUN )) || mkdir -p "$LOCAL_OUTPUT"
case "$BOARD_SELECTION" in
  8t) run_one_board board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$BOARD8_OM_REL" "$BOARD8_LOCK_REL" "$BOARD8_CONTRACT_REL" "$ENV_NAME_8" 0 ;;
  20t) run_one_board board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$BOARD20_OM_REL" "$BOARD20_LOCK_REL" "$BOARD20_CONTRACT_REL" "$ENV_NAME_20" 1 ;;
  both)
    run_one_board board8t "$BOARD8_HOST" "$BOARD8_ROOT" "$BOARD8_OM_REL" "$BOARD8_LOCK_REL" "$BOARD8_CONTRACT_REL" "$ENV_NAME_8" 0
    run_one_board board20t "$BOARD20_HOST" "$BOARD20_ROOT" "$BOARD20_OM_REL" "$BOARD20_LOCK_REL" "$BOARD20_CONTRACT_REL" "$ENV_NAME_20" 1
    ;;
esac
echo "acceptance campaign complete: run_id=$RUN_ID output=$LOCAL_OUTPUT"
