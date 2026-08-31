#!/usr/bin/env bash
set -euo pipefail

RELEASE_DIR="$(realpath "$1")"
BASE_DIR="$(realpath "$2")"
EXPECTED_BASE="/home/HwHiAiUser/Documents/ai-album"

if [[ "$BASE_DIR" != "$EXPECTED_BASE" ]]; then
  echo "refusing base directory: $BASE_DIR" >&2
  exit 1
fi
case "$RELEASE_DIR" in
  "$BASE_DIR"/releases/*) ;;
  *) echo "release is outside $BASE_DIR/releases: $RELEASE_DIR" >&2; exit 1 ;;
esac

mkdir -p \
  "$BASE_DIR/shared/models" \
  "$BASE_DIR/shared/data" \
  "$BASE_DIR/shared/photos" \
  "$BASE_DIR/shared/incoming" \
  "$BASE_DIR/shared/incoming/photoframe-test" \
  "$BASE_DIR/shared/reports" \
  "$BASE_DIR/shared/secrets" \
  "$BASE_DIR/shared/run" \
  "$BASE_DIR/shared/logs"

# User photographs are kept outside the release/shared tree.  The service
# resolves the same default from the account's home directory
# (~/Pictures/ai-album); creating these directories here makes the ownership
# boundary explicit without putting Pictures into the release archive.
PHOTO_LIBRARY_DIR="$(realpath -m -- "${SMART_ALBUM_PHOTO_DIR:-$HOME/Pictures/ai-album}")"
case "$PHOTO_LIBRARY_DIR" in
  /|"$BASE_DIR"|"$BASE_DIR"/*)
    echo "refusing photo library inside the release tree: $PHOTO_LIBRARY_DIR" >&2
    exit 1
    ;;
esac
mkdir -p "$PHOTO_LIBRARY_DIR/imports" "$PHOTO_LIBRARY_DIR/.upload-tmp"

if [[ ! -f "$BASE_DIR/shared/models/registry.json" ]]; then
  printf '%s\n' '{"schema_version":1,"generated_at":null,"hardware":null,"models":[]}' \
    > "$BASE_DIR/shared/models/registry.json"
fi

ln -sfn "$BASE_DIR/shared/models" "$RELEASE_DIR/models"
ln -sfn "$BASE_DIR/shared/data" "$RELEASE_DIR/data"
ln -sfn "$BASE_DIR/shared/photos" "$RELEASE_DIR/photos"
ln -sfn "$BASE_DIR/shared/reports" "$RELEASE_DIR/reports"
ln -sfn "$BASE_DIR/shared/secrets" "$RELEASE_DIR/secrets"

bash "$RELEASE_DIR/setup.sh" board
bash "$RELEASE_DIR/scripts/run_smart_album_service.sh" \
  --root "$RELEASE_DIR" \
  --port 7861 \
  --pid-file "$BASE_DIR/shared/run/smoke.pid" \
  --log-file "$BASE_DIR/shared/logs/smoke.log"

ready=0
for _ in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:7861/api/health >/dev/null; then
    ready=1
    break
  fi
  sleep 0.5
done
if [[ "$ready" != "1" ]]; then
  tail -100 "$BASE_DIR/shared/logs/smoke.log" >&2 || true
  bash "$RELEASE_DIR/scripts/run_smart_album_service.sh" \
    --root "$RELEASE_DIR" \
    --pid-file "$BASE_DIR/shared/run/smoke.pid" \
    --log-file "$BASE_DIR/shared/logs/smoke.log" \
    --stop >/dev/null 2>&1 || true
  exit 1
fi
bash "$RELEASE_DIR/scripts/run_smart_album_service.sh" \
  --root "$RELEASE_DIR" \
  --pid-file "$BASE_DIR/shared/run/smoke.pid" \
  --log-file "$BASE_DIR/shared/logs/smoke.log" \
  --stop

OLD_ROOT=""
OLD_WAS_RUNNING=0
if [[ -L "$BASE_DIR/current" ]]; then
  OLD_ROOT="$(readlink -f "$BASE_DIR/current")"
  if [[ -f "$BASE_DIR/shared/run/smart_album.pid" ]]; then
    OLD_WAS_RUNNING=1
    bash "$OLD_ROOT/scripts/run_smart_album_service.sh" \
      --root "$OLD_ROOT" \
      --pid-file "$BASE_DIR/shared/run/smart_album.pid" \
      --log-file "$BASE_DIR/shared/logs/smart_album.log" \
      --stop
  fi
fi

wait_for_health() {
  local port="$1"
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

rollback() {
  local reason="$1"
  echo "activation failed: $reason; restoring previous release" >&2
  bash "$RELEASE_DIR/scripts/run_smart_album_service.sh" \
    --root "$RELEASE_DIR" \
    --pid-file "$BASE_DIR/shared/run/smart_album.pid" \
    --log-file "$BASE_DIR/shared/logs/smart_album.log" \
    --stop >/dev/null 2>&1 || true
  if [[ -n "$OLD_ROOT" ]]; then
    ln -sfn "$OLD_ROOT" "$BASE_DIR/current"
    if [[ "$OLD_WAS_RUNNING" == "1" ]]; then
      if ! bash "$OLD_ROOT/scripts/run_smart_album_service.sh" \
        --root "$OLD_ROOT" \
        --pid-file "$BASE_DIR/shared/run/smart_album.pid" \
        --log-file "$BASE_DIR/shared/logs/smart_album.log"; then
        echo "rollback could not restart $OLD_ROOT" >&2
        return 1
      fi
      if ! wait_for_health 7860; then
        echo "rollback release did not become healthy" >&2
        return 1
      fi
    fi
  else
    rm -f "$BASE_DIR/current"
  fi
  return 1
}

ln -sfn "$RELEASE_DIR" "$BASE_DIR/current"
if ! bash "$RELEASE_DIR/scripts/run_smart_album_service.sh" \
  --root "$RELEASE_DIR" \
  --port 7860 \
  --pid-file "$BASE_DIR/shared/run/smart_album.pid" \
  --log-file "$BASE_DIR/shared/logs/smart_album.log"; then
  rollback "service launch"
fi

if ! wait_for_health 7860; then
  tail -100 "$BASE_DIR/shared/logs/smart_album.log" >&2 || true
  rollback "health check"
fi
curl -fsS http://127.0.0.1:7860/api/health
