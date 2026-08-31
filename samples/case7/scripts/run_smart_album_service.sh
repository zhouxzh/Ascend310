#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="0.0.0.0"
PORT="7860"
PID_FILE="${ROOT_DIR}/run/smart_album.pid"
LOG_FILE="${ROOT_DIR}/logs/smart_album.log"
ACTION="start"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT_DIR="$(realpath "$2")"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --pid-file) PID_FILE="$2"; shift 2 ;;
    --log-file) LOG_FILE="$2"; shift 2 ;;
    --stop) ACTION="stop"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$ROOT_DIR" in
  /home/HwHiAiUser/Documents/ai-album/*) ;;
  *) echo "refusing project root outside /home/HwHiAiUser/Documents/ai-album: $ROOT_DIR" >&2; exit 1 ;;
esac

mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")"

# A touchscreen kiosk may be opened through 127.0.0.1, but remote PhotoFrame
# devices need a routable LAN origin when they are given a pull URL.  Allow an
# explicit origin and otherwise derive the address used by the board's default
# route without changing shell startup files.
if [[ -z "${SMART_ALBUM_PUBLIC_URL:-}" ]]; then
  board_ip="$(ip route get 1.1.1.1 2>/dev/null | awk '
    /src/ { for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }
  ' || true)"
  if [[ -z "$board_ip" ]]; then
    board_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -n "$board_ip" ]]; then
    export SMART_ALBUM_PUBLIC_URL="http://${board_ip}:${PORT}"
  fi
fi

stop_service() {
  [[ -f "$PID_FILE" ]] || return 0
  local pid command
  pid="$(cat "$PID_FILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    command="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    if [[ "$command" != *"$ROOT_DIR/app.py"* ]]; then
      echo "PID $pid does not belong to $ROOT_DIR/app.py" >&2
      exit 1
    fi
    kill "$pid"
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
  fi
  rm -f "$PID_FILE"
}

if [[ "$ACTION" == "stop" ]]; then
  stop_service
  exit 0
fi

stop_service

# shellcheck disable=SC1091
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
# shellcheck disable=SC1091
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${PYTHONPATH:-}"
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd "$ROOT_DIR"
python -c 'import acl, faiss, cv2, fastapi, multipart; print("runtime preflight passed")'
python - <<'PY'
from model_registry import ModelRegistry
registry = ModelRegistry(require_artifacts=True)
print("admitted models:", list(registry.ids()))
PY

nohup python "$ROOT_DIR/app.py" \
  --host "$HOST" \
  --port "$PORT" \
  --backend npu \
  --touchscreen \
  >>"$LOG_FILE" 2>&1 &
pid=$!
echo "$pid" > "$PID_FILE"
sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  tail -80 "$LOG_FILE" >&2 || true
  rm -f "$PID_FILE"
  exit 1
fi
echo "smart album started: pid=$pid port=$PORT log=$LOG_FILE"
