# 第2章 CANN模型转换与ResNet50快速推理样例

本目录配套[第2章 CANN软件栈核心组件解析](../../src/book/chapter2.md)，用一个
ResNet50 分类流程演示 ATC 模型转换、`msame` 设备推理和 CPU 后处理。代码位于
[`sample_resnet_quick_start/`](sample_resnet_quick_start/)。

需要先区分三类工作：

- 图片预处理与 logits 后处理只依赖 Python、Pillow 和 NumPy，可在普通 CPU 上运行。
- ATC 转换、OM 加载与 `msame` 推理依赖昇腾 310B、CANN Runtime 和可用的设备节点。
- 仓库附带一份已生成的输入 BIN、输出 BIN 和 `msame` 可执行文件，但未附 OM、ONNX
  和板端运行记录。样例文件本身不能证明当前环境已通过 NPU 推理。

详细说明见[样例文档索引](docs/README.md)。

## 目录与入口

```text
sample_resnet_quick_start/
├── README.md                         # 上游样例的历史说明
├── msame                             # 预编译 AArch64 ELF，封装 ACL 推理流程
├── data/
│   ├── dog1_1024_683.jpg             # 原始测试图片
│   └── dog1_224_float32.bin          # 602112 字节的 NCHW float32 输入
├── out/20251213_11_6_52_564388/
│   └── resnet50_output_0.bin         # 4000 字节，1000 个 float32 logits
└── src/
    ├── make_bin_resnet224_float32.py # CPU 图片预处理
    ├── postprocess_resnet50.py       # CPU Top-K 后处理
    └── imagenet_class_index.json     # 1000 个 ImageNet 类别映射
```

当前检出中没有 `model/` 目录，也没有 `resnet50.onnx` 或 `resnet50.om`。不能把
`data/dog1_224_float32.bin` 直接交给 ATC，它是模型转换完成后的推理输入。

## 环境与依赖

CPU 步骤需要：

- Python 解释器，并安装 `Pillow` 和 `NumPy`。
- 从 `sample_resnet_quick_start/` 作为当前工作目录运行脚本，因为源码使用
  `data/...`、`src/...` 等相对路径。

NPU 步骤还需要：

- 与开发板匹配的 CANN Toolkit、Runtime 和 ResNet50 所需算子包。
- 可由 `npu-smi` 确认的 310B 设备，以及能够访问设备节点的普通用户或已审核权限。
- 与目标 SoC 匹配的 ATC `--soc_version`。本教材的板端命令使用
  `Ascend310B4`；其他 310B SKU 必须先查询设备事实，不能照搬。
- 与 `msame` ABI 兼容的 ACL 和驱动。仓库内的 `msame` 已确认是 AArch64 ELF，
  但这不证明它与当前 CANN、驱动或固件配套。

## CPU 预处理

在样例根目录运行：

```bash
cd samples/chapter2/sample_resnet_quick_start
python src/make_bin_resnet224_float32.py
```

脚本按以下顺序处理：

1. 读取 `data/dog1_1024_683.jpg` 并转换为 RGB。
2. 双线性缩放到 `256 x 256`，中心裁剪到 `224 x 224`。
3. 将像素缩放到 `[0, 1]`，按 ImageNet 均值和标准差逐通道归一化。
4. 从 HWC 转为 NCHW，增加 batch 维。
5. 以无文件头的原始 `float32` 数据写入
   `data/dog1_224_float32.bin`。

预期输出契约是 shape `(1, 3, 224, 224)`、dtype `float32`、602112 字节，
字节序为主机原生字节序。当前 aarch64 Linux 板端按小端解释。

## ATC 与 `msame` 推理

以下命令给出完整流程，但本次文档整理未重新执行 ATC 或 NPU 推理。OM 路径与
`msame` 参数必须按实际 CANN 版本核对。

```bash
cd samples/chapter2/sample_resnet_quick_start
mkdir -p model

# 将经过来源和哈希核验的 resnet50.onnx 放到 model/。
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export TE_PARALLEL_COMPILER=1
export MAX_COMPILE_CORE_NUMBER=1

atc \
  --model=model/resnet50.onnx \
  --framework=5 \
  --output=model/resnet50 \
  --input_shape="actual_input_1:1,3,224,224" \
  --soc_version=Ascend310B4

./msame \
  --model ./model/resnet50.om \
  --input ./data/dog1_224_float32.bin \
  --output ./out \
  --outfmt BIN
```

`msame` 成功执行时会在 `out/` 下创建时间戳目录，并写出类似
`resnet50_output_0.bin` 的原始输出。模型输入名、shape、dtype 和输出数量必须与
OM 实际描述一致；本篇中的 `actual_input_1` 来自配套正文示例，不是所有 ResNet50
导出的通用名称。

## CPU 后处理

对任意一个 1000 类、`float32` 的 BIN 输出运行：

```bash
python src/postprocess_resnet50.py \
  --bin out/20251213_11_6_52_564388/resnet50_output_0.bin \
  --topk 5
```

脚本将整个 BIN 解释为 `(1, 1000)` logits，执行数值稳定的 softmax，并从
`src/imagenet_class_index.json` 查类别名。当前仓库 fixture 的本地 CPU 解码结果为：

```text
 1: class=162  prob=0.964885 label=beagle
 2: class=161  prob=0.023230 label=basset
 3: class=166  prob=0.006107 label=Walker_hound
 4: class=167  prob=0.004985 label=English_foxhound
 5: class=164  prob=0.000334 label=bluetick
```

这只证明后处理脚本能读取该 fixture。它不证明该 BIN 由当前 OM、当前 CANN 或
本次构建产生，也不能替代板端推理、数值对齐和性能测试。

## 验证边界

- `observed-pass`：本章 2 个 Python 文件的语法解析通过；输入 fixture 可解释为
  `(1, 3, 224, 224)` 的有限 `float32` 数据；后处理脚本在 CPU 上完成了上述 Top-5
  解码。
- `untested`：当前环境没有执行图片重生成、ATC 转换、OM 加载、`msame` 推理、精度
  对齐或性能测量。
- `unknown`：`msame` 的来源提交、CANN 版本配套、OM 输入名、板端软件版本和
  fixture 的生成命令均未在本目录中留下可核验记录。

遇到路径、BIN 大小、软链接、ACL 初始化或后端精度问题，先按
[运行边界与故障定位](docs/02-runtime-boundaries-and-failures.md)逐层定位。
