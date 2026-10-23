# 基准与性能采集运行手册

## 公共准备

板端执行前：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
cd samples/chapter8
python tools/download_model.py --all
```

下载脚本只负责获取资产，不代表 OM 与当前板端 SoC/CANN 匹配。首次使用应先做模型存在、输入字节数、PyACL 加载和一次推理 smoke。

## 本地安全检查

以下命令不加载 PyACL：

```bash
python -m py_compile *.py tools/*.py
python 03_cpu_preprocess_benchmark.py --runs 10
python 05_pipeline_queue_demo.py --simulate --frames 30
```

`03` 的数值是当前主机上的 NumPy 性能。`05 --simulate` 的数值来自 `--pre-ms`、`--infer-ms`、`--post-ms` 的 `sleep`，只用于观察串行和队列流水在理想分段延迟下的行为，不是 NPU、PyACL 或真实模型性能。

## 同步推理基线

```bash
python 01_baseline_resnet_sync.py \
  --model model/resnet18_tiny_imagenet.om \
  --device 0 \
  --warmup 5 \
  --runs 100 \
  --output outputs/baseline_resnet_sync.json
```

计时层次：

- `preprocess`：生成并归一化一帧；
- `inference_total`：H2D + `acl.mdl.execute` + D2H；
- `h2d`、`execute`、`d2h`：runner 内部阶段；
- `alloc_dataset`、`free_dataset`：仅在 naive runner 中单独计时；
- `postprocess`：对第一个输出做 `argmax`；
- `end_to_end`：前三个应用阶段的实测和。

`01` 使用 `NaiveResNetRunner`，每帧重新分配 ACL 内存和 Dataset。它不是“最小设备执行时间”，而是包含准备与释放开销的同步基线。

## Buffer 复用对照

```bash
python 02_buffer_reuse_benchmark.py \
  --model model/resnet18_tiny_imagenet.om \
  --device 0 \
  --warmup 5 \
  --runs 100 \
  --output outputs/buffer_reuse_benchmark.json
```

相同模型和输入下依次运行：

- `naive_alloc_each_frame`：每帧分配/释放；
- `reuse_acl_buffers`：模型加载后复用 Buffer 和 Dataset。

报告中的 speedup 是两者 `end_to_end.mean_ms` 的比值。比较必须使用同一进程、设备、输入、预热和运行次数，且检查两个变体的阶段计数一致。

## CPU 预处理工作区

```bash
python 03_cpu_preprocess_benchmark.py \
  --resolution 1920x1080 \
  --warmup 10 \
  --runs 500 \
  --output outputs/cpu_preprocess_benchmark.json
```

该命令能生成大尺寸合成帧，内存占用随 `warmup + runs` 增长。输入分辨率、采样算法、归一化常量、Python/NumPy 版本和 CPU 型号共同决定结果。它不是 NPU 性能，也不能与 AIPP 推理延迟直接相加替代端到端对照。

## DVPP VPC 与 CPU resize

```bash
python 04_dvpp_resize_vs_cpu.py \
  --device 0 \
  --resolution 1920x1080 \
  --warmup 3 \
  --frames 60 \
  --output outputs/dvpp_resize_vs_cpu.json
```

比较对象：

- `dvpp_vpc_resize`：每帧 H2D、VPC 异步 resize、流同步和 D2H；
- `cpu_cv2_resize`：Y/UV 两个平面分别做 OpenCV resize。

DVPP 变体包含主机与设备拷贝，CPU 变体只包含主机 resize。速度比是当前实现口径，不等于单独 VPC kernel 加速比。测试帧为合成 NV12，成功时也不验证像素质量。

## 队列流水对照

模拟模式：

```bash
python 05_pipeline_queue_demo.py \
  --simulate \
  --frames 200 \
  --queue-size 8 \
  --pre-ms 5 \
  --infer-ms 8 \
  --post-ms 2 \
  --output outputs/pipeline_simulate.json
```

PyACL 模式：

```bash
python 05_pipeline_queue_demo.py \
  --model model/resnet18_tiny_imagenet.om \
  --device 0 \
  --frames 100 \
  --queue-size 8 \
  --output outputs/pipeline_pyacl.json
```

PyACL 模式先运行串行路径，再启动一个有独立 ACL context 的推理线程和多个 CPU worker。报告同时记录 wall time、FPS 和每帧从进入 producer 到 postprocess 完成的 latency。队列吞吐改善可能伴随排队延迟升高，必须同时看 FPS 与 p95，不能只看加速比。

## AIPP 对照

```bash
python 06_aipp_preprocess_compare.py \
  --model model/resnet18_tiny_imagenet.om \
  --aipp-model model/resnet18_tiny_imagenet_aipp.om \
  --device 0 \
  --warmup 5 \
  --runs 100 \
  --output outputs/aipp_preprocess_compare.json
```

该脚本比较普通 float32 OM 的 CPU 预处理路径与静态 AIPP RGB888_U8 OM 路径，并额外比较二者每个样本的 Top-1、Top-k 和 logits 误差。它不会调用 ATC，也不使用真实标签计算准确率；Top-1 相同只表示两个路径预测一致。

如需在板端重新转换 AIPP OM：

```bash
bash tools/convert_aipp_resnet18.sh
```

脚本要求 `atc` 可用、ONNX 与 AIPP cfg 存在，默认目标 SoC 为 `Ascend310B4`。

## Profiling 采集

```bash
bash tools/profile_with_msprof.sh --name baseline -- \
  python 01_baseline_resnet_sync.py --runs 200

bash tools/profile_with_msprof.sh --name buffer-reuse -- \
  python 02_buffer_reuse_benchmark.py --runs 200
```

包装脚本设置：

- `--task-time=on`
- `--aic-metrics=PipeUtilization`
- `--sys-hardware-mem=on`

每次输出到 `outputs/msprof/<name>-<timestamp>/`。原始 profiler 目录需要配套的 MindStudio/Ascend Insight 或解析流程才能形成 timeline 结论；目录存在本身不是性能结论。

## 汇总已有 JSON

```bash
python tools/summarize_metrics.py \
  outputs/baseline_resnet_sync.json \
  outputs/buffer_reuse_benchmark.json \
  outputs/cpu_preprocess_benchmark.json \
  outputs/dvpp_resize_vs_cpu.json \
  outputs/aipp_preprocess_compare.json \
  outputs/pipeline_queue_demo.json \
  --output outputs/summary.md
```

汇总器只读取每个 JSON 的 `summary_rows` 并生成 Markdown 表，不验证环境、样本数或报告之间是否属于同一轮实验。跨报告排名前必须先检查输入、设备、模型和命令是否一致。
