---
title: "第7章：性能分析与优化案例教学"
author: [周贤中]
date: 2026-06-08
subject: "Markdown"
keywords: [性能优化, Profiling, msprof, DVPP, AIPP, 流水线, PyACL]
lang: zh-cn
---

本章不从概念清单开始，而是从几段可以运行的代码开始。读者先建立一个能测量的 ResNet18-TinyImageNet 推理基线，再逐步观察内存申请、数据搬运、CPU 预处理、DVPP 图像处理和流水线并发带来的差异。所有样例代码都放在 `samples/chapter7/`，模型文件可直接从 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet` 下载到本章目录，因此不依赖读者先完成第 4 章。

本章使用的是 Tiny-ImageNet 版 ResNet18，输入为 64x64 RGB 图像，输出为 200 类分类结果。它的绝对 FPS 不能等同于标准 ImageNet 224x224 ResNet18，但内存生命周期、数据搬运、预处理下沉和流水线分析方法是通用的。

## 7.1 性能优化先从可测量开始 {#src-book-chapter7-h1}

性能优化的第一步不是改代码，而是把问题拆成可观测的阶段。边缘 AI 应用通常包含以下链路：

```text
输入采集 -> CPU/DVPP 预处理 -> Host to Device -> NPU 推理 -> Device to Host -> 后处理 -> 输出
```

本章统一记录以下指标：

| 指标 | 含义 | 适合回答的问题 |
|---|---|---|
| 平均耗时 | 多次运行的平均阶段耗时 | 系统总体是否变快 |
| p50 | 中位数耗时 | 稳态性能是否符合预期 |
| p95 | 95 分位耗时 | 是否存在长尾卡顿 |
| FPS | 单位时间处理帧数 | 吞吐量是否达标 |
| 加速比 | 优化前耗时 / 优化后耗时 | 单项优化是否值得保留 |

测试时需要遵守三个规则：

1. 先预热，再计时。模型加载、首次内存申请和缓存建立会污染第一批数据。
2. 一次只改一个变量。比如只改 Buffer 复用，不同时改 Batch 和预处理。
3. 同时看端到端和分阶段耗时。只看 `acl.mdl.execute` 会忽略 H2D、D2H 和 Host 阻塞。

## 7.2 第 7 章样例目录 {#src-book-chapter7-h2}

本章代码位于 `samples/chapter7/`：

| 文件 | 作用 |
|---|---|
| `01_baseline_resnet_sync.py` | ResNet18 同步推理基线，拆分预处理、H2D、Execute、D2H、后处理 |
| `02_buffer_reuse_benchmark.py` | 对比每帧申请 ACL 资源与复用 ACL Buffer/Dataset |
| `03_cpu_preprocess_benchmark.py` | 对比朴素 CPU 预处理和预分配工作区 |
| `04_dvpp_resize_vs_cpu.py` | 对比 DVPP VPC resize 与 CPU resize |
| `05_pipeline_queue_demo.py` | 对比串行流程与 Queue Pipeline |
| `06_aipp_preprocess_compare.py` | 对比 CPU 预处理与已有静态 AIPP OM |
| `tools/download_model.py` | 从 Hugging Face 下载 ResNet18-TinyImageNet 的 OM/ONNX 模型 |
| `tools/convert_aipp_resnet18.sh` | 调用 ATC 生成静态 AIPP OM |
| `tools/profile_with_msprof.sh` | 使用 `msprof` 采集应用 Timeline |
| `tools/summarize_metrics.py` | 将 JSON 指标汇总成 Markdown 表格 |
| `model/resnet18_rgb_static_aipp.cfg` | 静态 AIPP 配置示例 |
| `model/` | 第 7 章模型和 AIPP 配置目录 |

阅读本章时，建议先进入第 7 章样例目录，后续命令都默认在这个目录下执行：

```bash
cd samples/chapter7
```

本地开发机可以运行语法检查和 CPU/模拟样例。以下命令中的 `python3` 指当前已经安装 NumPy 等依赖的 Python 解释器：

```bash
python3 -m py_compile *.py tools/*.py
python3 03_cpu_preprocess_benchmark.py --runs 10
python3 05_pipeline_queue_demo.py --simulate --frames 30
```

真实 PyACL、DVPP、`msprof` 和 `npu-smi` 验证必须在 Ascend 310B 上运行：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/chapter7
python3 tools/download_model.py
python3 01_baseline_resnet_sync.py --runs 100
```

## 7.3 工具箱：从轻量计时到系统 Profiling {#src-book-chapter7-h3}

### 7.3.1 Python 分段计时 {#src-book-chapter7-h4}

`perf_counter()` 适合做应用层粗粒度计时。第 7 章的样例通过 `StageRecorder` 记录每一帧的阶段耗时，并输出 JSON：

```json
{
  "metrics": {
    "h2d": {"mean_ms": 0.12, "p95_ms": 0.18, "fps": 8333.33},
    "execute": {"mean_ms": 1.85, "p95_ms": 2.10, "fps": 540.54},
    "end_to_end": {"mean_ms": 3.40, "p95_ms": 4.20, "fps": 294.12}
  }
}
```

这种计时不能替代硬件 Profiling，但它有两个优势：代码改动小，能快速判断优化是否有方向。

### 7.3.2 msprof：观察 ACL API、Memcpy 和 NPU Timeline {#src-book-chapter7-h5}

`msprof` 是 CANN 提供的性能采集工具。它能记录 Host 侧 ACL API 调用、Runtime 任务、Stream Timeline、AI Core 指标和部分系统资源信息。

本章提供包装脚本：

```bash
bash tools/profile_with_msprof.sh --name baseline -- \
  python3 01_baseline_resnet_sync.py --runs 200
```

采集后重点观察：

| 视图 | 观察点 | 常见结论 |
|---|---|---|
| ACL API | `acl.mdl.execute` 调用间隔是否很大 | Host 预处理或同步等待过慢 |
| Memcpy | H2D/D2H 是否占比过高 | 输入输出过大或拷贝次数太多 |
| Stream Timeline | NPU 计算任务之间是否有大片空白 | NPU 在等待 Host 下发任务或等待数据 |
| AI Core Metrics | Pipe 利用率是否低 | 模型算子、Batch 或输入尺寸需要继续分析 |

### 7.3.3 npu-smi：确认设备状态 {#src-book-chapter7-h6}

`npu-smi info` 用于确认设备是否在线、温度是否异常、功耗是否接近限制。性能测试前后都应记录一次，避免把降频、设备异常或其他进程占用误判为代码问题。

```bash
npu-smi info
```

### 7.3.4 msame / ais_bench：独立测模型上限 {#src-book-chapter7-h7}

当应用端到端 FPS 不理想时，可以用 `msame` 或 `ais_bench` 单独测 OM 模型。它们绕过业务代码，只关注模型输入输出和 Runtime 执行，适合回答一个问题：模型本身的推理上限是多少？

如果基准工具显示模型很快，而业务程序很慢，瓶颈通常在预处理、拷贝、后处理或调度。如果基准工具也很慢，才继续考虑模型结构、算子、精度模式和 Batch。

例如，在已经进入 `samples/chapter7` 并下载模型后，可以用 `ais_bench` 测普通 OM 的独立推理耗时：

```bash
ais_bench --model model/resnet18_tiny_imagenet.om --batchsize 1
```

不同 CANN 版本和安装方式下，`ais_bench` 或 `msame` 的命令行参数可能略有差异；如果本机未安装该工具，可以跳过本节，继续使用本章 Python 分段计时脚本观察端到端瓶颈。

### 7.3.5 ATC/AIPP 与 DVPP：把图像工作下沉到设备侧 {#src-book-chapter7-h8}

AIPP 能在模型输入侧完成颜色格式处理和归一化，减少 Host CPU 的像素遍历。示例配置位于：

```text
model/resnet18_rgb_static_aipp.cfg
```

如果只复现实验结果，先下载本章模型资产：

```bash
python3 tools/download_model.py --all
```

这个命令会把普通 OM、ONNX、AIPP OM 和 AIPP 配置放到 `model/`。如果需要自己体验离线转换，应在 Ascend 310B 上重新生成 AIPP OM：

```bash
bash tools/convert_aipp_resnet18.sh
```

DVPP 适合处理视频编解码、JPEG 编解码、VPC resize/crop 等媒体任务。第 5 章已经介绍 DVPP API，本章只关心它在性能优化中的角色：减少 CPU 图像处理负担，并让数据尽量早地进入设备侧流水线。

## 7.4 案例一：建立同步推理基线 {#src-book-chapter7-h9}

先运行基线：

```bash
python3 01_baseline_resnet_sync.py --runs 100
```

这个脚本故意采用低效写法：每一帧都申请 Device 输入输出内存、创建 Dataset、执行推理、复制输出、释放资源。它的价值不是快，而是让问题暴露出来。

核心流程如下：

```python
input_ptr = acl.rt.malloc(input_size, ACL_MEM_MALLOC_HUGE_FIRST)
output_ptr = acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
input_dataset = acl.mdl.create_dataset()
output_dataset = acl.mdl.create_dataset()
acl.rt.memcpy(input_ptr, input_size, host_ptr, input_size, ACL_MEMCPY_HOST_TO_DEVICE)
acl.mdl.execute(model_id, input_dataset, output_dataset)
acl.rt.memcpy(host_out, output_size, output_ptr, output_size, ACL_MEMCPY_DEVICE_TO_HOST)
```

基线需要记录以下阶段：

| 阶段 | 含义 |
|---|---|
| `preprocess` | RGB 输入转换为 NCHW float32 |
| `alloc_dataset` | 申请输入输出 Buffer 并创建 Dataset |
| `h2d` | Host 输入复制到 Device |
| `execute` | 模型执行 |
| `d2h` | Device 输出复制回 Host |
| `free_dataset` | 销毁 Dataset 并释放 Buffer |
| `postprocess` | `argmax` 等轻量后处理 |
| `end_to_end` | 单帧端到端耗时 |

如果 `alloc_dataset` 和 `free_dataset` 占比明显，说明优化方向不是模型，而是资源生命周期。

## 7.5 案例二：复用 ACL Buffer 和 Dataset {#src-book-chapter7-h10}

推理服务通常会长时间运行，输入输出张量大小也是固定的。因此更合理的做法是在模型加载后一次性创建输入输出 Buffer 和 Dataset，在每帧循环里只做数据拷贝和执行：

```bash
python3 02_buffer_reuse_benchmark.py --runs 100
```

优化后的流程：

```text
初始化阶段：load model -> create dataset -> malloc input/output buffer
循环阶段：H2D -> execute -> D2H -> postprocess
退出阶段：destroy dataset -> free buffer -> unload model
```

这项优化的判断标准很直接：

1. `alloc_dataset` 和 `free_dataset` 从每帧计时中消失。
2. `end_to_end` 平均耗时下降。
3. `msprof` Timeline 中 Host 侧 ACL Runtime 调用密度更高，计算任务之间的空白减少。

如果模型很小，Buffer 复用的收益会特别明显；如果模型本身很大，收益占比会下降，但仍然能减少长尾抖动。

## 7.6 案例三：CPU 预处理为什么容易成为瓶颈 {#src-book-chapter7-h11}

很多边缘视觉应用在 NPU 推理前会做 resize、颜色转换、归一化和 HWC 到 CHW 转换。脚本 `03_cpu_preprocess_benchmark.py` 用确定性 RGB 图像模拟 1080p 摄像头帧：

```bash
python3 03_cpu_preprocess_benchmark.py --runs 100 --resolution 1920x1080
```

如果想观察更稳定的统计结果，可以在确认本机内存充足后把 `--runs` 增加到 500 或 1000。

脚本比较两种方式：

| 方式 | 特点 |
|---|---|
| `naive_numpy` | 每帧创建 resize、float32、NCHW 等中间数组 |
| `workspace_reuse` | 预先创建工作区，循环中复用数组 |

这个案例的学习重点不是把 CPU 预处理调到极致，而是理解何时应该停止优化 CPU。若预处理耗时已经接近或超过模型执行耗时，应优先考虑：

1. 使用 DVPP VPC 做 resize/crop。
2. 使用 AIPP 做归一化和部分颜色格式处理。
3. 使用视频链路时，让 VDEC/JPEGD 的输出直接衔接 VPC 或模型输入。

## 7.7 案例四：DVPP VPC resize 与 CPU resize {#src-book-chapter7-h12}

脚本 `04_dvpp_resize_vs_cpu.py` 生成 NV12 测试帧，将 1080p 图像缩小到 540p，对比 CPU resize 和 DVPP VPC resize：

```bash
python3 04_dvpp_resize_vs_cpu.py --frames 60 --resolution 1920x1080
```

该脚本包含 H2D、VPC resize、同步和 D2H，因此测到的是一个完整调用成本，而不是 VPC 内核的裸耗时。如果 DVPP 仍然明显领先 CPU，说明把 resize 从 Host 下沉到设备侧很有价值。如果差距不明显，要继续检查：

1. 输入尺寸是否太小，导致调用开销大于计算收益。
2. 是否每帧都重复创建 DVPP Channel、Stream 或配置对象。
3. 是否存在多余的 H2D/D2H 拷贝。

## 7.8 案例五：AIPP 把归一化下沉到模型输入侧 {#src-book-chapter7-h13}

前面的基线脚本使用 CPU 完成 resize、归一化和 HWC 到 CHW 转换，然后把 `float32 NCHW` 输入拷贝到 Device。AIPP 的思路不同：让模型接收 `RGB888_U8` 图像，在模型输入侧完成均值、方差等处理。这样可以减少 Host CPU 的像素遍历，也能减少输入拷贝字节数。

本章提供静态 AIPP 配置：

```text
model/resnet18_rgb_static_aipp.cfg
```

先下载本章模型资产：

```bash
python3 tools/download_model.py --all
```

`--all` 会把普通 OM、ONNX、AIPP OM 和 AIPP 配置都下载到 `model/`。如果 Hugging Face 仓库中已经有 `model/resnet18_tiny_imagenet_aipp.om`，可以直接运行后面的对比脚本，不必自己转换。

如果需要在板端重新生成 AIPP OM，再运行独立 shell 脚本：

```bash
bash tools/convert_aipp_resnet18.sh
```

脚本默认生成 `model/resnet18_tiny_imagenet_aipp.om`。转换或下载完成后，再运行 Python 对比脚本：

```bash
python3 06_aipp_preprocess_compare.py --runs 100
```

这里故意把 ATC 转换和 Python 推理对比分成两步。ATC 是离线模型转换，可能耗时较长，也可能受板端内存和 CANN 环境影响；Python 脚本只加载已有 OM 做性能测试，不会在测试过程中调用 ATC。

对比脚本会执行两条路径：

1. 使用原始 OM 跑 `CPU preprocess -> H2D -> execute -> D2H -> argmax`。
2. 使用 AIPP OM 跑 `RGB888_U8 prepare -> H2D -> AIPP+execute -> D2H -> argmax`，并比较输出差异。

需要注意，AIPP OM 的输入不再是 `float32 NCHW`，而是 `uint8 HWC RGB`。因此不能直接拿 `01_baseline_resnet_sync.py` 去跑 AIPP OM，必须使用本节的对比脚本或在自己的程序中按 AIPP 配置准备输入。

对外部读者来说，还有两个容易踩坑的地方：

1. 运行转换脚本前，需要先完成 CANN 环境配置，并确认当前终端可以直接使用 `atc`。
2. CANN 8.3 对 `RGB888_U8` 的 `mean_chn_*` 字段按整数解析，不能直接写 `123.675` 这样的浮点均值。本章配置使用 `mean_chn_*: 0` 加浮点 `min_chn_*` 表达 ImageNet 均值，并用 `var_reci_chn_*` 表达 `1 / (255 * std)`。

ATC 是离线转换步骤，第一次编译可能明显慢于普通推理脚本。如果转换耗时异常，应先检查板端负载、可用内存、CANN 日志和 AIPP 配置，再重新运行转换脚本。

## 7.9 案例六：串行流程与 Queue Pipeline {#src-book-chapter7-h14}

串行流程的单帧耗时为：

```text
T = T_pre + T_infer + T_post
```

三段流水线稳定后的吞吐上限更接近：

```text
FPS = 1000 / max(T_pre, T_infer, T_post)
```

先在本地使用模拟模式理解这个关系：

```bash
python3 05_pipeline_queue_demo.py --simulate --frames 200
```

再在 Ascend 310B 上使用 PyACL 模式：

```bash
python3 05_pipeline_queue_demo.py --frames 100
```

流水线优化的目标不是降低单帧端到端延迟，而是提高稳态吞吐。它会引入队列等待，因此 p95 latency 可能上升；如果业务目标是交互式低延迟，需要谨慎扩大队列深度。

## 7.10 作者实测结果 {#src-book-chapter7-h15}

本节记录作者在一台 Ascend 310B 设备上得到的实测数据。读者自己的板端温度、CANN 版本、设备健康状态和后台进程不同，绝对数值会有差异；更重要的是复现实验方法和观察相对趋势。

| 项目 | 内容 |
|---|---|
| 测试主机 | `313`，作者内部测试主机编号 |
| 设备名 | `orangepiaipro` |
| 远端仓库 | `/home/HwHiAiUser/Documents/Ascend310` |
| CANN 路径 | `/usr/local/Ascend/ascend-toolkit/latest` |
| Python | `/home/HwHiAiUser/.conda/envs/npu/bin/python` |
| 测试日期 | `2026-06-08` |
| 设备状态 | `npu-smi` 显示 310B4，Health 为 `Alarm`，温度约 61 C，测试前后内存约 2939-3003 / 7545 MB |

以下表格由 `samples/chapter7/tools/summarize_metrics.py` 汇总远端 JSON 指标生成。由于测试时设备状态为 `Alarm`，数据更适合作为章节教学样例和相对趋势参考；正式项目验收应在设备健康状态正常、无其他进程干扰时重新跑完整评测。

<!-- CHAPTER7_REMOTE_RESULTS_START -->
| 案例 | 变体 | 平均耗时(ms) | p95(ms) | FPS | 样本数 | 加速比 | 说明 |
|---|---|---:|---:|---:|---:|---:|---|
| ResNet18 同步推理 | `naive_alloc_each_frame` | 4.5088 | 4.712 | 221.791 | 100 |  | 每帧申请/释放 ACL 输入输出 Buffer 和 Dataset |
| ResNet18 Buffer 复用 | `naive_alloc_each_frame` | 4.6047 | 6.8023 | 217.1708 | 100 |  | 对照组 |
| ResNet18 Buffer 复用 | `reuse_acl_buffers` | 3.2072 | 3.3509 | 311.7963 | 100 | 1.436 | 模型加载后一次性创建输入输出 Buffer 和 Dataset |
| CPU 预处理 | `naive_numpy` | 0.62 | 0.6821 | 1612.8023 | 200 |  | 每帧创建中间数组 |
| CPU 预处理 | `workspace_reuse` | 0.3583 | 0.4154 | 2791.1275 | 200 | 1.731 | 复用 resize/CHW 工作区 |
| DVPP VPC Resize | `cpu_cv2_resize` | 2.6013 | 2.6659 | 384.4225 | 60 |  | CPU resize NV12 Y/UV |
| DVPP VPC Resize | `dvpp_vpc_resize` | 3.5966 | 3.7102 | 278.0433 | 60 | 0.723 | DVPP VPC resize + H2D/D2H |
| AIPP 预处理下沉 | `cpu_preprocess` | 3.2319 | 3.3155 | 309.4136 | 100 |  | CPU 完成 resize/normalize/HWC->CHW 后推理 |
| AIPP 预处理下沉 | `static_aipp_rgb` | 2.6076 | 2.6283 | 383.5017 | 100 | 1.239 | AIPP 接收 RGB888_U8 并在模型输入侧归一化 |
| Queue Pipeline | `pyacl_serial` | 3.1943 | 3.2332 | 313.0536 | 100 |  | Pre -> Infer -> Post 串行 |
| Queue Pipeline | `pyacl_queue_pipeline` | 3.993 | 100.0494 | 250.4402 | 100 | 0.8 | 三段线程通过 Queue 解耦 |
| Queue Pipeline | `simulate_serial` | 15.1757 | 15.1942 | 65.8947 | 200 |  | Pre -> Infer -> Post 串行 |
| Queue Pipeline | `simulate_queue_pipeline` | 8.1233 | 157.4201 | 123.1021 | 200 | 1.868 | 三段线程通过 Queue 解耦 |
<!-- CHAPTER7_REMOTE_RESULTS_END -->

从这组数据可以得到五个结论：

1. Buffer 复用是确定有效的优化。ResNet18 端到端平均耗时从 4.6047 ms 降到 3.2072 ms，提升约 1.436 倍。
2. CPU 预处理里的中间数组分配值得优化。复用工作区后，预处理平均耗时从 0.62 ms 降到 0.3583 ms。
3. DVPP 不是简单替换 API 就一定更快。本章脚本为了教学完整性包含每帧 `dvpp_malloc/free`、H2D 和 D2H，结果 DVPP VPC resize 平均 3.5966 ms，慢于 CPU resize 的 2.6013 ms。真实视频流水线应复用 DVPP 资源，并尽量让 VDEC/JPEGD/VPC 输出继续留在设备侧。
4. AIPP 在本章模型上有明确收益。原始 OM 的 CPU 预处理路径平均耗时为 3.2319 ms，AIPP OM 的 `RGB888_U8` 输入路径降到 2.6076 ms，提升约 1.239 倍；100 次测试中 top1 匹配率为 1.0，最大单点输出差异为 0.007812。AIPP 改变了模型输入格式，正确的比较对象不是“同一个 OM 换输入”，而是原始 OM 的 CPU 预处理路径与 AIPP OM 的 `RGB888_U8` 输入路径。
5. Pipeline 需要足够重的阶段才有收益。模拟场景中队列流水线从 65.8947 FPS 提升到 123.1021 FPS；但 ResNet18 小模型的 PyACL 场景中，队列和线程上下文开销使 FPS 从 313.0536 降到 250.4402，且 p95 latency 明显变差。

本次 `msprof` 采集输出目录如下，可用 MindStudio 或 Ascend Insight 打开：

```text
outputs/msprof/baseline-remote-latest-20260608-083957/PROF_000001_20260608083959526_RHNMOCNRMNBBHBCC/mindstudio_profiler_output
```

远端验证命令：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Documents/Ascend310/samples/chapter7
PY=/home/HwHiAiUser/.conda/envs/npu/bin/python
$PY -m py_compile *.py tools/*.py
$PY tools/download_model.py
$PY 01_baseline_resnet_sync.py --runs 100 --output outputs/baseline_resnet_sync_remote_latest.json
$PY 02_buffer_reuse_benchmark.py --runs 100 --output outputs/buffer_reuse_remote_latest.json
$PY 03_cpu_preprocess_benchmark.py --runs 200 --output outputs/cpu_preprocess_remote_latest.json
$PY 04_dvpp_resize_vs_cpu.py --frames 60 --output outputs/dvpp_resize_remote_latest.json
$PY 05_pipeline_queue_demo.py --simulate --frames 200 --output outputs/pipeline_simulate_remote_latest.json
$PY 05_pipeline_queue_demo.py --frames 100 --output outputs/pipeline_pyacl_remote_latest.json
$PY tools/download_model.py --all
$PY 06_aipp_preprocess_compare.py --runs 100 --output outputs/aipp_preprocess_remote_latest.json
$PY tools/summarize_metrics.py \
  outputs/baseline_resnet_sync_remote_latest.json \
  outputs/buffer_reuse_remote_latest.json \
  outputs/cpu_preprocess_remote_latest.json \
  outputs/dvpp_resize_remote_latest.json \
  outputs/aipp_preprocess_remote_latest.json \
  outputs/pipeline_pyacl_remote_latest.json \
  outputs/pipeline_simulate_remote_latest.json \
  --output outputs/summary_remote_latest.md
npu-smi info
```

Profiling 采集命令：

```bash
bash tools/profile_with_msprof.sh --name baseline-remote-latest -- \
  /home/HwHiAiUser/.conda/envs/npu/bin/python 01_baseline_resnet_sync.py \
    --runs 20 --output outputs/baseline_msprof_remote_latest.json
```

## 7.11 调优 Checklist {#src-book-chapter7-h16}

| 现象 | 优先检查 | 常见优化 |
|---|---|---|
| NPU Timeline 有大量空白 | Host 是否慢、是否同步等待 | 预处理下沉、异步流水线、减少全局同步 |
| H2D/D2H 占比高 | 输入输出大小和拷贝次数 | 合并 Buffer、复用内存、减少回传输出 |
| 每帧耗时抖动大 | 是否每帧 malloc/free | 预分配 Buffer、固定 Dataset |
| CPU 占用高 | resize/cvtColor/normalize 是否在 CPU | DVPP、AIPP、C++/NumPy 工作区复用 |
| 模型单独基准也慢 | 算子或模型结构 | 改输入尺寸、精度模式、Batch、算子优化 |
| Pipeline FPS 不升反降 | 队列太深或阶段不均衡 | 缩小队列、调整线程数、重新划分阶段 |

## 7.12 练习任务 {#src-book-chapter7-h17}

1. 把 `01_baseline_resnet_sync.py` 的 `--runs` 从 100 增加到 1000，对比 p95 是否稳定。
2. 用 `msprof` 分别采集基线和 Buffer 复用脚本，找出 Timeline 中 Host 空白是否减少。
3. 修改 `05_pipeline_queue_demo.py --simulate` 中的 `--pre-ms`、`--infer-ms`、`--post-ms`，验证吞吐是否接近最慢阶段。
4. 将自己的模型接入 `ReuseResNetRunner` 的思路，记录优化前后的 JSON 指标。
5. 运行 `tools/download_model.py --all` 获取 AIPP OM，或使用 `tools/convert_aipp_resnet18.sh` 重新生成 AIPP OM，再运行 `06_aipp_preprocess_compare.py` 比较 CPU 归一化下沉前后的端到端耗时和输出差异。

## 7.13 小结 {#src-book-chapter7-h18}

Ascend 310B 性能优化的核心是先定位瓶颈，再把瓶颈映射到合适的硬件或软件策略。小模型场景中，内存申请、同步和 Host 预处理往往比模型执行更值得关注；视频场景中，DVPP、AIPP 和流水线调度决定了端到端吞吐。调优的最终依据不是经验判断，而是可复现的指标、Timeline 和对比报告。
