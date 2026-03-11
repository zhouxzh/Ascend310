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

## 模型下载

仓库提供了 `models/download_models.py` 脚本，用于从 Hugging Face 镜像站下载 SSD 模型。脚本默认使用 `https://hf-mirror.com`，避免直接访问 Hugging Face。

下载后的本地文件名会按输入尺寸自动重命名：

*   `ssd_mobilenetv1.onnx` 会保存为 `ssd320_mobilenetv1.onnx`
*   `ssd_mobilenetv1.om` 会保存为 `ssd320_mobilenetv1.om`
*   `ssd_resnet18.onnx` 会保存为 `ssd300_resnet18.onnx`
*   `ssd_resnet50.om` 会保存为 `ssd300_resnet50.om`

命名规则含义如下：

*   `ssd320_*` 表示模型输入尺寸为 `320x320`
*   `ssd300_*` 表示模型输入尺寸为 `300x300`

脚本默认将模型下载到 `models/download_models.py` 所在目录，也就是 `models/` 目录。

常用命令示例：

```bash
# 默认下载 ssd_mobilenetv3.onnx，并保存为 models/ssd320_mobilenetv3.onnx
python3 models/download_models.py

# 下载所有模型
python3 models/download_models.py --all

# 仅下载所有 .om 模型
python3 models/download_models.py --om

# 仅下载所有 .onnx 模型
python3 models/download_models.py --onnx

# 显式下载到脚本所在目录
python3 models/download_models.py --script-dir

# 下载到自定义目录
python3 models/download_models.py --output-dir ./my_models
```

如果需要切换镜像地址，可以使用环境变量 `HF_ENDPOINT`，或者通过命令行参数指定：

```bash
python3 models/download_models.py --endpoint https://hf-mirror.com
```

## 模型转换

当你下载的是 ONNX 模型时，需要使用昇腾 ATC 工具将 ONNX 模型转换为 Ascend 310B 可以直接加载的 `.om` 模型。ATC 会根据你指定的输入张量形状、目标芯片型号和输出路径，生成对应的离线模型文件。

下面这条命令演示了如何把 `ssd320_mobilenetv3.onnx` 转换成 Ascend 310B 可用的 `ssd320_mobilenetv3.om` 模型：

```bash
atc --model=models/ssd320_mobilenetv3.onnx --framework=5 --output=models/ssd320_mobilenetv3 --input_shape="input:1,3,320,320" --soc_version=Ascend310B4
```

命令说明：

*   `--model=models/ssd320_mobilenetv3.onnx`：指定输入的 ONNX 模型文件。
*   `--framework=5`：表示输入模型格式为 ONNX。
*   `--output=models/ssd320_mobilenetv3`：指定输出模型名前缀，ATC 会生成 `models/ssd320_mobilenetv3.om`。
*   `--input_shape="input:1,3,320,320"`：指定模型输入张量形状，其中 `1` 是 batch size，`3` 是通道数，`320,320` 是输入图片尺寸。
*   `--soc_version=Ascend310B4`：指定目标芯片型号为 Ascend 310B4。

使用说明：

*   如果你下载的是 `ssd320_mobilenet*` 系列模型，输入尺寸通常应为 `320x320`。
*   如果你下载的是 `ssd300_resnet*` 系列模型，转换时应把 `--input_shape` 改成 `"input:1,3,300,300"`。
*   `--output` 不需要写 `.om` 后缀，ATC 会自动生成 `.om` 文件。
*   转换完成后，生成的 `.om` 文件可以直接用于昇腾 310B 推理部署。

例如，将 `ssd300_resnet50.onnx` 转换为 `.om` 时，可以使用：

```bash
atc --model=models/ssd300_resnet50.onnx --framework=5 --output=models/ssd300_resnet50 --input_shape="input:1,3,300,300" --soc_version=Ascend310B4
```

## 目录结构

```
case2/
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