# Case9 证据索引

_本索引以 2026-09-07 的双板证据和 MindSpore 候选复现包为当前入口。8T 报告采集于历史地址 `.178`/`.11.14`，当前地址为 `192.168.1.90`；这些地址属于同一块板。20T 当前地址为 `.95`，`.210` 仅为旧请求地址。缺口补测已产生 Qwen1.5/20T、TinyLlama/20T 与 DeepSeek/8T 原始报告；Qwen2.5-0.5B 在当前地址完成了六文件校验和严格 placement 复测，但严格门仍阻断；Qwen2.5-1.5B 七个文件已同步并校验，重启后的正确 CANN 诊断在生成阶段发生 `Alloc failed`，placement/API/质量/性能仍未运行，Profile 继续 `blocked`。2026-09-06 重启后的短探针再次观察到 LPM fault，严格 G0 又记录到 MindNLP 缺失和版本不匹配；同日 DeepSeek 的 `context1024` 配置级变体完成了单 token `diagnostic_passed`，但仍为 `context-only`，不改变 canonical Profile 状态；2026-09-07 新增 MindSpore Lite 2.5.0 独立环境和 Ascend 构建烟测，详见 [Lite 构建记录](45-mindspore-lite-2.5-mobilenetv2-ascend-build-smoke-20260907.md)。_

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
| [29-tinyllama-huggingface-publication.md](29-tinyllama-huggingface-publication.md) | TinyLlama 发布工件、哈希和已知问题 | 已发布为历史实验工件；中文和长输出门禁仍失败 |
| [30-orange-pi-mindspore-online-inventory.md](30-orange-pi-mindspore-online-inventory.md) | `192.168.3.10` Online 示例、环境和 Case9 适配映射 | 4 个文本示例（含 MiniCPM3）；Janus-Pro 保留为多模态后续 |
| [31-mindspore-llm-candidate-inventory.md](31-mindspore-llm-candidate-inventory.md) | HF/Modelers/官方 Orange Pi 候选、证据等级和条件候选 | 首批 8 个原生候选 + 9 个条件条目 |
| [32-mindspore-llm-candidate-validation-plan.md](32-mindspore-llm-candidate-validation-plan.md) | 双板执行顺序、环境边界和 G0-G8 门禁 | 新候选统一验收计划 |
| [33-mindspore-llm-candidate-validation-record.md](33-mindspore-llm-candidate-validation-record.md) | 逐板状态、报告模板和候选 UI 判定 | 持续追加的实测账本 |
| [34-qwen25-0.5b-mindspore-8t-loader-smoke-20260903.md](34-qwen25-0.5b-mindspore-8t-loader-smoke-20260903.md) | Qwen2.5-0.5B 固定工件、loader 和严格 placement 复测 | 8T 严格门阻断；context-only 仅历史旁证 |
| [35-qwen3-loader-compatibility-check-20260903.md](35-qwen3-loader-compatibility-check-20260903.md) | 当前 MindNLP Qwen3 loader 能力检查 | 8T loader `blocked` |
| [36-qwen25-1.5b-8t-preflight-20260904.md](36-qwen25-1.5b-8t-preflight-20260904.md) | Qwen2.5-1.5B 固定 revision、小文件哈希、配置和 Qwen2 loader 预检（历史阶段） | 历史配置门 `not-run`；后续完整权重内存门见 [docs/37](37-qwen25-1.5b-8t-memory-gate-20260904.md) |
| [37-qwen25-1.5b-8t-memory-gate-20260904.md](37-qwen25-1.5b-8t-memory-gate-20260904.md) | Qwen2.5-1.5B 完整权重内存门、环境污染复盘和重启后正确 CANN 复测 | 8T `blocked`；重启批次生成期 `Alloc failed`，API/质量/性能仍 `not-run` |
| [38-mindnlp-loader-capability-20260904.md](38-mindnlp-loader-capability-20260904.md) | 历史 `.11.14` 采集时的 MindNLP 类/架构只读探针 | Qwen3 loader 缺失；MiniCPM3 类可见但 20T 工件和运行仍 `not-run` |
| [39-qwen25-05b-context-only-20260904.md](39-qwen25-05b-context-only-20260904.md) | Qwen2.5-0.5B 当前地址 context-only 加载和短生成 | 加载/短生成观察完成；严格 placement、API、质量和性能仍 `blocked`/`not-run` |
| [40-8t-mindspore-llm-strict-test-plan.md](40-8t-mindspore-llm-strict-test-plan.md) | 8T 当前地址的严格 G0-G8 测试边界和停止条件 | 当前执行计划；不绕过设备/环境阻断 |
| [41-8t-mindspore-llm-strict-validation-record.md](41-8t-mindspore-llm-strict-validation-record.md) | 8T 重启、G0 环境、LPM fault 和门禁账本 | 2026-09-06 G0 `blocked`；MindNLP 缺失、版本不匹配、无 worker |
| [42-mindspore-candidate-search-record.md](42-mindspore-candidate-search-record.md) | MindSpore 聊天候选搜索与证据分级 | 条件候选不下载、不启动 |
| [43-mindspore-external-model-empirical-probe-20260906.md](43-mindspore-external-model-empirical-probe-20260906.md) | 用户 site、MindNLP loader 与外部 Qwen1.5 板端 smoke | `observed-pass`（context-only），不替代正式准入 |
| [44-mindspore-external-model-followup-20260906.md](44-mindspore-external-model-followup-20260906.md) | 当前地址 Qwen2.5/TinyLlama/DeepSeek 续测、资源故障与启动边界修复 | Qwen2.5 context-only；Tiny 生成内存失败；DeepSeek context1024 diagnostic passed、canonical OOM/blocked；API/长测未运行 |
| [45-mindspore-lite-2.5-mobilenetv2-ascend-build-smoke-20260907.md](45-mindspore-lite-2.5-mobilenetv2-ascend-build-smoke-20260907.md) | 独立 Lite 2.5.0/2.2.11 aarch64/cp39 环境、Ascend Context、官方 MobileNetV2 与最小 AddNet 烟测 | 2.5.0 导入/Context `observed-pass`；MindIR v2 Ascend `build_from_file` exit 139；2.2.11 明确拒绝 MindIR v2；`.ms` 返回 `Fail to support`；`predict`、placement、LLM 和性能 `not-run` |
| [archive/20260827/README.md](archive/20260827/README.md) | 重构前 27 份报告原文索引 | 只读归档 |

当前 Qwen2.5 和 MindSpore 模型、日志和报告只保存在被 Git 忽略的复现目录；Qwen2.5-0.5B
历史 NPU 活动旁证位于
`repro/mindspore-chat-20260903/reports/board8t/qwen2.5-0.5b-mindspore/qwen25-npu-evidence-20260903T115754Z/`，
当前地址的严格复测及逐文件 `SHA256SUMS.txt` 位于
`repro/mindspore-chat-20260904/reports/board8t/qwen2.5-0.5b-strict-placement-20260904-v2/`。
两者都不替代严格 placement/API 门，前者只作为历史活动旁证。
MindSpore 候选包为 `repro/mindspore-chat-20260829/`，最近同步批次为
`mindspore-chat-final-20260830e`，清单 `392/392` 通过 SHA-256（当前管理项
`source=124`、`board8t=170`，旧 20T 管理项仍为 `0`，历史保留 `98`）。DeepSeek 新的
独立复现包为 `repro/deepseek-r1-20t-20260830/`，包含 `.95` 的模型、环境和 API 报告。
本轮缺口报告集中在 `repro/case9-dual-board-gap-20260830/`：Qwen1.5/20T 与
DeepSeek/8T 已完成机器验收；TinyLlama/20T 已生成报告但机器门失败。
仓库中的文档不会把
不存在的板端文件或未运行的门禁写成通过。

当前地址 `192.168.1.90` 的只读 G0/G1 复核证据（同一块
Ascend310B4/8T，非新板、非性能重测）如下。报告文件本身已在控制机复现目录中做
SHA-256 校验；它们只证明工件和环境 provenance，不替代模型加载、API、质量或性能门：

| Profile | 运行批次 | 工件报告（SHA-256） | 环境报告（SHA-256） | 范围 |
| --- | --- | --- | --- | --- |
| `tinyllama-1.1b-mindspore` | `tinyllama-g0g1-20260904T065034Z` | [`tinyllama-g0g1-20260904T065034Z-artifacts.json`](../repro/mindspore-chat-20260904/reports/board8t/artifacts/tinyllama-g0g1-20260904T065034Z-artifacts.json) `a3d3938c092c28ff46088c82dabf5f63da244f9ef8bdf2331ccab520512b454a` | [`tinyllama-g0g1-20260904T065034Z-environment.json`](../repro/mindspore-chat-20260904/reports/board8t/environment/tinyllama-g0g1-20260904T065034Z-environment.json) `d26b7f2ca6b36791c5941f5cc15d10b974fa433decf1e37ff3828d2c65718fc5` | 7/7 工件；profile 已阻断；未加载模型/启动服务 |
| `deepseek-r1-qwen-1.5b-mindspore` | `deepseek-g0g1-b4-20260904T065856Z` | [`deepseek-g0g1-b4-20260904T065856Z-artifacts.json`](../repro/mindspore-chat-20260904/reports/board8t/artifacts/deepseek-g0g1-b4-20260904T065856Z-artifacts.json) `2f0dc0d59e7e7e92689ea8967ac0fb7b2364db4abc5c66b71fe5166b797db625` | [`deepseek-g0g1-b4-20260904T065856Z-environment.json`](../repro/mindspore-chat-20260904/reports/board8t/environment/deepseek-g0g1-b4-20260904T065856Z-environment.json) `1019436924d606c5f288ebaebf803face0e8a2e1c9517694a8c7495480f8f2ee` | 5/5 工件；修正为 B4 观测；未加载模型/启动服务 |

Qwen2.5-0.5B 的补充短生成目录为
`repro/mindspore-chat-20260904/reports/board8t/qwen2.5-0.5b-mindspore/qwen25-05-context-only-clean-20260904T073649Z/`；
`report.json` SHA-256 为
`16e5bf950a25f92766ea6d8d7f28062ba0fba507373b9b3d25ea9bbf25a0afbd`。这是显式
`--context-only` 诊断，不是 G2/API 或模型准入证据，完整说明见
[docs/39](39-qwen25-05b-context-only-20260904.md)。

当前地址最新的 MindNLP loader 只读探针位于
`repro/mindspore-chat-20260904/reports/board8t/loader-capability-20260904T082903Z/`；
`probe.json` SHA-256 为
`8cda8f579ea03381574d755840c310e36989412ab6d9fbb74872b7cc6acbb0e5`。它只证明类/映射
可见性，不证明模型加载或 NPU 推理。

同一地址的最新只读健康快照位于
`repro/mindspore-chat-20260904/reports/board8t/environment/post-qwen25-15-reboot-20260904T101948Z/`；
`npu-smi-info.txt` SHA-256 为
`2053610fed18fcd02a7be89a508db70bddd2c1483cccfd0d5e6e93dc781b02d3`，显示
`3005/15610 MB`；`dmesg-lpm-fault-tail.txt` SHA-256 为
`0618666c423440c503570a9c2804392245fd74e3757f6133eb11dadc60627687`，记录持续的
`DRV_LPM_FAULT 0x80E3A203`。该快照期间没有 Case9 进程或端口；此前 1.5B 诊断的峰值
`14993/15610 MB` 和 `Alloc failed` 见验收记录 §7.11，因此重模型加载和长压测继续暂停。

2026-09-06 当前地址的 DeepSeek `context1024` 配置级复核报告保留在板端：
`/home/HwHiAiUser/case9-mindspore-chat/run/mindspore-chat/exploratory/deepseek-context1024-exploratory-20260906T/`。
该独立进程 5/5 工件校验、加载 `58.292903 s`、一 token 生成 `14.023559 s`（输出
`您好`），报告 SHA-256 为
`3d20d659eaaa0ba6ff3583f3bfebf0aaca0b98e6dc0d0fba6eee5fee5688fd33`，运行日志 SHA-256 为
`014b6b29925b9e298f56048ed3b9ec45a53e897a9a5d119d41aeaba139e0be38`。
这是 `context-only` 诊断旁证，不是显式 placement、API、性能或准入结果；权重未改，
不能推出 canonical `131072` 上下文配置可加载。

DeepSeek 早先带 B1 目标元数据的 G0/G1 文件被标为排除，不作为当前 B4 证据；模型源目录
通过硬链接保留，未复制或修改源文件。详细字段和环境快照见
[docs/33-mindspore-llm-candidate-validation-record.md](33-mindspore-llm-candidate-validation-record.md)。

## 🧪 当前双板证据

| 板卡 | 工件 | 短批次报告 | 已支持的结论 | 不可推出 |
| --- | --- | --- | --- | --- |
| `192.168.1.90`（历史 `.11.14`；报告采集 `.178`） | B4 OM，SHA `f6650e52...1140eb8` | `reports/full-campaign/board8t/20260827T113500Z/acceptance.json`; `usage-perf/...124500Z...`; `reports/board8t/candidate/`; `repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/acceptance.json` | Qwen2.5 锁/descriptor、ACL smoke、JSON/SSE、长输出、10 轮稳定性；另有 DeepSeek/8T MindSpore 缺口批次 9/9 机器门、10 轮稳定性和 2+30 性能（总耗时 p50/p95 `3938.489/4008.768 ms`，吞吐 p50/p95 `0.510/0.517 token/s`） | 中文事实正确性和人工质量仍需单独说明；正式提升未执行；不声称存在 `20260827T130500Z-chain/` 目录 |
| `192.168.1.90` Qwen2.5 MindSpore 诊断（历史批次 `.11.14`） | Qwen2.5-0.5B Safetensors，SHA `fdf756fa...fb7fe`；严格复测报告 SHA `9f979283...e359c9` | `repro/mindspore-chat-20260904/reports/board8t/qwen2.5-0.5b-strict-placement-20260904-v2/`；[docs/34](34-qwen25-0.5b-mindspore-8t-loader-smoke-20260903.md) | 六文件、CANN `acl/te/hccl`、expected/actual B4 通过；运行中设备内存约 `5705 -> 12520/15610 MB` | `ProviderUnavailable: ... no explicit Ascend placement evidence`，exit 1，generation/API/质量/性能未运行；历史 context-only 旁证不构成准入 |
| `192.168.8.178` | 同一块 B4/8T 的旧采集地址 | 同上原始报告 provenance | 不作为当前连接入口 | 不应当当成第二块板 |
| `192.168.8.210` | B1 OM，SHA `6bca884f...6298609` | `reports/full-campaign/board20t/20260827T113500Z/acceptance.json`; `usage-perf/...124500Z...`; `...130500Z-chain-retry/`（历史 provenance） | 同上；dirty-base；这些路径属于旧地址采集批次，不代表当前连接状态 | 干净生产环境、正式提升 |
| `192.168.1.95`（当前 20T） | DeepSeek FP16 权重，SHA `706e1bfd...3419c`；Ascend310B1 | `repro/deepseek-r1-20t-20260830/reports/board20t/`；DeepSeek API `deepseek-api-full-20t-20260830T051523Z/`、`reopen-20260830T0536Z/`；Qwen1.5 缺口 `repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/acceptance.json`；TinyLlama 缺口 `repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/acceptance.json` | DeepSeek 原有 MindSpore/Ascend smoke、10 轮短稳定性、临时 API 9/9 和 2+30 已通过；Qwen1.5/20T 缺口批次 9/9 机器门、10 轮稳定性和 2+30 性能通过（总耗时 p50/p95 `1329.830/1440.076 ms`，吞吐 p50/p95 `1.505/1.619 token/s`）；TinyLlama/20T 缺口批次 8/9，32/48 token 长输出 UTF-8 失败，中文机器质量 7/10；三者共享 `base`，均为实验性 | 中文推理/事实质量与人工质量仍需单独说明；TinyLlama 保持 `blocked`；正式网关/UI 未启动，不能写成正式准入 |

完整批次参数为长输出 `8/16/24/32/48/64/80`、稳定性 10 轮、性能 2+30；
`usageperf` 报告提供 token/s。工程定性复核估计约 9/10 可理解，但硬件探测存在事实错误，
正式人工签字尚未完成，因此不能把可理解度等同于事实正确性。

Qwen2.5 历史复现包最近同步批次为 `20260829T012933Z`，schema 3 共 129 个条目。当前 8T `192.168.1.90`
（历史报告地址 `.11.14`/`.178`）已恢复可达，
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
8T `192.168.1.90`（历史地址 `.11.14`，采集 `.178`）和 20T `192.168.1.95`（历史请求
`.210`）。

| 路线 | 模型 | 8T B4 / `.90`（历史 `.11.14`） | 20T B1 / `.95` |
| --- | --- | --- | --- |
| ONNX→OM→ACL | Qwen2.5 Static-KV 1024 | 已有完整 ACL/API/性能证据；当前身份复核 `passed`（identity-only） | 有历史 B1 完整证据；当前身份复核 `blocked`（当前工件缺失） |
| MindNLP/MindSpore | Qwen1.5-0.5B-Chat | 历史报表 9/9，但严格身份门 8/9 待补证，当前 `blocked` | 缺口批次 9/9 机器门；总耗时 p50/p95 `1329.830/1440.076 ms`，吞吐 p50/p95 `1.505/1.619 token/s`；人工质量待审，`experimental_dirty_base` |
| MindNLP/MindSpore | TinyLlama-1.1B-Chat | 已测但长输出含 `U+FFFD`，`blocked` | 缺口批次 8/9；32/48 token 长输出 UTF-8 失败、中文机器质量 7/10；总耗时 p50/p95 `1939.938/2003.937 ms`，吞吐 p50/p95 `1.031/1.044 token/s`；保持 `blocked` |
| MindNLP/MindSpore | DeepSeek-R1-Distill-Qwen-1.5B | 既有缺口批次 9/9 机器门；总耗时 p50/p95 `3938.489/4008.768 ms`，吞吐 p50/p95 `0.510/0.517 token/s`；另有 2026-09-06 `context1024` 复核 `diagnostic_passed`（加载 `58.292903 s`、一 token `14.023559 s`，context-only）；人工质量待审，`experimental_dirty_base` | 隔离 API 机器门通过；中文质量未通过，`blocked` |
| MindNLP/MindSpore | Qwen2.5-1.5B-Instruct | `blocked`：七个文件已校验；重启后的正确 CANN 诊断加载通过但生成期 `Alloc failed, size:481482240`，placement/API/长输出/稳定性/质量/性能仍 `not-run` | `not-run`；未执行 |

机器门与质量门始终分开。MindNLP 的已测数字来自不同批次和 prompt/token 口径，
除明确注明的 DeepSeek 同协议 8T/20T 对照外，不应跨模型直接排名。缺口计划和字段
要求见 [27](27-case9-dual-board-gap-completion-plan.md)，逐组合账本见
[28](28-case9-dual-board-gap-validation-record.md)。任何组合若板端不可达或加载失败，
必须附原始错误后标为 `blocked`，不能填估算速度或继承另一板结果。TinyLlama/20T
已有原始报告但机器门失败，保持 `blocked`，不能因性能数据存在而解除。
