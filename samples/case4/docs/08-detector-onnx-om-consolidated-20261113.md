# 08 - 掌部检测与掌纹 ONNX/OM 综合实验记录

_实验性质：离线调试｜本轮测试日期：2026-11-13｜目标：Ascend 310B1 / 20T 与 Ascend 310B4 / 8T_

> **2026-11-14 后续修正：** [COCO 检测器双板扩展基准](09-coco-detector-benchmark-20261114.md)已补齐两板 NanoDet COCO FP32，替代本文相关“缺少 OM / 未测”记录。本文统一表中 20T PicoDet-S 的两条数值比较使用了不同 SHA-256 的 ONNX golden 与 OM 来源，不是有效的同源数值对照；新报告用同版 ONNX 重测。本文其他 2026-11-13 记录作为历史证据保留。

## 记录范围与证据边界

本文件只记录截至 2026-11-13 的最新有效测试结果，分为三条相互独立的测试轨道：

1. **训练后的手掌检测模型**：ACE1 训练的单类别 `palm` NanoDet，分别在 20T 与 8T 测试。
2. **掌纹编码模型**：CCNet ONNX，仅在新 8T 板上测试。
3. **通用检测基线**：COCO NanoDet、PicoDet、YOLOv5/8/10/11/12/26，仅用于转换、ACL、数值和性能对照。

三条轨道的输入合同、输出语义、数值门限、性能协议和结论不能互相替代。通用 COCO
模型没有 `palm`、`hand`、`left_hand` 或 `right_hand` 任务标签；通用模型结果不能写成
手掌检测质量。本文也不构成生产模型准入、mAP、左右手识别准确率或摄像头端到端验收报告。

## 统一判定规则

每条模型路径分别记录资产、ATC 转换、ACL 加载执行、原始输出数值、后处理语义和任务质量
六类证据。`observed-pass` 只表示对应门禁通过，不能推导出其他门禁通过；`observed-fail`
仅适用于记录的模型版本、输入合同、驱动、CANN、SoC 和命令组合。

所有板端任务均使用 `ascend` Python 3.9.25、`PYTHONNOUSERSITE=1`，并在同一 shell 中加载
`/usr/local/Ascend/ascend-toolkit/set_env.sh`。`Health: Alarm` 是 310B4/8T 的既有诊断状态，
不单独否决测试；具体转换、ACL、数值和资源释放结果单独判定。

## 板卡环境

| 项目 | 20T 板 | 8T 板 |
| --- | --- | --- |
| SoC | Ascend 310B1 / 20T | Ascend 310B4 / 8T |
| 地址 | `192.168.1.112` | `192.168.1.108` |
| CANN toolkit | 8.0.0 | 8.0.0 |
| Python | `ascend` / 3.9.25 | `ascend` / 3.9.25 |
| PyACL | `/usr/local/Ascend/ascend-toolkit/latest/python/site-packages/acl.so` | 同路径 |
| npu-smi / driver | 25.2.0 | 25.2.0 |
| 诊断状态 | `Health: Alarm` | `Health: Alarm` |

统一基准的 ONNX golden 在 20T 板端 ONNX Runtime 1.19.2 生成，然后以相同文件传到两板；
8T 的 ONNX Runtime 1.15.1 不参与本轮 ACL 对照。本地导出工具为 `sci-agent`、Ultralytics
8.4.0、PyTorch 2.14.0、ONNX 1.20.1；本地 ONNX Runtime 1.24.2 只属于导出元数据，
不是板端数值证据。两块板的结果分别记录，不合并排名。

## 最新测试结果总览

| 测试轨道 | 本轮覆盖 | 最新结果 | 尚未完成 |
| --- | --- | --- | --- |
| ACE1 单类别手掌 NanoDet | 20T/8T，各 FP32 与 mixed FP16 | 4 条 ATC 均成功；4 条 ACL 均执行并完成清理，但原始输出数值均未通过门限；20T 单帧烟测为零框 | 带标注掌部数据质量；8T 真实图像框级验证 |
| CCNet 掌纹编码 ONNX | 8T，mixed FP16 | ATC、ACL、3 个确定性输入数值比较和模型路径性能均通过；完整图像前处理 smoke 报 `Invalid image format` | Tongji/PolyU 质量、完整前处理、服务 API、模板和摄像头闭环 |
| COCO 通用检测 | 8 个模型 × 2 种精度 × 20T/8T | 每板 16 个位置：4 条通过、11 条数值未通过、1 条缺少 OM | 真实图像框级验证与 COCO 验证集 mAP |

下文只列本轮有效结果。旧轮次、异口径和作废数值不作为当前结果保留；未测项目明确写为
`untested`，资产缺失明确写为阻塞，不以空白或旧记录代替。

## 训练后的手掌 NanoDet

### 模型与输入合同

| 项目 | 记录 |
| --- | --- |
| 模型 | `nanodet-plus-m-320-palm.onnx` |
| 来源 | ACE1 训练运行 `nanodet-plus-m-320-portable-hagridv2-palm__data-v001__seed-42__20261112-0900` |
| 训练代码提交 | `a4cdd39919883b5d7f8502582a20fa0b122541b4` |
| 大小 / opset | 4,728,669 bytes / opset 11 |
| 输入 | `data: float32[1,3,320,320]` |
| 输出 | `output: float32[1,2125,33]`；1 个类别分数和 4 组 8-bin DFL 回归 |
| 类别 | 仅 `palm`；没有 left/right 输出 |

20T 和 8T 分别使用各自板端资产与工作区，OM 未跨板复用：

```text
20T assets:     /home/HwHiAiUser/Documents/Ascend310/assets/case4/models/20t/nanodet-palm-20261113
8T assets:       /home/HwHiAiUser/Documents/Ascend310/assets/case4/models/8t/nanodet-palm-20261113
20T/8T workspaces: /home/HwHiAiUser/Documents/Ascend310/workspaces/case4/nanodet-palm-20261113
```

### ATC 与 OM

固定参数为 `--framework=5 --input_format=NCHW --input_shape=data:1,3,320,320`，
并分别测试 `force_fp32` 和 `allow_fp32_to_fp16`。

| 板卡 | 精度 | ATC | OM 大小 |
| --- | --- | --- | ---: |
| 20T | FP32 | observed-pass | 6,546,431 bytes |
| 20T | mixed FP16 | observed-pass | 6,495,250 bytes |
| 8T | FP32 | observed-pass | 6,546,452 bytes |
| 8T | mixed FP16 | observed-pass | 6,495,271 bytes |

### ACL 数值与性能

固定种子 `20261113` 的合成 `float32 NCHW` 输入同时送入 ONNX Runtime 和 ACL OM；不含
图像解码、DFL decode 或 NMS。每条路径使用 30 次预热、5 轮、每轮 100 次。

| 板卡 | 精度 | 输出 | 最大绝对误差 | 平均绝对误差 | `allclose(1e-3,1e-3)` | 端到端 P50 | 端到端 FPS |
| --- | --- | --- | ---: | ---: | --- | ---: | ---: |
| 20T | FP32 | `1x2125x33` finite | 0.243766 | 0.023380 | observed-fail | 18.206 ms | 54.93 |
| 20T | mixed FP16 | `1x2125x33` finite | 0.242814 | 0.023412 | observed-fail | 19.005 ms | 52.62 |
| 8T | FP32 | `1x2125x33` finite | 0.243766 | 0.023380 | observed-fail | 18.286 ms | 54.69 |
| 8T | mixed FP16 | `1x2125x33` finite | 0.242814 | 0.023412 | observed-fail | 17.829 ms | 56.09 |

四条路径均完成 ACL 加载、执行和资源释放。20T 另有一帧语义烟测，ONNX、FP32 OM 和
mixed OM 均为零个检测框；该空结果不能证明检测质量。8T 尚未完成等价的真实图像语义烟测。
本轮没有使用带标注的掌部验证集计算 AP、召回率或小目标指标，板端任务质量仍为 untested。

**结论**：这是目前唯一带 `palm` 标签的检测模型，但四条路径均未通过原始数值一致性门禁，
尚不能进入生产 registry；还需带标注掌部数据上的 AP、召回率、小目标指标和左右手方案验证。

### 误差定位边界

为避免把最终输出差异直接归咎于某一个末端算子，在 20T / FP32 上从原始 ONNX 提取了
stride-8 检测头的十个中间边界输出，覆盖 FPN
输入、两组 `depthwise Conv -> LeakyRelu -> pointwise Conv -> LeakyRelu` 和末端
`gfl_cls.0 Conv`。探针经 ATC 和 ACL 成功执行，使用与上述数值门禁相同的固定合成输入。

| 边界 | 最大绝对误差 | 平均绝对误差 | 说明 |
| --- | ---: | ---: | --- |
| FPN 到 stride-8 检测头的输入 `top_down_blocks.1...Add_output_0` | 0.431532 | 0.012268 | 进入检测头前误差已存在 |
| 第一组 pointwise Conv 输出 | 0.230394 | 0.006436 | 误差随卷积和激活变化 |
| 第二组 pointwise Conv 输出 | 0.374586 | 0.016943 | 误差进一步放大 |
| `gfl_cls.0 Conv` 原始输出 | 0.243766 | 0.025926 | 与最终 stride-8 DFL 误差范围一致 |

分类置信度通道（每个位置第 0 通道）的差异很小：FP32 最大 `0.001204`、平均
`0.000196`；主误差集中在其余 32 个 DFL 框回归 logits，FP32 最大 `0.243766`、平均
`0.024104`。最大值位于输出 `[0, 81, 21]`，ONNX 为 `-3.610001`，OM 为 `-3.366235`。
同一 OM 连续两次执行的输出逐位完全相同，因此这不是输出缓冲区未初始化或异步执行造成的
随机差异。

这项探针证明误差不是由末端 `Split`、`Sigmoid`、`Concat`、`Reshape` 或 `Transpose`
首次引入：检测头入口的 FPN 特征已不同。它不能证明某一个 `Conv` 就是根因，因为探针
保留中间输出会改变编译器融合边界，FPN 输入还依赖多个 `Conv`、`LeakyRelu`、`Add`、
`Concat` 和两个双线性 `Resize`。正式四条路径的 ATC 日志均没有不支持、回退或失败的算子。

FP32 与 mixed FP16 的 DFL 误差几乎相同：平均绝对误差为 `0.024104` / `0.024137`，
最大绝对误差为 `0.243766` / `0.242814`，且 20T 与 8T 都复现。因此当前根因应记录为
“共享图路径的数值差异，尚未定位到单算子”，不能归因为单纯 FP16 量化或某个算子不兼容。
若要得到根因结论，需要继续按骨干与 FPN 边界二分探针，并在不改变融合边界的条件下采集
CANN 图编译与算子精度日志。

## CCNet 掌纹编码 ONNX（8T）

### 资产与合同

| 项目 | 实测值 |
| --- | --- |
| 来源 | Hugging Face `kyereboatengcaleb/palm-ccnet-onnx` revision `a20685bdc844e153227a0d7bf7b6bdb3d2da4ff6` |
| 文件 | `palm_ccnet.onnx`，257311904 bytes |
| 输入/输出 | `input: float32[1,1,128,128]` / `embedding: float32[1,2048]` |

### ATC、ACL 与数值

使用 `--soc_version=Ascend310B4`、`--input_shape=input:1,1,128,128`、
`--precision_mode=allow_fp32_to_fp16`，并设置 `TE_PARALLEL_COMPILER=1`、
`MAX_COMPILE_CORE_NUMBER=1`。本次 8T OM 为 131,904,389 bytes。

| 证据层 | 结果 | 状态 |
| --- | --- | --- |
| ONNX 合同 | 输入、固定形状和 2048 维输出通过 | observed-pass |
| ATC | Ascend310B4 mixed FP16 成功 | observed-pass |
| ACL 冒烟 | 10 次执行，输出 finite，P50 `43.202 ms` | observed-pass |
| ACL 清理 | `acl_reset_device=0`、`acl_finalize=0` | observed-pass |
| 数值一致性 | 3 个确定性非生物输入，最小余弦 `0.9999924`，最大绝对误差 `0.0003074` | observed-pass |
| 模型路径性能 | 50 次预热、500 次、5 次重复；P50 `43.167 ms`，`23.166 FPS` | observed-pass |
| 掌纹数据集质量 | 未运行 Tongji/PolyU 等数据集 | untested |
| API、模板、摄像头 | 未启动服务，未接触真实掌纹 | untested |

尝试完整图像前处理时，板端 `cv2.cvtColor` 结果未通过运行时的 NumPy 类型检查，报出
`Invalid image format`。这是前处理环境兼容性问题，不能解释为 CCNet 或 ACL 不支持；完整
前处理、服务 API 和端到端验收仍为 untested。

## COCO 通用检测基线

### 20T 与 8T 统一结果

这些模型均为通用 COCO checkpoint，没有手掌或左右手类别。本轮使用统一程序
[`tools/board/unified_detector_benchmark.py`](../tools/board/unified_detector_benchmark.py) 和
固定清单 [`tools/board/unified_detector_suite.json`](../tools/board/unified_detector_suite.json)，
只测试两板上已存在的 OM，不执行 ATC 转换，也不把数值一致性写成检测精度或 COCO mAP。

#### 统一输入、比较和计时口径

- 覆盖 8 个静态单输入 ONNX 模型的 FP32 与 mixed FP16 路径，共 16 个组合/板；ONNX 输入和
  程序按固定清单核验模型资产。只有现存 OM 才执行 ACL；缺失文件会留下
  `blocked_missing_artifact` JSON 和 TSV 行，不会被跳过或伪造结果。
- 20T 以 ONNX Runtime 1.19.2 生成每模型独立的固定合成 NCHW 输入及 ONNX 输出；种子为
  各模型使用固定的专属种子，统一基数为 `20261113`。输入值为 `[0,1)`，dtype 遵循 ONNX
  合同；不做图像解码、归一化、缩放或后处理。相同 NPZ 参考文件复制到 8T，两板使用相同的输入张量和 ONNX 输出，保证比较条件一致。
- 所有 FP32 使用 `rtol=0.001, atol=0.001`；mixed FP16 使用 `rtol=0.03, atol=0.06`。
  每个实际 OM 路径预热 30 次，5 轮各 100 次；表中 P50/P95 从 500 个完整样本计算，
  `FPS=1000/P50(ms)`。计时包含 H2D、ACL 执行、设备同步和所有输出 D2H，不含图像前后处理。
- 状态 `通过` 表示所有原始输出通过对应门限；`数值未通过` 表示 ACL 成功执行但至少一个
  输出超限；`缺少 OM` 表示本轮没有可执行资产。两板的 `Health: Alarm` 均在运行前后记录，
  是已知诊断状态，不作为单独失败条件。

#### 复跑统一基准

将 Python 程序和正式 JSON 清单放到各板任务目录的 `tools/` 与根目录 `suite.json` 后，在
两板分别使用 `ascend` 环境运行。先在 20T 生成参考，再把 `references/` 下的 NPZ 和
`reference-manifest.json` 复制到 8T；两板均只读现存 OM。

```bash
TASK_ROOT=/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${PYTHONPATH:-}"
source /usr/local/Ascend/ascend-toolkit/set_env.sh

python "$TASK_ROOT/tools/unified_detector_benchmark.py" prepare-references \
  --suite "$TASK_ROOT/suite.json" --onnx-board 20t \
  --output-dir "$TASK_ROOT/references" \
  --manifest "$TASK_ROOT/reference-manifest.json"

python "$TASK_ROOT/tools/unified_detector_benchmark.py" run-suite \
  --suite "$TASK_ROOT/suite.json" --board 20t \
  --reference-manifest "$TASK_ROOT/reference-manifest.json" \
  --reference-dir "$TASK_ROOT/references" \
  --report-dir "$TASK_ROOT/reports/20t/models" \
  --summary "$TASK_ROOT/reports/20t/summary.tsv"

python "$TASK_ROOT/tools/unified_detector_benchmark.py" run-suite \
  --suite "$TASK_ROOT/suite.json" --board 8t \
  --reference-manifest "$TASK_ROOT/reference-manifest.json" \
  --reference-dir "$TASK_ROOT/references" \
  --report-dir "$TASK_ROOT/reports/8t/models" \
  --summary "$TASK_ROOT/reports/8t/summary.tsv"
```

#### 统一 ONNX 输入合同

每个共享 NPZ 含合成输入与 ONNX 输出；两板读取同一份参考文件。各模型输入合同如下：

| COCO 模型 | ONNX 输入合同 / 输出数 |
| --- | --- |
| PicoDet-S 320 | `float32[1,3,320,320]` / 8 |
| NanoDet-Plus-M COCO-80 320 | `float32[1,3,320,320]` / 1 |
| YOLOv5n | `float16[1,3,640,640]` / 1 |
| YOLOv8n | `float32[1,3,640,640]` / 1 |
| YOLO11n | `float32[1,3,640,640]` / 1 |
| YOLOv10n | `float32[1,3,640,640]` / 1 |
| YOLO12n | `float32[1,3,640,640]` / 1 |
| YOLO26n | `float32[1,3,640,640]` / 1 |

本轮统一测试只比较固定合成输入下的 ONNX/OM 原始输出，两板均未执行真实图像框级对照、
图像后处理验证或 COCO 验证集评估。

#### 本轮统一 ACL 结果

本表是本轮跨板对照的唯一数值与性能表。每板 16 个组合中，4 个通过门限、11 个执行成功但
数值未通过、1 个因缺少现存 OM 阻塞。OM 字节数来自本轮运行时文件检查。

| 板卡 | COCO 模型 | 精度门限 / 状态 | 最大 / 平均绝对误差 | P50 / P95 (ms) / FPS | OM 字节 |
| --- | --- | --- | ---: | ---: | --- |
| 20T | PicoDet-S 320 | FP32 `1e-3/1e-3` / 未通过 | 0.112702 / 0.003445 | 12.542 / 12.811 / 79.73 | 7,785,006 |
| 20T | PicoDet-S 320 | mixed FP16 `0.03/0.06` / 未通过 | 0.294411 / 0.005637 | 40.171 / 43.220 / 24.89 | 7,954,135 |
| 20T | NanoDet-Plus-M COCO-80 320 | FP32 `1e-3/1e-3` / 缺少 OM | N/A | N/A | N/A |
| 20T | NanoDet-Plus-M COCO-80 320 | mixed FP16 `0.03/0.06` / 通过 | 0.043386 / 0.001021 | 19.383 / 20.636 / 51.59 | 6,415,892 |
| 20T | YOLOv5n | FP32 `1e-3/1e-3` / 未通过 | 8.125000 / 0.008229 | 38.439 / 40.719 / 26.02 | 5,057,018 |
| 20T | YOLOv5n | mixed FP16 `0.03/0.06` / 未通过 | 8.375000 / 0.008434 | 22.971 / 24.593 / 43.53 | 4,722,162 |
| 20T | YOLOv8n | FP32 `1e-3/1e-3` / 未通过 | 0.759949 / 0.001674 | 41.937 / 43.451 / 23.85 | 7,424,398 |
| 20T | YOLOv8n | mixed FP16 `0.03/0.06` / 通过 | 1.741089 / 0.002638 | 71.580 / 73.486 / 13.97 | 7,800,632 |
| 20T | YOLO11n | FP32 `1e-3/1e-3` / 未通过 | 1.282684 / 0.001707 | 41.085 / 42.207 / 24.34 | 6,964,988 |
| 20T | YOLO11n | mixed FP16 `0.03/0.06` / 通过 | 1.962341 / 0.002634 | 85.826 / 87.969 / 11.65 | 7,428,479 |
| 20T | YOLOv10n | FP32 `1e-3/1e-3` / 未通过 | 630.405396 / 71.361626 | 49.148 / 50.468 / 20.35 | 6,984,245 |
| 20T | YOLOv10n | mixed FP16 `0.03/0.06` / 未通过 | 631.474854 / 96.565834 | 83.306 / 85.610 / 12.00 | 7,408,851 |
| 20T | YOLO12n | FP32 `1e-3/1e-3` / 未通过 | 1.249268 / 0.001457 | 64.931 / 66.205 / 15.40 | 8,696,068 |
| 20T | YOLO12n | mixed FP16 `0.03/0.06` / 通过 | 2.473633 / 0.002267 | 102.628 / 104.942 / 9.74 | 9,250,537 |
| 20T | YOLO26n | FP32 `1e-3/1e-3` / 未通过 | 649.396362 / 36.016273 | 41.406 / 42.086 / 24.15 | 6,893,289 |
| 20T | YOLO26n | mixed FP16 `0.03/0.06` / 未通过 | 22,120.904297 / 1,654.252686 | 79.614 / 82.240 / 12.56 | 7,412,482 |
| 8T | PicoDet-S 320 | FP32 `1e-3/1e-3` / 未通过 | 0.112702 / 0.003445 | 19.117 / 19.589 / 52.31 | 7,785,042 |
| 8T | PicoDet-S 320 | mixed FP16 `0.03/0.06` / 未通过 | 0.294411 / 0.005637 | 26.623 / 26.817 / 37.56 | 7,954,110 |
| 8T | NanoDet-Plus-M COCO-80 320 | FP32 `1e-3/1e-3` / 缺少 OM | N/A | N/A | N/A |
| 8T | NanoDet-Plus-M COCO-80 320 | mixed FP16 `0.03/0.06` / 通过 | 0.043386 / 0.001021 | 19.135 / 19.279 / 52.26 | 6,415,908 |
| 8T | YOLOv5n | FP32 `1e-3/1e-3` / 未通过 | 8.125000 / 0.008229 | 51.755 / 52.350 / 19.32 | 5,057,486 |
| 8T | YOLOv5n | mixed FP16 `0.03/0.06` / 未通过 | 8.375000 / 0.008434 | 28.394 / 28.663 / 35.22 | 4,722,209 |
| 8T | YOLOv8n | FP32 `1e-3/1e-3` / 未通过 | 0.759949 / 0.001674 | 41.787 / 41.937 / 23.93 | 7,424,421 |
| 8T | YOLOv8n | mixed FP16 `0.03/0.06` / 通过 | 1.741089 / 0.002638 | 57.585 / 57.802 / 17.37 | 7,800,655 |
| 8T | YOLO11n | FP32 `1e-3/1e-3` / 未通过 | 1.282684 / 0.001707 | 46.922 / 47.096 / 21.31 | 6,965,022 |
| 8T | YOLO11n | mixed FP16 `0.03/0.06` / 通过 | 1.962341 / 0.002634 | 68.718 / 69.033 / 14.55 | 7,428,473 |
| 8T | YOLOv10n | FP32 `1e-3/1e-3` / 未通过 | 630.405396 / 71.361626 | 58.113 / 58.358 / 17.21 | 6,984,334 |
| 8T | YOLOv10n | mixed FP16 `0.03/0.06` / 未通过 | 631.191345 / 86.197334 | 77.844 / 78.155 / 12.85 | 7,409,135 |
| 8T | YOLO12n | FP32 `1e-3/1e-3` / 未通过 | 1.249268 / 0.001457 | 79.666 / 79.931 / 12.55 | 8,696,627 |
| 8T | YOLO12n | mixed FP16 `0.03/0.06` / 通过 | 2.473633 / 0.002267 | 105.347 / 105.662 / 9.49 | 9,251,381 |
| 8T | YOLO26n | FP32 `1e-3/1e-3` / 未通过 | 649.396362 / 36.016273 | 56.640 / 56.926 / 17.66 | 6,893,754 |
| 8T | YOLO26n | mixed FP16 `0.03/0.06` / 未通过 | 637.971191 / 44.453213 | 79.030 / 79.394 / 12.65 | 7,413,482 |

本轮两板共 30 条实际 OM 运行报告，ACL 资源释放全部成功；程序未调用 `acl.rt.reset_device`，
测试结束后两板均无残留 benchmark 进程。`npu-smi` 的 NPU 内存使用从本轮测试前到测试后分别为
20T `4784 -> 4823 MB`、8T `5573 -> 5608 MB`；两板前后均报告 `Health: Alarm`，温度分别为
68/68 C 与 77/78 C。

运行期间观察到 ONNX Runtime 线程亲和性和 `numpy_to_ptr` 弃用警告；所有实际运行项均完成 ACL
执行与资源释放，警告未改变报告状态。

本轮统一基准的快照、逐模型 JSON、汇总 TSV 和参考输入保留在两板任务目录：

```text
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113/reports/system-status-20t.txt
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113/reports/system-status-8t.txt
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113/reports/20t/summary.tsv
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113/reports/8t/summary.tsv
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113/reports/{20t,8t}/models/<model>__<precision>.json
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-unified-benchmark-20261113/reference-manifest.json
```

本轮 NanoDet-Plus-M COCO-80 的输出 `1x2125x112` 与 ACE1 训练的单类别手掌 NanoDet 输出
`1x2125x33` 属于不同模型。COCO 原始张量数值门限通过不代表检测框一致，也不代表 COCO mAP 保持不变。

## OM 跨板互操作验证

本轮验证现有 OM 文件能否在另一块不同算力的开发板上加载并执行，不重新运行 ATC。20T 板为
`Ascend310B1 / 20T`，8T 板为 `Ascend310B4 / 8T`；两板使用 CANN 8.0.0、`ascend` conda 环境，
Python 3.9.25。每个方向将源板的 OM 副本放入目标板的受控工作区，34 份副本的完整性检查均通过。

基准使用各 ONNX 模型对应的固定随机 NCHW 输入及 ONNX Runtime CPU 参考输出，两板使用同一组参考输入。
每个 OM 预热 30 次，再执行 5 轮、每轮 100 次；耗时包含 H2D、ACL 执行、设备同步和 D2H，
不含检测后处理。FP32 数值门限为 `rtol=0.001, atol=0.001`，mixed FP16 为
`rtol=0.03, atol=0.06`。`运行通过 / 数值未过` 表示 OM 已在对侧通过 ACL 加载、输入输出合同检查、
推理和资源清理，但输出未通过 ONNX 数值门限；数值门限是独立结果，不用于否定 ACL 运行互通。

| 模型 | 精度 | 8T OM → 20T | 20T OM → 8T |
| --- | --- | --- | --- |
| PicoDet-S 320 COCO | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| PicoDet-S 320 COCO | mixed FP16 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| NanoDet-Plus-M 320 COCO-80 | FP32 | 未测：缺少现存 OM | 未测：缺少现存 OM |
| NanoDet-Plus-M 320 COCO-80 | mixed FP16 | 运行通过 / 数值通过 | 运行通过 / 数值通过 |
| YOLOv5n | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLOv5n | mixed FP16 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLOv8n | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLOv8n | mixed FP16 | 运行通过 / 数值通过 | 运行通过 / 数值通过 |
| YOLO11n | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLO11n | mixed FP16 | 运行通过 / 数值通过 | 运行通过 / 数值通过 |
| YOLOv10n | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLOv10n | mixed FP16 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLO12n | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLO12n | mixed FP16 | 运行通过 / 数值通过 | 运行通过 / 数值通过 |
| YOLO26n | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| YOLO26n | mixed FP16 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| ACE1 单类别手掌 NanoDet 320 | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| ACE1 单类别手掌 NanoDet 320 | mixed FP16 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |

每个方向均有 18 条记录：17 个现存 OM 全部在对侧完成 ACL 推理且资源清理成功，其中 4 个通过数值门限、
13 个未通过；COCO NanoDet FP32 因两板都没有现存 OM 而阻塞。两方向都没有 ACL 运行或清理失败。
因此，本轮在当前两块板和软件组合下，验证了现存 PicoDet、COCO NanoDet mixed FP16、YOLO 以及掌心
NanoDet OM 的双向 ACL 运行互通；这不等于所有模型的数值一致，也不等于检测框精度或 mAP 保持不变。

### 按模型区分跨板可执行状态

下表中的“可执行”只表示源板生成的 OM 已在目标板通过 ACL 加载、推理和资源清理。当前**没有任何一个
已取得 OM 并实际测试的模型被判定为不能跨板执行**；COCO NanoDet FP32 没有现存 OM，因此结论是未测，
不能归类为不支持。数值一致性另见“同输入输出张量直接比较”。

| 模型 | FP32 8T OM → 20T | FP32 20T OM → 8T | mixed FP16 8T OM → 20T | mixed FP16 20T OM → 8T | 同输入输出结果 |
| --- | --- | --- | --- | --- | --- |
| PicoDet-S 320 COCO | 可执行 | 可执行 | 可执行 | 可执行 | 首轮逐位相同 |
| NanoDet-Plus-M 320 COCO-80 | 未测：缺少 OM | 未测：缺少 OM | 可执行 | 可执行 | mixed FP16 首轮逐位相同 |
| YOLOv5n | 可执行 | 可执行 | 可执行 | 可执行 | 两精度首轮逐位相同 |
| YOLOv8n | 可执行 | 可执行 | 可执行 | 可执行 | 两精度首轮逐位相同 |
| YOLO11n | 可执行 | 可执行 | 可执行 | 可执行 | 两精度首轮逐位相同 |
| YOLOv10n | 可执行 | 可执行 | 可执行 | 可执行 | mixed FP16 在 20T 复测输出不稳定 |
| YOLO12n | 可执行 | 可执行 | 可执行 | 可执行 | 两精度首轮逐位相同 |
| YOLO26n | 可执行 | 可执行 | 可执行 | 可执行 | mixed FP16 在 20T 复测输出不稳定 |
| ACE1 单类别手掌 NanoDet 320 | 可执行 | 可执行 | 可执行 | 可执行 | 两精度首轮逐位相同 |

这里的“首轮逐位相同”表示同一合成输入下直接比较两份 OM 的原始输出张量，并不表示它们都通过
ONNX 数值参考门限。反过来，ONNX 数值门限未过也不能据此判定 OM 无法加载或跨板运行。

交叉运行逐模型报告、汇总表、OM 副本完整性记录及测试前后板卡状态快照保留在对应板卡：

```text
8T OM → 20T：
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/8t-om-on-20t/summary.tsv
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/8t-om-on-20t/models/

20T OM → 8T：
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/20t-om-on-8t/summary.tsv
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/20t-om-on-8t/models/

两板共享任务目录：
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/transfer-integrity.tsv
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/system-before.txt
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/system-after.txt
```

### OM 文件名中的 CANN 版本

建议后续新生成的 OM 在扩展名前写入目标 SoC、精度和转换时 CANN 版本，便于区分同一模型的不同构建产物：

```text
<model>__soc-<Ascend310B1-or-Ascend310B4>__<fp32-or-mixed_fp16>__cann-<version>.om
nanodet_plus_m_320__soc-Ascend310B4__mixed_fp16__cann-8.0.0.om
```

CANN 版本写入文件名是本项目的追溯命名建议，不是 OM 格式本身的强制要求，也不能单靠文件名证明兼容。
转换记录还应保留精确 ATC 版本、`--soc_version`、精度转换参数和输入输出合同；部署前仍需在目标板执行
ACL smoke 和数值比较。官方 CANN 8.0.0 ATC 指南说明，OM 的软件版本存在有方向的兼容周期，并提供查询已有
OM 转换基础版本的方法；不能把低版本转高版本的兼容说明反推为高版本 OM 必然可在低版本运行。
本轮 8T 与 20T 两边均为 CANN 8.0.0，因此本轮只验证 SoC/算力跨板执行，不构成 CANN 跨版本兼容测试。
[官方 ATC 工具指南：版本兼容性与离线模型基础版本查询](https://www.hiascend.com/doc_center/source/zh/canncommercial/800/devaids/devtools/atc/CANN%20%E5%95%86%E7%94%A8%E7%89%88%208.0.0%20ATC%E5%B7%A5%E5%85%B7%E6%8C%87%E5%8D%97%2001.pdf)。

### 同输入输出张量直接比较

为验证计算结果本身，再分别在 20T、8T 上执行同一模型的两份 OM：20T 来源 OM 与 8T 来源 OM。
每份 OM 使用相同参考文件中的输入，预热 2 次后捕获 1 次输出；同时比较同一来源 OM 在两块板上的输出。
比较输入张量必须逐位相同，输出张量使用对应精度门限，并另外记录逐位相等状态。首次全量比较覆盖
18 个模型/精度组合和 4 种比较关系，共 72 条记录；COCO NanoDet FP32 的 4 条关系因缺少 OM 阻塞。

其余 64 条通过门限的比较均为输出张量逐位相同。首次全量比较中的不一致项如下：

| 比较关系 | 逐位相同 | 数值不一致 | 缺少 OM |
| --- | ---: | --- | ---: |
| 20T 上比较 20T OM 与 8T OM | 16 | YOLOv10n mixed FP16，最大绝对差 627.758 | 1 |
| 8T 上比较 20T OM 与 8T OM | 17 | 无 | 1 |
| 比较同一 20T OM 在两板上的输出 | 15 | YOLOv10n mixed FP16，最大绝对差 627.758；YOLO26n mixed FP16，最大绝对差 1053.383 | 1 |
| 比较同一 8T OM 在两板上的输出 | 16 | YOLO26n mixed FP16，最大绝对差 1053.383 | 1 |

随后对 YOLOv10n 和 YOLO26n mixed FP16 各追加两轮相同输入复测。8T 上两份 OM 的输出在三轮捕获中均逐位相同；
20T 上这两个模型的输出在重复运行间发生变化，不能判定为稳定的跨板计算结果。YOLOv10n 在 20T 上两份 OM
之间的最大绝对差三轮分别为 `627.758`、`633.104`、`632.186`；YOLO26n 的同一比较首轮通过，后两轮的
最大绝对差分别为 `21850.531`、`21770.531`。这两种模型的输出形状均为 `1x300x6`。本轮证据能确认
20T 端的重复输出不稳定；具体计算环节见下节。

### YOLOv10n 与 YOLO26n 的计算异常定位

对两模型分别在 20T 上复跑 20T 自产 OM 和 8T 来源 OM。相同输入、预热 2 次、每轮捕获 1 次输出；
mixed FP16 各捕获 3 轮，FP32 各捕获 2 轮。20T 自产 mixed FP16 OM 在本板也发生跨进程输出变化，
8T 来源 OM 在 20T 上同样发生变化；两份 mixed FP16 OM 在 8T 上的 3 轮输出均逐位相同。
两份 FP32 OM 在 20T 上的 2 轮输出均逐位相同。FP32 的重复稳定性不等于通过 ONNX 数值门限：
这两个模型的 FP32 原始输出与 ONNX 参考仍未通过既定门限。

| 模型 | 20T mixed FP16 的原始输出表现 | 20T FP32 对照 | 8T mixed FP16 对照 |
| --- | --- | --- | --- |
| YOLOv10n | 本板与外板来源 OM 均有跨轮差异；框候选及类别可改变 | 两份 OM 各两轮逐位相同 | 两份 OM 各三轮逐位相同 |
| YOLO26n | 本板与外板来源 OM 均有跨轮差异；300 个输出置信度均为 `1.0`，框坐标绝对值最高约 `22052`，而 ONNX 参考最高约 `657` | 两份 OM 各两轮逐位相同 | 两份 OM 各三轮逐位相同 |

为确定是否只是末端排序造成差异，另在 20T 上把原 ONNX 的第一次 `TopK` 前两个张量作为附加输出，
保留原最终输出，重新以 `Ascend310B1`、`allow_fp32_to_fp16` 转成临时探针 OM。两份探针均通过
ATC 和 ACL 执行，使用同一输入各在独立进程中运行 3 次。探针改变了图的输出边界，因此只用于
定位变化所在的图段，不替代原始 OM 的性能或准确率证据。

| 探针 | 第一次 `TopK` 前框张量 `1x8400x4` | 第一次 `TopK` 前分数张量 `1x8400x80` | 最终输出 `1x300x6` |
| --- | --- | --- | --- |
| YOLOv10n，第 0/1 轮 | 不同，最大绝对差 `3.25` | 不同，最大绝对差 `0.00001368` | 不同，最大绝对差 `631.02` |
| YOLOv10n，第 0/2 轮 | 不同，最大绝对差 `8.00` | 不同，最大绝对差 `0.00005941` | 不同，最大绝对差 `633.62` |
| YOLO26n，第 0/1 轮 | 不同，最大绝对差 `23680` | 不同，最大绝对差 `1.0` | 逐位相同 |
| YOLO26n，第 0/2 轮 | 不同，最大绝对差 `48124` | 不同，最大绝对差 `1.0` | 不同，最大绝对差 `21662.38` |

两模型的框和分数在进入第一次 `TopK` 前已经变化，所以不能把不稳定归因于 `TopK` 或
`GatherElements` 单个末端算子。YOLOv10n 的上游分数差很小，排序/索引可能将其放大；
YOLO26n 的上游框与分数已大幅偏离 ONNX 参考，不能解释为仅有候选排序差异。
现有证据把异常限定在当前 20T / Ascend310B1 的 mixed FP16 编译与执行组合及其 `TopK` 上游路径，
但还不能区分 ATC 融合、具体上游算子内核或运行时问题。ATC 日志对 `Expand`、`Mod` 和 `Div` 的
W11001 只提示可能影响性能，不能据此认定这些算子导致数值错误。
因此，这两个 mixed FP16 模型在 20T 上不能作为数值稳定的跨板复用模型。

### 单算子与等价改写复核

2026-11-14 在两板各自转换实际 ONNX 节点的单算子小图，使用原 ONNX 相应节点的输入张量，
在独立进程中各运行 3 次。YOLOv10n 和 YOLO26n 的检测头 `Sigmoid`、框缩放 `Mul`，
在 20T 与 8T 上均三次逐位相同且通过 ONNX 数值门限。20T 上另测 YOLO26n 的框、分类检测头
各一个 `Conv`，以及 YOLOv10n 注意力的第一处 `MatMul` 和缩放 `Mul`，均三次逐位相同且
通过对应的 ONNX 数值门限。YOLO26n 的 `/model.14/Resize` 单算子小图在 20T 上
5 次逐位相同，与 ONNX 输出完全相同。因此，不能宣称这些算子单独转换或单独运行必然错误。

在**原始 20T mixed FP16 OM**上做定点算子统计 Dump，观察到以下路径差异：

| 模型 | 原图中最早定位到的异常图段 | 定点监测和等价改写结果 |
| --- | --- | --- |
| YOLO26n | `/model.13`、`/model.4` 的监测输出稳定；`/model.16/cv1/conv/Conv` 的输入已异常，二者之间是 `/model.14/Resize` 和 `/model.15/Concat` | 只监测 `/model.14/Resize`，原 OM 五次输出逐位相同；不监测它的邻近对照仍波动。把该最近邻 `Resize` 等价改写为两个 `Gather` 后，ONNX 最终输出逐位相同，新 OM 在 20T 五次逐位相同，分数最高约 `0.365`、框坐标绝对值最高约 `656`；随后原 OM 五次中四次与首轮不同 |
| YOLOv10n | `qkv` 卷积的监测输出稳定；`Softmax` 的输入已跨轮变化，范围落在第一处注意力 `MatMul` 及缩放路径附近 | 只监测第一处 `MatMul`，原 OM 五次逐位相同；只监测后续 `Softmax`，五次中四次不同。将第一处批量 `MatMul` 等价拆为两个按头计算的 `MatMul`，ONNX 最终输出逐位相同，新 OM 在 20T 五次逐位相同；随后原 OM 五次中四次与首轮不同 |

这两处是**整图编译与执行路径的可复现触发区段**，不是已证实的“坏算子”。
特别是开启全量算子 Dump、或把中间节点加为 ONNX 输出，会改变图优化或执行时序，
并可能使原来的波动消失；因此不能仅凭 Dump 结果把责任归给某个内核。
等价改写仅验证固定合成输入的重复稳定性；两份改写 OM 的最终输出仍未通过原定
ONNX 数值门限，也没有真实图像框级质量验证，不应直接用作生产模型。

### 其他模型的跨板输出复核

2026-11-14 在两板重新捕获所有现存 OM：每板对本板和对板来源 OM 各预热 2 次、捕获 1 次，
并分别与首次捕获比较。下表只说明**相同固定输入的原始输出张量是否逐位相同**，
与 ONNX 数值门限、检测框准确率和 COCO mAP 是不同证据。

| 模型与精度 | 两板各自的两份 OM | 同一 OM 跨板及跨轮复核 |
| --- | --- | --- |
| PicoDet-S、YOLOv5n、YOLOv8n、YOLO11n、YOLO12n、ACE1 手掌 NanoDet：各 FP32 与 mixed FP16；COCO NanoDet-Plus-M：mixed FP16；YOLOv10n、YOLO26n：各 FP32 | 20T 与 8T 上两份来源 OM 均逐位相同，共 15 个组合 | 每份 OM 在两板及第二轮捕获中均逐位相同 |
| YOLOv10n、YOLO26n：mixed FP16 | 8T 上两份 OM 逐位相同；20T 上两份 OM 的重复输出不稳定 | 8T 跨轮逐位相同；20T 跨轮不相同，不通过跨板计算一致性门禁 |
| COCO NanoDet-Plus-M：FP32 | 两板均缺少现存 OM | 未测试 |

这 15 个组合的互换一致只覆盖当前 CANN 8.0.0、两块板和固定合成输入；
其中部分组合虽然 OM 对 OM 逐位相同，仍未通过各自与 ONNX 参考的数值门限。
首次全量比较和本次复核的口径一致：不能把“互换后输出相同”写成“模型计算正确”。

追加原始证据保留在板端任务目录：

```text
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/operator-microtest/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/attention-microtest/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/resize-microtest/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/operator-dump/20t/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/resize-rewrite-probe/20t/yolo26n/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/matmul-rewrite-probe/20t/yolov10n/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/all-model-repeat/20261114/
```

探针的转换日志、三轮原始输出和比较汇总保留在板端：

```text
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/pre-topk-probe/yolov10n/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/pre-topk-probe/yolo26n/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/fp32-control/20t/
```

以上是固定合成输入上的原始输出张量比较，不是实际图像检测框或 COCO mAP 对比。当前证据支持除上述
mixed FP16 异常项外的首轮输出完全一致；YOLOv10n、YOLO26n mixed FP16 尚不能通过跨板结果一致性门禁。
逐模型捕获、直接差分和复测汇总保留在板端任务目录：

```text
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/20t/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/8t/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/comparisons/summary.tsv
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/repeats/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/om-output-consistency/repeats2/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/system-after-output-comparison.txt
```

## 综合结论与后续门禁

- 真实手掌模型目前是单类别 NanoDet；8T/20T 的 ATC 和 ACL 运行通过，但原始输出数值门禁未通过。
- CCNet 是掌纹编码模型，不是手掌检测器；8T 的模型路径数值和性能通过，但掌纹质量、API、
  摄像头和前处理端到端仍未完成。
- COCO 通用模型本轮按统一协议完成 20T、8T 各 16 个模型/精度位置：各有 4 条通过、11 条
  原始输出数值未通过和 1 条缺少 NanoDet FP32 OM 的阻塞项。两板的 NanoDet mixed FP16、
  YOLOv8n/YOLO11n/YOLO12n mixed FP16 均通过对应门限。该测试没有重新转换 OM，也没有真实图像
  框级对照或 COCO 验证集 mAP，不能据此声称检测精度无损。
- OM 跨板交叉运行中，两方向各有 17 个现存 OM 在对侧完成 ACL 推理与资源清理，均无运行失败；
  每方向 4 个通过数值门限、13 个未通过，另有 1 个 NanoDet COCO FP32 因缺少现存 OM 而阻塞。
  因此，在本轮 8T/20T 板卡组合下，所有已测试模型均可双向执行；没有可判定为不可执行的模型，
  NanoDet COCO FP32 因缺少 OM 未测。这不代表所有模型数值一致。
- 同输入 OM 对 OM 的首轮张量差分中，64 条通过比较均逐位相同，4 条不一致，4 条因缺少 NanoDet COCO
  FP32 OM 阻塞。追加复测确认 15 个模型/精度组合在两板互换及第二轮捕获中逐位相同；YOLOv10n、
  YOLO26n mixed FP16 在 20T 上的重复输出不稳定，因此这两个组合不能通过计算结果一致性门禁。
  单算子小图均稳定；整图异常分别定位到 YOLOv10n 的第一处注意力 `MatMul` 路径和 YOLO26n 的
  `/model.14/Resize` 路径，但尚不能认定某个独立算子内核有缺陷。上述结论只覆盖固定合成输入。
- 后续新生成 OM 建议在文件名加入目标 SoC、精度和 CANN 版本，ATC 精确版本及转换参数放入构建记录；
  现有文件名不因本次测试批量改动。本轮两板 CANN 版本相同，未验证跨 CANN 版本兼容性。
- 后续必须使用带掌部真值的数据完成解码后框级一致性、AP、召回率、小目标指标和左右手方案；
  同时完成前处理、摄像头、服务 API、稳定性和许可证审查后，才能讨论生产准入。

## 原始证据位置

```text
手掌 NanoDet：
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/20t/nanodet-palm-20261113/
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/8t/nanodet-palm-20261113/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/nanodet-palm-20261113/

CCNet 8T：
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/ccnet-onnx-a20685bd-8t-20261113/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/ccnet-onnx-8t-20261113/

通用检测 20T：
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/hand-detector-onnx-smoke-20261109/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/hand-detector-onnx-smoke-20261109/
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/20t/yolo-comparison-20261112/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/yolo-comparison-20261112/
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/20t/yolo-comparison-20261113/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/yolo-comparison-20261113/

通用检测 8T：
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/8t/detector-parity-20t-exports-20261113/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-parity-8t-20261113/
```
