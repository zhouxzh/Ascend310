# 训练样例与输入输出合同

本章训练脚本直接以脚本文件为入口，没有统一 CLI、参数解析、配置文件或 checkpoint 管理。设备在源码中固定为 `npu`、`npu:0`、`cpu` 或 `cuda`，因此切换设备需要选择对应脚本。

## 共同运行基线

命令从仓库根目录开始：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import torch, torch_npu; print(torch.npu.is_available())"
```

预期第一条门槛是导入成功且 `torch.npu.is_available()` 返回 `True`。如果失败，保留 `import torch_npu` 的异常、CANN 的 `set_env.sh` 来源路径和 `ASCEND_LAUNCH_BLOCKING=1 python <entry>` 的输出；不得用 CPU 结果替代 NPU 结论。

共同 Python 依赖是 `torch`、`torch_npu`、`numpy`、`matplotlib` 和 `tqdm`。CIFAR-10/MNIST 还需要 `torchvision`；加州房价还需要 `scikit-learn`。仓库没有为本章提供锁定版本的 requirements 文件，因此版本兼容性属于 `untested`。

## 入口矩阵

| 主题 | 入口 | 固定设备 | 输入 | 主要输出 |
|---|---|---|---|---|
| 一维线性回归 | [linear_regression_npu.py](../linear_regression/linear_regression_npu.py) | `npu:0` | 1024 条合成电流/电压样本 | 终端中的 `R`、`b`，`linear_regression_training_loss.png` |
| 一维线性回归基准 | [linear_regression_cpu.py](../linear_regression/linear_regression_cpu.py) | `cpu` | 相同生成公式和随机种子 | 终端中的 `R`、`b`，`training_loss.png` |
| 加州房价线性模型 | [california_housing_linear_netword.py](../california_housing/california_housing_linear_netword.py) | `npu` | scikit-learn 下载的 California Housing | `california_housing_linear_network_results.png` |
| 加州房价双层 MLP | [california_housing_mlp.py](../california_housing/california_housing_mlp.py) | `npu:0` | 同上 | `california_housing_mlp_results.png` |
| 加州房价三层 MLP | [california_housing_mlp2.py](../california_housing/california_housing_mlp2.py) | `npu:0` | 同上 | 当前目录下的 `california_housing_mlp.png` |
| LeNet-5/MNIST | [lenet_npu.py](../LeNet/lenet_npu.py) | `npu` | torchvision 下载的 MNIST | 脚本目录下的 `accuracy_loss_curve.png` |
| AlexNet/CIFAR-10 | [NPU 入口](../AlexNet/alexnet_npu.py)、[NPU FP32 入口](../AlexNet/alexnet_npu_fp32.py)、[CUDA FP16 入口](../AlexNet/alexnet_cuda.py)、[CUDA FP32 入口](../AlexNet/alexnet_cuda_fp32.py) | 脚本内固定 | torchvision 下载的 CIFAR-10，放大到 224×224 | 训练曲线 PNG |
| VGG16/CIFAR-10 | [NPU 入口](../VGG/vgg_npu.py)、[CUDA 入口](../VGG/vgg_cuda.py) | 脚本内固定 | torchvision 下载的 CIFAR-10，32×32 | 训练曲线 PNG |
| ResNet18/CIFAR-10 | [NPU 入口](../ResNet/resnet_npu.py)、[CUDA 入口](../ResNet/resnet_cuda.py) | 脚本内固定 | torchvision 下载的 CIFAR-10，32×32 | 训练曲线 PNG |

## 合成线性回归

NPU 入口用 `torch.nn.Linear(1, 1)` 拟合 `V = R·I + b`：

```bash
cd samples/chapter3/linear_regression
python linear_regression_npu.py
```

- 数据合同：`I` 为 `[1024, 1]`、范围 `0..1 A`；噪声标准差 `0.005 V`；真值为 `R=120 Ω`、`b=0.02 V`。
- 训练合同：MSE、SGD、学习率 `0.01`、批大小 `16`、50 个 epoch、随机种子 `0`。
- 成功信号：终端打印 `device = npu:0`，并输出有限的 `R` 与 `b`；当前工作目录生成损失曲线。
- 失败边界：数据或模型迁移到 NPU 失败、算子编译失败、`loss.item()` 同步报错，或保存图片失败。脚本没有捕获这些异常，会以非零状态退出。

CPU 入口使用手写 `LinearNoMatmul`，并非与 NPU 入口完全相同的模型实现：

```bash
cd samples/chapter3/linear_regression
python linear_regression_cpu.py
```

两个脚本共享生成公式，但 NPU 版使用 `torch.nn.Linear`，CPU 版改用逐元素乘加。曲线和拟合参数可以用于教学对照，不能单独作为算子级数值一致性或公平性能对比证据。

## 加州房价回归

三种模型都读取 scikit-learn 的 `fetch_california_housing()`，按 8 个输入特征预测 1 个目标值。脚本先把特征和目标标准化，再按 80%/20% 划分训练/验证集，批大小为 32，使用 MSE 和 SGD。

运行示例：

```bash
cd samples/chapter3/california_housing
python california_housing_linear_netword.py
python california_housing_mlp.py
python california_housing_mlp2.py
```

| 脚本 | 模型与训练参数 | 图表产物 |
|---|---|---|
| `california_housing_linear_netword.py` | 单层 `Linear(8, 1)`，SGD `lr=1e-4`，50 epoch | `california_housing_linear_network_results.png` |
| `california_housing_mlp.py` | `8→64→1`，ReLU，SGD `lr=1e-4`，50 epoch | `california_housing_mlp_results.png` |
| `california_housing_mlp2.py` | `8→128→64→1`，ReLU，SGD `lr=1e-2`，20 epoch | `california_housing_mlp.png` |

三个脚本都会打印最终验证集 R²、MAE 和 RMSE。需要注意，`StandardScaler.fit_transform()` 在训练/验证切分之前执行，验证集统计量泄漏进了标准化参数；这些指标只能作为教学运行结果，不能当作严格的泛化评估。`california_housing_mlp2.py` 的注释提到 L2 正则，但优化器没有设置 `weight_decay`。

输入数据通常由 scikit-learn 缓存在用户目录。网络不可达且本机无缓存时会在数据获取阶段失败；此时应保留数据源 URL、缓存目录和异常，不得把缺少数据集写成 NPU 不支持。

## LeNet-5 与 MNIST

```bash
cd samples/chapter3/LeNet
python lenet_npu.py
```

- 数据合同：MNIST 灰度图 `[N, 1, 28, 28]`，像素缩放至 `[0, 1]`，输入张量转换为 `float16`，标签为 `int64`。
- 模型合同：两层卷积、ReLU、平均池化、三层全连接，输出 10 类 logits。
- 训练合同：交叉熵、SGD（`lr=0.01`、`momentum=0.9`）、批大小 `256`、10 epoch、模型参数为 FP16。
- 产物：`LeNet/accuracy_loss_curve.png`，无论启动时位于哪个工作目录。
- 可复现性：脚本没有设置随机种子，多次运行不保证相同曲线。

源码使用 `AvgPool2d`，而不是早期版本中会触发 `MaxPoolWithArgmaxV1` 失败的 `MaxPool2d`。[历史踩坑记录](../LeNet/errror.md)保留了一次 FP32 MaxPool 算子不被当前 AI Core 核支持的错误；该记录属于 `observed-fail`，不能推导为所有池化算子或所有 CANN 版本都失败。

## CIFAR-10 分类训练

所有 CIFAR-10 入口都由 `torchvision.datasets.CIFAR10` 自动下载数据。没有 CLI 参数，训练集和测试集固定为 50,000/10,000 张。

通用运行形式：

```bash
cd samples/chapter3/AlexNet
python alexnet_npu.py
```

```bash
cd samples/chapter3/VGG
python vgg_npu.py
```

```bash
cd samples/chapter3/ResNet
python resnet_npu.py
```

| 网络 | 输入 | 训练配置 | 当前产物名 |
|---|---|---|---|
| AlexNet NPU FP16 | `1×3×224×224` | batch 16，20 epoch，SGD `lr=0.01` | `AlexNet/training_metrics.png` |
| AlexNet NPU FP32 | `1×3×224×224` | batch 8，10 epoch，SGD `lr=0.01` | `AlexNet/training_metrics.png` |
| AlexNet CUDA FP16 | `1×3×224×224` | batch 16，20 epoch | `AlexNet/training_metrics_cuda_fp16.png` |
| AlexNet CUDA FP32 | `1×3×224×224` | batch 16，20 epoch | `AlexNet/training_metrics_cuda_fp32.png` |
| VGG16 NPU | `1×3×32×32` | batch 16，10 epoch | `VGG/training_metrics.png` |
| VGG16 CUDA | `1×3×32×32` | batch 16，10 epoch | `VGG/training_metrics_vgg_cuda.png` |
| ResNet18 NPU | `1×3×32×32` | batch 16，10 epoch | `ResNet/training_metrics_resnet_npu.png` |
| ResNet18 CUDA | `1×3×32×32` | batch 16，10 epoch | `ResNet/training_metrics_resnet_cuda.png` |

AlexNet 的 NPU FP16 与 NPU FP32 入口写入同一个 `training_metrics.png`，后运行者覆盖先运行者。VGG NPU 入口写 `training_metrics.png`，仓库中现存文件却名为 `training_metrics_vgg_npu.png`；只凭文件名不能证明现存图片由当前脚本生成。需要保留对比结果时，应在运行后立即重命名或复制产物，并记录命令、开始时间、退出码和设备快照。

两个 AlexNet NPU 配置的 epoch 数和批大小不同，AlexNet CUDA FP32 也与 FP16 不完全一致，且所有 CIFAR 脚本都没有设置随机种子。交叉设备准确率差异混合了实现、随机性、训练预算和数值精度，不能解释为单一硬件效应。

## 测试与停止条件

本章训练目录没有自动化训练测试。算子测试见[NPU 算子兼容性测试](02-operator-validation.md)。出现以下任一情况时停止并把结果记为失败或阻塞：

- `torch_npu` 导入失败、NPU 不可见或设备上下文创建失败。
- 数据集下载失败、磁盘不足或数据校验失败。
- 首个 NPU 算子编译失败，并伴随 `Unsupported_Operator`、`EZ3002` 或进程被终止。
- 训练损失变为 `NaN`/`Inf`，或产生全零/退化预测。
- 期望产物未生成，或同名产物被静默覆盖。

仅生成损失曲线不等于任务验收。当前源码也没有保存最终 checkpoint，因此无法从仓库产物复现某一次训练的参数状态。
