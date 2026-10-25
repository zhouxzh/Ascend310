# 案例 9 测试问题记录

> 本文件是案例 9 的唯一测试问题台账。详细测量和边界见 [验证与限制](04-validation-and-limitations.md) 与 [板端语音终端计划](08-board-voice-terminal-plan.md)。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-10-18；摄像头问题归档于 2026-10-20；8T 发布链为 2026-10-25 |
| 开发板 | Ascend310B1 / 20T 与 Ascend310B4 / 8T 分开记录 |
| SoC / 档位 | 20T MindSpore；8T ACL static-KV |
| CANN / 驱动 | CANN 8.0.0 / driver 25.2.0 |
| Python / 框架 | Python 3.9.2 历史记录；统一 `ascend` 3.9.25；MindSpore 2.4.10/MindNLP 0.4.1 与 ACL/OM 分开 |
| 模型 / revision | Qwen1.5/Qwen2.5 教学 profile 与公开 8T Qwen2.5 static-KV；revision 见各条目 |
| 代码提交 | 历史记录以部署目录为准；8T 正式验收要求公开 `main` 当前提交 |
| 输入与命令 | OpenAI JSON/SSE；浏览器 UI；USB UAC 麦克风；8T `8084 -> 7861 -> 7865` |
| 原始证据 | 板端 ignored evidence；公开 HF 工件清单见 `release-8t-static-kv.lock.json` |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CHATBOT-ISS-20261018-01` | `mitigated` | `major` | 2026-10-18 | 无间隔验收触发 HTTP 429 | 加请求间隔后通过 |
| `CHATBOT-ISS-20261018-02` | `resolved` | `major` | 2026-10-18 | Qwen2 tokenizer 缺少 ChatML 模板，出现角色标记续写 | 修正模板后合同检查通过 |
| `CHATBOT-ISS-20261018-03` | `open` | `major` | 2026-10-18 | 中文提问返回英文道歉，语义质量未通过该次期望 | 质量评审仍待完成 |
| `CHATBOT-ISS-20261020-01` | `open` | `major` | 2026-10-20 归档 | SN0002 UVC/UAC 摄像头麦克风读取 I/O error | 视频可用；麦克风不可用 |
| `CHATBOT-ISS-20261024-01` | `resolved` | `major` | 2026-10-24 | UI/gateway HTTP 200 时没有反映 MindSpore worker 已退出 | UI health/config 现在公开 worker readiness；未就绪时文字请求返回 503 |
| `CHATBOT-ISS-20261024-02` | `open` | `blocker` | 2026-10-24 | 8T Qwen1.5-0.5B 在 MindSpore kernel runtime 初始化阶段 HBM 不足 | 8T 在模型加载前按设备账本预检 fail-closed；无 token 生成 |
| `CHATBOT-ISS-20261024-03` | `resolved` | `major` | 2026-10-24 | 自动语音监听连续失败可无界积累 turn 或卡住 arecord | turn 历史有界、失败退避/自动停止、采集超时终止子进程 |
| `CHATBOT-ISS-20261025-01` | `resolved` | `major` | 2026-10-25 | 8T ATC 缺少隔离环境依赖，且默认 8 编译进程触发主机 OOM | 补齐依赖并固定 `MAX_COMPILE_CORE_NUMBER=1`；原生 B4 OM、PyACL 和 JSON 通过 |
| `CHATBOT-ISS-20261025-02` | `resolved` | `major` | 2026-10-25 | 8T UI 漏掉 profile 默认 16-token 档位，页面误选 80 | fallback 加入候选并成为初始选择；8T 桌面截图复核为 16 |
| `CHATBOT-ISS-20261025-03` | `resolved` | `major` | 2026-10-25 | 公开 B4 OM 仍是 schema-1 lock，严格 runtime 拒绝加载 | 保留公开 lock，并生成 data-dir 内 schema-2 lock |
| `CHATBOT-ISS-20261025-04` | `resolved` | `major` | 2026-10-25 | ACL execution timeout 300 秒超过 runtime 240 秒硬上限 | 8T ACL 默认/示例统一为 240 秒，全链路启动通过 |
| `CHATBOT-ISS-20261025-05` | `resolved` | `major` | 2026-10-25 | HF mirror 缓存重定向以 chunked 响应返回 tokenizer，下载器误报 Content-Length 缺失 | 允许无声明长度，但仍强制实际 bytes/SHA；全新目录 9/9 工件下载通过 |
| `CHATBOT-ISS-20261025-06` | `resolved` | `major` | 2026-10-25 | 随机 Gateway token 以 `-` 开头时被 argparse 当成选项，正式验收未启动 | `--token=`/`--model-source=` 使用等号形式，参数测试通过 |
| `CHATBOT-ISS-20261025-07` | `resolved` | `major` | 2026-10-25 | `.env` 只被 shell source 未导出，验收子进程误用 20T 模型 provenance | `set -a` 导出 `.env`；公开 8T 正式验收 `machine_status=passed` |
| `CHATBOT-ISS-20261025-08` | `mitigated` | `major` | 2026-10-25 | 多模态 USB 麦克风/喇叭停留在蓝牙/无线/仅播放模式时没有枚举为音频输入 | 切到有线 USB 音频模式并重新插入后出现 `EDIFIER M16 Pro`；喇叭听音通过，USB 麦克风 capture 仍不可用 |

## CHATBOT-ISS-20261018-01：验收突发流量触发 HTTP 429

- 状态：`mitigated`
- 级别：`major`
- 影响对象：`service admission / test harness`
- 症状：第一次完整验收无请求间隔，稳定性段返回 `HTTP 429: Too many request bodies are being read`。
- 判定：请求 body 突发触发服务 admission，不是 NPU 推理或模型崩溃。
- 处理：JSON 请求间隔 1 秒、流式请求间隔 2 秒并等待收尾；同一服务、模型和 20T 板通过。
- 根因：`confirmed`（客户端突发与服务单请求串行/限制边界冲突）。
- 回归：加间隔后完整机器门通过；原始报告为 `run/chatbot-acceptance-20261018-20t-spaced2.json`。
- 关联：[验证与限制](04-validation-and-limitations.md)

## CHATBOT-ISS-20261018-02：Qwen2 ChatML 模板缺失

- 状态：`resolved`
- 级别：`major`
- 影响对象：`tokenizer / chat-template / provider contract`
- 症状：Qwen2 tokenizer 目录缺少 `tokenizer_config.json` 和 `generation_config.json`，generic provider 使用了 Qwen1.5 形状提示，生成结果出现 `<|user|>` / `<|assistant|>` 续写。
- 判定：prompt-contract 错误，不是 NPU 内存或权重加载失败。
- 修复：profile 增加经过评审的 Qwen2 ChatML 文本模板；合同检查渲染 `<|im_start|>user ... <|im_end|>` 和 assistant 起止标记；adapter 拒绝缺少模板的 Qwen2/Qwen2.5 profile。
- 根因：`confirmed`。
- 回归：合同检查通过；重新测试的 Qwen2-1.5B 输出停止续写角色标记。
- 关联：[验证与限制](04-validation-and-limitations.md)、[教学模型目录](07-teaching-model-catalog.md)

## CHATBOT-ISS-20261018-03：中文回答质量未通过该次期望

- 状态：`open`
- 级别：`major`
- 影响对象：`task-quality`
- 症状：直接中文提问“请用一句中文介绍昇腾310B开发板”返回英文道歉句。HTTP、SSE、worker 和 NPU 路径仍完成。
- 判定：机器路径 `observed-pass`，该次回答语义质量不通过；不能反推 NPU 失败，也不能用 10 条探针的机器完成率代替质量。
- 处理：保留原始响应；使用固定提示集、完整响应和人工评审再更新 `quality_status`。
- 根因：`unknown`。需要区分 tokenizer/template、模型能力和训练语言分布。
- 回归：未完成。
- 关联：[验证与限制](04-validation-and-limitations.md)、[教学模型目录](07-teaching-model-catalog.md)

## CHATBOT-ISS-20261020-01：SN0002 摄像头 UAC 麦克风不兼容

- 状态：`open`
- 级别：`major`
- 影响对象：`USB camera / UAC / ALSA`
- 硬件：`SN0002 1080P USB Camera`，USB ID `2bdf:0289`
- 症状：20T 上 UVC 视频可枚举并启动，但 `arecord` 只收到 22 个样本后报 `pcm_read:2221: read error: Input/output error`；8T 独立复现相同 22 样本失败。
- 证据：`usb_set_interface failed (-110)`、`cannot set freq 16000 to ep 0x82`、`device descriptor read/64, error -71`，伴随 USB disconnect/reset。
- 判定：摄像头 UAC 麦克风路径不兼容；视频 UVC 路径不等同于麦克风可用。
- 处理：SN0002 只用于视频，不作为语音输入；使用经过验证的外部 USB 麦克风或 3.5 mm 输入。8T 对照的 UGREEN 摄像头可完成 16 kHz 立体声采集，说明 8T 采集路径本身可工作。
- 根因：`unknown`。未验证供应商固件、USB 音频时钟或系统镜像修复。
- 回归：SN0002 麦克风路径仍失败；UVC 视频在部分运行中可枚举，但当前 8T 复测出现 UVC `-110`、USB reset 和视频节点消失，不能作为稳定视频验收。
- 关联：[板端语音终端计划](08-board-voice-terminal-plan.md#known-camera-microphone-incompatibility)

## CHATBOT-ISS-20261024-01：UI 未反映本地 worker 失败

- 状态：`resolved`
- 判定层级：`UI / service health`
- 环境：Ascend310B4 / 8T，CANN 8.0，统一 `ascend` 环境；Qwen worker 因 HBM 初始化失败退出。
- 症状：UI `GET /health` 和 gateway health 仍返回 HTTP 200，页面显示“就绪”，但 `:8090` 无监听，文字请求只能在下游失败后才暴露问题。
- 根因：`confirmed`。UI health 只表达自身 HTTP 可用，未纳入 worker reachability/readiness。
- 修复：UI health 和 `/api/config` 增加 `worker.managed/reachable/ready/healthy`；worker 未就绪时 `/api/chat` 直接返回 `503 worker_unavailable`，前端禁用发送并显示受控原因。
- 回归：本地 UI 测试覆盖 worker 缺失时 `status=degraded` 和 503；20T 实测新 UI 返回 `worker.healthy=true` 后完成一次真实 JSON 生成。

## CHATBOT-ISS-20261024-02：8T Qwen HBM 初始化失败

- 状态：`open`
- 判定层级：`MindSpore runtime / HBM`
- 环境：Ascend310B4 / 8T；`.212` 使用 CANN 7.0 / MindSpore 2.2.14 / MindNLP 0.3.1，`.249` 使用 CANN 8.0 / MindSpore 2.4.10 / MindNLP 0.4.1。
- 输入与模型：Qwen1.5-0.5B-Chat，锁定 revision `4d14e384a4b037942bb3f3016665157c8bcb70ea`；权重 SHA-256 已通过。
- 症状：模型 tokenizer 和 decoder layer 构造后，MindSpore kernel runtime 报 HBM free 小于总量一半或 `max_device_memory` 大于 allocator 当前可接受范围；worker 未监听 8090，未生成 token。
- 根因：`unknown`。已排除 artifact hash、tokenizer 缺失和 CANN source/ACL import；当前证据指向 8T 上 MindSpore HBM 初始化与运行时保留占用的组合边界。
- 临时处理：加载前读取 `npu-smi` 设备账本。8T 默认要求至少 6144 MB accounted free，不满足时在模型构图前 fail-closed；该数值是防止已观测失败的预检，不是“模型一定能运行”的承诺。
- 回归：20T/CANN8 同一 profile 完成 runtime lock、worker health 和 8-token JSON；8T MindSpore 路径仍标为 `blocked`，不继承 20T 结果。独立的 Qwen2.5 静态 KV ACL 实验已在 B4 上通过短生成和 8-token JSON，但不改变本条 MindSpore 判定。

## CHATBOT-ISS-20261025-01：8T ATC 依赖缺失与编译内存耗尽

- 状态：`resolved`
- 判定层级：`ATC host environment`
- 环境：Ascend310B4 / 8T，CANN `8.0.0`，driver `25.2.0`，统一 `ascend` 环境 Python `3.9.25`
- 输入与模型：Qwen2.5-0.5B 静态 KV ONNX，1,261,082,122 bytes，SHA-256 `b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e`
- 症状：ATC 依次报缺少 `decorator`、`sympy`、`scipy`、`attrs` 和 `psutil`；补齐后，默认编译进程数在主机内存约 7.4 GiB 的板上触发 OOM，`dmesg` 明确记录 `atc.bin` 被 OOM killer 终止。
- 根因：`confirmed`。隔离环境没有包含完整 ATC Python 依赖；CANN 默认 `MAX_COMPILE_CORE_NUMBER=8`，多编译进程同时占用主机内存。
- 修复：在 `ascend` 中安装版本已记录的 ATC 依赖；转换入口使用 `MAX_COMPILE_CORE_NUMBER=1`，保留 `TE_PARALLEL_COMPILER=1`，并从 controller contract 生成 51 个输入 shape。
- 回归：`Ascend310B4` OM 1,266,010,489 bytes，SHA-256 `76396189f4d52ca34affa87d8670d32e0b8c5ea658b50d1b8bb3a36f689cb507`；schema-2 lock、51/49 descriptor、2-token PyACL smoke 和 8-token JSON 均通过。
- 证据边界：ATC 重转 OM 是开发诊断证据；正式发布改用 Hugging Face `zhouxzh/ascend310b-llm-om-zoo@0f516070…` 的 B4 OM `f6650e…40eb8`，九个工件全部重新校验。该修复不代表 MindSpore 8T HBM 问题已解决。

## CHATBOT-ISS-20261025-02：8T UI 忽略 16-token 默认值

- 状态：`resolved`
- 判定层级：`UI / output budget`
- 症状：8T external ACL profile 声明默认 16、上限 80，但页面把 `32/64/80` 作为档位，因 16 不在档位数组而自动选中 80。
- 根因：`confirmed`。前端 token budget 列表没有把 profile 的 `default_max_tokens` 加入候选，也没有为它生成 option。
- 修复：将 fallback/default 纳入排序后的预算集合，初始选择严格使用 profile 默认值；请求校验接受同一个集合。
- 回归：UI 单元测试检查 fallback 生成逻辑；8T 页面重新截图验证默认显示 16 token。

## CHATBOT-ISS-20261025-03：公开 B4 OM 锁仍是 schema 1

- 状态：`resolved`
- 判定层级：`model provenance / runtime artifact lock`
- 症状：公开 HF bundle 的 B4 OM lock 只记录 bytes/SHA 和 ATC 命令，没有顶层 `soc_version`、controller/runtime contract 双哈希；严格 runtime 正确拒绝加载。
- 根因：`confirmed`。公开历史锁早于统一环境的 schema-2 规则。
- 修复：下载器保留公开 schema-1 锁作为来源证据，在全部工件哈希通过后派生 data-dir 内 schema-2 runtime lock；派生锁绑定 B4、OM、ONNX、controller contract 和 descriptor contract，不覆盖内容不同的已有锁。
- 回归：9 项 HF artifact、派生 OM/tokenizer lock、51/49 descriptor 与 ACL health 均通过。

## CHATBOT-ISS-20261025-04：ACL 启动超时超过运行时硬上限

- 状态：`resolved`
- 判定层级：`service configuration`
- 症状：首次 8T launcher 启动时 ACL worker 报 `execution_timeout_seconds must be between 1 and 240`，服务未监听 8084。
- 根因：`confirmed`。环境示例配置为通用 gateway 的 300 秒，但静态 KV runtime 的无线程中止安全上限固定为 240 秒。
- 修复：8T ACL execution timeout 默认和示例统一为 240 秒；gateway 的 300 秒上游超时仍可覆盖模型执行与传输开销。
- 回归：完整 `8084 -> 7861 -> 7865` 启动、JSON、SSE、UI 和固定模型切换拒绝均通过。

## CHATBOT-ISS-20261025-05：HF 镜像 chunked 响应触发下载器误报

- 状态：`resolved`
- 判定层级：`model acquisition / HTTP transfer`
- 症状：全新 8T 数据目录下载 ONNX 后，tokenizer 下载失败并报 `download Content-Length mismatch`。
- 根因：`confirmed`。HF 镜像将 tokenizer 307 重定向到缓存地址，最终响应使用 `Transfer-Encoding: chunked`，没有 `Content-Length`；下载器把“缺少声明长度”误当成“长度不一致”。
- 修复：仅当服务端明确提供 `Content-Length` 时比较声明值；无论该头是否存在，都继续执行实际 bytes 与 SHA-256 校验，避免降低完整性门。
- 回归：单元测试覆盖 chunked 成功、错误声明长度、坏字节和坏哈希；8T 全新数据目录从固定 HF revision 下载 9/9 工件并派生 schema-2 锁通过。

## CHATBOT-ISS-20261025-06：随机 Token 被命令行解析成选项

- 状态：`resolved`
- 判定层级：`acceptance launcher / argument parsing`
- 症状：正式验收服务已经就绪，但 `release_acceptance.py` 报 `argument --token: expected one argument`，未创建报告。
- 根因：`confirmed`。随机生成的 URL-safe token 以 `-` 开头，空格分隔形式让它被 argparse 识别为另一个选项。
- 修复：静态验收统一使用 `--token="${...}"` 和 `--model-source="${...}"`；随机 token 内容和长度规则不变。
- 回归：单元测试固定检查等号形式；公开 commit 的正式验收通过。

## CHATBOT-ISS-20261025-07：验收子进程没有继承 `.env`

- 状态：`resolved`
- 判定层级：`acceptance launcher / environment propagation`
- 症状：服务链和静态 ACL 健康均通过，但 provenance 转去校验 20T `model.safetensors/tokenizer/config`，并报七项 byte/SHA mismatch。
- 根因：`confirmed`。脚本只 `source .env`，没有 `export`；父 shell 能读取变量，但直接启动的 Python 验收进程看不到 `ASCEND_EDGE_CHAT_BACKEND=acl-static-kv` 和 data dir，退回 MindSpore 默认分支。
- 修复：source 前后使用 `set -a` / `set +a`，把 `.env` 显式传播给验收子进程；新增 shell 合同测试。
- 回归：从公开 commit 和全新 HF 数据目录执行完整验收，来源、九项工件、JSON/SSE、长度、稳定性、中文探针和 30 样本性能均通过，`machine_status=passed`。

## CHATBOT-ISS-20261025-08：多模态 USB 音频设备必须先切到有线模式

- 状态：`mitigated`
- 判定层级：`USB device mode / ALSA enumeration`
- 环境：Ascend310B4 / 8T；USB `EDIFIER M16 Pro`；CANN 8.0.0；统一 `ascend` 环境。
- 症状：设备最初未出现在 `lsusb` 的 USB Audio 项、`/proc/asound/cards`、`arecord -l` 和 `aplay -l` 中，系统只看到板载 `ascend310b` 声卡。
- 根因：`confirmed by operator`。多模态音箱/麦克风可能默认处于蓝牙、无线、充电或仅播放模式；切换设备到有线 USB 音频/会议模式并直接重新插入 Host 口后才完成 USB Audio 枚举。
- 最小检查：

  ```bash
  lsusb -t
  cat /proc/asound/cards
  arecord -l
  aplay -l
  ```

- 修复后的现场：`lsusb` 显示 `2d99:a020 EDIFIER M16 Pro`，ALSA 枚举为 `card 1: Pro`，`plughw:CARD=Pro,DEV=0` 播放 48 kHz 双声道 440 Hz 测试音，操作者确认听到声音。
- 麦克风边界：`arecord -l` 仍未出现该卡的 capture 端点；USB descriptor 是 playback-only，`arecord -D plughw:CARD=Pro,DEV=0` 返回设备不存在。不得把 PulseAudio monitor、板载 capture 或其他输入 source 当成该麦克风。
- 处理：先切换实体设备到明确的有线 USB 音频或通话模式，再重新插拔并检查三层枚举。若 `aplay -l` 有设备而 `arecord -l` 没有，停在 USB capture endpoint 层，不要把“喇叭有声”写成“麦克风可用”。
- 回归/剩余工作：喇叭 `observed-pass`；麦克风 `blocked`，需要设备提供 capture endpoint，或改用已验证的 USB capture 设备。

## CHATBOT-ISS-20261024-03：自动语音失败资源累积

- 状态：`resolved`
- 判定层级：`voice resource lifecycle`
- 症状：`auto_listen` 在设备或依赖立即失败时可短间隔持续创建 turn；`arecord` 启动但不输出 PCM 时没有真实读取超时。
- 根因：`confirmed`。历史字典没有容量边界，VAD 的时间限制只依赖已收到的帧。
- 修复：保留最近 32 个终态 turn；自动监听按 1/2 秒退避，连续 3 次失败自动关闭；ALSA 读取使用 monotonic deadline，超时后 TERM/KILL 并等待子进程退出。
- 回归：voice 定向测试覆盖历史淘汰、连续失败自动停止和阻塞 `arecord` 的有界终止。

## 关联文档

- [验证与限制](04-validation-and-limitations.md)
- [教学模型目录](07-teaching-model-catalog.md)
- [板端语音终端计划](08-board-voice-terminal-plan.md)
