#!/usr/bin/env bash
# Launch the browser text UI for the isolated Qwen2.5 candidate gateway.
# The UI remains unauthenticated by design; use only on a trusted LAN.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${script_dir}"

if (( $# != 0 )); then
  echo "candidate text UI has fixed host 0.0.0.0 and port 7868; extra arguments are refused" >&2
  exit 2
fi

: "${GATEWAY_API_KEY:?set GATEWAY_API_KEY in the board shell; it is only passed to the server-side UI}"
export TEXT_CHAT_GATEWAY_URL="http://127.0.0.1:7867/v1"
export TEXT_CHAT_GATEWAY_API_KEY="${GATEWAY_API_KEY}"
export TEXT_CHAT_MODEL="case9-rag"
export TEXT_CHAT_HOST="0.0.0.0"
export TEXT_CHAT_PORT="7868"
export TEXT_CHAT_MAX_CHARACTERS="700"
# Prevent the generic launcher from loading a formal .env/.env.local value
# over the candidate settings above.
export TEXT_CHAT_ENV_ROOT_FILE="/dev/null"
export TEXT_CHAT_ENV_FILE="/dev/null"
export PYTHONNOUSERSITE=1

exec bash scripts/run_text_chat.sh
