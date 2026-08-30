#!/usr/bin/env bash
set -euo pipefail

HOST="127.0.0.1"
PORT="${QWEN25_PORT:-8082}"
ROOT="${QWEN25_ROOT:-$HOME/case9-qwen25}"
OM="${QWEN25_OM:-$ROOT/artifacts/qwen25-static-2048.om}"
TOKENIZER="${QWEN25_TOKENIZER:-$ROOT/artifacts/tokenizer.json}"
TOKENIZER_CONFIG="${QWEN25_TOKENIZER_CONFIG:-$ROOT/artifacts/tokenizer_config.json}"
CONTRACT="${QWEN25_CONTRACT:-$ROOT/contracts/qwen25-static-contract.json}"
MAX_TOKENS="${QWEN25_MAX_TOKENS:-8}"

if [[ "${1:-}" == "--port" ]]; then
  PORT="${2:?missing port}"
fi
if [[ "$HOST" != "127.0.0.1" ]]; then
  echo "Qwen2.5 ACL service is loopback-only" >&2
  exit 2
fi
if [[ ! -d "$ROOT" ]]; then
  echo "Qwen2.5 deployment root does not exist: $ROOT" >&2
  exit 2
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "invalid loopback port: $PORT" >&2
  exit 2
fi
if ! [[ "$MAX_TOKENS" =~ ^[0-9]+$ ]] || (( MAX_TOKENS < 1 || MAX_TOKENS > 32 )); then
  echo "invalid max token limit: $MAX_TOKENS" >&2
  exit 2
fi

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
# CANN's set_env.sh references LD_LIBRARY_PATH while nounset is enabled.
# Preserve an existing value, but make the variable defined for a clean shell.
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${PYTHONPATH:-}"
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1

python - <<'PY'
import importlib.util
for name in ("torch", "torch_npu", "torchaudio", "transformers", "onnxruntime", "mindspore", "mindtorch", "vllm", "mindie"):
    if importlib.util.find_spec(name) is not None:
        raise SystemExit(f"forbidden board package is importable: {name}")
import acl
print("acl import: ok")
PY

ROOT="$(cd "$ROOT" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python "$SCRIPT_DIR/serve_qwen25_acl.py" \
  --host "$HOST" --port "$PORT" --root "$ROOT" \
  --om "$OM" --tokenizer "$TOKENIZER" \
  --tokenizer-config "$TOKENIZER_CONFIG" --contract "$CONTRACT" \
  --max-tokens "$MAX_TOKENS"
