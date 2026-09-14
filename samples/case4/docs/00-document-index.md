# 00 - 掌纹识别工作台文档索引

本目录保存人工测试发布、模型资产和设备验收说明。源码包不包含真实掌纹图像、模板、密钥、原始板端日志或完整运行报告；这些只在受控设备目录中保存。

## 文档目录

| 文档 | 用途 |
| --- | --- |
| [01 - 模型评测与发布方案](01-model-evaluation-and-deployment-plan.md) | NPU/mixed-FP16 契约、profile 和人工测试范围 |
| [02 - 设备部署与功能验收](02-device-deployment-and-acceptance-report.md) | 手动启动、API、触摸屏、模板和摄像头检查 |
| [03 - 候选模型审计](03-model-candidate-audit.md) | 来源、许可、哈希和候选状态 |
| [04 - 发布与人工测试清单](04-release-checklist.md) | 冻结版本、部署、逐模型测试、回滚和问题记录 |
| `reports/candidates/`（板端/本地忽略目录） | 28 项候选的分任务测试报告和最新状态索引；运行时只暴露 6 项 active candidate |
| [evidence/README](evidence/README.md) | 可随源码发布的脱敏证据边界 |
| [合成 ROI API 冒烟摘要](evidence/manual-test-smoke-20260818.md) | 六模型接口/模板/重启预检；不替代触摸屏和摄像头验收 |
| [候选编排与板端测试摘要](evidence/candidate-campaign-20260910.md) | 28 项终态、六个 OM 候选 ACL smoke 和剩余门禁 |
| [28 项最终状态 JSON](evidence/candidate-final-status-20260911.json) | 机器可读的候选终态、阻断原因和 smoke/诊断分离 |
| [候选运行时工作台](evidence/candidate-runtime-workbench-20260911.md) | 六个已验证 OM 的独立任务化 NPU 实验入口 |
| [扩展模型族测试报告](evidence/extended-model-campaign-20260911.md) | ResNet/VGG/DenseNet/MobileNet/EfficientNet/ViT/Swin 及 ROI、分类器、掌静脉、生成模型和 Palm-ID 的分任务测试 |

## 发布边界

- `README.md` 只说明手动安装、启动、操作和常见错误。
- `production` profile 遵循完整生产准入门槛，当前默认 CCNet。
- `manual_test` profile 允许六个 NPU/mixed-FP16 模型进行人工功能测试；五个 CompNet 标记 `manual_test_pending=true`。
- 下载 OM 不等于稳定性验收通过。每个模型仍需人工记录上传识别、注册、模板查询、删除、摄像头和服务重启结果。
- 全候选测试按 embedding、classifier、ROI、掌静脉和 SDK/代码分轨；`audit_pass` 只表示静态审计通过，不表示 ACL、精度或质量通过；六个 OM 候选的 ACL smoke 结果在证据摘要中单独记录。
- `python -m tools.offline.candidate_campaign` 只生成研究审计报告；候选不会自动进入生产 registry 或工作台模型选择。
- 具体板端异常只作为通用故障类型写入文档；原始日志和历史报告不作为源码包内链目标。
- 六个 OM 的字节数和 SHA-256 见根目录 `om_manifest.json`；当前源码不自动下载或上传 HF，外部资产必须由操作者按授权和清单管理。
- 版本冻结期间不升级依赖、驱动、CANN 或应用版本。人工测试发现问题时先记录并回退，不在本版本内修复。
