#!/usr/bin/env bash
# Synchronize verified Case9 LLM OM artifacts to the isolated 20T campaign
# directory. Run this controller-side from Linux/WSL; it never invokes ATC.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${CASE9_OM20T_TARGET:-HwHiAiUser@192.168.1.95}"
REMOTE_ROOT="${CASE9_OM20T_ROOT:-/home/HwHiAiUser/case9-om-20t-20260914}"
IDENTITY_FILE="${CASE9_OM20T_IDENTITY:-}"
SSH_BIN="${CASE9_OM20T_SSH_BIN:-ssh}"
RETRIES="${CASE9_OM20T_RETRIES:-6}"
RETRY_DELAY_SECONDS="${CASE9_OM20T_RETRY_DELAY_SECONDS:-8}"
RSYNC_BWLIMIT="${CASE9_OM20T_RSYNC_BWLIMIT:-0}"

# This campaign intentionally reuses only one prebuilt, aarch64-compatible
# tokenizer wheel.  Keep its source, destination, byte count, and digest
# together so the runtime profile cannot silently retrieve a different wheel.
TOKENIZERS_WHEEL_SOURCE="repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/artifacts/wheels/tokenizers-0.19.1-cp39-cp39-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
TOKENIZERS_WHEEL_REMOTE="wheels/tokenizers-0.19.1-cp39-cp39-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
TOKENIZERS_WHEEL_BYTES=3570575
TOKENIZERS_WHEEL_SHA256="c85cf76561fbd01e0d9ea2d1cbe711a65400092bc52b5242b16cfd22e51f0c58"

usage() {
  cat <<'EOF'
Usage: bash scripts/sync_case9_om20t.sh --profile PROFILE [options]

Profiles:
  runtime           ACL runtime/source, campaign tools, probes, and the pinned
                    aarch64 tokenizers wheel only
  qwen25-kv-b1      Native Ascend310B1 Static-KV OM and its locked assets
  qwen25-kv-b4      Ascend310B4 Static-KV OM for an explicit B4->B1 experiment
  tinyllama-b4      Ascend310B4 TinyLlama OM and matching tokenizer assets
  all-local         runtime plus all three local profiles above

Options:
  --host TARGET     SSH target (default: HwHiAiUser@192.168.1.95)
  --remote-root DIR Isolated remote campaign directory
  --identity FILE   SSH identity file, useful from WSL
  --ssh-bin PATH    SSH executable; WSL may use Windows ssh.exe to reuse keys
  --retries N       Bounded reconnect attempts per allowlisted file (default: 6)
  --help            Show this message

Every file is copied as <name>.part, checked by byte count and SHA-256 on the
board, then atomically renamed. The script contains no --delete, does not
recurse over a home directory, and does not run ATC or start a model service.
EOF
}

die() {
  echo "Case9 20T OM sync refused: $*" >&2
  exit 2
}

PROFILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || die "--profile requires a value"; PROFILE="$2"; shift 2 ;;
    --host) [[ $# -ge 2 ]] || die "--host requires a value"; TARGET="$2"; shift 2 ;;
    --remote-root) [[ $# -ge 2 ]] || die "--remote-root requires a value"; REMOTE_ROOT="$2"; shift 2 ;;
    --identity) [[ $# -ge 2 ]] || die "--identity requires a value"; IDENTITY_FILE="$2"; shift 2 ;;
    --ssh-bin) [[ $# -ge 2 ]] || die "--ssh-bin requires a value"; SSH_BIN="$2"; shift 2 ;;
    --retries) [[ $# -ge 2 ]] || die "--retries requires a value"; RETRIES="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$PROFILE" ]] || die "--profile is required"
case "$PROFILE" in runtime|qwen25-kv-b1|qwen25-kv-b4|tinyllama-b4|all-local) ;; *) die "unsupported profile: $PROFILE" ;; esac
case "$REMOTE_ROOT" in /home/HwHiAiUser/case9-om-20t-*) ;; *) die "remote root must be an isolated /home/HwHiAiUser/case9-om-20t-* directory" ;; esac
[[ "$RETRIES" =~ ^[1-9][0-9]*$ ]] || die "--retries must be a positive integer"
[[ "$RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]] || die "CASE9_OM20T_RETRY_DELAY_SECONDS must be a non-negative integer"
[[ "$RSYNC_BWLIMIT" =~ ^(0|[1-9][0-9]*)$ ]] || die "CASE9_OM20T_RSYNC_BWLIMIT must be a non-negative integer KiB/s"
[[ "$(uname -s)" == "Linux" ]] || die "run this controller-side from Linux or WSL"
command -v rsync >/dev/null || die "rsync is required"
command -v sha256sum >/dev/null || die "sha256sum is required"
[[ -z "$IDENTITY_FILE" || -f "$IDENTITY_FILE" ]] || die "identity file does not exist: $IDENTITY_FILE"
if [[ "$SSH_BIN" == */* ]]; then
  [[ -x "$SSH_BIN" ]] || die "SSH executable is unavailable: $SSH_BIN"
else
  command -v "$SSH_BIN" >/dev/null || die "SSH executable is unavailable: $SSH_BIN"
fi

SSH_ARGS=(-o BatchMode=yes -o ConnectTimeout=12 -o ServerAliveInterval=5 -o ServerAliveCountMax=3)
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_ARGS+=(-i "$IDENTITY_FILE")
fi

ssh_board() {
  "$SSH_BIN" "${SSH_ARGS[@]}" "$TARGET" "$@"
}

RSYNC_RSH="$(printf '%q' "$SSH_BIN")"
for item in "${SSH_ARGS[@]}"; do
  RSYNC_RSH+=" $(printf '%q' "$item")"
done

# An unset or zero cap preserves rsync's normal transfer rate.
RSYNC_TRANSFER_ARGS=(-a --partial --append-verify --info=progress2)
if [[ "$RSYNC_BWLIMIT" != "0" ]]; then
  RSYNC_TRANSFER_ARGS+=(--bwlimit="$RSYNC_BWLIMIT")
fi

remote_existing_matches() {
  local remote_path="$1" expected_bytes="$2" expected_sha="$3"
  ssh_board bash -s -- "$remote_path" "$expected_bytes" "$expected_sha" <<'REMOTE'
set -Eeuo pipefail
path="$1" bytes="$2" sha="$3"
[[ -f "$path" ]] || exit 1
[[ "$(stat -c '%s' "$path")" == "$bytes" ]] || exit 1
[[ "$(sha256sum "$path" | awk '{print $1}')" == "$sha" ]]
REMOTE
}

verify_and_promote() {
  local part_path="$1" final_path="$2" expected_bytes="$3" expected_sha="$4"
  ssh_board bash -s -- "$part_path" "$final_path" "$expected_bytes" "$expected_sha" <<'REMOTE'
set -Eeuo pipefail
part="$1" final="$2" bytes="$3" sha="$4"
[[ -f "$part" ]] || { echo "missing partial file: $part" >&2; exit 1; }
actual_bytes="$(stat -c '%s' "$part")"
actual_sha="$(sha256sum "$part" | awk '{print $1}')"
[[ "$actual_bytes" == "$bytes" ]] || { echo "byte count mismatch: $actual_bytes != $bytes" >&2; exit 1; }
[[ "$actual_sha" == "$sha" ]] || { echo "SHA-256 mismatch: $actual_sha != $sha" >&2; exit 1; }
if [[ -e "$final" ]]; then
  [[ "$(stat -c '%s' "$final")" == "$bytes" ]] || { echo "existing final file differs: $final" >&2; exit 1; }
  [[ "$(sha256sum "$final" | awk '{print $1}')" == "$sha" ]] || { echo "existing final file hash differs: $final" >&2; exit 1; }
  rm -f -- "$part"
else
  mv -- "$part" "$final"
fi
REMOTE
}

sync_one() {
  local source_rel="$1" remote_rel="$2" expected_bytes="$3" expected_sha="$4"
  local source final_path part_path
  source="$REPO_DIR/$source_rel"
  final_path="$REMOTE_ROOT/$remote_rel"
  part_path="${final_path}.part"
  [[ -f "$source" ]] || die "allowlisted source is missing: $source_rel"
  [[ "$(stat -c '%s' "$source")" == "$expected_bytes" ]] || die "source byte count differs: $source_rel"
  [[ "$(sha256sum "$source" | awk '{print $1}')" == "$expected_sha" ]] || die "source SHA-256 differs: $source_rel"
  local attempt
  for ((attempt = 1; attempt <= RETRIES; attempt += 1)); do
    if remote_existing_matches "$final_path" "$expected_bytes" "$expected_sha"; then
      echo "verified existing: $remote_rel"
      return 0
    fi
    if ! ssh_board mkdir -p -- "$(dirname "$final_path")"; then
      echo "remote unavailable before $remote_rel (attempt $attempt/$RETRIES)" >&2
    elif ! rsync "${RSYNC_TRANSFER_ARGS[@]}" -e "$RSYNC_RSH" "$source" "$TARGET:$part_path"; then
      echo "transfer interrupted for $remote_rel (attempt $attempt/$RETRIES); retaining .part" >&2
    elif verify_and_promote "$part_path" "$final_path" "$expected_bytes" "$expected_sha"; then
      echo "verified and promoted: $remote_rel"
      return 0
    else
      echo "remote verification interrupted for $remote_rel (attempt $attempt/$RETRIES); retaining .part" >&2
    fi
    if (( attempt < RETRIES )); then
      sleep "$RETRY_DELAY_SECONDS"
    fi
  done
  die "all $RETRIES resumable sync attempts failed for $remote_rel"
}

sync_runtime() {
  # Runtime source files are small, but they are pinned too so a later report
  # can identify the exact code that interpreted an OM descriptor.  This list
  # is intentionally explicit: do not replace it with a directory copy.
  local files=(
    qwen25_kv_acl_contract.py qwen25_kv_acl_runtime.py qwen25_kv_acl_service.py qwen25_kv_tokenizer.py
    qwen25_acl_contract.py qwen25_acl_runtime.py qwen25_acl_service.py qwen25_tokenizer.py
    qwen25_legacy_compat_service.py
    acl_om_contract.py acl_om_runtime.py acl_om_service.py acl_om_tokenizer.py
    tinyllama_acl_contract.py tinyllama_acl_runtime.py tinyllama_acl_service.py tinyllama_tokenizer.py
    tinyllama_acl_b1_compat.py
    local_model_manifest.json scripts/serve_qwen25_kv_acl.py scripts/serve_qwen25_acl.py
    scripts/probe_acl_om.py scripts/probe_openai_compat_api.py scripts/qwen25_acceptance.py
    scripts/create_qwen25_b1_campaign_lock.py scripts/run_case9_om20t_campaign.sh
    scripts/run_qwen25_b1_20t_acceptance.sh
    scripts/serve_qwen25_legacy_compat.py scripts/create_qwen25_legacy_compat_pin_manifest.py
    scripts/run_tinyllama_acl_b1_compat.sh
    scripts/provision_case9_om20t_overlay.sh
    tests/fixtures/qwen25_chinese_probe.json
  )
  local item bytes sha remote
  for item in "${files[@]}"; do
    bytes="$(stat -c '%s' "$REPO_DIR/$item")"
    sha="$(sha256sum "$REPO_DIR/$item" | awk '{print $1}')"
    remote="src/$item"
    sync_one "$item" "$remote" "$bytes" "$sha"
  done
  sync_one "$TOKENIZERS_WHEEL_SOURCE" "$TOKENIZERS_WHEEL_REMOTE" \
    "$TOKENIZERS_WHEEL_BYTES" "$TOKENIZERS_WHEEL_SHA256"
}

sync_qwen_b1() {
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer.json" "artifacts/qwen25-b1/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer_config.json" "artifacts/qwen25-b1/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer.json.lock.json" "artifacts/qwen25-b1/tokenizer.json.lock.json" 258 02643b3266f05fe36f5067f0142d0a6f2346bc13722702c901b9d8550f779aab
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/om/Ascend310B1/qwen25-static-kv-1024-b1.om" "artifacts/qwen25-b1/qwen25-static-kv-1024-b1.om" 1266009438 6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/om/Ascend310B1/qwen25-static-kv-1024-b1.om.lock.json" "artifacts/qwen25-b1/qwen25-static-kv-1024-b1.om.lock.json" 793 47a4030202342132d5794bea1e1e5ae19316f6c132b2fa00895df87b4b9d3272
  sync_one "repro/qwen25-kv1024-dual-board-20260827/contracts/Ascend310B1/qwen25-static-kv-1024-fp32-contract.json" "artifacts/qwen25-b1/qwen25-static-kv-1024-fp32-contract.json" 34912 fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498
  sync_one "repro/qwen25-kv1024-dual-board-20260827/contracts/Ascend310B1/qwen25-static-kv-1024-v2-om-contract.json" "artifacts/qwen25-b1/qwen25-static-kv-1024-v2-om-contract.json" 32225 3ff15ccf682e85e32f521417aa7204347ff5ab4fb0608edd18f3c9a639af3c21
}

sync_qwen_b4() {
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer.json" "artifacts/qwen25-b4/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer_config.json" "artifacts/qwen25-b4/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer.json.lock.json" "artifacts/qwen25-b4/tokenizer.json.lock.json" 258 02643b3266f05fe36f5067f0142d0a6f2346bc13722702c901b9d8550f779aab
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/om/Ascend310B4/qwen25-static-kv-1024-v2.om" "artifacts/qwen25-b4/qwen25-static-kv-1024-v2.om" 1266010586 f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8
  sync_one "repro/qwen25-kv1024-dual-board-20260827/artifacts/om/Ascend310B4/qwen25-static-kv-1024-v2.om.lock.json" "artifacts/qwen25-b4/qwen25-static-kv-1024-v2.om.lock.json" 1421 7d6b35de8261ce3e5f077a6d6d3b6e19df43a2fba2470a2ef30c0d217a8c2770
  sync_one "repro/qwen25-kv1024-dual-board-20260827/contracts/Ascend310B4/qwen25-static-kv-1024-v2-om-contract.json" "artifacts/qwen25-b4/qwen25-static-kv-1024-v2-om-contract.json" 32172 923255c717165a2529e15563f2acd5cb1e31b67769e2a4afa4d3233053e310b6
}

sync_tinyllama_b4() {
  sync_one "repro/tinyllama-hf-upload-20260902/artifacts/om/tiny-llama.om" "artifacts/tinyllama/tiny-llama.om" 1493077371 604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967
  sync_one "repro/tinyllama-hf-upload-20260902/tiny-llama.om.lock.json" "artifacts/tinyllama/tiny-llama.om.lock.json" 562 cda3af97a0bca7bb8c1e73ae1c9dbdcf6124a278b6b5994cb0d32b32d4e2303a
  sync_one "repro/tinyllama-hf-upload-20260902/artifacts/tokenizer/tokenizer.zip" "artifacts/tinyllama/tokenizer/tokenizer.zip" 709459 d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917
  sync_one "repro/tinyllama-hf-upload-20260902/tokenizer.zip.lock.json" "artifacts/tinyllama/tokenizer/tokenizer.zip.lock.json" 478 4a8b17be338b627064532271ba0566f07346fe08ef654e7d686ba6935d2253c5
  sync_one "repro/tinyllama-hf-upload-20260902/artifacts/tokenizer/extracted/tokenizer.json" "artifacts/tinyllama/tokenizer/tokenizer.json" 1842767 bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e
  sync_one "repro/tinyllama-hf-upload-20260902/artifacts/tokenizer/extracted/tokenizer.model" "artifacts/tinyllama/tokenizer/tokenizer.model" 499723 9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347
  sync_one "repro/tinyllama-hf-upload-20260902/artifacts/tokenizer/extracted/tokenizer_config.json" "artifacts/tinyllama/tokenizer/tokenizer_config.json" 932 bcdc6f267b05e1afd27fa622a62fea649bcb941e6dc705d2835883a0746192da
  sync_one "repro/tinyllama-hf-upload-20260902/artifacts/tokenizer/extracted/special_tokens_map.json" "artifacts/tinyllama/tokenizer/special_tokens_map.json" 414 6fa06efa2785e450051989a6f8fb4416b10149ded485ddd3f127a40734f5cfd0
}

case "$PROFILE" in
  runtime) sync_runtime ;;
  qwen25-kv-b1) sync_qwen_b1 ;;
  qwen25-kv-b4) sync_qwen_b4 ;;
  tinyllama-b4) sync_tinyllama_b4 ;;
  all-local) sync_runtime; sync_qwen_b1; sync_qwen_b4; sync_tinyllama_b4 ;;
esac
