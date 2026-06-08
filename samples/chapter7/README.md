# 第7章 代码样例

本目录配套 `src/book/chapter7.md`，用小而完整的脚本演示 Ascend 310B 上的性能分析与优化流程。

所有涉及 PyACL、DVPP、`msprof`、`npu-smi` 的命令都需要在真实 Ascend 310B 设备上运行。本地开发机只适合语法检查和运行不依赖硬件的 CPU/模拟样例。

## 运行前提

在 Ascend 310B 上先加载 CANN 环境：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/chapter7
PY=/home/HwHiAiUser/.conda/envs/npu/bin/python
```

第 7 章默认使用 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet` 中的 OM 模型。首次运行前先下载到本章目录：

```bash
$PY tools/download_model.py
```

默认模型路径为：

```text
model/resnet18_tiny_imagenet.om
```

如果需要做 AIPP 实验，可以下载本章全部模型资产：

```bash
$PY tools/download_model.py --all
```

`--all` 会把普通 OM、ONNX、AIPP OM 和 AIPP 配置都下载到 `model/`。如果 Hugging Face 仓库中已经有 `resnet18_tiny_imagenet_aipp.om`，读者可以直接运行 AIPP 对比脚本，不必自己转换。

如果需要在板端重新生成 AIPP OM，再运行 `tools/convert_aipp_resnet18.sh`。AIPP 转换由 ATC 完成，默认生成 `model/resnet18_tiny_imagenet_aipp.om`。运行该脚本前应已经完成 CANN 环境配置。本章的 AIPP 配置使用 `mean_chn_*: 0` 搭配浮点 `min_chn_*` 表达 ImageNet 均值，因为 CANN 8.3 的 `RGB888_U8` AIPP 配置要求 `mean_chn_*` 是 0 到 255 的整数。`06_aipp_preprocess_compare.py` 只加载已有 OM 做性能对比，不会调用 ATC。

如果已经完成第 4 章，也可以通过 `--model ../../samples/chapter4/resnet18/model/resnet18_tiny_imagenet.om` 手动复用第 4 章产物。

## 本地检查

```bash
cd samples/chapter7
python3 -m py_compile *.py tools/*.py
python3 03_cpu_preprocess_benchmark.py --runs 10
python3 05_pipeline_queue_demo.py --simulate --frames 30
```

## Ascend 310B 验证命令

作者实测使用的远端主机编号是 `313`。读者复现时只需要在自己的 Ascend 310B 设备上进入本目录运行下面的命令。

```bash
$PY -m py_compile *.py tools/*.py
$PY tools/download_model.py --all

$PY 01_baseline_resnet_sync.py --runs 100
$PY 02_buffer_reuse_benchmark.py --runs 100
$PY 03_cpu_preprocess_benchmark.py --runs 200
$PY 04_dvpp_resize_vs_cpu.py --frames 60
$PY 05_pipeline_queue_demo.py --simulate --frames 200
$PY 05_pipeline_queue_demo.py --frames 100
$PY 06_aipp_preprocess_compare.py --runs 100
```

如果需要在当前板端重新生成 AIPP OM，可在运行 AIPP 对比脚本前执行：

```bash
bash tools/convert_aipp_resnet18.sh
```

## Profiling 采集

`msprof` 采集建议至少覆盖基线和 Buffer 复用两个脚本：

```bash
bash tools/profile_with_msprof.sh --name baseline -- \
  $PY 01_baseline_resnet_sync.py --runs 200

bash tools/profile_with_msprof.sh --name buffer-reuse -- \
  $PY 02_buffer_reuse_benchmark.py --runs 200
```

采集后可将 `outputs/msprof/` 下载到带 MindStudio 或 Ascend Insight 的环境中查看 Timeline。

## 指标汇总

```bash
$PY tools/summarize_metrics.py \
  outputs/baseline_resnet_sync.json \
  outputs/buffer_reuse_benchmark.json \
  outputs/cpu_preprocess_benchmark.json \
  outputs/dvpp_resize_vs_cpu.json \
  outputs/aipp_preprocess_compare.json \
  outputs/pipeline_queue_demo.json \
  --output outputs/summary.md
```

## 目录说明

| 文件 | 说明 |
|---|---|
| `perf_utils.py` | 计时、百分位统计、确定性输入和 JSON 报告工具 |
| `acl_resnet_runner.py` | ResNet18 PyACL runner，包含逐帧分配和 Buffer 复用两种实现 |
| `01_baseline_resnet_sync.py` | 同步推理基线，拆分预处理、H2D、Execute、D2H、后处理 |
| `02_buffer_reuse_benchmark.py` | 对比每帧申请资源与复用 ACL Buffer/Dataset |
| `03_cpu_preprocess_benchmark.py` | CPU 预处理对比，可在本地运行 |
| `04_dvpp_resize_vs_cpu.py` | DVPP VPC resize 与 CPU resize 对比，只在 310B 上运行 |
| `05_pipeline_queue_demo.py` | 串行与 Queue Pipeline 对比，支持本地模拟和 PyACL 模式 |
| `06_aipp_preprocess_compare.py` | 对比 CPU 预处理与已有静态 AIPP OM，只在 310B 上运行 |
| `tools/download_model.py` | 从 Hugging Face 下载 ResNet18-TinyImageNet 的 OM/ONNX 模型 |
| `tools/convert_aipp_resnet18.sh` | 调用 ATC 生成静态 AIPP OM，只在 310B 上运行 |
| `tools/profile_with_msprof.sh` | `msprof` 采集包装脚本 |
| `tools/summarize_metrics.py` | JSON 指标汇总为 Markdown 表格 |
| `model/resnet18_rgb_static_aipp.cfg` | 静态 AIPP 配置示例 |
| `model/` | 第 7 章模型和 AIPP 配置目录，模型文件不纳入源码版本管理 |
