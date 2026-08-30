#!/usr/bin/env bash
set -euo pipefail

HOST="127.0.0.1"
PORT="${QWEN25_KV_PORT:-8084}"
ROOT="${QWEN25_ROOT:-$HOME/case9-qwen25-kv1024}"
ENV_NAME="${CASE9_QWEN25_KV_ENV:-case9-acl-om}"
OM="${QWEN25_KV_OM:-$ROOT/artifacts/qwen25-static-kv-1024-v2.om}"
TOKENIZER="${QWEN25_KV_TOKENIZER:-$ROOT/artifacts/tokenizer.json}"
TOKENIZER_CONFIG="${QWEN25_KV_TOKENIZER_CONFIG:-$ROOT/artifacts/tokenizer_config.json}"
CONTRACT="${QWEN25_KV_CONTRACT:-$ROOT/contracts/qwen25-static-kv-1024-v2-om-contract.json}"
LOCK="${QWEN25_KV_LOCK:-$OM.lock.json}"
TOKENIZER_LOCK="${QWEN25_KV_TOKENIZER_LOCK:-$TOKENIZER.lock.json}"
MAX_TOKENS="${QWEN25_KV_MAX_TOKENS:-80}"

if [[ "${1:-}" == "--port" ]]; then
  PORT="${2:?missing port}"
fi
if [[ "$HOST" != "127.0.0.1" ]]; then
  echo "Qwen2.5 StaticCache service is loopback-only" >&2
  exit 2
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "invalid loopback port: $PORT" >&2
  exit 2
fi
if ! [[ "$MAX_TOKENS" =~ ^[0-9]+$ ]] || (( MAX_TOKENS < 1 || MAX_TOKENS > 80 )); then
  echo "invalid max token limit: $MAX_TOKENS" >&2
  exit 2
fi
if [[ ! -d "$ROOT" ]]; then
  echo "deployment root does not exist: $ROOT" >&2
  exit 2
fi

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate "$ENV_NAME"
if [[ -z "${CONDA_PREFIX:-}" || ! -x "${CONDA_PREFIX}/bin/python" ]]; then
  echo "$ENV_NAME did not expose a usable conda prefix" >&2
  exit 2
fi
export PATH="${CONDA_PREFIX}/bin:${PATH}"
hash -r 2>/dev/null || true
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${PYTHONPATH:-}"
source /usr/local/Ascend/ascend-toolkit/set_env.sh
# Some CANN environment scripts prepend their own Python directories and
# hide the activated conda interpreter.  Restore the explicitly selected
# environment before every launcher preflight and exec.
export PATH="${CONDA_PREFIX}/bin:${PATH}"
hash -r 2>/dev/null || true
export PYTHONNOUSERSITE=1

python - <<'PY'
import os
import pathlib
import sys
expected = pathlib.Path(os.environ["CONDA_PREFIX"]) / "bin" / "python"
if sys.version_info[:2] != (3, 9) or pathlib.Path(sys.prefix).resolve() != pathlib.Path(os.environ["CONDA_PREFIX"]).resolve():
    raise SystemExit(f"wrong case9-acl-om interpreter/prefix: {sys.executable}, {sys.prefix}; expected {expected}")
if pathlib.Path(sys.executable).resolve().parent != expected.resolve().parent:
    raise SystemExit(f"wrong case9-acl-om interpreter: {sys.executable}; expected a binary in {expected.parent}")
print(f"python={sys.executable} version={sys.version.split()[0]}")
PY

python - <<'PY'
import importlib.util
import os
allow_dirty = os.environ.get("CASE9_QWEN25_KV_ALLOW_DIRTY_BASE") == "1" and os.environ.get("CONDA_DEFAULT_ENV") == "base"
for name in ("torch", "torch_npu", "torchaudio", "transformers", "onnxruntime", "mindspore", "mindtorch", "vllm", "mindie"):
    if importlib.util.find_spec(name) is not None:
        if not allow_dirty:
            raise SystemExit(f"forbidden board package is importable: {name}")
        print(f"WARNING: dirty-base test override; package remains importable: {name}")
import acl
print("acl import: ok")
PY

ROOT="$(cd "$ROOT" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python "$SCRIPT_DIR/serve_qwen25_kv_acl.py" \
  --host "$HOST" --port "$PORT" --root "$ROOT" \
  --om "$OM" --tokenizer "$TOKENIZER" \
  --tokenizer-config "$TOKENIZER_CONFIG" --contract "$CONTRACT" \
  --lock "$LOCK" --tokenizer-lock "$TOKENIZER_LOCK" \
  --max-tokens "$MAX_TOKENS"
