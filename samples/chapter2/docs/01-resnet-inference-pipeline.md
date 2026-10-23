# ResNet50快速推理流程与接口契约

当前目录只包含 CPU 预处理、CPU 后处理和预编译 `msame`，不包含 ATC 源码、ONNX、
OM 或 `msame` 的构建脚本。完整的 NPU 链路需要外部提供与当前 CANN 和 SoC 匹配的
模型及运行时。

## 入口与职责

| 入口 | 运行位置 | 输入 | 输出或副作用 |
| --- | --- | --- | --- |
| `src/make_bin_resnet224_float32.py` | CPU | `data/dog1_1024_683.jpg` | 覆盖写入 `data/dog1_224_float32.bin` |
| `msame` | 310B 板端 | `.om` 模型和原始输入 BIN | 新建时间戳目录及输出 BIN/TXT |
| `src/postprocess_resnet50.py` | CPU 或板端 CPU | 原始 logits BIN、类别 JSON | 标准输出打印 Top-K |
| ATC | 已安装 CANN 的板端或匹配环境 | ONNX 和转换参数 | `.om`、编译日志及可能的融合 JSON |

`msame` 没有随目录提供可读源码，不能在文档中推导其内部 ACL 调用顺序。可确认的
事实仅是：文件为 Linux AArch64 ELF，大小 327272 字节，并在
`sample_resnet_quick_start/README.md` 和章节正文中被描述为 ACL 推理封装工具。

## 依赖

CPU 脚本的最低导入集合是：

```text
numpy
Pillow
```

`postprocess_resnet50.py` 还使用 Python 标准库 `os`、`argparse` 和 `json`。
脚本没有 `requirements.txt`、版本锁或包安装命令。课内板端统一使用 Python 3.9
环境，但当前样例没有记录它实际通过的 NumPy、Pillow 或 Python 精确版本。

NPU 路径依赖 CANN 提供的 ATC、ACL Runtime、模型算子实现、驱动与固件。这些都是
板端或 CANN 安装资产，不应复制到本地 Python 虚拟环境来伪造可运行性。

## 预处理命令与契约

```bash
cd samples/chapter2/sample_resnet_quick_start
python src/make_bin_resnet224_float32.py
```

脚本成功时打印缓冲区字节数，并创建或覆盖：

```text
data/dog1_224_float32.bin
```

输入约束：

- 输入 JPEG 必须存在，并且 Pillow 能识别其编码。
- 脚本强制转换为 RGB，因此灰度或带 alpha 的图片会先丢弃原编码的通道语义。
- 图片先缩放到 `256 x 256`，再从中心裁剪 `224 x 224`。该操作会改变非方形原图的
  视野比例。

输出约束：

| 属性 | 值 |
| --- | --- |
| 语义 | 单个 ImageNet RGB 图像 |
| shape | `(1, 3, 224, 224)` |
| layout | `NCHW` |
| dtype | `float32` |
| 数值范围 | 按 ImageNet mean/std 归一化后的浮点值 |
| 文件格式 | 无 header、无 shape、无 dtype 元数据 |
| 大小 | `1 * 3 * 224 * 224 * 4 = 602112` 字节 |

归一化均值是 `[0.485, 0.456, 0.406]`，标准差是
`[0.229, 0.224, 0.225]`。下游模型必须使用相同的颜色顺序、裁剪方式、归一化和
输入名。否则即使 `msame` 返回成功，分类结果也可能偏移。

## ATC 输入输出契约

章节中的典型命令是：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/chapter2/sample_resnet_quick_start
mkdir -p model

atc \
  --model=model/resnet50.onnx \
  --framework=5 \
  --output=model/resnet50 \
  --input_shape="actual_input_1:1,3,224,224" \
  --soc_version=Ascend310B4
```

`--framework=5` 表示 ONNX，`--input_shape` 将模型输入固定为 NCHW
`1 x 3 x 224 x 224`。`actual_input_1` 必须与 ONNX 实际输入名一致；这个名字不是
ResNet50 的通用常量。转换前应记录：

- ONNX 的来源、revision、字节数和 SHA-256。
- `onnx` 图的实际输入名、shape、dtype 和 layout。
- 目标设备型号与 `npu-smi` 事实。
- CANN 版本、完整 ATC 命令、退出码和日志。
- 生成 OM 的路径、字节数和 SHA-256。

当前目录没有以上转换证据，也没有 OM，因此 ATC 状态是 `untested`。

## `msame` 调用契约

章节使用的命令形态是：

```bash
./msame \
  --model ./model/resnet50.om \
  --input ./data/dog1_224_float32.bin \
  --output ./out \
  --outfmt BIN
```

需要以当前 `msame --help` 为准。正文记录了 `--model`、`--input`、`--output`、
`--outfmt`、`--loop`、`--device` 和动态 shape 等参数，但预编译二进制没有附带版本
说明，不能据此认定所有版本的参数集合相同。

成功时的文件契约是：

- 输入：静态 shape 的单个 BIN，示例为 602112 字节。
- 输出：`out/<timestamp>/resnet50_output_0.bin`。
- 当前 fixture：4000 字节，恰好对应 1000 个 `float32` 元素。
- 输出数值：分类 logits，尚未做 softmax。

输出目录和时间戳不是固定 fixture。后续脚本应接收实际路径，而不是写死
`20251213_11_6_52_564388`。

## Top-K 后处理契约

```bash
python src/postprocess_resnet50.py \
  --bin out/<timestamp>/resnet50_output_0.bin \
  --topk 5
```

参数：

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--bin` | 是 | 无 | `msame` 输出的原始 BIN 路径 |
| `--topk` | 否 | `5` | 输出的类别数量 |

脚本行为：

1. 用 `numpy.fromfile(..., dtype=numpy.float32)` 读取整个文件。
2. 将一维数据 reshape 成 `(1, -1)`，没有校验元素数是否恰好为 1000。
3. 对最后一维执行 max-shift softmax。
4. 从当前工作目录的 `src/imagenet_class_index.json` 读取类别映射。
5. 按概率降序打印类别编号、概率和类别名。

成功退出码为 `0`，普通异常未捕获。概率之和应为 1，但如果输入不是单个模型的
1000 个 float32 logits，脚本仍可能打印看似合理的结果。生产用途应显式校验元素数
与模型输出签名。

类别 JSON 当前有 1000 个键，键为 `"0"` 到 `"999"`，值形如
`[WNID, readable_name]`。脚本取第二个元素作为标签名。

## 当前 fixture 与已验证事实

本次本地 CPU 检查得到：

- `data/dog1_224_float32.bin` 包含 150528 个有限 `float32` 元素，共 602112 字节，
  与 `(1, 3, 224, 224)` 一致。
- `out/20251213_11_6_52_564388/resnet50_output_0.bin` 为 4000 字节，可被后处理
  脚本解释为 1000 个 logits。
- 后处理对 fixture 输出的 Top-1 是 `class=162`、`prob=0.964885`、
  `label=beagle`。

这些是 fixture 完整性检查和 CPU 后处理证据，不是当前 OM 的 NPU 精度或性能证据。
