# 08 测试问题记录

> 本文件是案例 5 的唯一测试问题台账。模型准入和性能数字来自 [异构处理与模型准入](07_ascend310b_heterogeneous_signal_processing.md)，不把候选数值通过、任务质量或性能结论互相替代。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-08-12（模型准入证据为主） |
| 开发板 | Ascend 310B / 8T |
| SoC / 档位 | Ascend 310B / 8T |
| CANN / 驱动 | 见板端 ATC/ACL 报告 |
| Python / 框架 | 板端 Python 与 ONNX/ACL 工具链 |
| 模型 / revision | TorchSig YOLO11、RealCNN、ComplexCNN 等候选 |
| 代码提交 | 未记录 |
| 输入与命令 | ATC/ACL 候选命令；固定输入形状见专题文档 |
| 原始证据 | `data/`、`models/generated/` 和被忽略的板端报告 |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CASE5-ISS-20260812-01` | `resolved` | `major` | 2026-08-12 | YOLO ATC 使用了错误输入名 `input_tensor` | 改用 `images` 后 ATC 成功 |
| `CASE5-ISS-20260812-02` | `mitigated` | `major` | 2026-08-12 | YOLO 基础 OM 的 box 坐标数值未通过 | 局部 `keep_dtype` OM 通过数值门 |
| `CASE5-ISS-20260812-03` | `open` | `observation` | 2026-08-12 | 当前 YOLO 权重是 `single_cls=True`，不能做 51 类识别 | 保持单类检测边界 |
| `CASE5-ISS-20260812-04` | `open` | `major` | 2026-08-12 | RealCNN/ComplexCNN 的源码模型键与权重键不匹配 | 保持 rejected |

## CASE5-ISS-20260812-01：YOLO ATC 输入名错误

### 状态与影响

- 状态：`resolved`
- 级别：`major`
- 影响对象：`ATC / input-contract`
- 影响范围：TorchSig YOLO11 ONNX 的首次转换
- 是否阻塞发布：已通过正确输入合同解除

### 预期行为 / 实际症状

按 ONNX 导出图的真实输入名执行 ATC。首次命令使用 `input_tensor`，板端返回：

```text
E10016 Opname[input_tensor] not found
```

### 判定层级与根因

`ATC / input-contract`。根因是命令输入名与导出图输入名不一致，不是算子不支持。

### 修复与回归

将输入名改为导出图的 `images`，ATC 成功返回。静态图包含 `Conv`、`Mul`、`Sigmoid`、`Concat`、`Reshape`、`Split`、`MaxPool`、`Transpose`、`MatMul`、`Softmax`、`Resize`、`Div`、`Slice` 和 `Sub`；不能把这次输入名错误写成“某个算子不支持”。

### 证据与关联

- [异构处理与模型准入](07_ascend310b_heterogeneous_signal_processing.md)
- 原始板端 ATC 日志未提交到 Git。

## CASE5-ISS-20260812-02：YOLO 基础 OM box 数值不一致

### 状态与影响

- 状态：`mitigated`
- 级别：`major`
- 影响对象：`OM numerical consistency`
- 影响范围：TorchSig YOLO11 `[1,3,1024,1024]` 基础 OM
- 是否阻塞发布：基础 OM 被拒绝；局部保精度 OM 可推荐

### 预期行为 / 实际症状

基础 OM 的 1,182,720 个输出中，367 个超出默认容差，全部位于 box 第 2、3 个坐标通道；类别置信度最大误差为 `8.14e-4`。

### 判定层级与根因

`OM numerical consistency`。这是解码/输出精度问题，不是 ACL 加载失败、摄像头输入或任务准确率。

### 修复与回归

使用 `allow_mix_precision` 并对原始 ONNX 图末端 23 个 DFL 解码/输出节点使用 `keep_dtype`。新 OM 的 SHA-256 为 `b7f6b5dd940ef906447a66f2f8ebc310b18236fd889ec955ddb59497d4077a80`；50 次预热、300 次测量的 `allclose` 通过，最大绝对误差 `3.8271` 低于允许误差 `4.0839`。

回归结论：`mitigated`。基础 OM 仍不应作为推荐产物；局部保精度策略有效。

## CASE5-ISS-20260812-03：YOLO 单类权重的语义边界

当前接受的 YOLO11 权重使用 `single_cls=True`，只能判断是否存在候选信号区域。51 项名称映射不会把权重变成 51 类调制识别器；无标签真实 IQ 上出现某个类别文字，不能作为调制类别、检测率或准确率证据。

- 状态：`open`
- 级别：`observation`
- 判定层级：`task-quality`
- 处理：保留单类检测叙事；需要明确多类监督训练数据和新权重，才能开展类别级评测。

## CASE5-ISS-20260812-04：RealCNN/ComplexCNN 源码与权重键不匹配

- 状态：`open`
- 级别：`major`
- 判定层级：`source / weight contract`
- 症状：RealCNN 期望旧 `net.*`，实际权重是 `stem/block1..4/fc1/fc2`；ComplexCNN 的 `conv*` 期望与复数权重、复数 BN 和 modReLU 键不匹配。
- 处理：记录为“源代码/权重合同阻断”，不重写网络掩盖失败，不宣称 24 类识别可用。临时重建 CLDNN 只用于 ONNX/ATC 算子可行性，不证明原模型准确率。

## 关联文档

- [异构处理与模型准入](07_ascend310b_heterogeneous_signal_processing.md)
- [RTL-SDR 服务说明](06_rtl_sdr_npu_demo.md)
- [系统架构与数据合同](03_architecture.md)
