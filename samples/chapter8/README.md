# 第8章 模型量化样例

本目录配套 `src/book/chapter8.md`，使用第 7 章相同的
ResNet18-TinyImageNet ONNX 模型，演示一条完整但尽量简单的量化实验主线：

1. 下载 FP32 ONNX 和 Tiny-ImageNet PNG 数据。
2. 用 ATC 从 ONNX 转出 FP32 OM 和 FP16 OM。
3. 用 `01_compare_outputs.py` 和 `02_perf_compare.py` 快速验证 FP16。
4. 用 AMCT PTQ + ATC 转出 INT8 OM。
5. 用同样的快速检查验证 INT8。
6. 用 `05_validate_accuracy.py` 在完整验证集上横向对比 FP32、FP16、INT8。
7. 用 sweep 脚本观察 INT8 对校准样本数的敏感程度。

涉及 ATC、PyACL 和 OM 推理的命令需要在真实 Ascend 310B 开发板上运行。本地
开发机适合阅读代码和做语法检查，不运行 CANN/OM 推理。

## 运行前提

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
conda activate npu
cd ~/Documents/Ascend310/samples/chapter8
```

310B 开发板内存有限，运行 ATC 前建议限制并行编译：

```bash
export TE_PARALLEL_COMPILER=1
export MAX_COMPILE_CORE_NUMBER=1
```

INT8 PTQ 还需要安装与 CANN 版本匹配的 AMCT ONNX。本章验证环境为：
CANN 8.3.RC1、aarch64、Python 3.11、ONNX 1.14.0、ONNX Runtime 1.16.0、
AMCT ONNX 0.23.2。

## 1. 下载模型和 PNG 数据

下载 FP32 ONNX：

```bash
python tools/download_model.py
```

下载训练集校准图片。脚本默认使用 `https://hf-mirror.com`，并把图片保存为 PNG：

```bash
python tools/download_tiny_imagenet.py train --force-download --per-class 2
```

这会生成：

```text
data/tiny_imagenet_train/  # 400 张 PNG
data/calib_list.txt         # AMCT 校准和快速对齐使用
```

下载验证集图片：

```bash
python tools/download_tiny_imagenet.py val --force-download
```

这会生成：

```text
data/tiny_imagenet_val/  # 10000 张 PNG
data/val_list.txt         # 05_validate_accuracy.py 使用，格式为：路径 标签
```

如果只想使用本地 Hugging Face cache，可加 `--offline`。

## 2. ATC 转 FP32 和 FP16 OM

FP32 OM 用作精度参考：

```bash
atc \
  --model=model/resnet18_tiny_imagenet.onnx \
  --framework=5 \
  --output=model/resnet18_tiny_imagenet_fp32 \
  --input_format=NCHW \
  --input_shape="input.1:1,3,64,64" \
  --soc_version=Ascend310B4 \
  --precision_mode=force_fp32 \
  --log=info
```

FP16 OM 是 310B 上常用的部署精度：

```bash
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

## 3. 快速验证 FP16

`01_compare_outputs.py` 用同一批校准图片比较两个 OM 的输出是否接近。它不看
真实标签，只回答“新模型是否像基准模型”。

```bash
python 01_compare_outputs.py \
  --base-model model/resnet18_tiny_imagenet_fp32.om \
  --candidate-model model/resnet18_tiny_imagenet_fp16.om \
  --output outputs/output_compare.json
```

`02_perf_compare.py` 用同一批图片比较端到端性能：

```bash
python 02_perf_compare.py \
  --models model/resnet18_tiny_imagenet_fp32.om model/resnet18_tiny_imagenet_fp16.om \
  --labels fp32 fp16 \
  --runs 100 \
  --output outputs/perf_compare.json
```

## 4. AMCT PTQ + ATC 转 INT8 OM

先生成 AMCT 配置和中间 ONNX：

```bash
python 03_prepare_quantization.py
```

再用 PNG 校准图片统计激活范围并冻结 deploy ONNX：

```bash
python 04_calibrate_quantization.py
```

最后用 ATC 转 INT8 OM。这里不要额外设置 `--precision_mode`：

```bash
atc \
  --model=model/resnet18_tiny_imagenet_int8_deploy.onnx \
  --framework=5 \
  --output=model/resnet18_tiny_imagenet_int8 \
  --input_format=NCHW \
  --input_shape="input.1:1,3,64,64" \
  --soc_version=Ascend310B4 \
  --log=info
```

## 5. 快速验证 INT8

先和 FP32 OM 做输出对齐：

```bash
python 01_compare_outputs.py \
  --base-model model/resnet18_tiny_imagenet_fp32.om \
  --candidate-model model/resnet18_tiny_imagenet_int8.om \
  --output outputs/output_compare_int8.json
```

再把三种 OM 放在同一个性能口径下比较：

```bash
python 02_perf_compare.py \
  --models model/resnet18_tiny_imagenet_fp32.om model/resnet18_tiny_imagenet_fp16.om model/resnet18_tiny_imagenet_int8.om \
  --labels fp32 fp16 int8 \
  --runs 100 \
  --output outputs/perf_compare.json
```

## 6. 完整验证集 accuracy

最终精度结论必须来自独立验证集，而不是 `01_compare_outputs.py` 的输出相似度：

```bash
python 05_validate_accuracy.py --output outputs/accuracy_compare.json
```

脚本会同时运行 FP32、FP16、INT8 三个 OM，输出 Top1、Top5、mean latency 和
p95 latency。默认评估 `data/val_list.txt` 中全部 10000 张验证图片。

调试时可以先少量运行：

```bash
python 05_validate_accuracy.py --samples 200
```

## 7. 校准样本数 sweep

如果要观察 INT8 对校准样本数量的敏感程度，运行：

```bash
bash tools/sweep_calibration_samples.sh 20 50 100 200 400
```

脚本会为每个样本数单独生成 deploy ONNX、INT8 OM 和验证集报告，最后写出：

```text
outputs/calibration_sweep/summary.csv
outputs/calibration_sweep/samples_*/accuracy_compare.json
outputs/calibration_sweep/samples_*/resnet18_tiny_imagenet_int8_s*.om
```

`summary.csv` 中的 `calibration_samples`、`int8_top1_pct`、`int8_top5_pct`
可以直接用来画“校准样本数 - INT8 精度”曲线。

常用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SAMPLE_COUNTS` | `20 50 100 200 400` | 不传命令行参数时使用的样本数列表 |
| `VAL_SAMPLES` | `0` | 每个点评估多少张验证图，`0` 表示全部验证集 |
| `OUT_DIR` | `outputs/calibration_sweep` | sweep 输出目录 |
| `SOC_VERSION` | `Ascend310B4` | ATC 目标芯片 |
| `SWEEP_SEED` | `2024` | 固定校准子集随机顺序 |

## 目录说明

| 文件 | 说明 |
|---|---|
| `01_compare_outputs.py` | 小样本输出对齐，比较两个 OM 的 Top1/Top5 和 logits 误差 |
| `02_perf_compare.py` | 端到端性能对比 |
| `03_prepare_quantization.py` | INT8 量化准备：opset 转换和 AMCT config |
| `04_calibrate_quantization.py` | INT8 校准并冻结 deploy ONNX |
| `05_validate_accuracy.py` | 验证集 Top1/Top5 横向对比 |
| `chapter8_utils.py` | 本章共享工具函数 |
| `tools/download_model.py` | 下载 FP32 ONNX |
| `tools/download_tiny_imagenet.py` | 下载 PNG 校准/验证图片 |
| `tools/calibration_sweep_helper.py` | sweep 的子集生成和 CSV 汇总辅助程序 |
| `tools/sweep_calibration_samples.sh` | sweep 不同校准样本数 |
| `model/` | ONNX、OM 和 deploy ONNX |
| `data/` | PNG 数据和清单 |
| `outputs/` | 本轮实验报告 |
