#!/usr/bin/env bash
# Run one explicit, loopback-only MindSpore acceptance profile on Ascend310B1.
#
# This is intentionally a per-profile runner.  It never starts a second
# worker, never changes the checked-in registry, and always stops the worker
# it launched before returning.  Invoke it once for every permitted profile so
# a failed model cannot contaminate the next model's NPU state or evidence.
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
profile=""
model_root=""
cache_dir=""
run_id=""
resolve_main=0
short_regression=0

usage() {
  cat <<'EOF'
Usage: run_mindspore_20t_campaign.sh --profile ID --model-root DIR [options]

Options:
  --cache-dir RELATIVE  Override the profile cache dir below --model-root.
  --run-id ID           Use a caller-selected safe run ID.
  --resolve-main        Resolve an unpinned HF revision before downloading.
  --short-regression    Run minimal API regression; reuse prior full evidence.

This script only permits native MindSpore profiles and verifies the observed
Ascend310B1/20T target before it starts the candidate loopback worker.
EOF
}

while (($#)); do
  case "$1" in
    --profile) profile="${2:-}"; shift 2 ;;
    --model-root) model_root="${2:-}"; shift 2 ;;
    --cache-dir) cache_dir="${2:-}"; shift 2 ;;
    --run-id) run_id="${2:-}"; shift 2 ;;
    --resolve-main) resolve_main=1; shift ;;
    --short-regression) short_regression=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

die() { echo "mindspore-20t-campaign: $*" >&2; exit 2; }
[[ "$profile" =~ ^[a-z0-9][a-z0-9._-]{2,63}$ ]] || die "--profile is required"
[[ -n "$model_root" && -d "$model_root" && ! -L "$model_root" ]] || die "--model-root must be an existing regular directory"
[[ -z "$cache_dir" || "$cache_dir" =~ ^[^/][^\\]*$ ]] || die "--cache-dir must be a relative POSIX path"
if [[ -z "$run_id" ]]; then
  run_id="${profile}-20t-$(date -u +%Y%m%dT%H%M%SZ)"
fi
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$ ]] || die "invalid --run-id"

conda_profile="${CONDA_PROFILE:-/usr/local/miniconda3/etc/profile.d/conda.sh}"
[[ -r "$conda_profile" ]] || die "conda profile is missing: $conda_profile"
set +u
source "$conda_profile"
set -u
conda activate base >/dev/null 2>&1 || die "cannot activate base"
cann_env="${CANN_ENV_SCRIPT:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
[[ -r "$cann_env" ]] || die "CANN environment script is missing: $cann_env"
set +u
source "$cann_env"
set -u
unset PYTHONNOUSERSITE
# The board cannot reliably reach huggingface.co.  Keep the endpoint
# overridable for controlled experiments, but use the public mirror by default
# for Hugging Face artifact acquisition.  Profiles using MindNLP's named
# ``modelers`` mirror are routed separately by the fetcher; HF_ENDPOINT does
# not turn a Modelers repository into a Hugging Face repository.  SHA-256 and
# fixed revisions remain mandatory.
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export PYTHONPATH="$script_dir${PYTHONPATH:+:$PYTHONPATH}"

observed_soc="$(npu-smi info 2>/dev/null | sed -nE 's/.*\b(310B[0-9A-Za-z]+)\b.*/Ascend\1/p' | sort -u | head -1 || true)"
[[ "$observed_soc" == "Ascend310B1" ]] || die "expected Ascend310B1, got ${observed_soc:-none}"

run_root="$script_dir/run/mindspore-20t-campaign/$run_id"
[[ ! -e "$run_root" ]] || die "run directory already exists: $run_root"
mkdir -p "$run_root/state" "$run_root/logs"
acceptance_root="$script_dir/reports/mindspore-chat/$run_id"
[[ ! -e "$acceptance_root" ]] || die "acceptance report directory already exists: $acceptance_root"
registry="$script_dir/configs/chat_model_profiles.json"
lock="$run_root/artifact-lock.json"
ephemeral_registry="$run_root/acceptance-registry.json"

stop_worker() {
  CASE9_MODEL_PROFILES="$ephemeral_registry" CASE9_MODEL_ROOT="$model_root" \
  CASE9_MODELCTL_STATE_DIR="$run_root/state" CASE9_MODELCTL_LOG_DIR="$run_root/logs" \
  CASE9_ALLOW_EXPERIMENTAL=1 bash "$script_dir/scripts/case9-modelctl.sh" stop >/dev/null 2>&1 || true
}
trap stop_worker EXIT
trap 'stop_worker; exit 128' HUP
trap 'stop_worker; exit 130' INT
trap 'stop_worker; exit 143' TERM

# Qwen3 has no loader in the fixed MindNLP release.  Detect that condition
# before any network activity so it is recorded as a loader boundary rather
# than an artifact-download failure.
loader_report="$run_root/loader-capability.json"
python "$script_dir/scripts/probe_mindnlp_loaders.py" >"$loader_report"
if [[ "$profile" == qwen3-* ]]; then
  if ! python - "$loader_report" <<'PY'
import json, sys
payload=json.load(open(sys.argv[1], encoding='utf-8'))
raise SystemExit(0 if payload.get('exported_names') and any(name.startswith('Qwen3') for name in payload['exported_names']) else 1)
PY
  then
    python - "$run_root" "$profile" "$run_id" "$loader_report" <<'PY'
import json, os, sys
root, profile, run_id, loader = sys.argv[1:]
payload={"schema_version":1,"status":"blocked","profile":{"id":profile},"target":{"board":"192.168.1.101","soc":"Ascend310B1","tier":"20T"},"run_id":run_id,"reason":"Current MindNLP does not expose a Qwen3 loader; no package upgrade or model download was performed.","gates":{"loader":False},"loader_report":loader}
with open(os.path.join(root,"acceptance.json"),"w",encoding="utf-8") as out: json.dump(payload,out,ensure_ascii=False,indent=2); out.write("\n")
PY
    echo "blocked: current MindNLP has no Qwen3 loader; report=$run_root/acceptance.json"
    exit 0
  fi
fi

fetch_args=(--profile "$profile" --registry "$registry" --root "$model_root" --lock-out "$lock")
[[ -n "$cache_dir" ]] && fetch_args+=(--cache-dir "$cache_dir")
(( resolve_main )) && fetch_args+=(--resolve-main)
python "$script_dir/scripts/fetch_mindspore_profile_artifacts.py" "${fetch_args[@]}" >"$run_root/fetch.json"

registry_args=(--registry "$registry" --lock "$lock" --profile "$profile" --run-id "$run_id" --output "$ephemeral_registry")
[[ -n "$cache_dir" ]] && registry_args+=(--cache-dir "$cache_dir")
python "$script_dir/scripts/create_mindspore_acceptance_registry.py" "${registry_args[@]}" >"$run_root/registry.json"

export CASE9_MODEL_PROFILES="$ephemeral_registry"
export CASE9_MODEL_ROOT="$model_root"
export CASE9_MODELCTL_STATE_DIR="$run_root/state"
export CASE9_MODELCTL_LOG_DIR="$run_root/logs"
export CASE9_ALLOW_EXPERIMENTAL=1
export CASE9_BOARD_HOST=192.168.1.101
export CASE9_ALLOW_CONTEXT_PLACEMENT=1
bash "$script_dir/scripts/case9-modelctl.sh" switch "$profile"

environment_report="$(find "$script_dir/run/mindspore-chat/environment-preflight" -name "${profile}-*.json" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
artifact_report="$(find "$script_dir/run/mindspore-chat/artifact-verification" -name "${profile}-*.json" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
[[ -n "$environment_report" && -f "$environment_report" ]] || die "worker did not write environment evidence"
[[ -n "$artifact_report" && -f "$artifact_report" ]] || die "worker did not write artifact evidence"

acceptance_args=(
  --execute --profile "$profile" --registry "$ephemeral_registry" --board 20t --soc Ascend310B1
  --run-id "$run_id" --environment-report "$environment_report" --artifact-report "$artifact_report"
  --require-evidence --output "$acceptance_root"
)
if (( short_regression )); then
  # Qwen1.5 has a separate, complete 30-run acceptance record. This refreshes
  # the current environment, NPU, health/models, JSON and SSE evidence only.
  acceptance_args+=(
    --long-budgets 8 --stability-loops 1 --perf-warmup 0 --perf-loops 1
    --perf-max-tokens 1 --probe-max-tokens 2 --skip-quality
  )
fi
python "$script_dir/scripts/mindspore_chat_acceptance.py" "${acceptance_args[@]}"
echo "passed: $acceptance_root/acceptance.json"
