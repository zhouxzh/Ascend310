# 脱敏证据边界

本目录只保存可随源码发布的摘要、哈希和人工测试表，不保存真实掌纹图像、模板、密钥、完整板端日志或原始运行报告。

允许记录：

- 版本、源码和前端 bundle SHA-256；
- OM 文件的字节数和 SHA-256；
- HTTP 状态和脱敏响应摘要；
- 人工测试的模型 ID、时间、耗时和通过/失败状态；
- 通用故障类型及处理步骤。

原始 dmesg、设备诊断、完整 benchmark、模板目录和报告只保留在板端私有目录。公开文档不展开某次设备事件的时间线，也不把设备异常归因于某个模型。

当前版本的合成 ROI API 冒烟摘要见
[`manual-test-smoke-20260818.md`](manual-test-smoke-20260818.md)；该摘要明确区分了已执行的接口闭环和仍待操作者完成的触摸屏、摄像头检查。

全候选编排和开发板只读预检摘要见
[`candidate-campaign-20260910.md`](candidate-campaign-20260910.md)。该摘要包含 28 项的可复现终态，以及 2026-09-11 在正确 CANN 环境完成的六个 OM 候选 10 次 ACL smoke；它仍明确区分未执行的质量、性能和设备诊断门禁。

机器可读的 28 项最终状态见
[`candidate-final-status-20260911.json`](candidate-final-status-20260911.json)。其中六项为 `acl_failed`（模型 smoke 通过，但设备诊断阻断），其余为可复现的 `blocked_source`、`blocked_license` 或 `not_applicable`。

用户请求的通用骨干、ROI、分类器、掌静脉、生成模型和 Palm-ID 扩展测试见
[`extended-model-campaign-20260911.md`](extended-model-campaign-20260911.md)。该报告明确区分随机权重架构 smoke、任务适配阻断和真实掌纹模型证据，不把通用 ImageNet 骨干当作可部署识别模型。

板外导出、Ascend 310B4 ATC 和纯 ACL/NumPy smoke 的研究 OM 迁移记录见
[`om-migration-20260911.md`](om-migration-20260911.md)。其中的分类器和 ROI OM 不属于生产 embedding registry。

所有当前板端 OM 的独立生命周期验证见
[`om-deployment-validation-20260911.md`](om-deployment-validation-20260911.md)。该报告覆盖 6 个现有工作台 OM、6 个研究 OM 和 1 个旧别名 OM，各执行 10 次独立 ACL 进程循环，并单独记录当前 API 兼容性与尚未完成的质量准入门禁。

验证通过的 13 个 OM 已同步到本地的清单见
[`om-sync-20260911.md`](om-sync-20260911.md)。研究 OM 放在各候选目录下，未加入生产模型目录或 registry。

候选模型独立运行时实验入口和 API 契约见
[`candidate-runtime-workbench-20260911.md`](candidate-runtime-workbench-20260911.md)。
