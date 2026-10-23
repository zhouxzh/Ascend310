# 评估、Sweep 与运行命令

## 2026-10-23 20T 实测

固定 ONNX revision `dfcf5885dcf7beab3a5d6e2143c73d7973bead36` 已在 20T 上重新执行 ATC FP32/FP16 并完成 ACL 推理：

| 门禁 | 结果 |
| --- | --- |
| ATC FP32 | `observed-pass` |
| ATC FP16 | `observed-pass` |
| FP32/FP16 5 样本 Top-1 | 5/5 一致 |
| FP32/FP16 Top-5 重合 | 5/5 |
| max abs logit diff | 0.006801 |
| FP32 end-to-end mean | 6.4284 ms |
| FP16 end-to-end mean | 6.9812 ms |
| AMCT / INT8 | `blocked`，20T 环境未安装 `amct_onnx` |
| 完整验证集 | `blocked`，镜像超时；数据集报告体积约 19.9 GB |

本表的性能输入是确定性生成图，不是完整业务数据。完整验证集准确率仍未产生。

## 板端公共环境

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
cd samples/chapter9
```

对内存较小的板卡，ATC 前可设置：

```bash
export TE_PARALLEL_COMPILER=1
export MAX_COMPILE_CORE_NUMBER=1
```

当前 chapter9 源码还存在共享 utility 路径问题，见 [03-dependencies-failures-and-evidence.md](03-dependencies-failures-and-evidence.md)。在该问题解决或显式提供兼容模块前，下面脚本可能在建 OM 之前就导入失败；不要将这种失败归因于模型或 NPU。

## 输出对齐

FP16 对 FP32：

```bash
python 01_compare_outputs.py \
  --base-model model/resnet18_tiny_imagenet_fp32.om \
  --candidate-model model/resnet18_tiny_imagenet_fp16.om \
  --calib-list data/calib_list.txt \
  --samples 0 \
  --output outputs/output_compare.json
```

INT8 对 FP32：

```bash
python 01_compare_outputs.py \
  --base-model model/resnet18_tiny_imagenet_fp32.om \
  --candidate-model model/resnet18_tiny_imagenet_int8.om \
  --calib-list data/calib_list.txt \
  --samples 0 \
  --output outputs/output_compare_int8.json
```

报告包含：

- 每个样本的 base/candidate Top-1 和 Top-k；
- Top-1 是否一致；
- Top-k 集合交集；
- 最大和平均绝对 logits 差；
- 两种模型的阶段延迟；
- 模型文件大小和 SHA-256。

该检查不读取标签，也不计算准确率。相同的 Top-1 可能同时错误；“Top-1 match rate”不是模型准确率。

## 同输入性能比较

```bash
python 02_perf_compare.py \
  --models model/resnet18_tiny_imagenet_fp32.om \
           model/resnet18_tiny_imagenet_fp16.om \
           model/resnet18_tiny_imagenet_int8.om \
  --labels fp32 fp16 int8 \
  --runs 100 \
  --output outputs/perf_compare.json
```

脚本逐模型加载、预热和计时，再按顺序释放。若 `data/calib_list.txt` 存在，则循环读取其中图像；若不存在，则退回 `--height`/`--width` 指定的合成 RGB 输入，并在报告的 `input_source` 中写 `deterministic_generated`。

计时包含：

- Python/PyAV 预处理；
- H2D、`acl.mdl.execute`、D2H；
- `argmax` 后处理；
- 上述阶段和得到的 `end_to_end`。

模型按列表顺序串行测量，不是并发执行。若需要严格排名，应固定输入、模型顺序、设备、预热和运行次数，并重复多轮。

## 完整验证集精度

```bash
python 05_validate_accuracy.py \
  --val-list data/val_list.txt \
  --om-models model/resnet18_tiny_imagenet_fp32.om \
              model/resnet18_tiny_imagenet_fp16.om \
              model/resnet18_tiny_imagenet_int8.om \
  --labels fp32 fp16 int8 \
  --warmup 3 \
  --topk 5 \
  --output outputs/accuracy_compare.json
```

默认评估清单中的全部样本；调试时可加 `--samples 200`。报告记录：

- 验证清单总条目数和实际评估数；
- 实际评估的类别数；
- 从 OM 输出形状推断的类别数；
- 每个模型 Top-1、Top-k、混淆样本和推理阶段延迟；
- 可选 `--save-samples` 逐样本预测。

如果多个 OM 的输出类数不一致，脚本抛 `ValueError` 并停止，不进行无法对齐的准确率比较。标签超出输出类别范围不会被自动拒绝，它只会表现为预测错误；数据清理阶段应单独校验标签范围。

Top-k 会按输出类数截断，并记录 `effective_topk`。只有有效 top-k 为 5 时才写 `top5_*` 字段。

## 校准样本数 sweep

```bash
bash tools/sweep_calibration_samples.sh 20 50 100 200 400
```

每个样本数执行：

1. 按固定 seed 从完整校准清单随机抽取子集；
2. 在独立 `int8_amct/` 目录准备量化；
3. 校准并冻结独立 deploy ONNX；
4. 使用 ATC 生成独立 INT8 OM；
5. 用同一 FP32/FP16 OM 和独立 INT8 OM 跑验证集；
6. 写入 `outputs/calibration_sweep/summary.csv`。

常用环境变量：

| 变量 | 默认值 | 影响 |
|---|---|---|
| `ONNX_MODEL` | `model/resnet18_tiny_imagenet.onnx` | 量化源模型 |
| `FP32_OM` | `model/resnet18_tiny_imagenet_fp32.om` | 精度和性能基线 |
| `FP16_OM` | `model/resnet18_tiny_imagenet_fp16.om` | sweep 对照模型 |
| `CALIB_LIST` | `data/calib_list.txt` | 可抽取的校准池 |
| `VAL_LIST` | `data/val_list.txt` | 评估数据 |
| `OUT_DIR` | `outputs/calibration_sweep` | sweep 根目录 |
| `SOC_VERSION` | `Ascend310B4` | ATC 目标 SoC |
| `VAL_SAMPLES` | `0` | `0` 表示全部验证集 |
| `SWEEP_SEED` | `2024` | 子集随机种子 |

sweep 每增加一个点都会重新执行 AMCT、ATC 和完整验证集；当 `VAL_SAMPLES=0` 且验证集为 10000 张时，耗时和板端占用都不可忽略。每个点应保留单独的报告和 OM，不能只保留最终 CSV。

## 图表和汇总

`summary.csv` 字段包括：

- `calibration_samples`
- `validation_samples`
- FP32/FP16/INT8 的 Top-1、Top-5 百分比
- INT8 相对 FP32 的百分点差
- INT8 mean 和 p95 延迟
- 报告路径和 OM 路径

可以直接用校准样本数对 `int8_top1_pct`/`int8_top5_pct` 画曲线。解释 plateau 时必须同时看验证集样本数、seed、完整环境 tuple 和每个点 OM 的哈希；不能只挑最高点作为单一结论。

## 输出与覆盖行为

- `01` 默认写 `outputs/output_compare.json`，FP16 和 INT8 若使用默认输出会互相覆盖，因此应显式区分文件名。
- `02` 默认写 `outputs/perf_compare.json`。
- `05` 默认写 `outputs/accuracy_compare.json`。
- sweep 会覆盖同一样本数目录中的旧的 deploy ONNX、OM 和报告。

这些文件都被 Git 忽略，发布前需要单独归档、校验哈希并标注生成命令。
