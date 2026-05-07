#!/bin/bash
# Case 6: Smart Car Perception — environment setup
set -e

echo "=== Case 6: 智能小车视觉感知 - 环境安装 ==="

# System dependencies
echo "[1/3] Installing system packages ..."
sudo apt update
sudo apt install -y python3-dev python3-pip

# Python packages
echo "[2/3] Installing Python packages ..."
pip3 install -r requirements.txt

# Prepare ResNet18 scene classifier (ONNX export; ATC if CANN available)
echo "[3/3] Preparing ResNet18 scene classifier model ..."
python3 prepare_models.py

echo ""
echo "=== 安装完成 ==="
echo "启动服务:  python3 app.py"
echo "如需重新转换模型: python3 prepare_models.py --force"
