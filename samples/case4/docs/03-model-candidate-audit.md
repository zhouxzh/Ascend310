# 03 - 候选模型审计

## 1. 审计规则

候选清单只记录来源、许可证、输入输出契约、资产哈希和当前状态。它不会自动下载、转换或启用模型。

生产模型必须通过完整生产准入；人工测试模型必须至少通过：

- 固定 source revision；
- palmprint single-input embedding 契约；
- 输入 `[1,1,128,128]`；
- 512-D cosine embedding；
- NPU `mixed_fp16` OM 字节数和 SHA-256；
- 本地 ONNX/checkpoint/OM 资产校验；
- 模型专属阈值记录。

## 2. 当前 CompNet 状态

| ID | 来源/许可 | OM | 状态 |
| --- | --- | --- | --- |
| `compnet_tongji_600` | CompNet / BSD-3-Clause；权重和数据条款需按上游要求使用 | mixed_fp16，见 `om_manifest.json` | `manual_test_pending` |
| `compnet_iitd_460` | 同上 | mixed_fp16，见 `om_manifest.json` | `manual_test_pending` |
| `compnet_rest_358` | 同上 | mixed_fp16，见 `om_manifest.json` | `manual_test_pending` |
| `compnet_xjtu_flash_200` | 同上 | mixed_fp16，见 `om_manifest.json` | `manual_test_pending` |
| `compnet_xjtu_natural_200` | 同上 | mixed_fp16，见 `om_manifest.json` | `manual_test_pending` |

五个 ID 具有 `manual_test_enabled=true`，仅在 `PALMPRINT_PROFILE=manual_test` 下进入运行时。`production_enabled` 与 `manual_test_pending` 分开保存，人工测试前不写稳定性通过结论。

## 3. 其他候选

PPNet、Holzweber、EE-PRNet、PalmNet、SDK 和其他候选仍只保留审计信息。没有固定 commit、输入输出契约或授权状态的候选不进入任何运行时 profile。

旧静态 CompNet 和 EDCC 只用于 `tools/offline`，不进入生产 API、模板 namespace 或前端模型选择。

## 4. 资产来源

六个 OM 的字节数和 SHA-256 记录在 `om_manifest.json`。当前源码不自动上传或下载 Hugging Face；任何外部资产同步都必须由操作者确认许可证、固定来源和哈希。HF、GitHub 和源码同步包均不得包含模板、真实图像或运行报告。

## 5. 结论

本文件只说明候选和资产状态。人工测试发布允许功能验证，但不替代完整的生产稳定性、跨域精度和长期资源验收。人工测试完成前保持当前版本冻结；问题通过下一版本单独修复和复验。
