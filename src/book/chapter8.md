---
title: "第8章：模型量化与精度性能权衡"
author: [周贤中]
date: 2026-06-08
subject: "Markdown"
keywords: [模型量化, FP16, INT8, PTQ, 校准, 精度对齐, ATC, Ascend310B]
lang: zh-cn
---

第 7 章解决的是“应用链路哪里慢”的问题。本章接着回答另一个问题：如果 Profiling 和模型独立基准都说明瓶颈主要在模型本身，是否可以通过降低数值精度来换取更高吞吐和更低内存占用？

模型量化不是简单地把 `float32` 改成 `int8`。它同时影响模型转换、输入预处理、校准数据、精度评估和部署回归。对初学者来说，本章的目标不是追求某一个模型的极限性能，而是建立一套安全的量化实验流程：先有 FP32/FP16 基线，再做 INT8 尝试，最后用同一批输入同时比较精度和性能。

## 8.1 什么时候需要量化 {#src-book-chapter8-h1}

不要一开始就量化。更稳妥的判断顺序是：

1. 先用第 7 章的分段计时确认端到端瓶颈。
2. 再用 `ais_bench` 或 `msame` 单独测 OM 模型上限。
3. 如果模型执行时间占主要部分，再考虑 FP16、混合精度或 INT8。
4. 如果瓶颈在 CPU 预处理、H2D/D2H 或后处理，优先回到 AIPP、DVPP、Buffer 复用和流水线优化。

量化通常适合以下场景：

| 场景 | 是否优先量化 | 原因 |
|---|---|---|
| 模型单独推理也慢 | 是 | 计算本身是主要瓶颈 |
| 模型文件和权重占用过大 | 是 | 低比特权重可降低存储和加载成本 |
| 端到端耗时主要来自预处理 | 否 | 量化不能解决 Host 侧瓶颈 |
| 精度指标已经接近业务下限 | 谨慎 | INT8 可能带来不可接受的精度损失 |
| 模型包含很多不支持 INT8 的算子 | 谨慎 | 可能回退到混合精度，收益有限 |

## 8.2 FP32、FP16 与 INT8 的区别 {#src-book-chapter8-h2}

深度学习推理常见的精度模式可以粗略理解为三层：

| 精度 | 特点 | 常见用途 |
|---|---|---|
| FP32 | 精度高，内存和带宽开销大 | 训练、CPU 对齐基准、精度参考 |
| FP16 | 精度略低，硬件通常更友好 | 昇腾 310B 上常用的推理精度 |
| INT8 | 数值范围更小，需要尺度映射 | 对性能和内存更敏感的部署场景 |

FP16 通常属于“比较温和”的优化。很多模型从 FP32 转 FP16 后，Top1、mAP 等指标变化很小。

INT8 则更敏感。它需要把浮点数映射到 8 位整数范围，因此必须决定每一层或每一组张量的 scale 和 zero point。这个过程如果估计不准，就会导致激活截断、输出漂移，甚至 Top1/mAP 明显下降。

## 8.3 PTQ 与 QAT {#src-book-chapter8-h3}

模型量化常见有两条路线：

| 路线 | 全称 | 特点 | 适合读者阶段 |
|---|---|---|---|
| PTQ | Post-Training Quantization | 训练后量化，不重新训练或只做少量校准 | 部署入门和快速评估 |
| QAT | Quantization-Aware Training | 训练时模拟量化误差 | 对精度要求高的项目 |

本章建议初学者先从 PTQ 开始，因为它更贴近部署工程流程：拿到已经训练好的 ONNX 模型，准备一小批有代表性的校准数据，转换出量化 OM，再比较精度和性能。

QAT 更适合作为进阶内容。它通常需要回到训练框架中修改模型、插入伪量化节点，并重新训练或微调。如果 PTQ 精度损失不可接受，再考虑 QAT。

## 8.4 校准数据集怎么准备 {#src-book-chapter8-h4}

INT8 PTQ 的关键不是数据越多越好，而是校准数据要覆盖真实输入分布。校准集建议满足以下要求：

1. 输入预处理与部署完全一致，包括 resize、crop、颜色通道、normalize。
2. 覆盖常见场景，例如白天、夜晚、遮挡、不同角度、不同背景。
3. 数量不必很大，入门实验可以从 50 到 200 张开始。
4. 固定清单和 hash，避免每次转换使用不同数据导致结果不可复现。

推荐目录结构：

```text
calibration/
  images/
    000001.jpg
    000002.jpg
  calib_list.txt
  README.md
```

`calib_list.txt` 只保存相对路径：

```text
images/000001.jpg
images/000002.jpg
```

对于 Tiny-ImageNet 版 ResNet18，可以直接从验证集抽取一小部分图像做校准。对于检测模型，则应覆盖不同目标数量、不同尺度和不同背景，否则量化后 NMS 前的置信度分布可能发生明显偏移。

本章 `samples/chapter8/01_collect_calibration_list.py` 为了让读者没有 Tiny-ImageNet 原始验证集时也能跑通流程，会生成一组确定性的 RGB `.npy` 样本。它适合教学闭环和脚本验证，但不能替代真实项目中的代表性校准集。正式评估时，应换成来自验证集或业务数据的真实图片。

## 8.5 昇腾模型转换中的精度模式 {#src-book-chapter8-h5}

本章使用的 ResNet18-TinyImageNet 模型来自 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet`。进入本章样例目录后，先下载基线 OM 和 ONNX：

```bash
cd samples/chapter8
PY=/home/HwHiAiUser/.conda/envs/npu/bin/python
$PY tools/download_model.py
```

这一步会把 `resnet18_tiny_imagenet.om` 和 `resnet18_tiny_imagenet.onnx` 保存到 `samples/chapter8/model/`。当前公开仓库已经提供基线 OM、ONNX 和 AIPP 相关文件；FP16/INT8 OM 默认由本章转换脚本生成。如果课程或仓库后续提供 FP16/INT8 OM，也可以把它们放在同一个 `model/` 目录中，后续对齐脚本会直接读取。

ATC 转换时，常见的精度相关参数包括：

| 参数 | 作用 | 说明 |
|---|---|---|
| `--precision_mode=allow_fp32_to_fp16` | 允许 FP32 转 FP16 | 常用的 FP16 推理模式 |
| `--precision_mode=allow_mix_precision` | 允许混合精度 | 由转换器根据算子情况选择 |
| `--input_format` | 指定输入布局 | 必须与模型和预处理一致 |
| `--input_shape` | 固定输入形状 | 边缘部署建议先固定 shape |
| `--insert_op_conf` | 插入 AIPP 配置 | 图像模型常与预处理下沉配合 |

FP16 转换模板：

```bash
cd samples/chapter8
atc \
  --model=model/resnet18_tiny_imagenet.onnx \
  --framework=5 \
  --output=model/resnet18_tiny_imagenet_fp16 \
  --input_format=NCHW \
  --input_shape="input.1:1,3,64,64" \
  --soc_version=Ascend310B4 \
  --precision_mode=allow_fp32_to_fp16 \
  --log=info
```

本章已经把这条命令封装到独立 shell 脚本中。读者只需要先进入本章样例目录，再运行：

```bash
cd samples/chapter8
bash tools/convert_fp16_resnet18.sh
```

INT8 转换通常还需要校准配置或配套工具链生成的量化信息。不同 CANN 版本的 INT8 流程和参数可能存在差异，因此不要直接把一个模板当成所有模型都能用的命令。正式项目中，应以当前 CANN 版本文档和实际 ATC 日志为准。

本章样例采用两步完成 INT8：

```text
FP32 ONNX + calibration/calib_list.txt
  -> 准备 AMCT 支持的 opset 中间模型
  -> AMCT 静态 PTQ，生成 deploy ONNX
  -> ATC 转换，生成 INT8 OM
```

对应命令为：

```bash
cd samples/chapter8
bash tools/convert_int8_resnet18.sh
```

这个脚本会先调用 `tools/quantize_int8_resnet18.py`，默认在 `outputs/int8_amct/` 中准备一份 opset 11 中间 ONNX，再通过 AMCT 生成 `model/resnet18_tiny_imagenet_int8_deploy.onnx`，最后由 shell 脚本直接调用 ATC 生成 `model/resnet18_tiny_imagenet_int8.om`。Python 脚本只负责 AMCT 校准和 deploy ONNX 生成，不调用 ATC。对于 AMCT deploy ONNX，脚本默认不再额外指定 `--precision_mode`，避免把量化模型又强行拉回普通混合精度转换路径。

这里要特别区分两类 INT8 ONNX。ONNX Runtime 普通静态量化生成的 QDQ/QOperator 模型适合在 ONNX Runtime 中验证量化误差，但当前 CANN/ATC 路径不能直接解析 `QuantizeLinear`、`DequantizeLinear`、`QLinearConv` 或 `com.microsoft::QGemm` 这类节点。本章使用 AMCT，是为了生成 ATC 可以继续转换的 deploy ONNX。

因此，如果脚本提示当前 Python 环境缺少 `amct_onnx`，需要先在开发板上安装与 CANN 版本和 CPU 架构匹配的 AMCT 包。只安装普通的 `onnx`、`onnxruntime` 并不能完成本章的真实 INT8 部署流程。如果你的 AMCT 版本支持课程下载的原始 ONNX opset，也可以给脚本传入 `--amct-opset 0` 关闭中间模型转换。

## 8.6 精度对齐流程 {#src-book-chapter8-h6}

量化后的第一件事不是测 FPS，而是检查输出是否还可信。建议使用同一批输入比较三组结果：

```text
PyTorch/ONNX FP32 输出 -> FP16 OM 输出 -> INT8 OM 输出
```

分类模型可以先看：

| 指标 | 含义 |
|---|---|
| Top1 是否一致 | 最直观，但不够细 |
| Top5 是否一致 | 对分类排序变化更宽容 |
| 最大绝对误差 | 是否存在异常离群 |
| 平均绝对误差 | 整体输出漂移是否可控 |
| Top1 准确率变化 | 对业务最有意义 |

检测模型还要额外比较：

| 指标 | 含义 |
|---|---|
| mAP 变化 | 检测任务主指标 |
| 框数量变化 | NMS 前后是否稳定 |
| 置信度分布 | 是否整体偏高或偏低 |
| 小目标召回 | INT8 常见风险点之一 |

建议设置明确阈值。例如：

```text
FP16: Top1 下降 <= 0.2%，平均绝对误差 <= 1e-3
INT8: Top1 下降 <= 1.0%，mAP 下降 <= 1.0 到 2.0 个百分点
```

这些阈值不是通用标准，而是教学阶段的起点。真实项目应由业务风险决定。

## 8.7 性能对比流程 {#src-book-chapter8-h7}

性能对比必须控制变量。不要同时改 Batch、输入尺寸、AIPP、后处理和精度模式。

推荐实验顺序：

1. 普通 FP16 OM，CPU 预处理。
2. 普通 FP16 OM，AIPP 预处理。
3. INT8 OM，CPU 预处理。
4. INT8 OM，AIPP 预处理。

每次都记录：

| 字段 | 说明 |
|---|---|
| 模型文件名和 hash | 避免混用旧模型 |
| CANN 版本 | 精度和算子选择可能受版本影响 |
| `npu-smi info` | 记录温度、Health、内存 |
| runs / warmup | 保证对比公平 |
| mean / p50 / p95 / FPS | 不只看平均值 |
| 精度指标 | 性能提升不能以不可接受精度损失为代价 |

第 7 章的 `StageRecorder` 和 PyACL Runner 已经复用到本章配套代码中。样例目录位于 `samples/chapter8/`：

```text
samples/chapter8/
  README.md
  chapter8_utils.py
  01_collect_calibration_list.py
  02_compare_outputs.py
  03_perf_compare.py
  tools/
    download_model.py
    convert_fp16_resnet18.sh
    convert_int8_resnet18.sh
    quantize_int8_resnet18.py
  model/
  calibration/
  outputs/
```

推荐读者按下面顺序运行：

```bash
cd samples/chapter8
PY=/home/HwHiAiUser/.conda/envs/npu/bin/python

$PY tools/download_model.py
$PY 01_collect_calibration_list.py --count 50
bash tools/convert_fp16_resnet18.sh
bash tools/convert_int8_resnet18.sh

$PY 02_compare_outputs.py \
  --base-model model/resnet18_tiny_imagenet.om \
  --candidate-model model/resnet18_tiny_imagenet_fp16.om \
  --samples 20

$PY 03_perf_compare.py \
  --models model/resnet18_tiny_imagenet.om model/resnet18_tiny_imagenet_fp16.om model/resnet18_tiny_imagenet_int8.om \
  --labels baseline fp16 int8 \
  --runs 100
```

## 8.8 常见失败模式 {#src-book-chapter8-h8}

| 现象 | 可能原因 | 处理建议 |
|---|---|---|
| ATC 转换失败 | 算子或量化参数不支持 | 先转 FP16，确认模型本身可转换 |
| INT8 性能收益很小 | 多数算子仍回退到 FP16 | 查看 ATC 日志和 Profiling |
| Top1 大幅下降 | 校准集不代表真实输入 | 重做校准集，检查预处理 |
| 某些类别明显变差 | 类别样本覆盖不足 | 分类别统计误差 |
| 输出全为异常值 | 输入 layout 或 AIPP 配置不匹配 | 回到 FP32/FP16 输出逐步对齐 |
| p95 变差 | 首次加载、内存抖动或混合精度回退 | 增加预热，检查资源复用 |

量化失败时不要急着调参数。先确认三件事：

1. FP16 OM 是否能与原始模型对齐。
2. 校准输入是否与部署输入完全一致。
3. INT8 OM 是否真的使用了目标量化路径。

## 8.9 与第 7 章的关系 {#src-book-chapter8-h9}

第 7 章强调“先定位瓶颈”。本章强调“如果瓶颈在模型本身，再改变模型精度”。

可以把两章合起来作为一个决策树：

```text
端到端慢
  -> 分段计时
    -> Host 预处理慢：AIPP / DVPP / 工作区复用
    -> H2D/D2H 慢：减少输入输出、复用 Buffer、预处理下沉
    -> 模型 execute 慢：FP16 / INT8 / 模型结构优化
    -> 队列吞吐不足：Pipeline / 多进程 / 批处理
```

量化不是替代性能分析，而是性能分析之后的一个优化分支。

## 8.10 练习任务 {#src-book-chapter8-h10}

1. 进入 `samples/chapter8`，运行 `tools/download_model.py` 从 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet` 下载 ResNet18-TinyImageNet 模型资产。
2. 运行 `01_collect_calibration_list.py --count 50`，生成 `calibration/calib_list.txt`，并说明这些样本与真实校准集的区别。
3. 运行 `tools/convert_fp16_resnet18.sh`，转换 `model/resnet18_tiny_imagenet_fp16.om`，并记录 ATC 日志中的模型输入名和精度模式。
4. 运行 `02_compare_outputs.py`，用同一批输入比较普通 OM 与 FP16 OM 的 Top1、最大绝对误差和平均绝对误差。
5. 运行 `03_perf_compare.py`，比较普通 OM 与 FP16 OM 的端到端耗时、p95 和 FPS。
6. 运行 `tools/convert_int8_resnet18.sh` 生成 AMCT deploy ONNX 和 INT8 OM。如果环境缺少 `amct_onnx`，先补齐 AMCT 工具链，再用 `02_compare_outputs.py` 与 `03_perf_compare.py` 记录精度下降和性能收益。

## 8.11 小结 {#src-book-chapter8-h11}

模型量化的核心是精度、性能和工程风险之间的权衡。FP16 通常是边缘部署的第一步，INT8 则需要校准数据、精度对齐和回归测试共同支撑。对初学者来说，最重要的习惯是：任何量化收益都必须同时给出性能数据和精度数据；只有“更快”而没有“仍然正确”，不能算完成部署优化。
