#!/usr/bin/env bash
set -euo pipefail

SSH_TARGET="HwHiAiUser@192.168.1.135"
REMOTE_DIR="/home/HwHiAiUser/Documents/ai-album"
RELEASE_ID="$(date +%Y%m%d-%H%M%S)"
APPLY=0
SSH_BIN="${CASE7_SSH_BIN:-ssh}"
SCP_BIN="${CASE7_SCP_BIN:-scp}"
SSH_CONNECT_TIMEOUT="${CASE7_SSH_CONNECT_TIMEOUT:-15}"
SSH_ARGS=(
  -o "BatchMode=yes"
  -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT}"
  -o "ConnectionAttempts=1"
)
if [[ -n "${CASE7_SSH_IDENTITY:-}" ]]; then
  SSH_ARGS+=( -i "$CASE7_SSH_IDENTITY" )
fi
if [[ -n "${CASE7_SSH_KNOWN_HOSTS:-}" ]]; then
  SSH_ARGS+=( -o "UserKnownHostsFile=${CASE7_SSH_KNOWN_HOSTS}" )
fi

usage() {
  cat <<'EOF'
Usage: deploy_ascend8t.sh [options]

Options:
  --ssh-target USER@HOST  SSH target (default: HwHiAiUser@192.168.1.135)
  --remote-dir PATH       Must be /home/HwHiAiUser/Documents/ai-album
  --release-id ID          Release directory suffix
  CASE7_SSH_BIN=PATH       SSH executable (useful from WSL)
  CASE7_SCP_BIN=PATH       SCP executable (useful from WSL)
  CASE7_SSH_IDENTITY=PATH  Explicit private key
  CASE7_SSH_KNOWN_HOSTS=PATH  Explicit known-hosts file
  --apply                 Upload and activate; default is dry-run
  --dry-run                Print the scoped release without uploading (default)
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-target) SSH_TARGET="${2:?missing value for --ssh-target}"; shift 2 ;;
    --remote-dir) REMOTE_DIR="${2:?missing value for --remote-dir}"; shift 2 ;;
    --release-id) RELEASE_ID="${2:?missing value for --release-id}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

EXPECTED_REMOTE="/home/HwHiAiUser/Documents/ai-album"
if [[ "$REMOTE_DIR" != "$EXPECTED_REMOTE" ]]; then
  echo "refusing remote directory: $REMOTE_DIR" >&2
  exit 1
fi
if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "release id contains unsupported characters" >&2
  exit 1
fi

CASE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="$REMOTE_DIR/releases/$RELEASE_ID"
FILES=(
  app.py candidate_manifest.json config.py embedding_backend.py epaper_album.py
  epaper_display.py feature_extractor.py model_registry.py photo_index.py
  admin_auth.py display_policy.py server_config.py device_registry.py
  smart_selector.py photoframe_push.py photoframe_provisioning.py prepare_models.py requirements.txt requirements-board.txt
  requirements-models.txt setup.sh README.md web/index.html web/style.css web/app.js
  atc_configs/mobileclip_s0_image_keep_dtype.cfg
  atc_configs/mobileclip_s0_image_precision/C0.keep_dtype.cfg
  atc_configs/mobileclip_s0_image_precision/C1.keep_dtype.cfg
  atc_configs/mobileclip_s0_image_precision/C2.keep_dtype.cfg
  atc_configs/mobileclip_s0_image_precision/C3.keep_dtype.cfg
  atc_configs/mobileclip_s0_image_precision/C4.keep_dtype.cfg
  scripts/deploy_ascend8t.sh scripts/activate_release.sh scripts/run_smart_album_service.sh
  scripts/launch_touchscreen_kiosk.sh scripts/collect_system_status.sh
  scripts/run_mobileclip_precision_sweep.py scripts/promote_mobileclip_precision_candidate.py
  scripts/rewrite_mobileclip_group_conv.py
  scripts/setup_photoframe_test.sh
  esp32/README.md esp32/patches/0001-case7-push-endpoint.patch
  scripts/prepare_coco_cn.py scripts/evaluate_coco_cn.py scripts/benchmark_case7.py
  tests/test_app_contract.py tests/test_benchmark_case7.py tests/test_coco_cn.py
  tests/test_embedding_manager.py tests/test_epaper_display.py tests/test_model_registry.py
  tests/test_photo_index.py tests/test_photo_storage.py tests/test_prepare_models.py tests/test_server_config.py
  tests/test_device_registry.py tests/test_photoframe_push.py tests/test_smart_selector.py tests/test_scheduler_push.py tests/test_server_api.py
  tests/test_photoframe_provisioning.py
  tests/test_admin_auth.py tests/test_display_policy.py
  tests/test_mobileclip_precision_sweep.py tests/test_precision_pipeline.py
  tests/test_mobileclip_precision_promotion.py tests/test_mobileclip_group_conv_rewrite.py
  docs/00-github-research-and-porting-plan.md
  docs/01-coco-cn-test-protocol.md docs/02-ascend310b4-deployment-and-acceptance.md
  docs/03-album-server-api-and-esp32-protocol.md docs/04-photopainter-7in3-integration.md
  docs/05-device-policy-rendering-and-security.md docs/06-photopainter-deployment-and-acceptance.md
  docs/07-touchscreen-ui-and-operations.md docs/08-model-pipeline-and-npu-admission.md
  docs/09-index-storage-and-photo-lifecycle.md docs/10-smart-selection-and-weather.md
  docs/11-photoframe-active-push.md docs/12-mobileclip-cross-board-compatibility.md
  docs/13-photopainter-serial-ip-and-wifi.md
)

for file in "${FILES[@]}"; do
  [[ -f "$CASE_ROOT/$file" ]] || { echo "deployment source is missing: $file" >&2; exit 1; }
done

echo "Target: $SSH_TARGET:$RELEASE_DIR"
printf '  %s\n' "${FILES[@]}"
if [[ "$APPLY" != "1" ]]; then
  echo "Dry run only. Re-run with --apply to deploy."
  exit 0
fi

ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/case7-${RELEASE_ID}.XXXXXX.tgz")"
cleanup() { rm -f "$ARCHIVE"; }
trap cleanup EXIT

tar -C "$CASE_ROOT" -czf "$ARCHIVE" -- "${FILES[@]}"
"$SSH_BIN" "${SSH_ARGS[@]}" "$SSH_TARGET" "mkdir -p '$RELEASE_DIR'"
"$SCP_BIN" "${SSH_ARGS[@]}" "$ARCHIVE" "$SSH_TARGET:$RELEASE_DIR/release.tgz"
"$SSH_BIN" "${SSH_ARGS[@]}" "$SSH_TARGET" "cd '$RELEASE_DIR' && tar -xzf release.tgz && rm -f release.tgz && chmod +x setup.sh scripts/*.sh && bash scripts/activate_release.sh '$RELEASE_DIR' '$REMOTE_DIR'"
echo "Deployed: http://${SSH_TARGET##*@}:7860"
