#!/usr/bin/env bash
# Launch the Qwen2.5 StaticCache candidate gateway in an isolated process.
# This wrapper never edits .env and never promotes the candidate ports.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${script_dir}"

if (( $# != 0 )); then
  echo "candidate gateway has fixed host 127.0.0.1 and port 7867; extra arguments are refused" >&2
  exit 2
fi

: "${GATEWAY_API_KEY:?set GATEWAY_API_KEY in the board shell; it is never stored in this script}"
export UPSTREAM_BASE_URL="http://127.0.0.1:8084/v1"
export UPSTREAM_API_KEY=""
export UPSTREAM_MODEL="qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
export RAG_ENABLED="false"
export MAX_CONCURRENT_REQUESTS="1"
export REQUEST_MAX_CHARACTERS="768"
export PUBLIC_MODEL_ID="case9-rag"
export PYTHONNOUSERSITE=1

exec bash scripts/run_xiaozhi_gateway.sh --host 127.0.0.1 --port 7867
