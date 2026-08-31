#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${1:-}" ]]; then
  BASE_URL="$1"
elif [[ -n "${SMART_ALBUM_PUBLIC_URL:-}" ]]; then
  BASE_URL="$SMART_ALBUM_PUBLIC_URL"
else
  board_ip="$(ip route get 1.1.1.1 2>/dev/null | awk '
    /src/ { for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }
  ' || true)"
  if [[ -z "$board_ip" ]]; then
    board_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  BASE_URL="${board_ip:+http://${board_ip}:7860}"
  BASE_URL="${BASE_URL:-http://127.0.0.1:7860}"
fi
BASE_URL="${BASE_URL%/}"
case "$BASE_URL" in
  *\?*) URL="${BASE_URL}&mode=touchscreen&ui=20260830-provision-pull-v10" ;;
  *) URL="${BASE_URL}?mode=touchscreen&ui=20260830-provision-pull-v10" ;;
esac
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/HwHiAiUser/.Xauthority}"

curl --fail --silent --show-error "$BASE_URL/api/health" >/dev/null
mkdir -p /home/HwHiAiUser/Documents/ai-album/shared/logs
nohup firefox --kiosk --new-window "$URL" \
  >>/home/HwHiAiUser/Documents/ai-album/shared/logs/firefox-kiosk.log 2>&1 &
echo "Firefox kiosk opened: $URL"
