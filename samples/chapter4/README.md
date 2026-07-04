# 第4章 代码样例

本章代码围绕 PyACL 应用开发，涵盖环境验证、模型推理（ResNet-18）和目标检测（SSD / SSDLite）。

## 运行前提

```bash
# 加载 CANN 环境变量
source /usr/local/Ascend/ascend-toolkit/set_env.sh

# 验证 acl 模块可导入
python -c "import acl; print('PyACL OK')"
```

## 目录与文件

### [check_ascend_device/](check_ascend_device/) — 环境验证

| 文件 | 说明 |
|------|------|
| `check_ascend_device.py` | ACL 初始化 → 查询设备数量 → 去初始化，最简验证流程 |

运行：`python samples/chapter4/check_ascend_device/check_ascend_device.py`

### [resnet18/](resnet18/) — ResNet-18 Tiny-ImageNet 推理

| 文件 | 说明 |
|------|------|
| `train_resnet18_tiny_imagenet.py` | 在 GPU 上训练 ResNet-18（产出 ONNX 模型） |
| `inference_npu.py` | PyACL 加载 .om 模型，NPU 推理 |
| `inference_cpu.py` | CPU 推理（对比基准） |
| `model/` | 预训练模型（ONNX + .om），从 HuggingFace 自动下载 |

运行：
```bash
# 下载模型（首次运行需要）
pip install huggingface_hub
huggingface-cli download zhouxzh/resnet18_tiny_imagenet --local-dir samples/chapter4/resnet18/model/

# ATC 模型转换（ONNX → OM）
atc --model=samples/chapter4/resnet18/model/resnet18_tiny_imagenet.onnx \
    --framework=5 --output=samples/chapter4/resnet18/model/resnet18_tiny_imagenet \
    --soc_version=Ascend310B4

# NPU 推理
python samples/chapter4/resnet18/inference_npu.py
```

### [SSD/](SSD/) — SSD300 目标检测（COCO）

| 文件 | 说明 |
|------|------|
| `train_cuda.py` | GPU 训练入口（ResNet-50 主干） |
| `inference_npu.py` | PyACL NPU 推理 |
| `inference_cuda.py` | GPU 推理（对比基准） |
| `inference_cpu.py` | CPU 推理 |
| `utils_cpu.py` | 数据预处理、可视化工具 |
| `download_models.py` | 下载预训练模型 |
| `ssd/` | 模型定义、训练/评估逻辑、数据流水线 |

SSD300 模型统一使用 `ssd300_{backbone}.onnx` / `ssd300_{backbone}.om` 命名，例如 `ssd300_resnet50.onnx`。`ssd300_` 表示输入尺寸为 300×300，用来和 SSDLite320 的 `ssd320_` 模型区分。模型默认从 Hugging Face 仓库 `zhouxzh/SSD300` 下载，脚本默认使用 `https://hf-mirror.com` 镜像。

运行：
```bash
# 下载 ONNX 模型
python samples/chapter4/SSD/download_models.py --onnx --backbone resnet50

# 在 Ascend 310B 开发板上直接下载 OM 模型
export HF_ENDPOINT=https://hf-mirror.com
python samples/chapter4/SSD/download_models.py --om --backbone resnet50

# GPU 训练
python samples/chapter4/SSD/train_cuda.py --device cuda --data /path/to/coco

# NPU 快速烟测，默认读取 samples/chapter4/SSD/models/ssd300_resnet50.om
python samples/chapter4/SSD/inference_npu.py --device 0 --limit 20 --skip-map
```

### [SSDLite/](SSDLite/) — MobileNet-SSDLite320 目标检测（COCO）

| 文件 | 说明 |
|------|------|
| `scripts/inference_npu.py` | PyACL NPU 推理 |
| `scripts/inference_cuda.py` | GPU 推理（对比基准） |
| `scripts/inference_cpu.py` | CPU 推理 |
| `scripts/download_models.py` | 模型下载 |
| `scripts/convert_onnx_to_om.py` | ONNX 转 OM（需在 Ascend 设备上运行） |
| `ssdlite320/` | Default Boxes、Decode、NMS、可视化与评估工具 |

运行：
```bash
# CPU ONNX 推理
python samples/chapter4/SSDLite/scripts/inference_cpu.py --backbone mobilenetv4_conv_small

# NPU 推理（需先下载/转换 .om 模型）
python samples/chapter4/SSDLite/scripts/inference_npu.py --backbone mobilenetv4_conv_small
```

## 学习路线

1. 先跑 `check_ascend_device/` —— 确认 ACL 环境正常，1 分钟
2. 再看 `resnet18/` —— 完整的 PyACL 推理流水线：模型加载 → 数据预处理 → NPU 推理 → 后处理
3. 最后看 `SSD/` 和 `SSDLite/` —— 更复杂的目标检测推理，涉及多输出、NMS 后处理、COCO 评估
