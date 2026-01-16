#!/bin/bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装Python依赖
pip3 install opencv-python numpy torch torchvision pillow matplotlib

# 安装CANN开发套件
# (此处应包含具体的CANN安装步骤)

echo "环境安装完成!"