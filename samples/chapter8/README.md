# 第8章 模型量化样例

本目录配套 `src/book/chapter8.md`，使用第 7 章相同的 ResNet18-TinyImageNet 作为例子，演示量化实验的基本闭环：

1. 准备校准样本清单。
2. 转换 FP16 或 INT8 OM。
3. 用同一批输入比较输出差异。
4. 用同一套计时口径比较性能。

涉及 ATC、PyACL 和 OM 推理的命令需要在真实 Ascend 310B 设备上运行。本地开发机适合阅读代码、生成校准清单、做语法检查和比较已有 `.npy` 输出。

## 运行前提

在 Ascend 310B 上先加载 CANN 环境，并进入本章目录：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/chapter8
PY=/home/HwHiAiUser/.conda/envs/npu/bin/python
```

本章模型来自 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet`。首次运行前下载基线 OM 和 ONNX：

```bash
$PY tools/download_model.py
```

这一步会下载 `resnet18_tiny_imagenet.om` 和 `resnet18_tiny_imagenet.onnx`。当前公开仓库已经提供基线 OM、ONNX 和 AIPP 相关文件；FP16/INT8 OM 默认由本章转换脚本生成。如果后续课程资料或 Hugging Face 仓库上传了转换后的模型，也可以运行：

```bash
$PY tools/download_model.py --converted
```

## 1. 生成校准清单

教学样例先生成一组确定性的 64x64 RGB `.npy` 文件，方便没有 Tiny-ImageNet 原始验证集的读者跑通流程：

```bash
$PY 01_collect_calibration_list.py --count 50
```

输出文件：

```text
calibration/generated_rgb/
calibration/calib_list.txt
calibration/calibration_manifest.json
```

正式项目中可以把 `calibration/generated_rgb/` 替换为真实图片目录，但 `calib_list.txt` 仍建议只写相对路径，并固定数据来源。

## 2. 转换 FP16 OM

FP16 转换脚本只调用 ATC，不会通过 Python 间接调用转换命令：

```bash
bash tools/convert_fp16_resnet18.sh
```

默认输入和输出：

```text
model/resnet18_tiny_imagenet.onnx
model/resnet18_tiny_imagenet_fp16.om
```

如果 ONNX 输入名与默认值不同，可以显式传入：

```bash
bash tools/convert_fp16_resnet18.sh --input-name input --input-shape 1,3,64,64
```

## 3. 转换 INT8 OM

INT8 不能只靠 `atc --precision_mode=allow_mix_precision` 完成。`allow_mix_precision` 只是让 ATC 按算子支持情况选择混合精度，并不会自动完成 INT8 校准。

本章脚本采用 CANN/ATC 更适合的两步流程：先用 AMCT 做静态 PTQ，生成可部署的 INT8 ONNX，再调用 ATC 生成 OM。

```bash
bash tools/convert_int8_resnet18.sh
```

流程是：

```text
model/resnet18_tiny_imagenet.onnx
  -> outputs/int8_amct/resnet18_tiny_imagenet_opset11.onnx
  -> model/resnet18_tiny_imagenet_int8_deploy.onnx
  -> model/resnet18_tiny_imagenet_int8.om
```

量化这一步需要 Python 环境里安装与当前 CANN 版本、CPU 架构匹配的 AMCT ONNX 包，也就是能 `import amct_onnx`。如果脚本提示缺少 `amct_onnx`，需要先在开发板上安装对应版本的 AMCT，而不是只安装普通的 `onnxruntime`。

AMCT ONNX 对输入模型的 opset 有版本要求。课程下载的原始 ONNX 会保留在 `model/` 目录，脚本默认在 `outputs/int8_amct/` 里准备一份 opset 11 中间模型，再交给 AMCT；如果你的 AMCT 版本支持原始 opset，也可以传入 `--amct-opset 0` 关闭这一步。

Python 脚本只负责 AMCT 校准和 deploy ONNX 生成，不会调用 ATC；ATC 仍由 shell 脚本直接调用。这样读者可以清楚地区分“量化”和“模型转换”两个步骤。对于 AMCT deploy ONNX，脚本默认不再额外指定 `--precision_mode`，避免把量化模型又强行拉回普通混合精度转换路径。

如果只想先生成 AMCT deploy ONNX，不运行 ATC，可以执行：

```bash
bash tools/convert_int8_resnet18.sh --skip-atc
```

不要把 ONNX Runtime 普通静态量化得到的 QDQ/QOperator ONNX 当作这里的 deploy ONNX。当前 CANN/ATC 路径不能直接解析 `QuantizeLinear`、`DequantizeLinear`、`QLinearConv` 或 `com.microsoft::QGemm` 这类节点。本章使用 AMCT，是为了得到 ATC 能继续转换的 INT8 模型。

## 4. 输出对齐

比较普通 OM 与 FP16 OM 的 Top1、Top5 重叠、最大绝对误差和平均绝对误差：

```bash
$PY 02_compare_outputs.py \
  --base-model model/resnet18_tiny_imagenet.om \
  --candidate-model model/resnet18_tiny_imagenet_fp16.om \
  --samples 20
```

如果需要比较 INT8，把 `--candidate-model` 改成 `model/resnet18_tiny_imagenet_int8.om`。

脚本也支持本地比较两个预先保存的 logits 文件：

```bash
python3 02_compare_outputs.py \
  --base-npy outputs/base_logits.npy \
  --candidate-npy outputs/fp16_logits.npy
```

## 5. 性能对比

用同一批输入比较多个 OM 的端到端耗时：

```bash
$PY 03_perf_compare.py \
  --models model/resnet18_tiny_imagenet.om model/resnet18_tiny_imagenet_fp16.om \
  --labels baseline fp16 \
  --runs 100
```

如果已经生成 INT8 OM，可以一起加入：

```bash
$PY 03_perf_compare.py \
  --models model/resnet18_tiny_imagenet.om model/resnet18_tiny_imagenet_fp16.om model/resnet18_tiny_imagenet_int8.om \
  --labels baseline fp16 int8 \
  --runs 100
```

## 本地检查

```bash
cd samples/chapter8
python3 -m py_compile *.py
bash -n tools/convert_fp16_resnet18.sh tools/convert_int8_resnet18.sh
python3 01_collect_calibration_list.py --count 3 --output-dir /tmp/chapter8_calib/generated_rgb --list /tmp/chapter8_calib/calib_list.txt --manifest /tmp/chapter8_calib/manifest.json --overwrite
```

## 目录说明

| 文件 | 说明 |
|---|---|
| `chapter8_utils.py` | 复用第 7 章预处理、计时和报告工具，并补充校准清单、hash 和输出比较函数 |
| `01_collect_calibration_list.py` | 生成确定性 RGB 校准样本、`calib_list.txt` 和 manifest |
| `02_compare_outputs.py` | 对比两个 OM 或两个 `.npy` logits 的输出差异 |
| `03_perf_compare.py` | 对比多个 OM 的端到端性能和相对加速比 |
| `tools/download_model.py` | 从 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet` 下载本章模型 |
| `tools/convert_fp16_resnet18.sh` | 调用 ATC 生成 `resnet18_tiny_imagenet_fp16.om` |
| `tools/quantize_int8_resnet18.py` | 使用 AMCT 静态 PTQ 生成 `resnet18_tiny_imagenet_int8_deploy.onnx`，不调用 ATC |
| `tools/convert_int8_resnet18.sh` | 先生成 AMCT deploy ONNX，再调用 ATC 生成 `resnet18_tiny_imagenet_int8.om` |
| `model/` | 本章模型目录，保存 ONNX、基线 OM、FP16 OM 和 INT8 OM |
| `calibration/` | 校准样本和清单目录 |
| `outputs/` | 输出对齐和性能对比报告目录 |
