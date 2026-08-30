# Case9 MindSpore 聊天模型实测验收账本

_记录版本：2.0｜更新日期：2026-08-30｜当前结论：Qwen/Tiny 已完成一轮板端机器协议测试，Qwen post-restart d/e、post-mask n 小批次和 Qwen -> Tiny -> Qwen 切换冒烟通过；Tiny 长输出失败，现已标记 blocked 并禁止再次激活；DeepSeek 已在实际可达的 20T（192.168.1.95）完成隔离 NPU 短生成、稳定性、9/9 API 机器门和完整 2+30 SSE 性能对照，但中文质量仍未通过；8T 随后发生可重复的 LPM fault，Qwen 仅完成受控恢复烟测，硬件稳定性仍未通过_

本文只记录已经产生的报告和可复核的板端事实。`passed` 仅表示对应机器检查通过，
不表示中文回答正确、服务适合生产或已经获准进入模型选择器。`human_review` 和
`admission` 由人工单独决定，不能由验收脚本推导。

## 1. 结论摘要

| Profile | 板卡 | 本批次状态 | 已有证据 | 当前边界 |
| --- | --- | --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | `192.168.1.90`，Ascend310B4 / 8T | `passed`（9/9 完整机器门；d/e 与 n 小批次通过） | 板端/本地 artifact verifier 7/7；`/health`、`/v1/models`、JSON、SSE、8/16/32/64 长输出、10 轮稳定性、2+30 性能、错误边界和协议中断检查；`20260829c` 严格 health 身份门；post-restart d/e、post-mask n；候选网关鉴权、候选 UI 和 JSON/SSE 链 HTTP 冒烟 | 共享 `base` 是 `experimental_dirty_base`；人工质量仍 `pending`；成功切换已冒烟，浏览器会话清空、失败回滚和 watchdog 生命周期仍未完成 |
| `tinyllama-1.1b-mindspore` | `192.168.1.90`，Ascend310B4 / 8T | `failed`（8/9 机器门），Profile `blocked` | 板端/本地 artifact verifier 7/7；基础 API、SSE、错误边界、协议中断、稳定性和性能请求可完成 | `max_tokens=32` 输出含 `U+FFFD`，`quality_machine=7/10`；已有切换仅为历史证据，当前 CLI 禁止激活 |
| `deepseek-r1-qwen-1.5b-mindspore` | `192.168.1.95`，Ascend310B1 / 20T（`.210` 为旧地址） | `blocked`（隔离实验已运行） | 固定 Modelers revision、权重哈希、MindSpore/Ascend 加载、短生成、10 轮稳定性、临时 OpenAI API 9/9 机器门、2+30 SSE 性能和中文探测原始报告 | 中文回答仍在推理前缀截断或出现事实错误；正式网关/UI 未运行；共享 `base` 为 dirty-base，不能直接准入 |

Qwen 和 Tiny 的验收脚本是只读 campaign，要求服务已经启动，`process_management` 为
`none`。因此历史报告中的 `passed`/`failed` 不包含服务启动、停止、切换或回滚的证明；
新增 d 报告仍是小批次 API/health 复核，另有独立板端切换冒烟记录。`errors.json` 和
`protocol.json` 已完成的是 HTTP/API 边界，不是完整生命周期门。
正式链路 `8080 -> 7861 -> 7865` 没有因本账本改变。

## 2. 实测环境指纹

两批报告均采集于同一块 `.90` 板。报告时间为 UTC，当前 IP 已是
`192.168.1.90`；早期 `.178` 只是同一块板的历史地址，不是第二个测试节点。

| 字段 | Qwen 批次 | Tiny 批次 |
| --- | --- | --- |
| 报告目录 | [`qwen-full-20260829b`](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/) | [`tiny-full-20260829b`](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/) |
| 记录时间 | `2026-08-29T08:11:32Z` | `2026-08-29T07:54:51Z` |
| SoC / 算力 | `Ascend310B4` / `8T` | `Ascend310B4` / `8T` |
| CANN | `8.0.0` | `8.0.0` |
| Python | `3.9.2` | `3.9.2` |
| MindSpore / MindNLP | `2.4.10` / `0.4.1` | `2.4.10` / `0.4.1` |
| NumPy | `1.22.4` | `1.22.4` |
| 环境指纹 | `90082a2db25c0c1d0f36fdb765be50b9c465c9d43b9b8b9f1039137b8d31778b` | 同左 |
| `npu-smi` | `25.2.0`，`Health: Alarm` | `25.2.0`，`Health: Alarm` |
| 服务 worker PID | `34548` | `30720` |
| 服务地址 | `127.0.0.1:8090` | `127.0.0.1:8090` |

`Health: Alarm` 是板端诊断字段，不单独阻断或证明推理。两个 Profile 都运行在共享
`base`，不能把本轮结果描述为干净生产环境。已知该环境原有
`torch`、`torch_npu`、`torchaudio` 等包；本轮没有删除或安装这些包，适配层也不以它们
为推理后端。

环境和 health 原始文件：

- [Qwen health](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/health.json)、[snapshots](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/snapshots.json)
- [Tiny health](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/health.json)、[snapshots](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/snapshots.json)
- [Qwen 严格 health gate `20260829c`](../repro/mindspore-chat-20260829/reports/board8t/qwen-health-gate-20260829c/health.json)、[acceptance](../repro/mindspore-chat-20260829/reports/board8t/qwen-health-gate-20260829c/acceptance.json)、[metadata](../repro/mindspore-chat-20260829/reports/board8t/qwen-health-gate-20260829c/metadata.json)

验收 health gate 从本轮起还要求响应明确提供 `npu_model`、`device_target`、
`worker_pid` 和 `environment_fingerprint`（此外仍需 `ready`、`healthy`、`busy` 与
`cache_cleared`）。旧的 `qwen-full-20260829b/health.json` 是在该字段门加入前采集的
历史记录，其中 `npu_model` 为 `null`；它只能按当时的机器门结果保存，不能用新规则
重新计算为通过或失败。

新增的 `qwen-health-gate-20260829c` 是严格字段门的原始证据：HTTP 200 的 health 响应
报告 `npu_model=Ascend310B4`，与 Qwen Profile 的 `board_soc` 精确一致，
`device_target=Ascend`，`worker_pid=42803`，并给出 64 位环境指纹
`90082a2db25c0c1d0f36fdb765be50b9c465c9d43b9b8b9f1039137b8d31778b`。该目录包含
14 个已同步的报告文件；其中 `health.json` SHA-256 为
`d74081d4e9321e37cf7ffba41568401c02b00e398cf2d0924eeebc234afabf7b`，全量逐文件哈希
由复现包 `bundle-manifest.json` 和 `SHA256SUMS.txt` 保存。该批次仅以 2-token 长输出、
1 次稳定性和 1 次性能测量验证新 health 契约，不能替代已有完整批次的长输出、稳定性、
性能或人工质量结论。

## 3. 模型来源、revision 和哈希

### 3.1 Qwen1.5

下表是 `configs/chat_model_profiles.json` 中声明的不可变 revision、字节数和 SHA-256。
本轮板端和控制机 verifier 均逐项核对通过（7/7）；对应原始文件见
[`qwen-artifact-verification-board.json`](../repro/mindspore-chat-20260829/reports/board8t/qwen-artifact-verification-board.json)
和 [`qwen-artifact-verification.json`](../repro/mindspore-chat-20260829/reports/board8t/qwen-artifact-verification.json)。

| 文件 | bytes | SHA-256 | 证据级别 |
| --- | ---: | --- | --- |
| `model.safetensors` | `1,239,173,352` | `72453a8ccb338811935ab95a3a6ffa86b586807bf5b3dc327f28b5389b5636e6` | verified |
| `tokenizer.json` | `7,028,015` | `f7c9b2dba4a296b1aa76c16a34b8225c0c118978400d4bb66bff0902d702f5b8` | verified |
| `config.json` | `661` | `adac3bb8bf1b15c74312a2835e3796f6b352d817ff726960e1d5c2cd171da951` | verified  |
| `tokenizer_config.json` | `1,287` | `482bd979881423375ca5414e4e0d94cd7c5349dbb17fffd46b4d36d71e62a1bc` | verified  |
| `vocab.json` | `2,776,833` | `ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910` | verified  |
| `merges.txt` | `1,671,839` | `599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3` | verified  |
| `generation_config.json` | `206` | `5cf6fb81bd473eeb55678afbaee79e8d5f8b6e9bc2f942c30ee94721a0a91945` | verified  |

Qwen revision 和 tokenizer revision 均为
`4d14e384a4b037942bb3f3016665157c8bcb70ea`，缓存相对目录为
`artifacts/models/qwen1.5-0.5b-chat`。

### 3.2 TinyLlama

Tiny 的板端下载批次另外保存了 SHA-256 清单和来源记录：
[`SHA256SUMS.txt`](../repro/mindspore-chat-20260829/reports/board8t/tinyllama-download-20260829/SHA256SUMS.txt)
和 [`source.txt`](../repro/mindspore-chat-20260829/reports/board8t/tinyllama-download-20260829/source.txt)。
来源是 `TinyLlama/TinyLlama-1.1B-Chat-v1.0`，revision
`fe8a4ea1ffedaf415f4da2f062534de366a451e6`，镜像为 `hf-mirror.com`，完成时间
`2026-08-29T07:10:49Z`。

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| `model.safetensors` | `2,200,119,864` | `6e6001da2106d4757498752a021df6c2bdc332c650aae4bae6b0c004dcf14933` |
| `tokenizer.json` | `1,842,767` | `bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e` |
| `tokenizer.model` | `499,723` | `9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347` |
| `config.json` | `608` | `486bedda3a6988332e60d9638a09ca4b260d34ebcf1b19e22cf3b140b63d8fe9` |
| `generation_config.json` | `124` | `18046d04f5bd8b4998095ecabdd17a1bf0053d9acdccead4a05be4a3575f3c5c` |
| `special_tokens_map.json` | `551` | `82d96d7a9e6ced037f12394b7ea6a5b02e6ca87e0d11edaa8d60d9be857ce7db` |
| `tokenizer_config.json` | `1,289` | `7b41ba7d0eb91e77914ca3dafde559ea3e19878769b7e68409e89bed5222e77a` |

本轮板端和控制机 verifier 均逐项核对通过（7/7）；对应原始文件见
[`tiny-artifact-verification-board.json`](../repro/mindspore-chat-20260829/reports/board8t/tiny-artifact-verification-board.json)
和 [`tiny-artifact-verification.json`](../repro/mindspore-chat-20260829/reports/board8t/tiny-artifact-verification.json)。
复现目录已经同步这两个 Profile 的完整权重、tokenizer 和配置，并保留报告与逐文件哈希；
这些大文件位于 Git 忽略的 `repro/` 目录，仍不要把模型权重或运行日志提交 Git。

### 3.3 DeepSeek

历史计划中的 DeepSeek Profile 曾指向 `192.168.8.210`、可变 `main` revision，因而
保持 `blocked`。本轮发现同一块 20T 板的实际 WLAN 地址为 `192.168.1.95`，并在隔离
目录完成了固定 Modelers revision `0a28897fe71fdd30de350b667ae588601a85990f` 的工件
同步和 NPU 实测。原始配置、权重、运行副本、环境快照及报告集中在
[`26-deepseek-20t-validation-20260830.md`](26-deepseek-20t-validation-20260830.md)
和 `repro/deepseek-r1-20t-20260830/`。

权重为 `3,554,214,416` bytes，SHA-256 为
`706e1bfd7cb0680fbf73df6a2506766e447246e4291d7054c8b395dc3583419c`。在不覆盖原始
配置的前提下，实验副本把 `ms_dtype` 改为 `float16`、上下文限制为 1024；MindSpore
2.4.10/MindNLP 0.4.1 在 Ascend310B1 上完成单 token 和 4-token 生成，20T 相同协议
稳态吞吐约 `0.511 token/s`，比 8T 的 `0.388 token/s` 高约 31.8%。10 轮短稳定性
通过。随后临时 `service-test` 完成 `/health`、`/v1/models`、JSON/SSE、8/16/32/64-token、
错误边界、超上下文、超大请求和客户端中断测试，机器门 `9/9` 通过；2+30 SSE 的总耗时
p50/p95 为 `2489.698/2537.096 ms`，吞吐 p50 为 `0.805 token/s`。这只证明接口和 NPU
执行链可工作，不证明中文答案质量；正式网关/UI 未启动，Profile 仍保持 `blocked`。
用户重新开放 20T 后又按相同协议完成批次
[`deepseek-reopen-20t-20260830T0536Z`](../repro/deepseek-r1-20t-20260830/reports/board20t/api/reopen-20260830T0536Z/)，
机器门仍为 `9/9`，总耗时 p50/p95 `2484.751/2557.242 ms`，吞吐 p50
`0.805 token/s`；服务已停止，正式入口未变。该复核不改变中文质量和 dirty-base 准入结论。

## 4. Qwen1.5 实测结果

原始报告：[acceptance.json](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/acceptance.json)、
[command.json](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/command.json)、
[JSON smoke](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/json-smoke.json)、
[SSE smoke](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/sse-smoke.json)、
[长输出](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/long-output.json)、
[稳定性](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/stability.json)、
[性能](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/performance.json)、
[机器质量探测](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/quality.json)、
[错误边界](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/errors.json) 和
[协议边界](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/protocol.json)。

### 4.1 API 和长输出

| 检查 | 实测值 | 结果 |
| --- | --- | --- |
| `/health` | HTTP 200；`ready=true`、`healthy=true`、`busy=false`、`cache_cleared=true` | passed |
| `/v1/models` | HTTP 200；返回 `case9-active` | passed |
| JSON smoke | 2 tokens；`8,337.652 ms`；文本 `我是来自` | passed |
| SSE smoke | 首事件 `769.118 ms`；总计 `1,389.032 ms`；2 个前缀 delta；无重复 | passed |

| `max_tokens` | 实际 tokens | 总耗时 | finish reason | UTF-8 / replacement |
| ---: | ---: | ---: | --- | --- |
| 8 | 8 | `4,938.293 ms` | `length` | valid / none |
| 16 | 16 | `9,660.409 ms` | `length` | valid / none |
| 32 | 32 | `18,942.570 ms` | `length` | valid / none |
| 64 | 55 | `32,471.872 ms` | `stop` | valid / none |

长输出机器门为通过。错误边界为 6/6：错误模型、非法角色、非 greedy 采样、超 token
上限和未知字段均返回预期 4xx。协议边界也为通过：超上下文返回 400、超大正文返回
413，客户端提前关闭 SSE 后 health 仍为 healthy。详见 `errors.json` 和 `protocol.json`。
这里的“通过”只表示机器契约，不等于内容事实正确。

### 4.2 稳定性和性能

- 稳定性：固定 `max_tokens=2`，10/10 请求返回 HTTP 200、机器有效；原始文件为
  `stability.json`。
- 性能：2 次预热、30 次测量、SSE、`max_tokens=2`。总耗时 p50/p95 为
  `1,412.236 / 1,603.883 ms`；首事件 p50/p95 为
  `761.847 / 810.331 ms`；按每次报告的 completion token 和耗时计算，token/s
  p50/p95 约为 `1.420 / 1.479`。

### 4.3 资源告警

Qwen 服务日志中出现以下运行时告警，不能被 9/9 机器门覆盖：

- TBE `main process disappeared`；
- `resource_tracker` 报告约 30 个 leaked semaphore objects；
- MindSpore 将 bfloat16 自动转换为 float16；
- `do_sample=False` 与 `top_p=0.8` 的参数警告；
- 因 `pad_token_id=eos_token_id` 未显式设置 attention mask；
- sliding-window attention 在 eager 路径未实现的提示。

已知板端日志路径（未同步到 Git）：
`~/case9-mindspore-chat/run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T054225Z.log`。
这是早期批次的历史记录。后续代码已显式传入等长 `attention_mask` 并固定 `top_p=1.0`；
补丁后的 worker 日志未再出现这两类警告。bfloat16 转换、sliding-window eager 未实现、
TBE 进程和 resource_tracker 泄漏提示仍需单独解释，不能被机器门覆盖。

### 4.4 进程组切换与重启复核

本次另外执行了受控的 `Qwen -> TinyLlama -> Qwen` 切换，以及 Qwen 的停止/重启：旧
Qwen worker `48018` 已释放 `8090` 后，Tiny worker `51243` 启动并报告
`PID=PGID=SID=51243`、`Ascend310B4`、`CANN 8.0.0`、`ready=true`；随后 Tiny worker
被停止，Qwen 再次启动为 `54036`。更新进程组保护后，又执行了一次 Qwen `stop`，确认
无 `mindspore_chat_service.py` 进程且 `8090` 无监听，再重新启动为 `57343`。

第一轮 [post-restart d acceptance](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/acceptance.json)
记录 `worker_pid=57343`。随后受控停止该 worker、确认 `8090` 无监听并重新启动为 `68723`；
第二轮 [post-restart e acceptance](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/acceptance.json)
的十个机器 gate 也都为 true，其 [health](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/health.json)
记录 `worker_pid=68723`、`npu_model=Ascend310B4`、`device_target=Ascend` 和同一环境指纹。
d/e 两批都只使用 2-token 长输出、1 次稳定性和 1 次性能测量，作为重启后协议/身份复核，
不能替代 `qwen-full-20260829b` 的 8/16/32/64、10 轮和 2+30 结果。

随后部署了显式 mask/top_p 修正并再次受控 stop/start，历史补丁批次 worker 为 `90531`。缩小批次
[post-mask n acceptance](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/acceptance.json)
的 `health`、`models`、JSON、SSE、长输出、稳定性、性能、错误和协议门均为 `true`；JSON
文本 `我是来自`、SSE 文本 `你好！` 均为完整 UTF-8，代码点检查未发现 `U+FFFD`。该批次
只用于补丁后的运行回归，不替代完整长输出、10 轮稳定性、2+30 性能和人工质量验收。

旧 worker 的一次受控迁移记录及新切换日志已同步到复现包：
[migration](../repro/mindspore-chat-20260829/board8t/run/mindspore-chat/process-migration-20260829T105131Z.log)、
[Tiny launch](../repro/mindspore-chat-20260829/board8t/run/mindspore-chat/logs/tinyllama-1.1b-mindspore-20260829T105946Z-51165-31067.log)、
[Qwen restart](../repro/mindspore-chat-20260829/board8t/run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T110958Z-57319-24854.log)。
它们只证明一次成功的停止/启动；没有构造失败启动、失败回滚或 watchdog 杀进程场景。
[post-mask worker log](../repro/mindspore-chat-20260829/board8t/run/mindspore-chat/logs/qwen1.5-0.5b-mindspore-20260829T132104Z-90436-6247.log)
记录了补丁后的启动和无 `top_p`/缺失 mask 警告的回归。
在同一 worker 上又执行了候选网关当前链路复核：未授权 `/v1/models` 返回 `401`，使用
板端进程环境中的内部密钥（未输出）经 `7867 -> 8090` 的 JSON 和 SSE 均返回 `200`，
公开模型名为 `case9-rag`，SSE 只出现前缀差量和 `[DONE]`。原始网关日志见
[`candidate-gateway.log`](../repro/mindspore-chat-20260829/board8t/run/mindspore-chat/logs/candidate-gateway.log)；
这仍是链路复核，不是正式入口切换或质量准入。

### 4.5 LPM fault 后的受控恢复（2026-08-29）

在 post-mask 批次之后，`.90` 板端内核日志出现 Ascend 驱动低功耗路径异常：
`DRV_LPM_FAULT fault=0x80E3A203`，blackbox 模块为 `lpm`，描述为
`lpm get current error`。已观察到至少三次带时间戳的事件（本地时间
`22:42:56`、`22:47:58`、`22:52:59`）；原 worker `90531` 退出并释放监听端口，不能把该
退出归因于应用正常停止，也不能把它计入模型稳定性通过。原始内核、进程和 NPU 快照保存在
[`lpm-dmesg-and-processes.txt`](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/lpm-dmesg-and-processes.txt)。

按 modelctl 的受控流程重新启动 Qwen 后，恢复批次记录的 worker 为 `15897`，其
`PID=PGID=SID`，`group_isolated=true`、`identity_match=true`、`stale=false`；
`/health` 报告 `healthy=true`、`cache_cleanup=idle`、`cache_cleared=true`、
`Ascend310B4`、CANN `8.0.0`。恢复报告
[`qwen-lpm-recovery-20260829r/acceptance.json`](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-lpm-recovery-20260829r/acceptance.json)
使用 `2/4` token 长输出、1 次稳定性、1 次性能和完整错误/协议检查，9 个可执行机器门均为
`true`；这是服务恢复和接口契约证据，不替代完整 Qwen 批次，也不证明 LPM fault 已解决。

恢复期间 `npu-smi` 仍为 `Health: Alarm`，设备内存约 `14.9/15.6 GB`、HugePages
`547/547`；该状态连同 LPM 日志是硬件诊断风险。恢复后的候选链也单独复核：
网关 `7867` 和 UI `7868` 的进程分别为 `21231`、`21232`，未授权网关请求为 `401`，
授权 JSON/SSE 和 UI `/api/chat` 为 `200`，SSE 无重复 delta。链路摘要见
[`chain-summary-final.json`](../repro/mindspore-chat-20260829/board8t/run/mindspore-chat/candidate-recovery-20260829r/chain-summary-final.json)，
诊断和控制器状态见恢复报告目录中的 `modelctl-status.txt` 与 `health-final.json`。

因此 Qwen 仍保持 `experimental_dirty_base`，不得因为恢复烟测或候选链可用而提升为
`admitted`；需要先定位/消除 LPM 事件，再重新执行完整稳定性和性能批次。

### 4.6 控制器安全加固与当前只读核验（2026-08-30）

在不重启候选服务的情况下，`case9-modelctl.sh` 已完成本地安全回归并部署到 `.90`：
启动 journal、`worker.pid`、`worker.pgid` 和活动 state 现在都是持久化硬门；缺失、非法、
不一致或符号链接 sidecar 会 fail-closed，只有确认进程已退出且进程组为空才允许清理。
持久化失败时会停止已知 worker，无法安全停止则保留失败指针，不启动无跟踪 worker。
旧板端脚本保留为 `scripts/case9-modelctl.sh.pre-20260830a`，未参与运行。

本地验证：`bash -n` 通过，modelctl 专项 `27` 项中 `27` 通过（`4` 项因 Windows/WSL
进程表能力跳过），全套 `pytest` 为 `298 passed, 9 skipped`。板端只读核验显示 Qwen
worker `4378`（`PID=PGID=SID`）、候选网关 `8463`、文字 UI `8578` 均存活，`8090/7867/7868`
正在监听，`/health` healthy；NPU 为 `Ascend310B4`、CANN `8.0.0`、`Health: Alarm`。
这只是当前服务状态和控制器部署证据，不替代 LPM 故障后的完整稳定性/性能重测。

同日 `2026-08-30T01:09:55Z` 的短复核：对 `127.0.0.1:8090/v1/chat/completions` 发送
`max_tokens=2`、greedy 中文请求，HTTP `200`，返回内容为“您好！”，`completion_tokens=2`；
这是单次 API smoke，不计入新的性能或稳定性批次。对 `192.168.8.210` 的 SSH 连接再次检查
返回 `No route to host`（退出码 `255`）。该地址是旧记录；随后确认 20T 的 WLAN 地址为
`192.168.1.95`，并在独立目录完成了 DeepSeek 工件和 NPU 实测。详见第 3.3 节及
[20T 独立验证记录](26-deepseek-20t-validation-20260830.md)；这些结果不改变 Profile 的
`blocked` 状态。

## 5. TinyLlama 实测结果

原始报告：[acceptance.json](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/acceptance.json)、
[JSON smoke](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/json-smoke.json)、
[SSE smoke](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/sse-smoke.json)、
[长输出](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/long-output.json)、
[稳定性](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/stability.json)、
[性能](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/performance.json)、
[机器质量探测](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/quality.json)、
[错误边界](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/errors.json) 和
[协议边界](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/protocol.json)。

### 5.1 API 和长输出

| 检查 | 实测值 | 结果 |
| --- | --- | --- |
| `/health` | HTTP 200；`ready=true`、`healthy=true`、`busy=false`、`cache_cleared=true` | passed |
| `/v1/models` | HTTP 200；返回 `case9-active` | passed |
| JSON smoke | 2 tokens；`3,155.880 ms`；文本 `Me:` | passed |
| SSE smoke | 首事件 `3,312.709 ms`；总计 `3,313.704 ms`；1 个 delta；无重复 | passed |

| `max_tokens` | 实际 tokens | 总耗时 | finish reason | UTF-8 / replacement | 门 |
| ---: | ---: | ---: | --- | --- | --- |
| 8 | 8 | `11,798.865 ms` | `length` | valid / none | passed |
| 16 | 16 | `23,196.903 ms` | `length` | valid / none | passed |
| 32 | 32 | `47,814.185 ms` | `length` | **invalid / contains `U+FFFD`** | **failed** |
| 64 | 64 | `92,706.772 ms` | `length` | valid / none | passed |

`max_tokens=32` 的实际文本为 `静态 KV cache 的作用是为了提高服务器的响应速度，�����`，
其中出现 Unicode replacement character。该单次失败足以使 `acceptance.status=failed`
和 `gates.long_output=false`；不能用 8、16 或 64 的结果抵消它。

错误边界为 6/6，协议边界为通过：错误请求、超上下文、超大正文和客户端提前关闭 SSE
均返回预期结果，health 在中断后保持可用。这些通过不抵消长输出和质量门失败。

### 5.2 稳定性和性能

- 稳定性：固定 `max_tokens=2`，10/10 请求返回 HTTP 200、机器有效；输出主要为
  `Yes,`。这只说明服务连续运行，不能说明中文质量。
- 性能：2 次预热、30 次测量、SSE、`max_tokens=2`。总耗时 p50/p95 为
  `3,114.857 / 3,185.731 ms`；首事件 p50/p95 为
  `3,110.338 / 3,181.192 ms`；token/s p50/p95 约为 `0.642 / 0.651`。

板端快照显示 worker RSS 从 `7,428,476 KB` 变为 `7,138,416 KB`，FD 保持 78，
HugePages 保持 `551/551`；这是一次 campaign 的前后观察值，不能单独定性为泄漏，
但应在下一轮重复采样。

## 6. 机器探测、人工质量和准入边界

Qwen 质量文件记录 `machine_valid_count=10`，Tiny 更新批次记录
`machine_valid_count=7`；两批均为 `human_review=pending`。机器有效只检查 HTTP/JSON、
UTF-8、预算和结构，不判断答案是否正确或是否适合作为中文聊天机器人。

Qwen 探测包含“你是谁”“能做什么”“昇腾开发板”“12 加 30”等固定问题；本批次
`probe-max-tokens=8`，许多回答在句子中途因预算结束。例如数学探测返回
`12 + 30 = `，硬件探测回答不完整。Tiny 探测混合中英文，出现
`I am a machine learning engineer with a`、`Ascend 310B`、`Happy New Year! May the`
等片段；这不能作为中文能力通过证据。

因此当前状态为：

| Profile | machine quality | human quality | admission |
| --- | --- | --- | --- |
| Qwen1.5 | `10/10` machine-valid | `pending`，未完成正式人工评分 | `not-run`，保持 `experimental_dirty_base` |
| TinyLlama | `7/10` machine-valid，且长输出门失败 | 历史记录保留；当前 Profile `blocked` | `blocked`，不得准入或激活 |
| DeepSeek | `10/10` UTF-8/非空机器探测；事实/完整回答未通过；临时 API 机器门 `9/9`（另有重新开放后的复核批次） | `not-run/pending`（已有输出，未完成正式人工复核） | `blocked`，正式网关/UI 和 dirty-base 准入未完成 |

## 7. 已完成与仍未执行的门

`qwen-full-20260829b` 的 9/9 机器门全部通过；`tiny-full-20260829b` 的错误边界和
协议门也通过，但长输出和 `quality_machine` 失败。两批的 `errors.json` 均为 6/6，
`protocol.json` 均通过超上下文、超大正文、客户端中断后 health 检查。对应报告目录：

- [Qwen b acceptance](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/acceptance.json)
- [Qwen 严格 health gate acceptance](../repro/mindspore-chat-20260829/reports/board8t/qwen-health-gate-20260829c/acceptance.json)：新字段门 9/9；缩小参数仅用于身份契约复核
- [Qwen post-restart d acceptance](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829d/acceptance.json)：10/10 小参数协议/身份复核，不替代完整批次
- [Qwen post-restart e acceptance](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-restart-20260829e/acceptance.json)：当前 worker `68723` 的 10/10 小参数协议/身份复核，不替代完整批次
- [Qwen post-mask n acceptance](../repro/mindspore-chat-20260829/board8t/reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-post-mask-20260829n/acceptance.json)：当前代码补丁后的 9 个可执行机器 gate 全部通过，不替代完整批次
- [Tiny b acceptance](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/acceptance.json)

以下生命周期或人工项目仍没有本轮正向证据，必须保持 `not-run`，不能从 acceptance
机器门、HTTP 冒烟或一次成功切换推断通过：

- 浏览器会话清空、失败切换与自动回滚；
- 服务 watchdog 触发后的进程退出、资源释放和恢复；
- 正式入口切换、音频、ASR/TTS、XiaoZhi 设备协议；
- 10 条探测的正式人工可理解度/事实正确性签字。

候选网关/UI 的 HTTP 冒烟已有独立原始记录：未鉴权请求返回 401，鉴权后 `/v1/models`、
JSON completion、SSE 前缀差量和页面加载均通过，且 UI 配置不包含网关密钥。该记录只
覆盖 HTTP 链，不覆盖切换、回滚或 watchdog：
[`candidate-chain-20260829b/raw.txt`](../repro/mindspore-chat-20260829/reports/board8t/candidate-chain-20260829b/raw.txt)。

Qwen 和 Tiny 的 artifact verifier 已分别在控制机和板端缓存根目录通过 7/7；见
[`qwen-artifact-verification.json`](../repro/mindspore-chat-20260829/reports/board8t/qwen-artifact-verification.json)、
[`qwen-artifact-verification-board.json`](../repro/mindspore-chat-20260829/reports/board8t/qwen-artifact-verification-board.json)、
[`tiny-artifact-verification.json`](../repro/mindspore-chat-20260829/reports/board8t/tiny-artifact-verification.json)
和 [`tiny-artifact-verification-board.json`](../repro/mindspore-chat-20260829/reports/board8t/tiny-artifact-verification-board.json)。

## 8. 可复现 preflight 和下一步

在目标板同一 shell 中显式准备环境：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
# The verified board image keeps MindSpore 2.4.10/MindNLP 0.4.1 in the base
# user site. Leave it visible; setting PYTHONNOUSERSITE=1 selects an older
# conda copy without MindNLP and must be used only as a diagnostic negative test.
unset PYTHONNOUSERSITE
cd ~/case9-mindspore-chat
```

先做只读工件核验，失败时不要下载替代模型或切换后端。当前已保存的控制机和板端
结果分别为：

- Qwen：[local](../repro/mindspore-chat-20260829/reports/board8t/qwen-artifact-verification.json)、[board](../repro/mindspore-chat-20260829/reports/board8t/qwen-artifact-verification-board.json)
- Tiny：[local](../repro/mindspore-chat-20260829/reports/board8t/tiny-artifact-verification.json)、[board](../repro/mindspore-chat-20260829/reports/board8t/tiny-artifact-verification-board.json)

重现命令：

```bash
python scripts/verify_mindspore_profile_artifacts.py \
  --profile qwen1.5-0.5b-mindspore \
  --root ~/case9-mindspore-chat \
  --output reports/mindspore-chat/qwen1.5-0.5b-mindspore/artifact-verify.json
```

服务必须由 operator 预先启动，验收脚本不会管理 PID。下列命令生成与本账本相同的
Qwen b 批次格式；Tiny 只需替换 Profile 和输出目录：

```bash
python scripts/mindspore_chat_acceptance.py \
  --profile qwen1.5-0.5b-mindspore \
  --execute \
  --run-id qwen-full-20260829b \
  --timeout 600 \
  --probe-file tests/fixtures/mindspore_chat_probe.json \
  --output reports/mindspore-chat/qwen1.5-0.5b-mindspore/qwen-full-20260829b
```

Tiny 使用同样流程，但必须保留中文/英文质量分栏，并在长输出出现 replacement
character 时停止准入流程；本轮对应目录为
[`tiny-full-20260829b`](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/)。
DeepSeek 的 20T 隔离实测已在 `.95` 完成；`.210` 仅保留为旧地址证据。临时 API 服务的
机器门和完整 2+30 SSE 性能已通过，但正式候选网关/UI、人工质量复核和共享 `base` 风险
审批仍未完成，不能在 `.90` 代替 20T 结果，也不能据此接入 XiaoZhi。

## 9. 失败处理和证据保存

失败时只停止本批次明确记录且命令行匹配的 worker PID；保留 acceptance、health、
long-output、stability、performance、quality、命令、服务日志和 `npu-smi` 快照。不得
删除共享 conda 缓存、系统 CANN、其他模型或正式服务，也不得自动改用 CPU、云端、Torch、
MindSpore 之外的推理框架、vLLM 或 MindIE 掩盖失败。

模型权重、缓存、板端日志和运行报告位于 Git 忽略目录。控制机复现包只同步显式允许的
清单，并为每次同步记录来源 IP、远程路径、字节数和 SHA-256。

候选链的同步清单另外固定包含 `scripts/run_mindspore_chat_acceptance.sh`、
`scripts/run_text_chat.sh`、`tests/fixtures/mindspore_chat_probe.json`，以及构建后的
`frontend/dist/index.html` 和两个带内容哈希的静态资源文件。当前候选文字 UI 实际由
`text_chat_app.py` 返回受控的内嵌 HTML，不动态挂载 `frontend/dist`；dist 仍是板端/控制机
可复核的构建材料，必须按文件大小和 SHA-256 校验后原子落盘。它不是源码快照，也不应通过
复制 `node_modules` 或未列入清单的构建输出来替代；`source/` 快照保存可审阅源码，dist
工件由同步脚本的显式 allowlist 单独传输。

## 10. 相关文档

- [MindSpore 聊天移植计划](23-mindspore-chat-porting-plan.md)
- [Profile 运行手册](25-chat-model-profile-runbook.md)
- [当前 Case9 运行手册](00-case9-current-runbook.md)
- [历史结果与边界](03-case9-history-and-boundaries.md)
- [MindSpore Orange Pi 在线推理目录](https://www.mindspore.cn/tutorials/zh-CN/master/orange_pi/model_infer.html)
- [Qwen1.5-0.5B-Chat 模型卡](https://huggingface.co/Qwen/Qwen1.5-0.5B-Chat)
- [TinyLlama-1.1B-Chat-v1.0 模型卡](https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0)
- [DeepSeek-R1-Distill-Qwen-1.5B 模型卡](https://huggingface.co/MindSpore-Lab/DeepSeek-R1-Distill-Qwen-1.5B)
