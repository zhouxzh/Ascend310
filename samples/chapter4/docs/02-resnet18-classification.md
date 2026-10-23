# ResNet18 Tiny-ImageNet 分类

`resnet18/` 包含 CUDA/CPU 训练、ONNX Runtime CPU 推理和 PyACL NPU 推理。训练脚本不使用 NPU；NPU 路径只加载已经转换好的 OM。

## 模型合同

| 项目 | 合同 |
|---|---|
| 任务 | Tiny-ImageNet 200 类图像分类 |
| 输入 | RGB，`float32`，NCHW `1×3×64×64` |
| 预处理 | 像素缩放到 `[0,1]`，ImageNet 均值 `[0.485,0.456,0.406]`，标准差 `[0.229,0.224,0.225]` |
| 输出 | `float32`，`1×200` logits |
| 推理结果 | `argmax(logits)` 对应的类别索引 |
| NPU 文件 | [resnet18_tiny_imagenet.om](../resnet18/model/resnet18_tiny_imagenet.om) |
| ONNX 文件 | [resnet18_tiny_imagenet.onnx](../resnet18/model/resnet18_tiny_imagenet.onnx) |

当前 ONNX 文件大小为 `45,223,270` 字节，SHA-256 为：

```text
e461aecee8bfafe95cd4528d3e836783c7546a0bb201c98e25109cde51b3bd6e
```

该哈希与本地 `.cache/huggingface/download/resnet18_tiny_imagenet.onnx.metadata` 记录一致，元数据记录的仓库 revision 是 `09f4f2af179c640bc764ecf5b4daf5b634137778`。

当前 OM 文件大小为 `22,821,283` 字节，实际 SHA-256 为：

```text
0b9e112751181aab82b43963f1b975adf4abbcdeec7c919ee93e496c0df8d46d
```

同目录缓存元数据却记录 `4213a060c11e575c8d6000d78a5fbbe604e3378e8ee6a8670d7eb011588ac560`。二者不一致，因此不能把当前 OM 的 ATC 来源、CANN 版本或是否由上述 ONNX 转换得到写成已验证事实。使用前应重新执行 ATC 并保存完整日志，或从可追溯 artifact store 重新获取 OM。

## 数据合同

推理脚本调用：

```python
load_dataset("zh-plus/tiny-imagenet", split="valid", cache_dir="./data")
```

每项使用 `image` 和 `label`。当前预处理代码没有调用 `resize()`；它直接按 PIL 图像的 `HWC` 形状转成 CHW。代码中的注释和模型名约定输入为 `64×64`，因此替换数据集或混入其他尺寸图像会破坏输入合同。

训练脚本使用 `train` 和 `valid` split，训练增强包含随机裁剪、水平翻转、旋转和颜色抖动；验证只做 `ToTensor` 和 ImageNet 归一化。模型卡把代码许可记录为 Apache-2.0，数据集许可没有绑定到固定数据集 revision，属于 `untested`。

## 训练入口

[训练脚本](../resnet18/train_resnet18_tiny_imagenet.py)从当前工作目录创建 `./data`、`./model` 和 `./logs/resnet18_tiny_imagenet`：

```bash
cd samples/chapter4/resnet18
python train_resnet18_tiny_imagenet.py
```

- 设备由 `cuda if torch.cuda.is_available() else cpu` 决定；本脚本没有 `npu` 分支。
- 模型是适配 64×64 输入的 ResNet18：`BasicBlock=[2,2,2,2]`、首个 `3×3` 卷积且无 MaxPool、AdaptiveAvgPool、Dropout `0.5`、200 类输出。
- 训练参数：batch 128、150 epoch、SGD `lr=0.1`、momentum `0.9`、weight decay `5e-4`、label smoothing `0.1`。
- 每个 epoch 覆盖写 `./model/checkpoint_epoch.pth`。
- 训练结束后在 `./model/resnet18_tiny_imagenet.onnx` 导出 ONNX。
- 脚本没有设置随机种子，也没有保存 Git revision、包版本、CUDA 信息和数据 revision。

如果训练意外终止，只会留下最近 checkpoint，不会生成最终 ONNX。训练和设备推理是不同证据层，训练成功不证明 OM 转换或 PyACL 推理成功。

## 推荐模型获取

本地缓存元数据指向固定 revision；重新下载时应显式指定，而不是依赖 `main`：

```bash
cd samples/chapter4/resnet18
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download zhouxzh/resnet18_tiny_imagenet \
  --revision 09f4f2af179c640bc764ecf5b4daf5b634137778 \
  --local-dir model
```

下载后至少核对 ONNX 字节数和 SHA-256。OM 应在本板重新转换，或从带 artifact manifest 的可信位置获取。

## ATC 转换

现有 README 给出的命令如下，属于 `untested`：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/chapter4/resnet18
atc --model=model/resnet18_tiny_imagenet.onnx \
    --framework=5 \
    --output=model/resnet18_tiny_imagenet \
    --soc_version=Ascend310B4
```

执行前应确认目标芯片确实是 `Ascend310B4`，并检查 ONNX 的输入名和 shape。转换后必须验证输出文件存在、非空、可被 `acl.mdl.load_from_file` 加载，并记录 CANN/ATC 版本、完整命令、日志、OM 大小和 SHA-256。ATC 非零退出时不得保留旧的同名 OM 冒充新结果。

## CPU 基准

```bash
cd samples/chapter4/resnet18
python inference_cpu.py
```

[CPU 入口](../resnet18/inference_cpu.py)固定使用 ONNX Runtime `CPUExecutionProvider`，读取 `./data` 和 `./model/resnet18_tiny_imagenet.onnx`。它遍历 `valid` split，输出总图片数、总耗时、全流程 FPS 和 Top-1 正确率。

该计时包含数据集迭代、预处理、ONNX Runtime 执行和 `argmax`，不是纯模型延迟。CPU 结果只能作为同一输入协议下的软件基准，不能称为 310B 性能。

## Ascend PyACL 推理

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/chapter4/resnet18
python inference_npu.py
```

[NPU 入口](../resnet18/inference_npu.py)固定读取 `./data` 和 `./model/resnet18_tiny_imagenet.om`，执行流程为：

1. `acl.init`、`set_device`、`create_context`。
2. `acl.mdl.load_from_file`、创建并读取 model description。
3. 每次推理分配设备输入/输出内存，把连续的 `float32` NCHW 数组复制到设备。
4. `acl.mdl.execute`，再把输出复制回 Host，按 `float32` 转化为 numpy 一维数组并 `argmax`。
5. 遍历结束后释放 dataset、buffer、model、context，并 reset/finalize。

严格合同是输入字节数等于模型输入大小、输出为 200 个 `float32` logits。当前代码发现输入大小不一致时只打印 warning 后继续复制；`get_desc`、device malloc、memcpy 和 `execute` 的部分返回码也没有统一检查。输入尺寸、dtype 或模型输出发生变化时，该脚本可能继续运行却产生错误结果，因此调用方必须把不匹配视为硬失败。

推理循环不在 `try/finally` 中，若图像解码、memcpy 或执行抛异常，`release()` 可能不会执行。调试长时间任务时应监控进程和设备内存，并在脚本外保留退出码。

## 验证与限制

- 当前目录没有自动化 pytest。
- 本次任务没有在 310B 上重跑 OM 加载、分类准确率或 FPS。
- 当前 CPU/NPU 脚本的准确率口径都是 `correct/total`；两者的数据集、预处理和计时范围大致对应，但只有相同 Python/模型 artifact 和相同 split 才能比较。
- OM 缓存元数据哈希不一致是当前明确的 provenance 问题；在解决前，任何当前 OM 的 310B 准确率或性能结论都应标为 `blocked`。
