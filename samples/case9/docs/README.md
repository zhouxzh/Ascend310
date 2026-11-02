# 文档索引

| 文档 | 用途 |
| --- | --- |
| [00-release-overview.md](00-release-overview.md) | 范围、架构、支持矩阵与非目标 |
| [01-20t-deployment.md](01-20t-deployment.md) | 板端部署、启动、健康检查与回滚 |
| [02-model-artifacts.md](02-model-artifacts.md) | 锁定产物、镜像下载、离线导入与校验 |
| [03-api-and-security.md](03-api-and-security.md) | OpenAI API、端口、身份认证与可信局域网边界 |
| [04-validation-and-limitations.md](04-validation-and-limitations.md) | 实测基线、发布门禁与限制 |
| [05-extension-guide.md](05-extension-guide.md) | 提供方、配置档、产物与准入规则 |
| [06-release-process.md](06-release-process.md) | 版本管理、验证、审查与回滚检查清单 |
| [07-teaching-model-catalog.md](07-teaching-model-catalog.md) | 模型技术选型、已观测结果与质量说明 |
| [08-board-voice-terminal-plan.md](08-board-voice-terminal-plan.md) | 规划中的板端/浏览器语音模式、USB音频、HTTPS、VAD与ASR/TTS边界，以及尚未测试的验收门禁 |
| [09-troubleshooting.md](09-troubleshooting.md) | 准入、分词器/模板、回答质量与USB音频故障的统一问题台账 |
| [10-8t-static-kv-release.md](10-8t-static-kv-release.md) | 正式Ascend310B4静态KV发布、公开产物锁、生命周期与验收 |
| [11-quantized-onnx-screening.md](11-quantized-onnx-screening.md) | 公开量化ONNX契约，以及两个310B档位上的ATC/PyACL转换筛选 |
| [12-q4f16-onnx-migration-and-decode-rate.md](12-q4f16-onnx-migration-and-decode-rate.md) | q4f16图反量化、B4/B1转换、数值门禁与FP16解码速率比较 |
| [13-review.md](13-review.md) | 范围覆盖、模型配置、音频控制面与发布审查 |
| [14-20t-b1-static-kv-token-ceiling.md](14-20t-b1-static-kv-token-ceiling.md) | 当前20T B1固定KV请求上限、强制解码、内存与剩余稳定性门禁 |
| [15-8t-16gb-static-kv-token-ceiling.md](15-8t-16gb-static-kv-token-ceiling.md) | 独立的16GB 8T B4强制解码及RAM/NPU内存证据 |
| [16-8t-16gb-mindspore-validation.md](16-8t-16gb-mindspore-validation.md) | 独立的16GB 8T MindSpore环境、模型加载、生成与内存门禁 |
| [17-8t-16gb-model-validation-matrix.md](17-8t-16gb-model-validation-matrix.md) | 16GB 8T MindSpore候选矩阵、已测试预算与失败边界 |
| [archive/](archive/) | 历史实验；不属于当前发布能力 |
