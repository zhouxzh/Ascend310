---
name: ascend310b-api-best-practices
description: Ascend 310B1/310B4 的 Ascend C/ACL API 使用审查。用于检查 DataCopy 对齐、dtype、Buffer、同步、Tiling、Host Runtime 和算子实现中的平台假设；当前 CANN 文档和板端实测优先，禁止复制 A2/A3/A5 或 950 专用 API 限制。
---

# Ascend 310B API 最佳实践

## 适用范围

用于 310B1/310B4 的 Ascend C、ACL runtime 和 host 侧接口审查。它不是所有 Ascend 平台的通用 API 手册。

本 skill 参考 CANNBot `ascendc-api-best-practices` 的“先查 API 限制、再写代码”思想，但不直接继承其平台矩阵。

## 审查清单

### 数据搬运

- `DataCopy` 的 GM/UB 地址和对齐是否满足当前 CANN 版本要求。
- 非对齐输入是否使用 `DataCopyPad` 或明确的 padding 策略。
- 是否记录字节数、元素 dtype、stride 和边界条件。
- 是否在数据搬运后正确同步，而不是立即读取未完成结果。

### Buffer 与流水

- TBuf/TQue 的容量、生命周期和队列深度是否匹配真实 shape。
- 是否出现同一 buffer 被多个阶段覆盖、未等待就复用。
- 是否把“能编译”误当作“同步正确”。
- 多核或 AIC/AIV 场景是否符合当前 310B 的可用接口。

### dtype 与精度

- 输入、输出、累加和中间结果 dtype 是否明确。
- 低精度输入是否发生意外的 FP16/BF16 假设；当前平台是否支持该 dtype以板端和当前 CANN 文档为准。
- Cast 的位置是否稳定，是否记录 golden 和容差。
- 不把 910/950 的 FP8/BF16/RegBase 示例直接复制。

### 高阶 API 与通信

- Matmul/GMM/HCCL/跨卡通信等示例若声明 A2/A3/950，先确认当前 310B 的 CANN 和 API 是否提供对应实现。
- 310B 边缘产品不应盲目使用云端多卡、HCCS 或大规模通信参数。
- 自定义算子注册、TilingKey 和 Kernel 查找错误交由 runtime-debug/code-review 处理。

## 验证步骤

1. 用 `ascend_probe` 确认 CANN、驱动、Python、ACL 和 8T/20T。
2. 写一个最小输入，固定 shape、dtype 和输出合同。
3. 先用板端编译/运行验证 API 可调用，再测完整模型。
4. 与 golden 比较并保存日志。
5. 把平台假设、CANN 版本和失败条件写入证据。

## 停止条件

- API 只在 A2/A3/A5/950 资料中出现，且当前 310B CANN 没有对应实现。
- 对齐、dtype 或 shape 合同无法确定。
- 需要修改硬件、升级 CANN 或绕过安全限制才能继续。
- 没有可复现输入和输出证据。
