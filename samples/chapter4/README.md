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

运行：
```bash
# GPU 训练
python samples/chapter4/SSD/train_cuda.py --device cuda --data /path/to/coco

# NPU 推理（需先运行 download_models.py 获取 .om 模型）
python samples/chapter4/SSD/inference_npu.py
```

### [SSDLite/](SSDLite/) — MobileNet-SSDLite320 目标检测（COCO）

| 文件 | 说明 |
|------|------|
| `train_ddp.py` | 多卡 GPU 训练（DDP） |
| `inference_npu.py` | PyACL NPU 推理 |
| `inference_cuda.py` | GPU 推理（对比基准） |
| `inference_cpu.py` | CPU 推理 |
| `download_models.py` | 模型下载 |
| `ssdlite/` | 模型定义、Default Boxes、训练/评估逻辑 |
| `logs/` | 导出的 ONNX 模型（多主干变体） |

运行：
```bash
# GPU 训练
python samples/chapter4/SSDLite/train_ddp.py --device cuda --backbone mobilenetv3

# NPU 推理（需先下载/转换 .om 模型）
python samples/chapter4/SSDLite/inference_npu.py
```

## 学习路线

1. 先跑 `check_ascend_device/` —— 确认 ACL 环境正常，1 分钟
2. 再看 `resnet18/` —— 完整的 PyACL 推理流水线：模型加载 → 数据预处理 → NPU 推理 → 后处理
3. 最后看 `SSD/` 和 `SSDLite/` —— 更复杂的目标检测推理，涉及多输出、NMS 后处理、COCO 评估
