---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "第8章：性能分析与优化"
---

<!-- _class: cover -->

# 第8章：性能分析与优化

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 先测量，再定位，最后验证优化


<div class="source">依据：<a href="../book/chapter8.md">src/book/chapter8.md</a>；样例：<a href="../../samples/chapter8/README.md"><code>samples/chapter8</code></a></div>

<!--
本章不只讲“怎么优化”，更强调优化前后的测量协议。没有测量，就无法区分模型慢、拷贝慢、预处理慢还是同步慢。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 把一帧推理拆成可测阶段
- 固定基线变量与计时样本
- 用 msprof 定位时间去向
- 把优化动作绑定到可观测症状

- 区分实验记录、优化结论与尚未验证的边界

> 本讲主线：基线 → 测量 → 假设 → 验证

<div class="source">教材：<a href="../book/chapter8.md"><code>src/book/chapter8.md</code></a></div>

<!--
先给出本讲路线图；后续每一段都对应这里的一行，最后回到路线收束。
-->
---


<!-- _class: section -->

# 01

## 基线、计时与 profiler

---

<!-- _class: center -->

## 一帧推理是一条可分段链路

`采集 → CPU 或 DVPP 预处理 → H2D → execute → D2H → 后处理`

| 指标 | 用途 |
|---|---|
| 平均值 | 判断整体方向 |
| p50 | 观察稳态 |
| p95 | 发现长尾 |
| FPS | 判断吞吐 |
| 加速比 | 判断优化是否值得保留 |

只看 `execute` 会漏掉预处理、Host 与 Device 拷贝、同步等待和后处理。

<div class="source">教材：<a href="../book/chapter8.md#src-book-chapter8-h1"><code>src/book/chapter8.md</code> §8.1</a></div>

<!--
先把端到端链路拆开，再决定计时边界。一个“优化”如果只减少 execute，却让端到端 p95 变差，就不能直接验收。
-->

---

<!-- _class: center -->

## 可比较的基线必须锁住变量

- 固定模型、输入尺寸、batch 和精度模式。
- 先预热，再进入计时区间。
- 一次只改一个因素。
- 同时记录端到端与分阶段耗时。
- 固定 CANN、Python、驱动、板卡和运行协议。

<div class="source">教材：<a href="../book/chapter8.md#src-book-chapter8-h1"><code>src/book/chapter8.md</code> §8.1</a>；记录：<a href="../../docs/ascend310b-chapter-board-comparison-20261017.md"><code>双板记录</code></a></div>

<!--
10次记录的作用是验证脚本和展示方法，不足以宣布生产 SLA。尤其设备仍有 Health Alarm 等背景因素时必须标注。
-->

---

<!-- _class: center -->

## msprof 看的是时间去了哪里

| 视图 | 观察 | 可能瓶颈 |
|---|---|---|
| ACL API | 调用间隔、同步等待 | Host 太慢或频繁阻塞 |
| Memcpy | H2D/D2H 的占比 | 输入输出过大或拷贝次数过多 |
| Stream Timeline | NPU 任务之间是否有空白 | 数据未到或下发不足 |
| AI Core Metrics | 流水线利用率 | 模型、batch 或输入尺寸 |

Python 分段计时用于快速定位，msprof 用于检查 ACL、内存搬运与 NPU 时间线。

<div class="source">教材：<a href="../book/chapter8.md#src-book-chapter8-h3"><code>src/book/chapter8.md</code> §8.3</a>；工具：<a href="../../samples/chapter8/tools/profile_with_msprof.sh"><code>samples/chapter8/tools/profile_with_msprof.sh</code></a></div>

<!--
计时工具和 Profiling 不是互相替代关系。先知道哪个阶段可疑，再用 Timeline 解释为什么可疑。
-->

---


<!-- _class: section -->

# 02

## 六类优化假设

---

<!-- _class: center -->

## 六个脚本对应六种优化假设

| 脚本 | 对比问题 |
|---|---|
| `01_baseline_resnet_sync.py` | 每帧资源申请与同步推理基线 |
| `02_buffer_reuse_benchmark.py` | 每帧申请与 Buffer/Dataset 复用 |
| `03_cpu_preprocess_benchmark.py` | 朴素的 CPU 预处理与工作区复用 |
| `04_dvpp_resize_vs_cpu.py` | CPU resize 与 DVPP VPC resize |
| `06_aipp_preprocess_compare.py` | CPU 预处理与 AIPP 输入路径 |
| `05_pipeline_queue_demo.py` | 串行与 Queue Pipeline |

每个脚本只回答一个优化问题，避免同时改变多个变量。

<div class="source">样例：<a href="../../samples/chapter8/README.md"><code>samples/chapter8/README.md</code> 目录说明</a></div>

<!--
先让学生把“性能问题”映射到脚本。一个脚本同时改预处理、拷贝和并发，就无法解释收益来源。
-->

---

## 基线代码为什么故意低效

```python
input_ptr = acl.rt.malloc(input_size, ACL_MEM_MALLOC_HUGE_FIRST)
output_ptr = acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
acl.rt.memcpy(input_ptr, input_size, host_ptr,
              input_size, ACL_MEMCPY_HOST_TO_DEVICE)
acl.mdl.execute(model_id, input_dataset, output_dataset)
acl.rt.memcpy(host_out, output_size, output_ptr,
              output_size, ACL_MEMCPY_DEVICE_TO_HOST)
```

<div class="source">代码：<a href="../../samples/chapter8/01_baseline_resnet_sync.py"><code>samples/chapter8/01_baseline_resnet_sync.py</code></a>；教材：<a href="../book/chapter8.md#src-book-chapter8-h9"><code>src/book/chapter8.md</code> §8.4</a></div>

它每帧申请、执行并释放资源，是为了让 `alloc_dataset` 和 `free_dataset` 的成本暴露出来。

<!--
这不是推荐写法，而是可诊断基线。先让低效路径可测量，再用同一协议验证复用是否改善端到端指标。
-->

---

<!-- _class: center -->

## 双板 ResNet18：10 个计时样本

| 板卡 | 计时样本 | Execute mean | End-to-end mean | warmup记录 |
|---|---:|---:|---:|---|
| 8T / `Ascend310B4` | 10 | 4.9787 ms | 6.0424 ms | 未记录 |
| 20T / `Ascend310B1` | 10 | 3.7255 ms | 4.9569 ms | 未记录 |

这 10 是计时样本数，不是总执行次数。两板均为 Tiny-ImageNet ResNet18、64×64 单模型输入、同步路径，不构成完整基准或跨板排名。

同批次 meta 记录 Python 3.9.2、CANN 8.0.0、驱动与 `npu-smi` 25.2.0、Health Alarm；8T/20T 温度为 80/70 C。但 baseline 日志自身没有绑定这些快照字段。

<div class="source">日志：<a href="../../docs/chapter-board-runs-20261017/8t/run1/chapter8_baseline.log"><code>8T baseline</code></a>、<a href="../../docs/chapter-board-runs-20261017/20t/run1/chapter8_baseline.log"><code>20T baseline</code></a>；环境：<a href="../../docs/chapter-board-runs-20261017/8t/run1/meta.txt"><code>8T meta</code></a>、<a href="../../docs/chapter-board-runs-20261017/20t/run1/meta.txt"><code>20T meta</code></a></div>

<!--
原始日志只有 count=10，没有实际 warmup 数、完整命令和逐样本板况。脚本默认 warmup 5/runs 100 不能反推为本次参数，因此这里必须把 warmup 写成“未记录”。四个均值只覆盖 10 个计时样本，不是总执行次数或稳定吞吐。
-->

---

<!-- _class: center -->

## 为什么不能把这10次叫完整基准

- 样本数只有10次，不能代表长稳和周期波动。
- 只覆盖一个模型、一个输入尺寸和一套同步实现。
- 板端后台负载、温度和进程状态会改变结果。
- `Execute` 与端到端包含不同的计时代码边界。
- 8T与20T是两个独立证据集，不能合并成一张排名表。

<div class="source">范围：<a href="../../docs/ascend310b-chapter-board-comparison-20261017.md"><code>双板记录</code> §3、§5</a>；教材：<a href="../book/chapter8.md#src-book-chapter8-h15"><code>src/book/chapter8.md</code> §8.10</a></div>

<!--
重点训练“数字要带范围”。离开模型、输入、代码路径和板卡版本，性能数字没有可复用意义。
-->

---

<!-- _class: center -->

## Buffer 复用消除每帧资源生命周期

| 阶段 | 每帧申请基线 | Buffer/Dataset 复用 |
|---|---|---|
| 模型加载后 | 未预创建 | 一次性创建输入输出资源 |
| 每帧循环 | malloc、Dataset、free | H2D、execute、D2H、后处理 |
| 退出 | 逐帧释放 | 统一销毁与卸载 |

判断标准是 `alloc_dataset` 和 `free_dataset` 从循环中消失，同时端到端耗时是否改善。

<div class="source">代码：<a href="../../samples/chapter8/02_buffer_reuse_benchmark.py"><code>samples/chapter8/02_buffer_reuse_benchmark.py</code></a>；教材：<a href="../book/chapter8.md#src-book-chapter8-h10"><code>src/book/chapter8.md</code> §8.5</a></div>

<!--
Buffer 复用是确定内存生命周期，不是单纯追求一个加速比。必须验证资源没有提前释放，且输出仍然正确。
-->

---

<!-- _class: center -->

## CPU 预处理：本次双板记录

| 项目 | 8T 板 | 20T 板 |
|---|---:|---:|
| 朴素 NumPy | 0.7756 ms | 0.8391 ms |
| 工作区复用 | 0.3812 ms | 0.4208 ms |
| 平均耗时加速比 | 2.034x | 1.994x |
| 每组样本数 | 20 | 20 |

这组是 CPU 预处理实验，不是 NPU 推理基准。两组比例接近，但仍是各自独立记录。

<div class="source">原始日志：<a href="../../docs/chapter-board-runs-20261017/8t/run1/chapter8_cpu.log"><code>8T CPU</code></a>、<a href="../../docs/chapter-board-runs-20261017/20t/run1/chapter8_cpu.log"><code>20T CPU</code></a></div>

<!--
保留 2.034x 和 1.994x，但不要把 CPU 工作区复用写成 NPU 加速。样本数是20，也不能冒充 ResNet18 的10次执行记录。
-->

---

<!-- _class: center -->

## DVPP VPC：完整调用成本不一定更低

- CPU resize 与 DVPP VPC 的输入格式和路径不同。
- DVPP 测试包含 H2D、VPC、同步与 D2H。
- 每帧创建 Channel、Stream 或配置会增加固定开销。
- 输入太小时，调用开销可能超过计算收益。
- 真正的视频链路应尽量复用资源并保持设备侧数据。

<div class="source">代码：<a href="../../samples/chapter8/04_dvpp_resize_vs_cpu.py"><code>samples/chapter8/04_dvpp_resize_vs_cpu.py</code></a>；教材：<a href="../book/chapter8.md#src-book-chapter8-h12"><code>src/book/chapter8.md</code> §8.7</a></div>

<!--
不要预设“用了 DVPP 就一定更快”。本次课件不加入新的 DVPP 数字，只讲比较方法和固定开销来源。
-->

---

<!-- _class: center -->

## AIPP：改变输入合同，才可能减少 Host 工作

| 路径 | 输入给模型前 | 设备侧动作 |
|---|---|---|
| 原始 OM | CPU 完成 resize、归一化、HWC→CHW | H2D 后直接 execute |
| AIPP OM | 准备 `RGB888_U8` | AIPP 与模型执行衔接 |

AIPP 改变了模型输入格式，必须使用对应 AIPP OM 和配置，不能把同一份 float32 输入直接套用。

<div class="source">代码：<a href="../../samples/chapter8/06_aipp_preprocess_compare.py"><code>samples/chapter8/06_aipp_preprocess_compare.py</code></a>；配置：<a href="../../samples/chapter8/model/resnet18_rgb_static_aipp.cfg"><code>resnet18_rgb_static_aipp.cfg</code></a></div>

<!--
AIPP 的比较对象是两条不同输入合同。先校验预处理后的数值与输出一致性，再看耗时，不能只换 OM 文件。
-->

---

<!-- _class: center -->

## Queue Pipeline：吞吐收益需要足够重的阶段

| 模式 | 8T 本次记录 | 20T 本次记录 | 参数记录 |
|---|---|---|---|
| 串行模拟 | 65.91 FPS | 65.91 FPS | frames、队列深度未记录 |
| Queue 模拟 | 119.84 FPS | 119.63 FPS | frames、队列深度未记录 |
| 模拟加速比 | 1.818x | 1.815x | 仅FPS比值 |

这是 `--simulate` 的线程/队列模拟，不是 PyACL NPU 流水线实测。原始日志只保留 FPS 与 wall，没有实际帧数、队列深度或阶段耗时。

脚本默认 `frames=200`，但日志时长与该默认值不一致，不能把默认值冒充本次参数。因此这两个加速比可以展示趋势，尚不能完整复现测试协议。

<div class="source">原始日志：<a href="../../docs/chapter-board-runs-20261017/8t/run1/chapter8_queue.log"><code>8T queue</code></a>、<a href="../../docs/chapter-board-runs-20261017/20t/run1/chapter8_queue.log"><code>20T queue</code></a>；代码：<a href="../../samples/chapter8/05_pipeline_queue_demo.py"><code>samples/chapter8/05_pipeline_queue_demo.py</code></a></div>

<!--
1.818x 和 1.815x 只能归到模拟模式。日志缺实际 frames、queue depth 和 stage sleep，脚本默认 frames=200 又不能解释 wall，因此必须明确协议未完整保留，不能写成 NPU 端到端吞吐或完整可复现基准。
-->

---

<!-- _class: center -->

## 优化动作要对应可观测症状

| 症状 | 优先工具 | 候选动作 |
|---|---|---|
| Timeline 有空白 | msprof | 减少 Host 阻塞、调整异步流水线 |
| H2D/D2H 占比高 | msprof + 分段计时 | 复用 Buffer、减少拷贝 |
| 每帧抖动大 | p95 + 分配日志 | 停止每帧 malloc/free |
| CPU 占用高 | CPU 分段计时 | 工作区复用、AIPP、DVPP |
| Pipeline FPS 不升 | 队列与延迟指标 | 缩小队列、重新划分阶段 |

先证明瓶颈位置，再选择动作。优化名称不能代替证据。

<div class="source">教材：<a href="../book/chapter8.md#src-book-chapter8-h16"><code>src/book/chapter8.md</code> §8.11</a></div>

<!--
要求学生从症状出发，而不是从 API 出发。每个动作都要说明预期改善哪个指标，以及失败时如何回退。
-->

---

<!-- _class: center -->

## 验收一次优化至少回答五个问题

1. 改动前后的代码路径是否只差一个变量？
2. 指标来自多少次运行，包含哪些阶段？
3. 平均值和 p95 是否同时记录？
4. 结果是否绑定模型、输入、板卡和版本？
5. 失败或退化是否保留原始日志？

<div class="source">教材：<a href="../book/chapter8.md#src-book-chapter8-h1"><code>src/book/chapter8.md</code> §8.1</a>；记录：<a href="../../docs/ascend310b-chapter-board-comparison-20261017.md"><code>双板记录</code></a></div>

<!--
验收报告必须让下一位同学能复现。没有运行次数、阶段边界和版本，任何加速比都不能复用。
-->

---

<!-- _class: center -->

## 课堂任务：做一张优化证据卡

| 字段 | 必须填写 |
|---|---|
| 假设 | 哪个阶段是瓶颈 |
| 对照 | 改前与改后的唯一变量 |
| 数据 | 运行次数、mean、p95、FPS |
| 边界 | 模型、输入、板卡、版本 |
| 结论 | 保留、回退或继续实验 |

在现有记录中，ResNet18 只能填写“本次10次”；CPU 和 Queue 记录必须标注各自模式和样本数。

<div class="source">作业：<a href="../book/chapter8.md#src-book-chapter8-h17"><code>src/book/chapter8.md</code> §8.12</a>；原始记录：<a href="../../docs/chapter-board-runs-20261017/README.md"><code>chapter-board-runs-20261017</code></a></div>

<!--
最后检查学生有没有把不同实验混成一张性能表。正确的结论必须带样本数、阶段边界和证据组。
-->
