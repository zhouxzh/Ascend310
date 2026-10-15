#!/usr/bin/env bash
# Fetch a fixed, integrity-bound Case9 OM profile from a controller-side HTTP
# staging service. This is intentionally board-side so large transfers survive
# a controller SSH session drop. It never invokes ATC or changes CANN/conda.
set -Eeuo pipefail

PROFILE=""
ROOT="${CASE9_OM20T_ROOT:-/home/HwHiAiUser/case9-om-20t-20260914}"
RETRIES="${CASE9_OM20T_FETCH_RETRIES:-120}"
RETRY_DELAY_SECONDS="${CASE9_OM20T_FETCH_RETRY_DELAY_SECONDS:-5}"
OM_URL_BASE="${CASE9_OM20T_OM_URL_BASE:-http://192.168.1.80:8769}"
COMMON_URL_BASE="${CASE9_OM20T_COMMON_URL_BASE:-http://192.168.1.80:8770}"
CONTRACT_URL_BASE="${CASE9_OM20T_CONTRACT_URL_BASE:-http://192.168.1.80:8771}"

usage() {
  cat <<'EOF'
Usage: bash scripts/fetch_case9_om_http.sh --profile PROFILE [options]

Options:
  --profile NAME          One of qwen25-kv-b1, qwen25-kv-b4, tinyllama-b4.
  --root DIR              Isolated Case9 campaign root.
  --retries N             Attempts per file, default 120.
  --retry-delay-seconds N Delay after an interrupted HTTP transfer.
  --om-url-base URL       Controller HTTP directory containing OM and OM lock.
  --common-url-base URL   Controller HTTP directory containing tokenizer files.
  --contract-url-base URL Controller HTTP directory containing the selected
                          profile's controller and runtime contracts.

Files are downloaded to <name>.part, verified by exact byte count and SHA-256,
then atomically renamed. HTTP transport is accepted only because the fixed
SHA-256 contract detects corruption or substitution. This script does not run
ATC, start inference, install packages, or expose any service port.
EOF
}

die() {
  echo "Case9 OM HTTP fetch refused: $*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) [[ $# -ge 2 ]] || die "--profile requires a value"; PROFILE="$2"; shift 2 ;;
    --root) [[ $# -ge 2 ]] || die "--root requires a value"; ROOT="$2"; shift 2 ;;
    --retries) [[ $# -ge 2 ]] || die "--retries requires a value"; RETRIES="$2"; shift 2 ;;
    --retry-delay-seconds) [[ $# -ge 2 ]] || die "--retry-delay-seconds requires a value"; RETRY_DELAY_SECONDS="$2"; shift 2 ;;
    --om-url-base) [[ $# -ge 2 ]] || die "--om-url-base requires a value"; OM_URL_BASE="$2"; shift 2 ;;
    --common-url-base) [[ $# -ge 2 ]] || die "--common-url-base requires a value"; COMMON_URL_BASE="$2"; shift 2 ;;
    --contract-url-base) [[ $# -ge 2 ]] || die "--contract-url-base requires a value"; CONTRACT_URL_BASE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$PROFILE" in
  qwen25-kv-b1|qwen25-kv-b4|tinyllama-b4) ;;
  *) die "profile must be qwen25-kv-b1, qwen25-kv-b4, or tinyllama-b4" ;;
esac
case "$ROOT" in /home/HwHiAiUser/case9-om-20t-*) ;; *) die "root must be an isolated /home/HwHiAiUser/case9-om-20t-* path" ;; esac
[[ "$RETRIES" =~ ^[1-9][0-9]*$ ]] || die "retries must be a positive integer"
[[ "$RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]] || die "retry delay must be a non-negative integer"
command -v curl >/dev/null || die "curl is required"
command -v sha256sum >/dev/null || die "sha256sum is required"

fetch_one() {
  local url="$1" final_path="$2" expected_bytes="$3" expected_sha="$4"
  local part_path="${final_path}.part" actual_bytes actual_sha attempt
  mkdir -p "$(dirname "$final_path")"
  if [[ -f "$final_path" ]]; then
    actual_bytes="$(stat -c '%s' "$final_path")"
    actual_sha="$(sha256sum "$final_path" | awk '{print $1}')"
    if [[ "$actual_bytes" == "$expected_bytes" && "$actual_sha" == "$expected_sha" ]]; then
      echo "verified existing: $final_path"
      return 0
    fi
    die "existing final file is not the admitted artifact: $final_path"
  fi
  for ((attempt = 1; attempt <= RETRIES; attempt += 1)); do
    echo "fetch attempt $attempt/$RETRIES: $(basename "$final_path")"
    local -a curl_args=(--fail --location --connect-timeout 8 --max-time 180 --output "$part_path")
    if [[ -s "$part_path" ]]; then
      curl_args+=(--continue-at -)
    fi
    if curl "${curl_args[@]}" "$url"; then
      actual_bytes="$(stat -c '%s' "$part_path")"
      actual_sha="$(sha256sum "$part_path" | awk '{print $1}')"
      if [[ "$actual_bytes" == "$expected_bytes" && "$actual_sha" == "$expected_sha" ]]; then
        mv -- "$part_path" "$final_path"
        echo "verified and promoted: $final_path"
        return 0
      fi
      echo "partial or invalid result retained: bytes=$actual_bytes sha=$actual_sha" >&2
      # A complete file with the wrong digest is not resumable evidence.
      if [[ "$actual_bytes" == "$expected_bytes" ]]; then
        die "complete download SHA-256 differs from the admitted artifact: $final_path"
      fi
    else
      echo "HTTP transfer interrupted; retaining $part_path" >&2
    fi
    (( attempt < RETRIES )) && sleep "$RETRY_DELAY_SECONDS"
  done
  die "all $RETRIES fetch attempts failed: $final_path"
}

fetch_qwen_common() {
  local destination="$1"
  fetch_one "$COMMON_URL_BASE/tokenizer.json" "$destination/tokenizer.json" 7031645 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539
  fetch_one "$COMMON_URL_BASE/tokenizer_config.json" "$destination/tokenizer_config.json" 7305 5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583
  fetch_one "$COMMON_URL_BASE/tokenizer.json.lock.json" "$destination/tokenizer.json.lock.json" 258 02643b3266f05fe36f5067f0142d0a6f2346bc13722702c901b9d8550f779aab
}

case "$PROFILE" in
  qwen25-kv-b1)
    DEST="$ROOT/artifacts/qwen25-b1"
    fetch_qwen_common "$DEST"
    fetch_one "$OM_URL_BASE/qwen25-static-kv-1024-b1.om.lock.json" "$DEST/qwen25-static-kv-1024-b1.om.lock.json" 793 47a4030202342132d5794bea1e1e5ae19316f6c132b2fa00895df87b4b9d3272
    fetch_one "$CONTRACT_URL_BASE/qwen25-static-kv-1024-fp32-contract.json" "$DEST/qwen25-static-kv-1024-fp32-contract.json" 34912 fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498
    fetch_one "$CONTRACT_URL_BASE/qwen25-static-kv-1024-v2-om-contract.json" "$DEST/qwen25-static-kv-1024-v2-om-contract.json" 32225 3ff15ccf682e85e32f521417aa7204347ff5ab4fb0608edd18f3c9a639af3c21
    fetch_one "$OM_URL_BASE/qwen25-static-kv-1024-b1.om" "$DEST/qwen25-static-kv-1024-b1.om" 1266009438 6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609
    ;;
  qwen25-kv-b4)
    DEST="$ROOT/artifacts/qwen25-b4"
    fetch_qwen_common "$DEST"
    fetch_one "$OM_URL_BASE/qwen25-static-kv-1024-v2.om.lock.json" "$DEST/qwen25-static-kv-1024-v2.om.lock.json" 1421 7d6b35de8261ce3e5f077a6d6d3b6e19df43a2fba2470a2ef30c0d217a8c2770
    fetch_one "$CONTRACT_URL_BASE/qwen25-static-kv-1024-v2-om-contract.json" "$DEST/qwen25-static-kv-1024-v2-om-contract.json" 32172 923255c717165a2529e15563f2acd5cb1e31b67769e2a4afa4d3233053e310b6
    fetch_one "$OM_URL_BASE/qwen25-static-kv-1024-v2.om" "$DEST/qwen25-static-kv-1024-v2.om" 1266010586 f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8
    ;;
  tinyllama-b4)
    DEST="$ROOT/artifacts/tinyllama"
    fetch_one "$OM_URL_BASE/tiny-llama.om.lock.json" "$DEST/tiny-llama.om.lock.json" 562 cda3af97a0bca7bb8c1e73ae1c9dbdcf6124a278b6b5994cb0d32b32d4e2303a
    fetch_one "$OM_URL_BASE/tiny-llama.om" "$DEST/tiny-llama.om" 1493077371 604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967
    fetch_one "$COMMON_URL_BASE/tokenizer.zip" "$DEST/tokenizer/tokenizer.zip" 709459 d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917
    fetch_one "$COMMON_URL_BASE/tokenizer.zip.lock.json" "$DEST/tokenizer/tokenizer.zip.lock.json" 478 4a8b17be338b627064532271ba0566f07346fe08ef654e7d686ba6935d2253c5
    fetch_one "$COMMON_URL_BASE/tokenizer.json" "$DEST/tokenizer/tokenizer.json" 1842767 bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e
    fetch_one "$COMMON_URL_BASE/tokenizer.model" "$DEST/tokenizer/tokenizer.model" 499723 9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347
    fetch_one "$COMMON_URL_BASE/tokenizer_config.json" "$DEST/tokenizer/tokenizer_config.json" 932 bcdc6f267b05e1afd27fa622a62fea649bcb941e6dc705d2835883a0746192da
    fetch_one "$COMMON_URL_BASE/special_tokens_map.json" "$DEST/tokenizer/special_tokens_map.json" 414 6fa06efa2785e450051989a6f8fb4416b10149ded485ddd3f127a40734f5cfd0
    ;;
esac
