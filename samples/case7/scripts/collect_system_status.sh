#!/usr/bin/env bash
set -euo pipefail

OUTPUT="${1:-/home/HwHiAiUser/Documents/ai-album/shared/reports/system-status-$(date +%Y%m%d-%H%M%S).txt}"
case "$OUTPUT" in
  /home/HwHiAiUser/Documents/ai-album/*) ;;
  *) echo "refusing output outside project directory: $OUTPUT" >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$OUTPUT")"
{
  date --iso-8601=seconds
  hostname
  uname -a
  whoami
  free -h
  df -h /home
  npu-smi info || true
  cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg 2>/dev/null || true
  DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority xrandr --current 2>/dev/null || true
  DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority xinput list 2>/dev/null || true
  source /usr/local/miniconda3/etc/profile.d/conda.sh
  conda activate base
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  export PYTHONPATH="${PYTHONPATH:-}"
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
  python --version
  python -c 'import acl, cv2, faiss, fastapi, multipart, numpy; print("acl", acl.__file__); print("cv2", cv2.__version__); print("faiss", faiss.__version__); print("fastapi", fastapi.__version__); print("multipart", multipart.__version__); print("numpy", numpy.__version__)'
} >"$OUTPUT" 2>&1
echo "$OUTPUT"
