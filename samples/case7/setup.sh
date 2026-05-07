#!/bin/bash
# Case 7: Smart Album — environment setup
set -e

echo "=== Case 7: 智能相册 - 环境安装 ==="

# System dependencies
echo "[1/3] Installing system packages ..."
sudo apt update
sudo apt install -y python3-dev python3-pip

# Python packages
echo "[2/3] Installing Python packages ..."
pip3 install -r requirements.txt

# Prepare ResNet50 model (ONNX export; ATC conversion if CANN available)
echo "[3/3] Preparing ResNet50 feature model ..."
python3 prepare_models.py

echo ""
echo "=== 安装完成 ==="
echo "启动服务:  python3 app.py"
echo "如需重新转换模型: python3 prepare_models.py --force"
