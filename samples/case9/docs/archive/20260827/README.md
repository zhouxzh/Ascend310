# Case9 2026-08-27 前历史报告索引

_本目录保存 canonical 文档重构前的原文快照，用于追溯当时的命令、链接、失败原因和板端证据。_

---

## 📋 使用边界

归档中的结论绑定原始板卡、IP、CANN、Python、OM、源码哈希、端口和报告时间。它们不表示
当前服务正在运行，也不能自动提升 `192.168.8.178` 或 `192.168.8.210` 的状态。

当前可执行入口与新双板记录位于父目录：

- [当前运行手册](../../00-case9-current-runbook.md)
- [双板验证记录](../../01-qwen25-dual-board-validation.md)
- [复现包与同步](../../02-qwen25-reproducibility-and-sync.md)
- [历史边界摘要](../../03-case9-history-and-boundaries.md)

## 🗂️ 原文分组

| 范围 | 历史文件 |
| --- | --- |
| 网关与本地聊天 | `00-xiaozhi-gateway-architecture.md`、`01-board-gateway-acceptance.md`、`02-local-chat-architecture.md`、`03-local-chat-validation.md`、`04-xiaozhi-phase2-plan.md`、`10-text-chat-ui.md` |
| 模型研究与 ACL/OM | `05-llm-backend-research-and-decision.md`、`06-acl-om-llm-deployment-plan.md`、`07-acl-om-validation-record.md` |
| TinyLlama | `08-tinyllama-acl-om-porting-plan.md`、`09-tinyllama-acl-om-validation-record.md`、`13-tinyllama-complete-validation-record.md` |
| 旧 Qwen2.5 与 `.90` | `11-code-review-optimization-and-board-192-168-1-90.md`、`14-historical-test-results.md`、`15-qwen25-static-onnx-validation-record.md`、`16-qwen25-optimization-research-and-last-logits-validation.md`、`17-qwen25-static-kv-1024-porting-plan.md`、`18-qwen25-static-kv-1024-validation-record.md` |
| 同步与跨板实验 | `19-qwen25-om-local-copy-record.md`、`20-qwen25-kv1024-reproducibility-bundle.md`、`21-qwen25-20t-performance-comparison.md`、`22-qwen25-cross-board-om-validation.md` |

旧的 `00` 至 `03` 文档也保留在本目录，作为重构前快照；阅读当前状态时请优先使用父目录
的 canonical 文档。
