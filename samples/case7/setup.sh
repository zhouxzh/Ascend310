#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-local}"

case "$MODE" in
  local)
    python -m pip install -r "$ROOT_DIR/requirements.txt"
    ;;
  board)
    if [[ ! -f /usr/local/miniconda3/etc/profile.d/conda.sh ]]; then
      echo "conda profile script not found" >&2
      exit 1
    fi
    # shellcheck disable=SC1091
    source /usr/local/miniconda3/etc/profile.d/conda.sh
    conda activate base
    export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
    export PYTHONPATH="${PYTHONPATH:-}"
    # shellcheck disable=SC1091
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    python -c 'import acl; print("PyACL preflight passed")'
    # Do not contact PyPI when the board already has the pinned runtime
    # packages. This keeps release activation deterministic on LAN-only boards
    # and avoids unnecessary memory/network pressure.
    if ! python -c 'import faiss; assert faiss.__version__ == "1.7.4"' >/dev/null 2>&1; then
      python -m pip install --user --no-deps --only-binary=:all: --timeout 30 --retries 1 faiss-cpu==1.7.4
    fi
    if ! python -c 'import periphery' >/dev/null 2>&1; then
      python -m pip install --user --no-deps --timeout 30 --retries 1 python-periphery==2.4.1
    fi
    if ! python -c 'import multipart' >/dev/null 2>&1; then
      python -m pip install --user --no-deps --timeout 30 --retries 1 'python-multipart>=0.0.9,<1.0'
    fi
    python -c 'import faiss, periphery, multipart; print("board dependencies passed")'
    ;;
  model-tools)
    python -m pip install -r "$ROOT_DIR/requirements-models.txt"
    ;;
  *)
    echo "usage: bash setup.sh [local|board|model-tools]" >&2
    exit 2
    ;;
esac

python -m py_compile \
  "$ROOT_DIR/app.py" \
  "$ROOT_DIR/embedding_backend.py" \
  "$ROOT_DIR/model_registry.py" \
  "$ROOT_DIR/photo_index.py" \
  "$ROOT_DIR/prepare_models.py" \
  "$ROOT_DIR/admin_auth.py" \
  "$ROOT_DIR/display_policy.py"

echo "setup completed: $MODE"
