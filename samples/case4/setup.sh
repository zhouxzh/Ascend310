#!/bin/bash
# Case 4: Smart Palmprint Recognition — environment setup
set -e

echo "=== Case 4: 智能掌纹识别机 - 环境安装 ==="

# System dependencies
echo "[1/3] Installing system packages ..."
sudo apt update
sudo apt install -y python3-dev python3-pip

# Python packages
echo "[2/3] Installing Python packages ..."
pip3 install -r requirements.txt

# Prepare GhostNet model (ONNX export; ATC if CANN available)
echo "[3/3] Preparing GhostNet palmprint model ..."
if [ -f models/ghostnet_palmprint.pth ]; then
    python3 prepare_models.py
else
    echo "  NOTE: No trained weights found (models/ghostnet_palmprint.pth missing)."
    echo "  Run 'python3 train.py --data-dir /path/to/palmprint/dataset' first,"
    echo "  then re-run 'python3 prepare_models.py'."
    echo "  The app will still start but use random weights."
fi

echo ""
echo "=== 安装完成 ==="
echo "启动服务:  python3 app.py"
echo "训练模型:  python3 train.py --data-dir /path/to/palmprint/dataset"
echo "转换模型:  python3 prepare_models.py"
