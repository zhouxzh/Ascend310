---
name: ascend310b-precision-debug
description: Ascend 310B1/310B4 的数值精度问题排查。用于 ACL/ACLNN、ATC/OM、torch_npu、MindSpore 和自定义算子的输出偏差、NaN/Inf、dtype 或 layout 问题；必须建立 golden、输入合同、8T/20T 独立证据和明确容差，不能用一次输出看似正常代替精度验证。
---

# Ascend 310B 精度排查

本 skill 参考 CANNBot `ascendc-precision-debug` 的精度排查思路，但平台范围、8T/20T 证据和停止条件已改为 310B；不替代 CANNBot 原技能。

## 先定义“正确”

在运行前固定：

- 模型/算子名称和字节哈希；
- 输入文件、shape、layout、dtype 和预处理；
- golden 的生成环境和算法；
- 输出比较规则：逐元素、最大误差、相对误差、阈值或任务指标；
- 目标板是 8T 还是 20T。

没有 golden 和容差时，不能给出“精度通过”。

## 推荐流程

1. 用 `ascend_probe` 确认目标板、CANN、驱动、Python 和 ACL。
2. 用最小输入分别跑 CPU/reference（仅离线对照）和 NPU；记录两侧完整版本。
3. 检查输入是否在 NPU 前被错误归一化、量化、转 layout 或裁剪。
4. 检查 NPU 输出后处理是否重复或漏做。
5. 如果误差集中在某类输入，缩小 shape、dtype、通道和算子范围。
6. 对 ATC 路径记录转换精度模式、输入 shape、soc 和 OM 哈希；对 torch_npu/MindSpore 记录框架和插件版本。
7. 把完整误差表、失败样本和原始日志作为证据。

## 310B 规则

- 8T 与 20T 的精度结果分开记录，不能互相替代。
- 不直接引用 910/950 的 FP8、BF16、RegBase 或 matmul 精度结论。
- 不把 CPU fallback 的结果当成 NPU 精度通过。
- 不因为一个 dtype 或 shape 失败就写“整个模型不支持”。
- 当前默认探测不采集固件版本；不要用缺失的固件字段替代数值证据。

## 停止条件

- 输入合同或 golden 不明确。
- 目标板档位或 CANN 版本不确定。
- 失败发生在环境/加载层，先修那一层。
- 需要修改模型精度模式、量化策略或升级运行时才能继续时，先取得用户授权。
