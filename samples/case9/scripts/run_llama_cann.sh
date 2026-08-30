#!/usr/bin/env bash
# Start only the CANN-backed llama.cpp server for the local-chat experiment.
# No CPU fallback is configured here. A launch error is an experiment failure.
set -euo pipefail

home_dir="${CASE9_LOCAL_CHAT_HOME:-$HOME/case9-local-chat}"
binary="${LLAMA_SERVER_BIN:-$home_dir/src/llama.cpp/build-cann/bin/llama-server}"
model="${LOCAL_LLM_MODEL:-$home_dir/artifacts/qwen2.5-0.5b-instruct-q4_0.gguf}"
alias="${LOCAL_LLM_ALIAS:-qwen2.5-0.5b-instruct-q4_0}"
port="${LOCAL_LLM_PORT:-8080}"
gpu_layers="${LOCAL_LLM_GPU_LAYERS:-99}"

test -f /usr/local/Ascend/ascend-toolkit/set_env.sh
if [[ ! -x "$binary" ]]; then echo "CANN llama-server binary is unavailable: $binary" >&2; exit 1; fi
if [[ ! -f "$model" ]]; then echo "GGUF model is unavailable: $model" >&2; exit 1; fi
# shellcheck disable=SC1091
set +u
source /usr/local/Ascend/ascend-toolkit/set_env.sh
set -u
echo "Starting experimental CANN llama.cpp on 127.0.0.1:$port with -ngl $gpu_layers"
exec "$binary" --host 127.0.0.1 --port "$port" --model "$model" \
  --alias "$alias" --n-gpu-layers "$gpu_layers"
