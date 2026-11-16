# 08 - 掌部检测与掌纹 ONNX/OM 综合实验记录

_实验性质：离线调试｜本轮测试日期：2026-11-13｜目标：Ascend 310B1 / 20T 与 Ascend 310B4 / 8T_

> **2026-11-16 范围调整：** 通用 COCO 检测器已从本案例迁到独立仓库 [zhouxzh/ascend310b-coco-detector-benchmarks](https://huggingface.co/zhouxzh/ascend310b-coco-detector-benchmarks)。本文只保留手掌 NanoDet 与 CCNet 记录；COCO 数值、ais_bench 和 mAP 不再作为本案例功能维护。

## 记录范围与证据边界

本文件只记录截至 2026-11-13 的最新有效测试结果，分为两条相互独立的测试轨道：

1. **训练后的手掌检测模型**：ACE1 训练的单类别 `palm` NanoDet，分别在 20T 与 8T 测试。
2. **掌纹编码模型**：CCNet ONNX，仅在新 8T 板上测试。

两条轨道的输入合同、输出语义、数值门限、性能协议和结论不能互相替代。本文也不构成生产模型准入、mAP、左右手识别准确率或摄像头端到端验收报告。

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

手掌 NanoDet 的 ONNX 对照在板端记录中单独保存。两块板的结果分别记录，不合并排名。

## 最新测试结果总览

| 测试轨道 | 本轮覆盖 | 最新结果 | 尚未完成 |
| --- | --- | --- | --- |
| ACE1 单类别手掌 NanoDet | 20T/8T，各 FP32 与 mixed FP16 | 4 条 ATC 均成功；4 条 ACL 均执行并完成清理，但原始输出数值均未通过门限；20T 单帧烟测为零框 | 带标注掌部数据质量；8T 真实图像框级验证 |
| CCNet 掌纹编码 ONNX | 8T，mixed FP16 | ATC、ACL、3 个确定性输入数值比较和模型路径性能均通过；完整图像前处理 smoke 报 `Invalid image format` | Tongji/PolyU 质量、完整前处理、服务 API、模板和摄像头闭环 |

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

## 通用 COCO 检测器

通用 COCO 检测器已迁出本案例，公开入口为 [zhouxzh/ascend310b-coco-detector-benchmarks](https://huggingface.co/zhouxzh/ascend310b-coco-detector-benchmarks)。该仓库保存 ONNX、OM、`SHA256SUMS`、ais_bench 结果和 `evaluator/` 评测器。COCO 模型没有 `palm` 或左右手类别，其数值、速度和 mAP 不能写成手掌检测或掌纹识别结果。

## OM 跨板互操作验证

本轮也验证了 ACE1 单类别手掌 NanoDet 的现存 OM 能否在另一块不同算力的开发板上加载并执行，不重新运行 ATC。20T 板为 `Ascend310B1 / 20T`，8T 板为 `Ascend310B4 / 8T`；两板使用 CANN 8.0.0、`ascend` conda 环境和 Python 3.9.25。通用 COCO 检测器的跨板结果见独立仓库，不在本案例继续维护。

手掌 NanoDet 的 FP32 与 mixed FP16 OM 在两个方向均完成 ACL 加载、推理和资源清理：

| 模型 | 精度 | 8T OM → 20T | 20T OM → 8T |
| --- | --- | --- | --- |
| ACE1 单类别手掌 NanoDet 320 | FP32 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |
| ACE1 单类别手掌 NanoDet 320 | mixed FP16 | 运行通过 / 数值未过 | 运行通过 / 数值未过 |

“运行通过 / 数值未过”表示对侧 ACL 执行和清理成功，但原始输出未通过当时的 ONNX 数值门限。这不等于检测框精度或任务质量通过。同输入下，两份手掌 NanoDet OM 的首轮原始输出逐位相同；该一致性只覆盖当前 CANN 8.0.0、两块板和固定合成输入。

交叉运行报告保留在板端任务目录：

```text
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/8t-om-on-20t/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/detector-cross-board-om-20261113/reports/20t-om-on-8t/
```

后续新生成 OM 建议在文件名加入目标 SoC、精度和 CANN 版本。本轮两板 CANN 版本相同，未验证跨 CANN 版本兼容性。

## 综合结论与后续门禁

- 真实手掌模型目前是单类别 NanoDet；8T/20T 的 ATC 和 ACL 运行通过，但原始输出数值门禁未通过。
- CCNet 是掌纹编码模型，不是手掌检测器；8T 的模型路径数值和性能通过，但掌纹质量、API、摄像头和前处理端到端仍未完成。
- 手掌 NanoDet 的现存 OM 可在当前 8T/20T 组合上双向加载执行，原始输出仍未通过 ONNX 数值门限。
- 通用 COCO 检测器不再作为本案例功能维护。
- 后续必须使用带掌部真值的数据完成解码后框级一致性、AP、召回率、小目标指标和左右手方案；同时完成前处理、摄像头、服务 API、稳定性和许可证审查后，才能讨论生产准入。

## 原始证据位置

```text
手掌 NanoDet：
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/20t/nanodet-palm-20261113/
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/8t/nanodet-palm-20261113/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/nanodet-palm-20261113/

CCNet 8T：
/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/ccnet-onnx-a20685bd-8t-20261113/
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/ccnet-onnx-8t-20261113/
```
