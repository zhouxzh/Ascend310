#!/usr/bin/env bash
# Candidate gateway for the active MindSpore profile. Formal ports are not
# touched by this wrapper.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${script_dir}"
if (( $# != 0 )); then
  echo "candidate gateway has fixed host 127.0.0.1 and port 7867" >&2
  exit 2
fi

: "${GATEWAY_API_KEY:?set GATEWAY_API_KEY in the board shell}"
export UPSTREAM_BASE_URL="http://127.0.0.1:8090/v1"
export UPSTREAM_API_KEY=""
export UPSTREAM_MODEL="case9-active"
export RAG_ENABLED="false"
export MAX_CONCURRENT_REQUESTS="1"
export REQUEST_MAX_CHARACTERS="4000"
export UPSTREAM_TIMEOUT_SECONDS="300"
export STREAM_MAX_SECONDS="300"
export PUBLIC_MODEL_ID="case9-rag"

exec bash scripts/run_xiaozhi_gateway.sh --host 127.0.0.1 --port 7867
