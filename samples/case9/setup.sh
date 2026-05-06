#!/bin/bash
# Ascend 310B Smart Chatbot — environment setup
set -e

echo "=== Ascend 310B 智能聊天机器人 - 环境安装 ==="

# System dependencies for audio
echo "[1/3] Installing system packages ..."
sudo apt update
sudo apt install -y python3-dev python3-pip portaudio19-dev espeak

# Python packages
echo "[2/3] Installing Python packages ..."
pip3 install -r requirements.txt

# Prepare embedding model
echo "[3/3] Preparing embedding model ..."
python3 prepare_models.py

echo ""
echo "=== 安装完成 ==="
echo "启动服务:  python3 app.py"
echo "或指定端口: python3 app.py --port 8080"
