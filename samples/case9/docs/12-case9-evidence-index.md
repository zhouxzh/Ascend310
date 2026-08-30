# Case9 证据索引

_本索引以 2026-08-30 的双板证据和 MindSpore 候选复现包为当前入口。8T 报告采集于 `.178`，同一块板当前地址为 `.90`；20T 当前地址为 `.95`，`.210` 仅是旧请求地址。缺口补测已产生 Qwen1.5/20T、TinyLlama/20T 与 DeepSeek/8T 原始报告；Qwen2.5 当前身份复核在 `.90` 通过（identity-only），在 `.95` 因当前工件缺失而阻断。_

---

## 📍 Canonical 文档

| 文档 | 用途 | 当前状态 |
| --- | --- | --- |
| [00-case9-current-runbook.md](00-case9-current-runbook.md) | 当前启动、端口、边界和回滚 | 当前入口 |
| [01-qwen25-dual-board-validation.md](01-qwen25-dual-board-validation.md) | B4/B1 门禁矩阵与完整 campaign | 当前双板证据 |
| [02-qwen25-reproducibility-and-sync.md](02-qwen25-reproducibility-and-sync.md) | 复现包、同步和换板流程 | 当前入口 |
| [03-case9-history-and-boundaries.md](03-case9-history-and-boundaries.md) | 历史候选、失败和暂停边界 | 当前入口 |
| [23-mindspore-chat-porting-plan.md](23-mindspore-chat-porting-plan.md) | Qwen1.5、TinyLlama、DeepSeek Profile 移植边界 | 当前候选计划 |
| [24-mindspore-chat-validation-record.md](24-mindspore-chat-validation-record.md) | MindSpore 候选板端验收、LPM 诊断和恢复证据 | 当前候选证据 |
| [25-chat-model-profile-runbook.md](25-chat-model-profile-runbook.md) | 单模型启动、切换、API 和回滚操作 | 当前候选运行手册 |
| [26-deepseek-20t-validation-20260830.md](26-deepseek-20t-validation-20260830.md) | 20T DeepSeek 隔离加载、API 机器门、性能对照和中文输出证据 | 最新 20T 实测（仍未准入） |
| [27-case9-dual-board-gap-completion-plan.md](27-case9-dual-board-gap-completion-plan.md) | 双板模型缺口、统一协议和完成判据 | 当前补测计划 |
| [28-case9-dual-board-gap-validation-record.md](28-case9-dual-board-gap-validation-record.md) | 缺口批次账本、已有数据和 not-run/blocked 矩阵 | Qwen1.5/20T、Tiny20、DeepSeek/8T 报告及 Qwen2.5 身份记录已归档 |
| [archive/20260827/README.md](archive/20260827/README.md) | 重构前 27 份报告原文索引 | 只读归档 |

当前 Qwen2.5 和 MindSpore 模型、日志和报告只保存在被 Git 忽略的复现目录；
MindSpore 候选包为 `repro/mindspore-chat-20260829/`，最近同步批次为
`mindspore-chat-final-20260830e`，清单 `392/392` 通过 SHA-256（当前管理项
`source=124`、`board8t=170`，旧 20T 管理项仍为 `0`，历史保留 `98`）。DeepSeek 新的
独立复现包为 `repro/deepseek-r1-20t-20260830/`，包含 `.95` 的模型、环境和 API 报告。
本轮缺口报告集中在 `repro/case9-dual-board-gap-20260830/`：Qwen1.5/20T 与
DeepSeek/8T 已完成机器验收；TinyLlama/20T 已生成报告但机器门失败。
仓库中的文档不会把
不存在的板端文件或未运行的门禁写成通过。

## 🧪 当前双板证据

| 板卡 | 工件 | 短批次报告 | 已支持的结论 | 不可推出 |
| --- | --- | --- | --- | --- |
| `192.168.1.90`（报告采集 `.178`） | B4 OM，SHA `f6650e52...1140eb8` | `reports/full-campaign/board8t/20260827T113500Z/acceptance.json`; `usage-perf/...124500Z...`; `reports/board8t/candidate/`; `repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/acceptance.json` | Qwen2.5 锁/descriptor、ACL smoke、JSON/SSE、长输出、10 轮稳定性；另有 DeepSeek/8T MindSpore 缺口批次 9/9 机器门、10 轮稳定性和 2+30 性能（总耗时 p50/p95 `3938.489/4008.768 ms`，吞吐 p50/p95 `0.510/0.517 token/s`） | 中文事实正确性和人工质量仍需单独说明；正式提升未执行；不声称存在 `20260827T130500Z-chain/` 目录 |
| `192.168.8.178` | 同一块 B4/8T 的旧采集地址 | 同上原始报告 provenance | 不作为当前连接入口 | 不应当当成第二块板 |
| `192.168.8.210` | B1 OM，SHA `6bca884f...6298609` | `reports/full-campaign/board20t/20260827T113500Z/acceptance.json`; `usage-perf/...124500Z...`; `...130500Z-chain-retry/`（历史 provenance） | 同上；dirty-base；这些路径属于旧地址采集批次，不代表当前连接状态 | 干净生产环境、正式提升 |
| `192.168.1.95`（当前 20T） | DeepSeek FP16 权重，SHA `706e1bfd...3419c`；Ascend310B1 | `repro/deepseek-r1-20t-20260830/reports/board20t/`；DeepSeek API `deepseek-api-full-20t-20260830T051523Z/`、`reopen-20260830T0536Z/`；Qwen1.5 缺口 `repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/acceptance.json`；TinyLlama 缺口 `repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/acceptance.json` | DeepSeek 原有 MindSpore/Ascend smoke、10 轮短稳定性、临时 API 9/9 和 2+30 已通过；Qwen1.5/20T 缺口批次 9/9 机器门、10 轮稳定性和 2+30 性能通过（总耗时 p50/p95 `1329.830/1440.076 ms`，吞吐 p50/p95 `1.505/1.619 token/s`）；TinyLlama/20T 缺口批次 8/9，32/48 token 长输出 UTF-8 失败，中文机器质量 7/10；三者共享 `base`，均为实验性 | 中文推理/事实质量与人工质量仍需单独说明；TinyLlama 保持 `blocked`；正式网关/UI 未启动，不能写成正式准入 |

完整批次参数为长输出 `8/16/24/32/48/64/80`、稳定性 10 轮、性能 2+30；
`usageperf` 报告提供 token/s。工程定性复核估计约 9/10 可理解，但硬件探测存在事实错误，
正式人工签字尚未完成，因此不能把可理解度等同于事实正确性。

Qwen2.5 历史复现包最近同步批次为 `20260829T012933Z`，schema 3 共 129 个条目。`.90` 已恢复可达，
7 个明确列出的候选日志/响应文件已进入 `reports/board8t/candidate/` 并完成本地 SHA-256
校验；旧批次的 `.210` raw 同步仍 pending，不能解释为当前 `.95` 不可达。当前双板缺口包
`repro/case9-dual-board-gap-20260830/` 的 `bundle-manifest.json` 与 `SHA256SUMS.txt`
已完成 `197/197` 文件校验；完整批次和
`usageperf` 的四份 JSON 继续作为 `local-verified` 条目记录。人工中文质量签字仍为 `not-run`，工程定性复核
不等于正式质量门通过。

## 🔐 证据标签

| 标签 | 含义 | 不代表 |
| --- | --- | --- |
| `artifact_verified` | 字节、来源、SHA-256 和 lock 一致 | 可执行或质量通过 |
| `descriptor_verified` | OM descriptor 的名称、顺序、shape、dtype、byte size 已记录 | ACL 推理通过 |
| `acl_smoke_passed` | 至少一次真实 ACL execute | 长输出或稳定性 |
| `api_passed` | JSON/SSE HTTP 契约通过 | 网关/UI或设备闭环 |
| `quality_reviewed` | 固定探测集完成机器检查和工程定性复核 | 正式人工签字或其他板/OM 自动继承 |
| `formally_promoted` | 指定板卡、工件、环境和全部门禁通过 | 新板自动继承 |
| `not-run` / `blocked` | 尚未执行或被前置条件阻断 | 任意正向能力 |

## 🧱 工件身份

| 工件 | bytes | SHA-256 |
| --- | ---: | --- |
| 静态 KV ONNX | `1,261,082,122` | `b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e` |
| B4 OM | `1,266,010,586` | `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| B1 OM | `1,266,009,438` | `6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609` |
| tokenizer | `7,031,645` | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| source checkpoint | `988,097,824` | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |

OM 必须绑定 `Ascend310B4` 或 `Ascend310B1` 的 ATC provenance。跨 SoC 短 smoke
只属于 compatibility experiment；不能替换目标 SoC 的正式工件。

## 🧭 依赖和硬件边界

- Qwen2.5 ACL 路线和 ACL/OM 候选不在板端安装或运行 Torch、Torch-NPU、Torchaudio、Transformers、ONNX Runtime、vLLM、MindIE 或未经审核 OPP。
- MindSpore Profile 路线仅复用 `.90` 已存在的 `base` 环境；不删除或升级其既有包，并将该环境明确标记为 `experimental_dirty_base`，适配代码不导入 Torch 或其他推理框架。
- 不把 CPU、云端或其他模型作为 NPU 失败时的自动回退。
- 不把 `Health: Alarm` 单独当作失败或通过；保存真实 `npu-smi` 快照和具体错误。
- 不将 B4 与 B1 的性能、内存、稳定性或中文质量合并排名。
- 不把文本 API、音频 I/O 或网关 stub 当作 XiaoZhi 设备语音闭环。

## 📚 历史入口

旧的 TinyLlama、Qwen1.5、full-context Qwen2.5、last-logits、llama.cpp、音频和 XiaoZhi
记录全部保留在 [历史边界](03-case9-history-and-boundaries.md) 与
[`archive/20260827/`](archive/20260827/README.md)。它们用于解释决策和失败，不会改变
当前双板门禁状态。


## 🧭 双板缺口矩阵（2026-08-30）

本节只汇总当前“已测”和“尚缺”的组合，不新增未经报告支持的结果。当前地址为
8T `192.168.1.90`（历史采集 `.178`）和 20T `192.168.1.95`（历史请求
`.210`）。

| 路线 | 模型 | 8T B4 / `.90` | 20T B1 / `.95` |
| --- | --- | --- | --- |
| ONNX→OM→ACL | Qwen2.5 Static-KV 1024 | 已有完整 ACL/API/性能证据；当前身份复核 `passed`（identity-only） | 有历史 B1 完整证据；当前身份复核 `blocked`（当前工件缺失） |
| MindNLP/MindSpore | Qwen1.5-0.5B-Chat | 已有完整机器批次，`experimental_dirty_base` | 缺口批次 9/9 机器门；总耗时 p50/p95 `1329.830/1440.076 ms`，吞吐 p50/p95 `1.505/1.619 token/s`；人工质量待审，`experimental_dirty_base` |
| MindNLP/MindSpore | TinyLlama-1.1B-Chat | 已测但长输出含 `U+FFFD`，`blocked` | 缺口批次 8/9；32/48 token 长输出 UTF-8 失败、中文机器质量 7/10；总耗时 p50/p95 `1939.938/2003.937 ms`，吞吐 p50/p95 `1.031/1.044 token/s`；保持 `blocked` |
| MindNLP/MindSpore | DeepSeek-R1-Distill-Qwen-1.5B | 缺口批次 9/9 机器门；总耗时 p50/p95 `3938.489/4008.768 ms`，吞吐 p50/p95 `0.510/0.517 token/s`；人工质量待审，`experimental_dirty_base` | 隔离 API 机器门通过；中文质量未通过，`blocked` |

机器门与质量门始终分开。MindNLP 的已测数字来自不同批次和 prompt/token 口径，
除明确注明的 DeepSeek 同协议 8T/20T 对照外，不应跨模型直接排名。缺口计划和字段
要求见 [27](27-case9-dual-board-gap-completion-plan.md)，逐组合账本见
[28](28-case9-dual-board-gap-validation-record.md)。任何组合若板端不可达或加载失败，
必须附原始错误后标为 `blocked`，不能填估算速度或继承另一板结果。TinyLlama/20T
已有原始报告但机器门失败，保持 `blocked`，不能因性能数据存在而解除。
