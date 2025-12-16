# 边缘端实时目标跟踪系统

## 项目简介

本项目是一个基于昇腾310B NPU的实时目标跟踪系统。它能够在视频流中高效地检测和跟踪目标，适用于安防、交通、机器人等多种边缘计算场景。

## 快速开始

1.  **硬件准备**: 准备昇腾310B开发板、摄像头和显示器。
2.  **环境配置**: 运行 `bash setup_env.sh` 安装所需依赖。
3.  **模型准备**:
    *   将预训练的PyTorch检测模型（如YOLOv8）放置在 `data/pretrained` 目录下。
    *   运行 `python3 scripts/convert_to_onnx.py --model data/pretrained/yolov8s.pt --output models/detection/yolov8s.onnx` 将模型转换为ONNX格式。
    *   使用昇腾ATC工具将ONNX模型转换为昇腾支持的 `.om` 格式。
4.  **启动程序**: 运行 `python3 demo/tracking_app.py --config configs/config.yaml` 启动实时跟踪。

## 目录结构

```
tracking_project/
├── models/             # 模型与算法
│   ├── detection/      # 检测模型 (.om)
│   ├── tracking/       # 跟踪算法实现
│   └── utils/          # 工具函数 (如NPU推理)
├── data/               # 数据集与预训练模型
│   ├── datasets/       # 训练数据集
│   └── pretrained/     # 预训练PyTorch模型
├── configs/            # 配置文件
├── scripts/            # 辅助脚本
├── demo/               # 演示程序
└── output/             # 输出结果
```

## 自定义配置

通过修改 `configs/config.yaml` 文件，可以调整跟踪参数、视频源和模型路径。