# 第9章样例工程文档

本目录补充 `samples/chapter9` 的量化和评估流程。根目录 `README.md` 提供逐步命令；这里集中说明脚本之间的产物合同、依赖、失败边界，以及快速对齐、性能、完整验证集和 sweep 分别能证明什么。

2026-10-23 在在线 `Ascend310B1 / 20T` 上完成了固定 ONNX 的 FP32/FP16 ATC 转换和 OM 推理验证：

- ATC FP32：`observed-pass`，生成 `validation_20t_fp32.om`，SHA-256 `61a154f82c252518fc2294ba24fa4febd697f6f7f062edc3bfd8cbc00b58a764`。
- ATC FP16：`observed-pass`，生成 `validation_20t_fp16.om`，SHA-256 `38fb13269d2da55ecdb5ce02a89f3b7c883479de23b52f4fc2dc848871ab45ba`。
- 5 张确定性输入的 FP32/FP16 对齐：Top-1 5/5 一致，Top-5 5/5 重合，max abs diff 0.006801。
- FP32/FP16 各 20 次性能采样完成：end-to-end mean 分别为 6.4284 ms 和 6.9812 ms。
- AMCT 未安装，INT8 路径为 `blocked`。
- Tiny-ImageNet 完整验证集下载因镜像超时且数据集体积约 19.9 GB，未完成，准确率为 `untested`。

完整机器报告位于板端：

```text
/tmp/ascend310-validation-20261023/reports/chapter9-fp32-vs-fp16.json
/tmp/ascend310-validation-20261023/reports/chapter9-perf-fp32-fp16.json
```

两个 JSON 的 SHA-256：

```text
205cd6a8cf5a80f50c9c9778216ab8b8a7c95adb88d29429402994036de81250  chapter9-fp32-vs-fp16.json
f2edaac443afee3d999c4f9e32c12183097e0744290bd79bfe42606023c8f2fd  chapter9-perf-fp32-fp16.json
```

这些结果仍不能替代 INT8 精度、完整验证集或 8T 证据。

## 文档索引

| 文档 | 内容 |
|---|---|
| [01-quantization-workflow-and-contracts.md](01-quantization-workflow-and-contracts.md) | ONNX、校准数据、AMCT、ATC、OM 和数据清单合同 |
| [02-evaluation-sweep-and-runbook.md](02-evaluation-sweep-and-runbook.md) | 输出对齐、性能比较、完整验证集和校准样本数 sweep |
| [03-dependencies-failures-and-evidence.md](03-dependencies-failures-and-evidence.md) | 环境依赖、已知导入阻断、失败语义、测试缺口和历史证据边界 |

## 流程入口

| 阶段 | 入口 | 产物 |
|---|---|---|
| 下载原始 ONNX | `tools/download_model.py` | `model/resnet18_tiny_imagenet.onnx` |
| 下载校准/验证 PNG | `tools/download_tiny_imagenet.py train|val` | 图像目录与 `data/*_list.txt` |
| 转换 FP32/FP16 OM | README 中的 `atc` 命令 | `model/*_fp32.om`、`model/*_fp16.om` |
| 输出对齐 | `01_compare_outputs.py` | `outputs/output_compare*.json` |
| 性能比较 | `02_perf_compare.py` | `outputs/perf_compare.json` |
| 量化准备 | `03_prepare_quantization.py` | `outputs/int8_amct/config.json`、`updated_model.onnx` |
| 校准和冻结 | `04_calibrate_quantization.py` | `scale_offset_record.txt`、deploy ONNX |
| 转换 INT8 OM | README 中的 `atc` 命令 | `model/*_int8.om` |
| 验证集精度 | `05_validate_accuracy.py` | `outputs/accuracy_compare.json` |
| 校准样本 sweep | `tools/sweep_calibration_samples.sh` | `outputs/calibration_sweep/**` |

## 产物与 Git

`model/`、`outputs/` 和 `data/` 下的大多数文件由 `.gitignore` 排除。源码仓库只保存脚本和说明，清理工作区后必须重新下载、校准和转换。不要把当前工作区里偶然存在的 ONNX、OM、PNG 清单或精度报告当作已由 Git 发布的产物。

## 证据边界

- `01_compare_outputs.py` 衡量候选 OM 与 FP32 参考 OM 的输出相似度，不使用真实标签。
- `02_perf_compare.py` 衡量端到端执行时间；其 Top-1 预览不是完整准确率。
- `05_validate_accuracy.py` 才在带标签验证集上计算 Top-1/Top-k。
- `tools/sweep_calibration_samples.sh` 将不同校准子集各自的 INT8 OM 与固定 FP32/FP16 结果比较。
- AMCT 校准阶段使用 ONNX Runtime CPU provider，不是 NPU 推理证据。
- ATC 转换成功、ACL smoke、数值一致性、验证集精度和性能是独立门槛。

既有报告缺少完整 CANN/firmware 环境元组，且部分路径指向旧的 chapter8 目录。本次没有重新验证这些结果。
