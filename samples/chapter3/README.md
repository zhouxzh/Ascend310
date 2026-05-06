# 第3章 代码样例

本章代码围绕 PyTorch + torch_npu 在昇腾 310B 上的迁移与训练，包含多个经典网络的 NPU vs CPU/CUDA 对比实验。

## 运行前提

```bash
# 确保 CANN 环境变量已加载
source /usr/local/Ascend/ascend-toolkit/set_env.sh

# 确认 torch_npu 可用
python -c "import torch; import torch_npu; print(torch.npu.is_available())"
```

## 目录与文件

### [linear_regression/](linear_regression/) — 线性回归（合成数据）

| 文件 | 说明 |
|------|------|
| `linear_regression_npu.py` | 在 NPU 上训练线性回归（V = R·I + b），含高斯噪声 |
| `linear_regression_cpu.py` | CPU 版本对比 |
| `training_loss.png` | 训练损失曲线（NPU） |
| `linear_regression_training_loss.png` | 训练损失曲线（CPU） |

运行：`python samples/chapter3/linear_regression/linear_regression_npu.py`

### [california_housing/](california_housing/) — 加州房价预测

| 文件 | 说明 |
|------|------|
| `california_housing_linear_netword.py` | 线性神经网络拟合加州房价 |
| `california_housing_mlp.py` | 多层感知机（MLP）拟合加州房价 |
| `california_housing_mlp2.py` | MLP 变体（更深网络） |
| `california_housing_linear_network_results.png` | 线性网络训练结果图 |
| `california_housing_mlp_results.png` | MLP 训练结果图 |

运行：`python samples/chapter3/california_housing/california_housing_mlp.py`

### [LeNet/](LeNet/) — LeNet-5 手写数字识别

| 文件 | 说明 |
|------|------|
| `lenet_npu.py` | NPU 上训练 LeNet-5（MNIST），FP16 精度，ReLU 激活 |
| `accuracy_loss_curve.png` | 精度与损失曲线 |
| `errror.md` | 踩坑记录（Sigmoid vs ReLU，FP32 → FP16 兼容性） |

运行：`python samples/chapter3/LeNet/lenet_npu.py`

### [test/](test/) — 算子兼容性测试

| 文件 | 说明 |
|------|------|
| `conftest.py` | pytest 配置与日志收集 |
| `test_float32_ops.py` | FP32 算子测试（Linear、Conv2d 等核心算子） |
| `test_float16_ops.py` | FP16 算子测试 |
| `test_nn_layers_float32.py` | FP32 网络层测试 |
| `test_nn_layers_float16.py` | FP16 网络层测试 |
| `pytest_realtime_log.txt` | 测试运行日志 |

运行：`pytest samples/chapter3/test/`

### [AlexNet/](AlexNet/) — AlexNet CIFAR-10 分类

| 文件 | 说明 |
|------|------|
| `alexnet_npu.py` | NPU FP16 训练 |
| `alexnet_npu_fp32.py` | NPU FP32 训练 |
| `alexnet_cuda.py` | CUDA FP16 训练（对比基准） |
| `alexnet_cuda_fp32.py` | CUDA FP32 训练（对比基准） |
| `training_metrics.png` | NPU FP16 训练指标曲线 |
| `training_metrics_cuda_fp32.png` | CUDA FP32 训练指标曲线 |
| `training_metrics_cuda_fp16.png` | CUDA FP16 训练指标曲线 |

运行：`python samples/chapter3/AlexNet/alexnet_npu.py`

### [VGG/](VGG/) — VGG CIFAR-10 分类

| 文件 | 说明 |
|------|------|
| `vgg_npu.py` | NPU 训练（CIFAR-10，VGG11/13/16/19 可选） |
| `vgg_cuda.py` | CUDA 训练（对比基准） |
| `training_metrics_vgg_npu.png` | NPU 训练指标曲线 |
| `training_metrics_vgg_cuda.png` | CUDA 训练指标曲线 |

运行：`python samples/chapter3/VGG/vgg_npu.py`

### [ResNet/](ResNet/) — ResNet CIFAR-10 分类

| 文件 | 说明 |
|------|------|
| `resnet_npu.py` | NPU 训练 |
| `resnet_cuda.py` | CUDA 训练（对比基准） |
| `training_metrics_resnet_npu.png` | NPU 训练指标曲线 |
| `training_metrics_resnet_cuda.png` | CUDA 训练指标曲线 |

运行：`python samples/chapter3/ResNet/resnet_npu.py`

## 学习路线

1. 先跑 `linear_regression/` —— 最简单的合成数据示例，验证 torch_npu 环境基本可用
2. 再看 `california_housing/` —— 真实数据集的线性网络与 MLP
3. 然后跑 `LeNet/` —— 第一个卷积网络，理解 FP16 兼容性问题
4. 跑 `test/` 测试套件 —— 确认当前环境对核心算子的支持情况
5. 最后跑 `AlexNet/`、`VGG/`、`ResNet/` —— 更深网络，NPU vs CUDA 性能对比
