# Case9 证据索引与边界

## 文档用途

本文是 `docs/00` 至当前编号文档的证据入口。它只索引已经记录的检查和板端报告，
不把计划、静态检查、协议冒烟或模型下载完整性当作真实 NPU 质量结果。模型文件、
ONNX/OM、录音、运行日志和板端报告默认只保留在开发板；经用户明确要求同步的
Qwen2.5 OM 及最小 provenance 副本例外记录在 `docs/19`，仍不进入 Git。

本索引的审计日期为 2026-08-27。凡是没有原始命令、退出码、工件哈希和报告路径的
项目，均标为 `not-run`、`observed-only` 或 `blocked`，不推断为通过。

## 证据导航

| 编号 | 文档 | 证据范围 |
| --- | --- | --- |
| `00` | [小智网关架构](00-xiaozhi-gateway-architecture.md) | 服务边界、OpenAI 协议和小智接入前置条件 |
| `01` | [网关板端验收](01-board-gateway-acceptance.md) | 受控上游的网关 JSON/SSE 协议，不含真实 LLM |
| `02` | [本地聊天架构](02-local-chat-architecture.md) | 浏览器、网关、音频和模型的运行边界 |
| `03` | [本地聊天验证](03-local-chat-validation.md) | 早期 `.178` 音频、ASR/TTS、Qwen 和 llama.cpp 结果 |
| `04` | [小智第二阶段计划](04-xiaozhi-phase2-plan.md) | 未安装的小智服务端和设备验收边界 |
| `05` | [LLM 后端研究决策](05-llm-backend-research-and-decision.md) | 候选后端比较和 fail-closed 决策 |
| `06` | [ACL/OM 部署计划](06-acl-om-llm-deployment-plan.md) | 旧 Qwen1.5 ACL/OM 流程（历史候选） |
| `07` | [旧 Qwen ACL/OM 验证](07-acl-om-validation-record.md) | Qwen1.5 ONNX 契约阻断证据 |
| `08` | [TinyLlama 移植计划](08-tinyllama-acl-om-porting-plan.md) | TinyLlama 预编译 OM 的实施门禁 |
| `09` | [TinyLlama 验证](09-tinyllama-acl-om-validation-record.md) | `.178` 的完整 ACL/NPU/API 报告 |
| `10` | [文字聊天 UI](10-text-chat-ui.md) | 文字页面和历史页面冒烟 |
| `11` | [代码审核与 `.90` 复核](11-code-review-optimization-and-board-192-168-1-90.md) | `.90` 的隔离环境、API/UI 和回归结果 |
| `12` | 本文 | 跨文档证据索引、身份和哈希边界 |
| `13` | [TinyLlama 完整验证归档](13-tinyllama-complete-validation-record.md) | 两块板的统一状态表和最终判定 |
| `14` | [历史测试结果](14-historical-test-results.md) | 按时间和候选归档的通过/阻断/未执行项 |
| `15` | [Qwen2.5 静态 ONNX 验证](15-qwen25-static-onnx-validation-record.md) | `.90` 的外部导出、ATC、OM descriptor、ACL、API、隔离网关和文字 UI 证据；正式切换/质量按实际报告区分 |
| `16` | [Qwen2.5 优化案例与 last-logits 验证](16-qwen25-optimization-research-and-last-logits-validation.md) | GitHub/Hugging Face 案例核查、last-logits 优化图、8083 隔离 ACL/NPU/API 对照和后续静态 KV 边界 |
| `17` | [Qwen2.5 静态 KV 1024 移植计划](17-qwen25-static-kv-1024-porting-plan.md) | FP32 静态 KV decode 候选定义、网关限制、隔离验证和正式提升边界 |
| `18` | [Qwen2.5 静态 KV 1024 验证记录](18-qwen25-static-kv-1024-validation-record.md) | 控制机与板端 ONNX/ATC/OM/ACL/NPU/API/UI 证据；中文 8/10、性能通过、正式入口已提升 |
| `19` | [Qwen2.5 OM 本地副本记录](19-qwen25-om-local-copy-record.md) | 用户授权的 OM 回传、断点续传、哈希复核和本地运行边界 |
| `20` | [Qwen2.5 复现包与更换开发板流程](20-qwen25-kv1024-reproducibility-bundle.md) | 模型、ONNX、OM、源码、环境锁、全量哈希和新板重复验收步骤 |
| `21` | [Qwen2.5 20T 性能对比记录](21-qwen25-20t-performance-comparison.md) | `192.168.8.210` 的 Ascend310B1/20T 静态 KV ATC、ACL、JSON/SSE 和同协议测速；dirty-base 实验性结果，未提升正式入口 |
| `22` | [Qwen2.5 同一 OM 跨板验证](22-qwen25-cross-board-om-validation.md) | 同一 B4/B1 OM 在 8T 与 20T 的交叉加载、JSON/SSE 对照及兼容性边界；实验性，不替代目标 SoC ATC |

## 板卡身份

`.178` 和 `.90` 是先后使用的不同开发板。两块板都报告为
`Ascend310B4 / 8T`，但 IP、进程、环境、设备状态和报告目录不同；除非报告明确写出
同一工件和同一命令，否则不能合并为一套性能、稳定性或质量数据。

| 证据集 | 地址和时期 | 设备/软件 | 允许支持的结论 |
| --- | --- | --- | --- |
| 历史板 A | `192.168.8.178`，主要为 2026-08-21 | `aarch64`，Ascend310B4/8T，CANN toolkit `8.0.0`，`case9-acl-om` Python `3.9.25` | TinyLlama OM 的 descriptor、真实 ACL execute、NPU 生成、8080 JSON/SSE、7861 网关和 10 轮观察；中文失败和资源增长必须保留 |
| 当前板 B | `192.168.1.90`，主要为 2026-08-22 | `aarch64`，Ascend310B4/8T，CANN `8.0.0`，专用 Python `3.9.16` 环境 | 隔离 TinyLlama 8081、网关 7861、文字页面 7863 的协议/ACL smoke；中文质量、10 轮稳定性、音频和小智仍未完成 |
| 当前板 B/Qwen 批次 | `192.168.1.90`，2026-08-23 批次 | `aarch64`，Ascend310B4/8T，CANN `8.0.0`，`case9-acl-om` | 历史 2048 full-context 的 ATC、OM descriptor、ACL 生成和隔离 API/UI；保留为性能基线，不作为当前正式模型 |
| 当前板 B/Qwen 静态 KV | `192.168.1.90`，2026-08-23 执行批次 | `case9-qwen25-kv1024`；Ascend310B4/8T、CANN `8.0.0` | ONNX 原子传输、ATC、OM descriptor、ACL/NPU、8084/7867/7868 隔离链路、中文 8/10、性能改善 48.79%/48.70%，并已提升到正式 8080/7861/7865 |
| 替换板 C/Qwen 静态 KV | `192.168.8.178`，2026-08-27 批次 | `case9-qwen25-kv1024-20260825`；Ascend310B4/8T、CANN `8.0.0`、Python `3.9.25` | 完整复现包 47 项哈希、环境检查、静态 ONNX 契约、ACL/NPU smoke、候选 8084 JSON/SSE 通过；正式网关/UI和中文质量尚未重新验收 |
| 20T 板 D/Qwen 静态 KV | `192.168.8.210`，2026-08-27 测试批次 | `Ascend310B1/20T`、CANN `8.0.0`、Python `3.9.2`；SSH 已恢复 | B1 专用 OM、descriptor、ACL smoke、8084 JSON/SSE 和固定 1+5 测速通过；base 仍含既有禁止包，使用用户 overlay 与显式 dirty-base override，属实验性结果 |

两块板的 `npu-smi` 都记录了 `Health: Alarm`。项目规则只把它作为诊断信息，不能单独
阻断测试，也不能因为忽略它就虚构通过结果。每块板必须分别保存 `npu-smi` 快照、
进程 PID、CANN 版本和原始报告。

## TinyLlama 工件身份

以下工件身份在两个证据集都相同，但每块板都重新计算并记录了内容哈希：

| 工件 | 来源/revision | bytes | SHA-256 | 结论 |
| --- | --- | ---: | --- | --- |
| `tiny-llama.om` | `wan-zutao/tiny-llama-manual-reset`，`114a158718411d8b0a252806ca14144c01a7e3db` | `1,493,077,371` | `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967` | 内容完整性通过；不是当前 CANN 8.0 的官方 OM 兼容声明 |
| `tokenizer.zip` | 同一来源/revision | `709,459` | `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` | 内容完整性通过 |
| `tokenizer.json` | ZIP 解出 | `1,842,767` | `bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e` | 与运行时绑定 |
| `tokenizer.model` | ZIP 解出 | `499,723` | `9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347` | 与运行时绑定 |
| `special_tokens_map.json` | ZIP 解出 | `414` | `6fa06efa2785e450051989a6f8fb4416b10149ded485ddd3f127a40734f5cfd0` | 与运行时绑定 |
| `tokenizer_config.json` | ZIP 解出 | `932` | `bcdc6f267b05e1afd27fa622a62fea649bcb941e6dc705d2835883a0746192da` | 与运行时绑定 |

OBS 的 multipart ETag 不等于 SHA-256。完整的下载、锁文件和报告仍以
[`docs/09`](09-tinyllama-acl-om-validation-record.md) 与
[`docs/11`](11-code-review-optimization-and-board-192-168-1-90.md) 中的板端路径为准。

## 源文件身份与 SHA 差异

`docs/09` 的源文件哈希来自 2026-08-21 同步到 `.178` 的
`~/case9-tinyllama/source/`。下表右列是 2026-08-22 控制机当前工作树重新计算的
哈希；它们不是对 `.90` 远端文件的重新取样。差异意味着旧报告不能自动归因到当前
工作树，必须以报告中记录的同步目录和哈希为准。

| 文件 | `.178` 报告中的旧同步 SHA-256 | 当前工作树 SHA-256 | 是否相同 |
| --- | --- | --- | --- |
| `tinyllama_acl_contract.py` | `7c553063cfb3cc9e0301d3c7311393455e2901215b2a7ad5d051e59599841343` | `7c553063cfb3cc9e0301d3c7311393455e2901215b2a7ad5d051e59599841343` | 是 |
| `tinyllama_acl_runtime.py` | `6e2bd4ec8314f179415eba7a24edb526b6933de6dd20b399731548f589b96ce6` | `ab685210ed292e192d2a4cf9fd0f43b83bbd2ee65971da7b46517568dfba0b61` | 否 |
| `tinyllama_acl_service.py` | `b73d297815e8e544a7dc0baef15abf2876fe81fba3dbe84a4f33afedca0627a6` | `a941c4c626c92dab709dea9825164f8d8eb775c53a4af0120a026c8c5f25f59c` | 否 |
| `tinyllama_tokenizer.py` | `ba995928c3861337b083f22e26720b630e3e3d9505d59c0752046ed42e85a88f` | `ba995928c3861337b083f22e26720b630e3e3d9505d59c0752046ed42e85a88f` | 是 |
| `scripts/provision_tinyllama_board.sh` | `9875bd31a47f66d62425f49b3e66d66ea13378072eac64552cc633919d9fef8a` | `1d3ff87844f8665027174596182bf7499e4c9794b3427267ce5dcf5cb91d202e` | 否 |
| `scripts/run_tinyllama_acl_service.sh` | `a94860b00d89753c27bb7477c0f807bbadb6f0326a47f9c562b1746d40bfe6eb` | `4a23e52c21b24003333bd4f1b8c3dc94a44f2704cbf30ac078187ba5130f149f` | 否 |
| `local_model_manifest.json` | `ed80c4efd69ba8d41b8c2d18ef4293e52189166c9d31ddd7b714a29143b39081` | `172c48ceeaa70edd49179a6fd32ec6e93d768026bf178fc671359fdfc6adfa4e` | 否（新增 Qwen2.5、last-logits 与短上下文条目） |
| `requirements-tinyllama-acl-om.txt` | `8486d2fcf212a42985e09057649962972f1e3cd3e160ecf933e67bf5ed8fa24f` | `8486d2fcf212a42985e09057649962972f1e3cd3e160ecf933e67bf5ed8fa24f` | 是 |

这不是源代码“损坏”的判断，而是版本边界：`.178` 的真实运行证据只支持其旧 SHA；
当前 `.90` 的 `docs/11` 记录支持的是隔离同步后的代码和测试命令。今后若要比较
性能或复现实验，必须把源文件 SHA、模型 SHA、端口和板卡地址写入同一份报告，不能仅
引用“TinyLlama 已通过”。

## 统一门禁状态

| 门 | `.178`（`docs/09`） | `.90`（`docs/11`） | 当前解释 |
| --- | --- | --- | --- |
| 工件完整性 | `passed` | `passed` | 只说明字节/SHA/revision 匹配 |
| OM descriptor | `passed` | `passed` | 只说明输入输出描述符合 Tiny contract |
| 真实 ACL smoke/NPU 生成 | `passed` | `passed` | 设备上实际执行过 `acl.mdl.execute` |
| OpenAI JSON/SSE | `passed`（最终 8080；早期 8081） | `passed`（隔离 8081） | 协议通过，不是中文质量 |
| 网关转发 | `passed`（7861） | `passed`（7861） | 代理鉴权和转发通过 |
| 中文质量 | `failed/not-admitted` | 未通过/未接纳 | 英文回退和替换字符，不能作为中文模型 |
| 数值一致性 | `not-run` | `not-run` | 没有外部 ONNX CPU top-k/余弦参考 |
| 10 轮资源稳定性 | `passed-with-risk` | `not-run`（仅单次 smoke 快照） | `.178` 有 NPU/HugePages 增长风险，不能迁移给 `.90` |
| ASR/TTS 音频闭环 | 未完成 | 未执行 | 不能由文本 API 替代 |
| XiaoZhi 服务/真机 | 未安装/未执行 | 未安装/未执行 | 依赖和设备边界仍阻断 |

## 证据使用规则

1. 选模型时先绑定板卡、工件 SHA、源文件 SHA 和端口，再引用门禁结果。
2. `.178` 的 8080 结果不能证明 `.90` 的旧 TinyLlama 8081 或当前 Qwen 静态 KV 8080
   可用；当前 Qwen 静态 KV 的正式 8080 证据只引用 `docs/18` 的同一工件、源 SHA、PID
   和报告，不能回填 TinyLlama 或旧 full-context 结果。
3. `Health: Alarm` 只能作为诊断字段；实际 ACL 初始化、descriptor、execute、NPU
   快照和资源释放仍需逐项有日志。
4. TinyLlama 的 API 通过只表示实验性文本链路。中文失败、数值未执行和内存风险必须
   保留在最终状态中。
5. 旧 Qwen、llama.cpp、音频和 XiaoZhi 记录是历史/未完成证据，不能作为 TinyLlama
   成功的旁证，也不能自动成为下一模型的实现方案。

## 下一步证据入口

新的中文 ONNX 静态输入模型应从独立 candidate 开始，使用新的 contract/runtime/service
和独立端口；不要复用 TinyLlama 的 22 层、32000 词表或 KV 布局。其顺序应为：工件哈希
和静态图审计、ATC、OM descriptor、ACL smoke、NPU 生成、JSON/SSE、网关、数值参考、
中文质量和稳定性。任何门失败都保留日志并停止，不安装 Torch、CPU、云端或其他后端。

当前 Qwen2.5 批次是一个独立的静态全上下文候选，不替换 TinyLlama 的结论。原始图输入为
`input_ids`、`attention_mask`、`position_ids` 三个 `int64 [1,2048]` 张量，输出为
`float16 [1,2048,151936]` logits；last-logits 候选保留相同输入并将公开输出收窄为
`float16 [1,1,151936]`。外部 `sci-agent` 仅用于 CPU 导出和参考检查，板端运行时仍为无
Torch 的原生 ACL。8082 原图和 8083 优化图的实测边界分别见 `docs/15`、`docs/16`；
两者都不代表已经完成中文质量或稳定性验收。

静态 KV 1024 已完成独立候选验收，使用 FP32 split cache：每层 key/value 各为
`[1,2,1024,64]`，共 48 个输入，并输出 48 个 `[1,1,2,64]` 单 token cache；它与旧的 1024 last-logits 图
不是同一工件。单文件 ONNX/OM bytes、板端 ATC、descriptor、ACL、NPU、API、中文和性能
证据见 [`docs/18`](18-qwen25-static-kv-1024-validation-record.md)。候选目录和隔离端口
`~/case9-qwen25-kv1024`、`8084`、`7867`、`7868` 的报告保留；当前正式链路为
`8080 -> 7861 -> 7865`。

## 复现包入口

用户授权同步的完整复现包位于被 Git 忽略的
`repro/qwen25-kv1024-20260825/`，约 4.46 GiB。它包含 Qwen checkpoint、单文件
ONNX、原板生成 OM、tokenizer、两套运行源码、控制机/板端环境锁、所有已复制的
报告和全量 `SHA256SUMS.txt`。包内 `README.md` 和
[`docs/20`](20-qwen25-kv1024-reproducibility-bundle.md) 说明新板前置条件、哈希
校验、ACL smoke、服务启动和精确停止边界。实际 CANN、驱动、固件和内核不在包内，
必须在新板单独安装并核对；包不会安装或替换 Torch、Torch-NPU、Torchaudio 或其他
被禁止框架。原板最近一次 OS_PANIC 后三项服务没有自动恢复，故复现流程不能依赖
开机自启。
