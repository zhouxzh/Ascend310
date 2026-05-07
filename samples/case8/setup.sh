#!/bin/bash
# Case 8: Gesture Recognition — environment setup
set -e

echo "=== Case 8: 手势识别 - 环境安装 ==="

# System dependencies
echo "[1/3] Installing system packages ..."
sudo apt update
sudo apt install -y python3-dev python3-pip

# Python packages
echo "[2/3] Installing Python packages ..."
pip3 install -r requirements.txt

# Prepare gesture model
echo "[3/3] Preparing gesture model ..."
python3 prepare_models.py --download

echo ""
echo "=== 安装完成 ==="
echo "启动服务:  python3 app.py"
echo "如需自行训练模型: python3 train.py"
