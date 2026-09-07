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
# The board X session can expose a second HDMI output.  Without an explicit
# mapping libinput sends the QDtech touch coordinates across the whole virtual
# desktop (2944 px here), producing a visible horizontal offset on the 1920 px
# panel.  Reapply the mapping at every kiosk launch because X resets it after a
# reboot or display hotplug.
if command -v xinput >/dev/null 2>&1; then
  touch_name="${SMART_ALBUM_TOUCH_DEVICE:-QDtech MPI1001}"
  touch_output="${SMART_ALBUM_TOUCH_OUTPUT:-HDMI-1}"
  touch_id="$(xinput list --id-only "$touch_name" 2>/dev/null || true)"
  if [[ -n "$touch_id" ]] && xrandr --query 2>/dev/null | grep -Eq "^${touch_output} connected"; then
    xinput map-to-output "$touch_id" "$touch_output"
  else
    echo "warning: touchscreen mapping not applied (device=$touch_name output=$touch_output)" >&2
  fi
fi
mkdir -p /home/HwHiAiUser/Documents/ai-album/shared/logs
nohup firefox --kiosk --new-window "$URL" \
  >>/home/HwHiAiUser/Documents/ai-album/shared/logs/firefox-kiosk.log 2>&1 &
echo "Firefox kiosk opened: $URL"
