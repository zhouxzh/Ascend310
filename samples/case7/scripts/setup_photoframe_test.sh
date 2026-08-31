#!/usr/bin/env bash
set -euo pipefail

# Keep the fixed test selection independent of the board's locale.
export LC_ALL=C

# Prepare a small, deterministic PhotoFrame test on the board.  The normal
# flow is device URL Rotation (the PhotoFrame pulls from 310B).  The
# --push-* flags remain only for explicit legacy maintenance tests; they are
# not part of device registration and are never inferred from a response.
# The script deliberately keeps all image work in the normal upload/index path;
# it does not create thumbnails, JPEG files, EPDGZ files, or frame caches.

BASE_DIR="/home/HwHiAiUser/Documents/ai-album"
SOURCE=""
PROFILE_ID=""
LIMIT=20
SERVER_URL="http://127.0.0.1:7860"
PUBLIC_SERVER_URL="http://192.168.1.135:7860"
DEVICE_URL=""
PUSH_URL=""
PUSH_PROTOCOL=""
DEVICE_ID=""
ROTATE_NOW=0
PUSH_NOW=0
POLL_SECONDS=900
REPORT_PATH=""

usage() {
  cat <<'EOF'
Usage: setup_photoframe_test.sh --source shared/incoming/photoframe-test --profile-id PROFILE [options]

Prepare a deterministic PhotoFrame playlist. The profile is required and is
never inferred from dimensions or a device name. Registration and normal
operation use device URL Rotation: the PhotoFrame requests the returned
Case7 URL. The --push-url/--push-protocol options are legacy maintenance
only and explicitly send to a known endpoint; the protocol is never guessed.
The source must resolve below
/home/HwHiAiUser/Documents/ai-album/shared/incoming/.

Options:
  --source PATH          Input directory (required; under shared/incoming)
  --profile-id PROFILE   Required: waveshare_photopainter_73 or seeedstudio_reterminal_e1002
  --limit N              Number of sorted images (default: 20)
  --server-url URL       Local server URL used for API calls (default: http://127.0.0.1:7860)
  --public-server-url URL URL used by optional URL Rotation (default: http://192.168.1.135:7860)
  --device-url URL       Optional device base URL for an immediate rotate request
  --push-url URL         [legacy maintenance] explicit device base URL for server-side JPEG push
  --push-protocol NAME   [legacy maintenance] required with --push-url
  --device-id ID         Existing PhotoFrame device ID; otherwise create one
  --rotate-now           POST /api/rotate to --device-url after configuration
  --push-now             Send the current rendered image immediately (JPEG or BMP by protocol)
  --poll-timeout SEC     Upload job timeout (default: 900)
  --report PATH          Report path under shared/reports (default: timestamped)
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="${2:?missing value for --source}"; shift 2 ;;
    --profile-id) PROFILE_ID="${2:?missing value for --profile-id}"; shift 2 ;;
    --limit) LIMIT="${2:?missing value for --limit}"; shift 2 ;;
    --server-url) SERVER_URL="${2:?missing value for --server-url}"; shift 2 ;;
    --public-server-url) PUBLIC_SERVER_URL="${2:?missing value for --public-server-url}"; shift 2 ;;
    --device-url) DEVICE_URL="${2:?missing value for --device-url}"; shift 2 ;;
    --push-url) PUSH_URL="${2:?missing value for --push-url}"; shift 2 ;;
    --push-protocol) PUSH_PROTOCOL="${2:?missing value for --push-protocol}"; shift 2 ;;
    --device-id) DEVICE_ID="${2:?missing value for --device-id}"; shift 2 ;;
    --rotate-now) ROTATE_NOW=1; shift ;;
    --push-now) PUSH_NOW=1; shift ;;
    --poll-timeout) POLL_SECONDS="${2:?missing value for --poll-timeout}"; shift 2 ;;
    --report) REPORT_PATH="${2:?missing value for --report}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$SOURCE" ]] || { echo "--source is required" >&2; usage >&2; exit 2; }
[[ "$PROFILE_ID" == "waveshare_photopainter_73" || "$PROFILE_ID" == "seeedstudio_reterminal_e1002" ]] || {
  echo "--profile-id is required and must be waveshare_photopainter_73 or seeedstudio_reterminal_e1002" >&2
  exit 2
}
[[ "$LIMIT" =~ ^[1-9][0-9]*$ ]] || { echo "--limit must be a positive integer" >&2; exit 2; }
[[ "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "--poll-timeout must be a positive integer" >&2; exit 2; }
if [[ "$PUSH_NOW" == "1" && -z "$PUSH_URL" ]]; then
  echo "--push-now requires --push-url and --push-protocol" >&2
  exit 2
fi
if [[ -n "$PUSH_URL" && "$PUSH_PROTOCOL" != "photoframe_api" && "$PUSH_PROTOCOL" != "waveshare_dataup" && "$PUSH_PROTOCOL" != "case7_push" ]]; then
  echo "--push-url requires --push-protocol photoframe_api, waveshare_dataup, or case7_push; refusing to guess firmware" >&2
  exit 2
fi
if [[ -z "$PUSH_URL" && -n "$PUSH_PROTOCOL" ]]; then
  echo "--push-protocol requires --push-url" >&2
  exit 2
fi
if [[ -n "$DEVICE_ID" && ! "$DEVICE_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "--device-id contains unsupported characters" >&2
  exit 2
fi

case "$SERVER_URL" in */) SERVER_URL="${SERVER_URL%/}" ;; esac
case "$PUBLIC_SERVER_URL" in */) PUBLIC_SERVER_URL="${PUBLIC_SERVER_URL%/}" ;; esac
case "$DEVICE_URL" in */) DEVICE_URL="${DEVICE_URL%/}" ;; esac
case "$PUSH_URL" in */) PUSH_URL="${PUSH_URL%/}" ;; esac

# Use the board's conda/CANN shell, matching the service launcher. No package
# installation or environment mutation is performed here.
if [[ -f /usr/local/miniconda3/etc/profile.d/conda.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/miniconda3/etc/profile.d/conda.sh
  conda activate base
fi
if [[ -f /usr/local/Ascend/ascend-toolkit/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
fi
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v python >/dev/null || { echo "board Python is required" >&2; exit 1; }

if [[ "$SOURCE" = /* ]]; then
  SOURCE_DIR="$(realpath -e -- "$SOURCE")"
else
  SOURCE_REL="$SOURCE"
  [[ "$SOURCE_REL" == ./* ]] && SOURCE_REL="${SOURCE_REL#./}"
  case "$SOURCE_REL" in
    shared/incoming|shared/incoming/*) SOURCE_DIR="$(realpath -e -- "$BASE_DIR/$SOURCE_REL")" ;;
    *) echo "source must be relative to shared/incoming or an absolute path below it" >&2; exit 1 ;;
  esac
fi
case "$SOURCE_DIR" in
  "$BASE_DIR/shared/incoming"/*) ;;
  *) echo "refusing source outside $BASE_DIR/shared/incoming: $SOURCE_DIR" >&2; exit 1 ;;
esac
[[ -d "$SOURCE_DIR" ]] || { echo "source directory does not exist: $SOURCE_DIR" >&2; exit 1; }

if [[ -z "$REPORT_PATH" ]]; then
  REPORT_PATH="$BASE_DIR/shared/reports/photoframe-test-$(date +%Y%m%d-%H%M%S)-$$.json"
elif [[ "$REPORT_PATH" != /* ]]; then
  REPORT_PATH="$BASE_DIR/$REPORT_PATH"
fi
case "$REPORT_PATH" in
  "$BASE_DIR/shared/reports"/*) ;;
  *) echo "refusing report outside $BASE_DIR/shared/reports: $REPORT_PATH" >&2; exit 1 ;;
esac

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/case7-photoframe.XXXXXX")"
cleanup() { rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT
SELECTED_TSV="$WORK_DIR/selected.tsv"
VALIDATION_JSON="$WORK_DIR/validation.json"
HEALTH_JSON="$WORK_DIR/health.json"
UPLOAD_JSON="$WORK_DIR/upload.json"
JOB_JSON="$WORK_DIR/job.json"
DEVICE_JSON="$WORK_DIR/device.json"
PLAYLIST_JSON="$WORK_DIR/playlist.json"
PHOTO_IDS_JSON="$WORK_DIR/photo_ids.json"
STATE_JSON="$WORK_DIR/state.json"
PUSH_JSON="$WORK_DIR/push.json"
STATS_BEFORE_JSON="$WORK_DIR/stats-before.json"
STATS_AFTER_JSON="$WORK_DIR/stats-after.json"

# Reject symlinks and choose a stable lexicographic prefix. The fixed test set
# is intentionally limited to one directory so an accidental broad upload is
# impossible.
mapfile -d '' ALL_FILES < <(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type l -print0)
if [[ "${#ALL_FILES[@]}" -ne 0 ]]; then
  echo "source contains symlinks; refusing: ${ALL_FILES[0]}" >&2
  exit 1
fi
mapfile -d '' ALL_FILES < <(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type f -print0 | sort -z)
[[ "${#ALL_FILES[@]}" -ge "$LIMIT" ]] || { echo "source has ${#ALL_FILES[@]} files; need at least $LIMIT" >&2; exit 1; }
SELECTED=("${ALL_FILES[@]:0:LIMIT}")

if [[ "$LIMIT" -eq 20 ]]; then
  for index in "${!SELECTED[@]}"; do
    expected="CIMG$((2780 + index)).JPG"
    actual="$(basename -- "${SELECTED[$index]}")"
    [[ "$actual" == "$expected" ]] || {
      echo "fixed 20-photo batch must start at CIMG2780.JPG and end at CIMG2799.JPG; got $actual at position $((index + 1))" >&2
      exit 1
    }
  done
fi

printf '%s\n' "${SELECTED[@]}" > "$SELECTED_TSV"

# Decode and hash before uploading. Pillow is used only as an input validity
# check; NPU encoding remains in the server's single worker.
python - "$SELECTED_TSV" > "$VALIDATION_JSON" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

allowed = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
rows = []
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    path = Path(raw)
    suffix = path.suffix.lower()
    if suffix not in allowed:
        raise SystemExit(f"unsupported image extension: {path.name}")
    size = path.stat().st_size
    if size <= 0 or size > 25 * 1024 * 1024:
        raise SystemExit(f"image exceeds 25 MB or is empty: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image.load()
    except Exception as exc:
        raise SystemExit(f"cannot decode {path.name}: {exc}") from exc
    if width * height > 50_000_000:
        raise SystemExit(f"image exceeds 50 MP: {path.name}")
    rows.append({
        "filename": path.name,
        "sha256": digest.hexdigest(),
        "size": size,
        "width": width,
        "height": height,
        "source_name": path.name,
    })
print(json.dumps(rows, ensure_ascii=False, indent=2))
PY

echo "validated ${#SELECTED[@]} images under $SOURCE_DIR"

# Fail before copying anything into the upload worker when the board is not
# ready for production indexing.  This keeps the NPU-only contract explicit:
# the script never turns a metadata-only job into a successful playlist.
curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
  "$SERVER_URL/api/health" > "$HEALTH_JSON"
python - "$HEALTH_JSON" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
required = {
    "mobileclip_s0__npu__mixed_fp16",
    "chinese_clip_rn50__npu__mixed_fp16",
    "resnet50_feature__npu__mixed_fp16",
}
missing = sorted(required - set(value.get("admitted_models") or []))
if value.get("status") != "ready" or missing:
    raise SystemExit(
        "server is not ready for NPU indexing; missing admitted models: "
        + ", ".join(missing or ["unknown"])
    )
PY
echo "server health is ready; all required NPU models are admitted"

curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
  "$SERVER_URL/api/index/stats" > "$STATS_BEFORE_JSON"

upload_args=(--silent --show-error --fail --connect-timeout 10 --max-time 120 -X POST "$SERVER_URL/api/photos/upload")
for path in "${SELECTED[@]}"; do
  upload_args+=( -F "files=@$path" )
done
curl "${upload_args[@]}" > "$UPLOAD_JSON"
JOB_ID="$(python - "$UPLOAD_JSON" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
job_id = value.get("job_id")
if not job_id:
    raise SystemExit("upload response did not contain job_id")
print(job_id)
PY
)"
echo "upload job: $JOB_ID"

deadline=$((SECONDS + POLL_SECONDS))
while :; do
  curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
    "$SERVER_URL/api/jobs/$JOB_ID" > "$JOB_JSON"
  JOB_STATUS="$(python - "$JOB_JSON" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("status", ""))
PY
)"
  case "$JOB_STATUS" in
    completed|succeeded) break ;;
    failed|error)
      python - "$JOB_JSON" <<'PY' >&2
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(value.get("error", "upload/index job failed"))
PY
      ;;
    queued|running) [[ "$SECONDS" -lt "$deadline" ]] || { echo "upload job timed out: $JOB_ID" >&2; exit 1; }; sleep 2 ;;
    *) echo "unexpected upload job status: $JOB_STATUS" >&2; exit 1 ;;
  esac
done

mapfile -t PHOTO_IDS < <(python - "$JOB_JSON" "$VALIDATION_JSON" <<'PY'
import json, sys
job = json.load(open(sys.argv[1], encoding="utf-8"))
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
summary = job.get("summary") or {}
ids = summary.get("photo_ids") or job.get("photo_ids") or summary.get("photo_ids_by_file")
if isinstance(ids, dict):
    ids = [ids.get(row["filename"]) for row in manifest]
if not isinstance(ids, list) or len(ids) != len(manifest) or any(not isinstance(v, int) or v <= 0 for v in ids):
    raise SystemExit("completed upload job must include one positive integer photo_id per input file")
if len(set(ids)) != len(ids):
    raise SystemExit("upload job returned duplicate photo IDs")
for value in ids:
    print(value)
PY
)
[[ "${#PHOTO_IDS[@]}" -eq "$LIMIT" ]] || { echo "expected $LIMIT photo IDs, got ${#PHOTO_IDS[@]}" >&2; exit 1; }
printf '%s\n' "${PHOTO_IDS[@]}" | python -c 'import json, sys; print(json.dumps([int(line) for line in sys.stdin if line.strip()]))' > "$PHOTO_IDS_JSON"

if [[ -z "$DEVICE_ID" ]]; then
  if [[ "$PROFILE_ID" == "waveshare_photopainter_73" ]]; then
    DEVICE_NAME="waveshare-photopainter-test"
  else
    DEVICE_NAME="e1002-photoframe-test"
  fi
  python - "$DEVICE_NAME" "$PROFILE_ID" > "$WORK_DIR/create-device.json" <<'PY'
import json
import sys

name, profile_id = sys.argv[1:]
print(json.dumps({
    "name": name,
    "profile_id": profile_id,
    "display": {
        "kind": "photoframe",
        "width": 800,
        "height": 480,
        "codecs": ["jpeg"],
        "max_bytes": 2097152,
        "rotation": 0,
    },
}, separators=(",", ":")))
PY
  curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
    -H 'Content-Type: application/json' -X POST \
    --data-binary "@$WORK_DIR/create-device.json" "$SERVER_URL/api/admin/devices" > "$DEVICE_JSON"
  DEVICE_ID="$(python - "$DEVICE_JSON" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
if not value.get("device_id"):
    raise SystemExit("device creation response did not contain device_id")
print(value["device_id"])
PY
)"
else
  # Confirm the supplied ID exists before mutating its playlist. The public
  # admin collection is intentionally used because older releases do not have
  # a single-device GET route.
  curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
    "$SERVER_URL/api/admin/devices" > "$WORK_DIR/devices.json"
  python - "$WORK_DIR/devices.json" "$DEVICE_ID" "$PROFILE_ID" <<'PY'
import json, sys
devices = json.load(open(sys.argv[1], encoding="utf-8")).get("devices") or []
matches = [item for item in devices if str(item.get("device_id")) == sys.argv[2]]
if not matches:
    raise SystemExit(f"unknown PhotoFrame device: {sys.argv[2]}")
actual = matches[0].get("profile_id") or (matches[0].get("display") or {}).get("profile_id")
if actual != sys.argv[3]:
    raise SystemExit(f"device profile mismatch: expected {sys.argv[3]}, got {actual or 'missing'}")
PY
fi

PLAYLIST_PAYLOAD="$(python - "${PHOTO_IDS[@]}" <<'PY'
import json, sys
ids = [int(value) for value in sys.argv[1:]]
print(json.dumps({
    "photo_ids": ids,
    "rotation_cron": ["*/5 * *"],
    "start_immediately": True,
}, separators=(",", ":")))
PY
)"
curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
  -H 'Content-Type: application/json' -X POST \
  -d "$PLAYLIST_PAYLOAD" "$SERVER_URL/api/admin/devices/$DEVICE_ID/playlist" > "$PLAYLIST_JSON"
curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
  "$SERVER_URL/api/admin/devices/$DEVICE_ID/state" > "$STATE_JSON"
curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
  "$SERVER_URL/api/index/stats" > "$STATS_AFTER_JSON"

mkdir -p "$(dirname "$REPORT_PATH")"
python - "$REPORT_PATH" "$VALIDATION_JSON" "$PHOTO_IDS_JSON" "$JOB_JSON" "$PLAYLIST_JSON" "$STATE_JSON" "$STATS_BEFORE_JSON" "$STATS_AFTER_JSON" "$DEVICE_ID" "$PROFILE_ID" "$SERVER_URL" "$PUBLIC_SERVER_URL" "$DEVICE_URL" "$PUSH_URL" "$PUSH_PROTOCOL" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

report_path, validation_path, ids_path, job_path, playlist_path, state_path, stats_before_path, stats_after_path, device_id, profile_id, server_url, public_url, device_url, push_url, push_protocol = sys.argv[1:]
validation = json.load(open(validation_path, encoding="utf-8"))
photo_ids = json.load(open(ids_path, encoding="utf-8"))
job = json.load(open(job_path, encoding="utf-8"))
playlist = json.load(open(playlist_path, encoding="utf-8"))
state = json.load(open(state_path, encoding="utf-8"))
stats_before = json.load(open(stats_before_path, encoding="utf-8"))
stats_after = json.load(open(stats_after_path, encoding="utf-8"))
for row, photo_id in zip(validation, photo_ids):
    row["photo_id"] = photo_id
# Never persist device tokens or token hashes in the evidence report.
for value in (job, playlist):
    if isinstance(value, dict):
        value.pop("token", None)
        value.pop("token_hash", None)
        value.pop("device_token", None)
if isinstance(state, dict):
    state.pop("token", None)
report = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "source_dataset": "local-photoframe-test",
    "profile_id": profile_id,
    "rotation_cron": ["*/5 * *"],
    "server_url": server_url,
    "public_image_url": f"{public_url}/api/devices/{device_id}/photoframe",
    "device_url": device_url or None,
    "push_url": push_url or None,
    "push_protocol": push_protocol or None,
    "device_id": device_id,
    "files": validation,
    "upload_job": job,
    "playlist": playlist,
    "state": state,
    "index_stats_before": stats_before,
    "index_stats_after": stats_after,
    "notes": [
        "The profile is explicit; the script never probes or guesses firmware from dimensions or names.",
        "Images are validated and indexed through the normal serial NPU upload task.",
        "No derived image or frame cache is created by this script.",
    ],
}
Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

IMAGE_URL="$PUBLIC_SERVER_URL/api/devices/$DEVICE_ID/photoframe"
echo
echo "PhotoFrame playlist configured: $DEVICE_ID"
echo "rotation_cron: */5 * *"
echo "Optional URL Rotation image_url: $IMAGE_URL"
echo "Optional URL Rotation configuration for profile $PROFILE_ID (enter in the device local web UI):"
python - "$IMAGE_URL" <<'PY'
import json, sys
print(json.dumps({
    "auto_rotate": True,
    "rotate_cron": ["*/5 * *"],
    "rotation_mode": "url",
    "image_url": sys.argv[1],
    "deep_sleep_enabled": False,
}, ensure_ascii=False, indent=2))
PY
echo "report: $REPORT_PATH"

PUSH_STATUS="not_requested"
if [[ -n "$PUSH_URL" ]]; then
  echo "configuring explicit active-push target: $PUSH_URL (protocol=$PUSH_PROTOCOL)"
  python - "$PUSH_URL" "$PUSH_PROTOCOL" >"$PUSH_JSON" <<'PY'
import json, sys
print(json.dumps({"push": {
    "enabled": True,
    "base_url": sys.argv[1],
    "protocol": sys.argv[2],
    "timeout_seconds": 60,
    "attempts": 1,
}}))
PY
  curl --silent --show-error --fail --connect-timeout 10 --max-time 30 \
    -X PATCH "$SERVER_URL/api/admin/devices/$DEVICE_ID" \
    -H 'Content-Type: application/json' --data-binary "@$PUSH_JSON" >/dev/null
  if [[ "$PUSH_NOW" == "1" ]]; then
    echo "sending first image to $PROFILE_ID via protocol $PUSH_PROTOCOL"
    if curl --silent --show-error --fail --connect-timeout 10 --max-time 130 \
        -X POST "$SERVER_URL/api/admin/devices/$DEVICE_ID/push" \
        -H 'Content-Type: application/json' --data '{"force":false,"force_send":true}'; then
      PUSH_STATUS="succeeded"
      echo
    else
      PUSH_STATUS="failed"
      echo "warning: active push failed; inspect /api/admin/devices/$DEVICE_ID/state" >&2
    fi
  else
    PUSH_STATUS="configured"
  fi
fi

ROTATE_STATUS="not_requested"
if [[ "$ROTATE_NOW" == "1" ]]; then
  [[ -n "$DEVICE_URL" ]] || { echo "--rotate-now requires --device-url" >&2; exit 2; }
  echo "requesting immediate rotation for $PROFILE_ID: $DEVICE_URL/api/rotate"
  if curl --silent --show-error --fail --connect-timeout 10 --max-time 130 \
      -X POST "$DEVICE_URL/api/rotate"; then
    ROTATE_STATUS="succeeded"
    echo
  else
    # A sleeping/offline device must not invalidate the completed server-side
    # upload and playlist. The next explicit push attempt can retry it.
    ROTATE_STATUS="failed"
    echo "warning: device /api/rotate failed; server playlist remains configured" >&2
  fi
fi

python - "$REPORT_PATH" "$ROTATE_STATUS" "$PUSH_STATUS" <<'PY'
import json
import sys
from pathlib import Path
path, status, push_status = sys.argv[1:]
value = json.loads(Path(path).read_text(encoding="utf-8"))
value["rotate_now_status"] = status
value["push_status"] = push_status
Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

# The server has already copied accepted images into managed storage. Remove
# only the validated incoming files after the full playlist request succeeds.
rm -f -- "${SELECTED[@]}"
echo "removed ${#SELECTED[@]} temporary incoming files; managed originals were preserved"
