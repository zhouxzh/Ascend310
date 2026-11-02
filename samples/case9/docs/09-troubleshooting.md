# 案例 9 测试问题记录

> 本文件是案例 9 的唯一测试问题台账。详细测量和边界见 [验证与限制](04-validation-and-limitations.md) 与 [板端语音终端计划](08-board-voice-terminal-plan.md)。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-10-18至2026-11-02；板级问题按SoC分开记录 |
| 开发板 | Ascend310B1/20T与Ascend310B4/8T，独立地址和独立验收 |
| SoC / 档位 | 两板默认共享MindSpore/MindNLP程序；PyACL static-KV为可选候选 |
| CANN / 驱动 | CANN 8.0.0 / driver 25.2.0 |
| Python / 框架 | 统一`ascend` Python 3.9.25；MindSpore 2.4.10 / MindNLP 0.4.1 |
| 模型 / revision | 默认Qwen1.5-0.5B-Chat固定HF revision；可选PyACL候选单独锁定 |
| 代码提交 | 发布验收必须使用固定公开40位commit和干净工作树；当前工作区结果不是发布复现 |
| 输入与命令 | OpenAI JSON/SSE；统一浏览器UI；可选loopback PyACL候选 |
| 原始证据 | 原始报告保留在各板ignored report目录；8T/20T证据不合并 |

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
| `CHATBOT-ISS-20261025-02` | `resolved` | `major` | 2026-10-25 | 历史 8T UI 漏掉当时 profile 的 16-token 默认档位，页面误选 80 | fallback 加入候选并成为初始选择；当前默认预算后来提高到 64 |
| `CHATBOT-ISS-20261025-03` | `resolved` | `major` | 2026-10-25 | 公开 B4 OM 仍是 schema-1 lock，严格 runtime 拒绝加载 | 保留公开 lock，并生成 data-dir 内 schema-2 lock |
| `CHATBOT-ISS-20261025-04` | `resolved` | `major` | 2026-10-25 | ACL execution timeout 300 秒超过 runtime 240 秒硬上限 | 8T ACL 默认/示例统一为 240 秒，全链路启动通过 |
| `CHATBOT-ISS-20261025-05` | `resolved` | `major` | 2026-10-25 | HF mirror 缓存重定向以 chunked 响应返回 tokenizer，下载器误报 Content-Length 缺失 | 允许无声明长度，但仍强制实际 bytes/SHA；全新目录 9/9 工件下载通过 |
| `CHATBOT-ISS-20261025-06` | `resolved` | `major` | 2026-10-25 | 随机 Gateway token 以 `-` 开头时被 argparse 当成选项，正式验收未启动 | `--token=`/`--model-source=` 使用等号形式，参数测试通过 |
| `CHATBOT-ISS-20261025-07` | `resolved` | `major` | 2026-10-25 | `.env` 只被 shell source 未导出，验收子进程误用 20T 模型 provenance | `set -a` 导出 `.env`；公开 8T 正式验收 `machine_status=passed` |
| `CHATBOT-ISS-20261025-08` | `mitigated` | `major` | 2026-10-25 | 多模态 USB 麦克风/喇叭停留在蓝牙/无线/仅播放模式时没有枚举为音频输入 | 切到有线 USB 音频模式并重新插入后出现 `EDIFIER M16 Pro`；喇叭听音通过，USB 麦克风 capture 仍不可用 |
| `CHATBOT-ISS-20261025-09` | `resolved` | `major` | 2026-10-25 | 8T 页面默认 16 token，正常中文回答很容易过早截断 | 默认预算提高到 64，正常页面只提供 64/80；gateway/UI/catalog 回归通过 |
| `CHATBOT-ISS-20261025-10` | `mitigated` | `major` | 2026-10-25 | 240 秒 ACL watchdog 是工程安全预算而非硬件标定，80 token 已消耗约225秒 | 绕过 watchdog 完成8T 128/256 token和20T 256/853 token；正式配置仍保留保守上限 |
| `CHATBOT-ISS-20261025-11` | `blocked` | `major` | 2026-10-25 | 两个公开 Qwen2.5 int8 ONNX 均含 ATC 未注册的量化算子，无法生成 OM | 8T/B4 和20T/B1 的 ATC 均 exit255；PyACL 门未开始 |
| `CHATBOT-ISS-20261025-12` | `blocked` | `major` | 2026-10-25 | 扩展筛选的12个公开 Qwen 量化 ONNX 全部被算子或 vendor domain 阻断 | 静态图全部 blocked；三个代表族在单板上执行 ATC，均 exit255 |
| `CHATBOT-ISS-20261025-13` | `mitigated` | `major` | 2026-10-25 | q4f16原图不能ATC，但可反量化为FP16后运行；该派生模型没有INT4收益 | 双板ATC/PyACL通过；固定shape速率与官方FP16相同 |
| `CHATBOT-ISS-20261026-01` | `resolved` | `major` | 2026-10-26 | 8T外接USB摄像头麦克风与USB喇叭需要独立硬件验收 | C922 UAC采集和EDIFIER播放通过；完整语音应用未验证 |
| `CHATBOT-ISS-20261102-01` | `mitigated` | `blocker` | 2026-11-02 | 20T PulseAudio板载ALSA sink触发NMI watchdog硬锁及OS panic重启 | runtime-mask音频后12分钟空载及MindSpore 5轮推理无硬锁；LPM告警仍周期出现，根因待驱动/硬件方确认 |

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

- 状态：`mitigated`
- 判定层级：`MindSpore runtime / HBM`
- 环境：Ascend310B4 / 8T；早期`.212`为CANN7.0/MindSpore2.2.14/MindNLP0.3.1，`.249`为CANN8.0/MindSpore2.4.10/MindNLP0.4.1；通过门的16GB复测使用统一`ascend` Python3.9.25、CANN8.0、driver25.2.0。
- 输入与模型：Qwen1.5-0.5B-Chat，revision`4d14e384a4b037942bb3f3016665157c8bcb70ea`；权重SHA-256已通过。
- 症状：早期低空闲HBM状态下，MindSpore kernel runtime在tokenizer/decoder layer构造后初始化失败；worker未监听8090、未生成token。
- 根因：`confirmed`为设备可用HBM不足触发分配失败；不同保留内存状态下是否存在其他B4 runtime边界仍需分开报告。
- 处理：共享MindSpore worker启动前读取npu-smi设备账本；8T默认要求至少6144MB accounted free，不满足则在模型构图前fail-closed。此阈值是已观察失败组合的安全预检，不保证所有占用状态都可加载。
- 回归：2026-10-31 16GB B4清洁启动后，模型在加载前约3727/15610MB已用、accounted free 9556MB时加载并完成非流式/SSE及256-token输出门；20T结果独立记录。旧的低HBM失败历史保留，不据此把B4整体标为不支持。

## CHATBOT-ISS-20261102-01：20T板载音频sink硬锁导致整板重启

- 状态：`mitigated`
- 级别：`blocker`（仅限共享音频/板级稳定性路径；隔离音频后的文字聊天可用）
- 环境：Orange Pi AI Pro 20T，Ascend310B1/20T，Ubuntu22.04.3，kernel5.10.0+，CANN8.0.0，driver25.2.0，统一`ascend` Python3.9.25；UART COM23/115200。
- 输入与命令：未连接外部音频设备；登录桌面后系统自动启动PulseAudio、PipeWire和板载`ascend310b` ALSA sink。服务清单和逐次32-token非音频推理见本问题证据。
- 症状：多次整板重启；设备黑匣子记为`OS_PANIC` / `reboot_reason=OS_PANIC`。NMI watchdog报告CPU0/CPU2 hard lockup，最早panic调用栈线程为`alsa-sink-ascen`，位于`queued_spin_lock_slowpath -> snd_pcm_group_lock_irq -> snd_pcm_sync_ptr/snd_pcm_common_ioctl -> snd_pcm_ioctl`。部分同启动周期出现`xHCI host controller not responding`。
- 同期背景事件：LPM约每301秒记录`Get soc power/current failed`和event `0x80E3A203`；8T也有相同周期性LPM事件但未出现相同音频sink硬锁。LPM是相关底层线索，但仅凭现有证据不能认定为唯一根因。
- 根因状态：`hypothesis`。音频sink内核自旋锁硬锁和watchdog->OS panic链条为`confirmed`；导致ALSA锁挂起的底层硬件/驱动触发仍`unknown`，需厂商按黑匣子进一步分析。
- 隔离处理：以HwHiAiUser对当前user systemd units执行`systemctl --user mask --runtime --now pulseaudio.service pulseaudio.socket pipewire.service pipewire.socket pipewire-media-session.service`，并停止Case9 voice-agent；未改系统持久配置、CANN、driver或固件。XFCE音量面板插件进程可保留，但PulseAudio daemon/socket及`alsa-sink-ascen`线程必须缺席。
- 回归证据：隔离后12分钟空载，uptime从27增至39分钟；LPM计数10增至14，NMI hard-lock计数保持0，xHCI host-controller-dead计数保持0。随后MindSpore Qwen1.5-0.5B成功加载，5轮32-token SSE请求全部返回usage=30 prompt/32 completion/62 total；累计观察约18分钟，无硬锁或重启。该测试证明隔离下文字路径可运行，不证明长期稳定性或音频路径已修复。
- 当前残余风险：音频服务unit runtime-mask仅对本次开机有效；整板重启后unit会恢复启用，需在启动策略中再次隔离。20T语音功能保持关闭。不得为继续测试而取消隔离并重新引发已知硬锁。
- 证据位置：控制器`tmp/case9/20t-isolation-watch.log`和`tmp/case9/20t-serial-isolation.log`；板端`/var/log/npu/hisi_logs/device-0/<timestamp>/bbox/{bbox_info.txt,lpm.txt,kbox.txt}`及`log/kernel.log`。用户授权后可提供脱敏副本给板卡供应商；原始黑匣子不提交Git。
- 后续行动：保持音频隔离用于20T文字测试；向供应商提交SoC/driver/CANN tuple、LPM事件和OS panic kbox；由供应商确认电源测量失败、ALSA锁挂起和xHCI死亡是否相关后，再设计一次受控音频A/B。固件升级、reset、panic策略修改或永久mask前须另行审核。

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
- 症状：历史 8T external ACL profile 当时声明默认 16、上限 80，但页面把 `32/64/80` 作为档位，因 16 不在档位数组而自动选中 80。
- 根因：`confirmed`。前端 token budget 列表没有把 profile 的 `default_max_tokens` 加入候选，也没有为它生成 option。
- 修复：将 fallback/default 纳入排序后的预算集合，初始选择严格使用 profile 默认值；请求校验接受同一个集合。
- 回归：UI 单元测试检查 fallback 生成逻辑；当时 8T 页面截图验证默认显示 16 token。后续正常聊天可用性调整将默认值提高到 64，见 `CHATBOT-ISS-20261025-09`。

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

## CHATBOT-ISS-20261025-09：16-token 默认预算不适合正常聊天

- 状态：`resolved`
- 判定等级：`UI/API default budget`
- 症状：8T 页面默认请求 16 token，短句或自我介绍常在完整回答前返回 `finish_reason=length`。
- 根因：`confirmed`。16 token 适合冒烟和延迟测试，但不足以覆盖普通中文回答；80 token 硬上限本身并非问题。
- 修复：把 ACL 固定 profile、gateway 和 UI 的默认值统一提高到 64；正常页面只提供 `64/80` 两个档位，80 为硬上限。低于 64 的 API 预算仍仅供自动化协议和性能诊断，不作为用户选项。后端模型和 1024-token 上下文未改变。
- 回归：gateway、UI 和 catalog 测试检查默认值为 64。板端固定问题“请简单介绍一下昇腾310B”在 `max_tokens=80` 下返回 41 个 completion token、54 个字符、`finish_reason=stop`，说明 80-token 硬上限能生成一段完整回答；41 token 也低于新的 64-token 默认值。
- 质量边界：该回答虽然正常结束，但出现重复句，不能据此宣称中文语义质量通过。
- 剩余边界：64 token 仍不是汉字数保证，复杂回答可能达到预算并以 `length` 结束；页面必须保留该终止原因，不能把截断当作完整回答。

## CHATBOT-ISS-20261025-10：240 秒 watchdog 的来源与 token 上限实验

- 状态：`mitigated`
- 判定层级：`ACL runtime safety policy / generation budget`
- 来源：`confirmed as engineering policy`。历史静态 ACL 方案把预算固定为 ACL `240 s`、gateway `270 s`、UI `300 s`。代码注释说明 PyACL 同步执行无法由 Python 线程安全中断，因此超时只能取消请求、毒化 backend、标记 unhealthy 并要求重启。没有证据表明 240 秒来自硬件规格或穷举校准。
- 当前风险：8T 的 80-token 压力请求耗时 `225.071 s`，距离 240 秒只剩约 15 秒。该配置可以用于受控短回答，但不是更高输出预算的余量证明。
- 绕过 watchdog 的 8T 实验：临时测试 worker 使用 `max_tokens=1024`、`execution_timeout=3600 s`，同一 B4 OM 和 1024 context：
  - 80 token：80 completion、`length`、约 `225.1 s`，NPU 约 `5.9 GB`。
  - 128 token：128 completion、`length`、约 `516.0 s`，worker healthy，`watchdog_triggered=false`。
  - 256 token：256 completion、`length`、约 `1983.8 s`，worker healthy，NPU 仍约 `5.9 GB`。
  - prompt 55 token 时，context 理论上限为 `1024-55=969`；未用完整生成等待约两小时，不能写成 observed-pass。
- 20T 实验：公开 MindSpore profile 正式默认128、上限256；同一长输出方法下：
  - 256 token：256 completion、`length`、约 `144.0 s`。
  - 临时把 worker/gateway/profile 上限提高到1024后，171-token prompt 生成850 token约 `508.0 s`，再生成853 token约 `483.6 s`，`context_used=1024`、`length`，worker healthy。
  - 20T 的该 prompt 最大可接受 completion 为 `1024-171=853`。
- 结论：实际 token 上限由 `context_length - prompt_tokens` 决定，不由显存总量直接决定。8T 静态 KV 在固定窗口下显存近似恒定；20T MindSpore 的 worker RSS/NPU 使用量随模型/序列状态变化，但850/853 token 未 OOM。
- 未采用：直接把正式 8T 上限提升到128/256。单次请求会达到8到33分钟，当前产品交互和240秒清理策略无法承受；如要提升，必须同时重新设计 watchdog、流式心跳、取消和容量预算。
- 正式保留：8T UI默认64、上限80；20T默认128、平台上限256。实验上限不等于发布上限。

## CHATBOT-ISS-20261025-11：公开 int8 ONNX 在 ATC 转换层被拒绝

- 状态：`blocked`
- 判定层级：`ONNX operator compatibility / ATC conversion`
- 环境：Ascend310B4 / 8T 与 Ascend310B1 / 20T；均为 CANN 8.0.0、驱动25.2.0、统一 `ascend` 环境。
- 候选：
  - `onnx-community/Qwen2.5-0.5B-Instruct@22942cb7...`，`model_int8.onnx`，512,096,557 bytes，SHA-256 `41834041...16100`。
  - `Ringoacid/Qwen2.5-0.5B-onnx-attention-int8@8b6afbb2...`，`generate_int8.onnx` 3,254,466 bytes + external data494,222,848 bytes，SHA-256 `7c547253...d421` 和 `8da9f4b7...b00d`。
- 合同：两个图均为51输入/49输出、动态 batch/cache；分别含168个 `MatMulInteger`、96个 `DynamicQuantizeLinear` 和2个 `DequantizeLinear`。
- 复现：给51个输入指定静态 decode shape后，分别在 B4 和 B1 上执行 `atc --framework=5 --precision_mode=must_keep_origin_dtype`。
- 结果：四个 ATC 调用全部 exit255，CANN 报 `No parser is registered`，没有生成任何 OM。两个板上的 unsupported 计数一致。
- 边界：这是 ATC conversion 失败，不是 PyACL 加载失败，也不是精度或性能结果。不能把 `.onnx` 交给 PyACL 直接运行。
- 处理方向：先导出不含 ORT 量化算子的标准 FP16/FP32 静态 ONNX，建立 CPU/框架 golden，再走 CANN 支持的 ATC 量化和校准路径；出现自定义算子时必须有版本匹配的自定义 OPP。
- 数据清理：实验结束后已删除两台板上的候选 ONNX、外部权重、派生模型、OM 和临时日志；公开来源、revision、哈希及结论保留在 `11-quantized-onnx-screening.md`，可按固定 revision 重建。

## CHATBOT-ISS-20261025-12：公开 Qwen 量化 ONNX 扩展筛选全部阻断

- 状态：`blocked`
- 判定层级：`ONNX domain/operator compatibility`
- 候选：12个唯一公开文件，来自 Qwen1.5-0.5B-Chat、Qwen2.5-0.5B-Instruct GQA、Qwen2.5-0.5B-Instruct MHA、Hazem ORT int4 和 AMD Ryzen AI int4。完整 repo、revision、字节数和 SHA-256 见 `configs/online-quantized-qwen-candidates.json`。
- 静态检查：12/12 命中禁止的量化算子或 `com.microsoft` domain。主要是 `MatMulInteger`、`DynamicQuantizeLinear`、`DequantizeLinear`、`MatMulNBits`、`GroupQueryAttention`、`MultiHeadAttention`、`RotaryEmbedding` 和 vendor normalization。
- 8T ATC：Qwen1.5 uint8 exit255（量化 parser）；Qwen1.5 q4 exit255（两个 domain version）；Qwen2.5 GQA uint8 exit255（两个 domain version）。
- 20T ATC：Qwen1.5 uint8 exit255（量化 parser）；Qwen1.5 q4f16 和 Qwen2.5 MHA uint8 exit255（两个 domain version）。
- 结果：六个代表性 ATC 均未生成 OM；其余文件复用已经确认的运算符模式，未做重复 ATC。
- 根因边界：失败发生在图解析/算子注册层，早于 OM 分配和 NPU 执行，不是8T/20T内存不足。
- 后续效率：不同新候选先分配到一块板做图门和 ATC；只有生成 OM 的候选才在另一块板独立复验。
- 工具：`scripts/verify/screen-online-quantized-onnx.py` 对候选锁执行字节/SHA、ONNX checker、domain、operator 和动态 shape 检查。详细记录见 `11-quantized-onnx-screening.md`。

## CHATBOT-ISS-20261025-13：q4f16图迁移成功但不保留INT4

- 状态：`mitigated`
- 判定层级：`ONNX graph surgery / ATC / PyACL performance`
- 来源：`onnx-community/Qwen1.5-0.5B-Chat-ONNX@6d413dd9...` 的 `model_q4f16.onnx`，SHA-256 `320e83b2...11cf`。
- 根因：原图含168个 `com.microsoft.MatMulNBits`。CANN 8.0不能解析第二个domain和该贡献算子。
- 迁移：按q4字节内相邻K元素的低/高半字节布局解包，默认zero point为8，scale顺序为 `[N,K_blocks]`，生成标准FP16 `MatMul`。同时给embedding的隐式 `Transpose` 写入显式 `perm=[1,0]`。
- 结果：生成928,515,726字节派生ONNX，SHA-256 `9707358b...d090`；8T与20T独立生成相同哈希。
- 数值：对原ORT q4f16图，logits cosine `0.99999994`、Top-1一致、Top-5交集5/5、最大KV差 `0.00390625`。
- 转换与运行：B4和B1 ATC均通过，PyACL均加载51输入/49输出OM并返回相同Top-1 token。
- 速率：短shape下8T约`5.95 token/s`、20T约`10.23 token/s`，与同仓库官方FP16模型差异低于0.05%。B4长上下文execute-only约`2.57 token/s`，也与FP16相同。
- 结论：迁移解决的是ATC可转换性，不是NPU INT4执行。派生模型将463MB的q4 ONNX扩展为886MB FP16 ONNX，没有观察到量化性能收益。
- 原始与派生哈希、四块OM及两轮基准见 `12-q4f16-onnx-migration-and-decode-rate.md`。

## CHATBOT-ISS-20261026-01：8T USB音频硬件验证

- 状态：`resolved`
- 判定层级：`ALSA/PulseAudio hardware path`
- 开发板：Ascend310B4 / 8T，CANN 8.0.0，驱动25.2.0。
- 喇叭：Logitech以外的外接设备为`EDIFIER M16 Pro`，USB `2d99:a020`，ALSA `CARD=Pro,DEV=0`，只有playback端点。默认sink为该USB sink，25%音量、未静音。直接ALSA播放和PulseAudio `paplay`均成功，用户人耳确认发声。
- 麦克风：Logitech `C922 Pro Stream Webcam`，USB `046d:085c`，同时提供UVC（`/dev/video2`、`/dev/video3`）和UAC capture（`CARD=Webcam,DEV=0`，32 kHz stereo）。PulseAudio source为`alsa_input.usb-046d_C922...analog-stereo`，不是monitor。
- 路由与增益：C922硬件`Mic Capture Volume`从0提高到实际读数12，`Mic Capture Switch=on`；Pulse source为80%、未静音，并设为默认source。
- 采集证据：增益前5秒WAV为160,000帧、32 kHz、双声道，已经有非零信号；提高增益后6秒录音峰值最高3631；15秒敲击测试在约6.8–8.7秒出现明确冲击峰，最高3168；不带`--device`的`parecord`从默认source录得RMS461、峰值3087的非零PCM，证明应用默认输入路径指向实体麦克风。
- 语音软件：已在统一`ascend`环境安装`sherpa-onnx 1.13.6`，安装系统`espeak-ng 1.50`，并从`csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23@204ad334...`下载校验int8 ASR模型；官方测试WAV能转写为中文。
- 服务状态：voice-agent已启动并监听`127.0.0.1:7862`，ASR/TTS readiness均为true，设备已绑定为`alsa-capture-2-0`和`alsa-playback-1-0`。
- 剩余问题：实际C922语音turn出现ASR空文本和一次`arecord capture timed out`，自动监听已停止；需要继续校准VAD阈值/输入增益并完成真实语音闭环，不能宣称语音对话已通过。
- 自动化改进：程序加入200ms噪声底校准、动态VAD阈值和有界软件AGC；合成PCM单元测试通过，但尚未在8T真实房间和C922上完成验收。
- 部署状态：2026-10-26已同步到8T正式checkout并重启voice-agent、UI、gateway和ACL worker；板端定向测试`32 passed`。语音接口确认AGC启用、动态VAD启用、C922与EDIFIER型号可见，设备已重新绑定且`auto_listen=false`。
- 设备变化与修复：后续现场USB摄像头变为`Jieli Technology USB Composite Device`（`4c4a:4a55`），其输入不接受直接`hw:2,0`的2声道16kHz合同，报`Channels count non available`。音频枚举已保留实际USB型号，录音统一通过`plughw`完成格式/声道转换。
- 监听交互：固定30秒窗口无语音时由普通失败改为`no_speech`，不再累计三次失败后停止监听；语音回复预算改为32 token，前端每1.5秒刷新turn状态并显示识别文本、回复或错误。
- USB热插拔：voice-agent原先永久缓存第一次ALSA设备枚举；现改为每次读取配置和turn状态都重新枚举。2026-10-26在8T将摄像头更换为Jieli `4c4a:4a55`后，接口已刷新为新设备，不再保留旧C922标签。
- 双板统一部署：8T正式checkout和20T `case9-20t-max-token-7c60983` 已同步相同的`web.py`、voice核心及测试文件，关键文件SHA-256一致。两板定向测试分别通过。
- 20T音频：C922 capture绑定`alsa-capture-0-0`，板载3.5mm playback绑定`alsa-playback-1-0`；`Deviceid=2`、Playback 25%，短WAV播放命令成功。20T双板和8T文本聊天API均完成一次非流式回复；20T 16-token结果为截断，需单独记录模型质量。
- 按住说话回放：现有语音面板增加指针按住录音、松开内存回放，不经过ASR/LLM/TTS。停止`arecord`时预期的EINTR返回不再被误判为失败；voice-agent启动时补齐用户`XDG_RUNTIME_DIR`和DBus地址以支持PulseAudio回退。8T API实测完成1.0秒录音并成功从板载3.5mm播放。
- 原始WAV仅保留在板端`/tmp`，不是发布工件；可删除并重新生成。

## CASE9-ISS-20261030-01：20T B1 固定KV候选的64-token后重启观察

- 状态：`open`
- 判定层级：`runtime stability / board restart`
- 测试环境：Ascend310B1 / 20T，CANN 8.0.0，driver 25.2.0，统一`ascend` Python 3.9.25；B1 static-KV OM为1,266,009,438 bytes、SHA-256 `6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609`。
- 症状：初始候选目录允许32/64/80 token，但worker错误地把候选默认值32作为实例硬上限，64-token请求首先返回`max_tokens must be between 1 and 32`。修复后实例健康上报`max_tokens=80`，64-token UI请求抵达ACL并在gateway日志中记录HTTP 200，之后开发板SSH中断并在`2026-10-30 18:40:45`重新启动。
- 最小复现：以独立`127.0.0.1:8086`启动B1 ACL候选，运行`bash scripts/verify/run-20t-b1-static-kv-token-ceiling.sh`；不要使用正式UI端口或8T B4 OM。
- 根因：`unknown`。重启前的内核环形日志在重启后不可用，不能把重启归因于64-token请求、ACL执行、音频或网络。
- 临时处理：浏览器B1候选默认32 token，可选32/64/80作为实验请求预算；运行时实例上限为80。64/80不应表述为重启自由的稳定性保证。
- 回归：独立8086逐档请求8至80均HTTP 200且worker健康；正常解码提示自然在5或18 token停止。新增仅限B1隔离实验的强制解码模式，屏蔽合同EOS并关闭句末停；80、128和256档分别实际生成80/128/256 token并以`length`结束，耗时188.726/465.970/1829.767秒，各档boot ID前后一致且worker健康。详见 [20T B1 Static-KV Token Ceiling](14-20t-b1-static-kv-token-ceiling.md)。
- 剩余边界：当前观测到的强制运行时上限为256 token。512档正在独立8086 worker上运行；只有其满足`length`、boot ID、健康与NPU内存门，才继续768和上下文余量。

## CASE9-ISS-20261031-01：16GB 8T MindSpore加载前HBM基线污染

- 状态：`resolved`
- 判定层级：`environment / model load / API smoke`
- 测试环境：Ascend310B4 / 8T，NPU总内存15,610 MB，CANN 8.0.0，driver 25.2.0；统一`ascend`环境，Python 3.9.25、MindSpore 2.4.10、MindNLP 0.4.1，`PYTHONNOUSERSITE=1`。
- 初始事件：旧NPU记账占用13,982/15,610 MB时，worker按HBM门拒绝加载；该次是脏基线，不是16GB能力结论。
- 根因与适配：清洁重启后HBM门通过；8T旧MindNLP序列化器把BF16 NumPy itemsize识别为8，统一worker启用程序级BF16兼容适配，不修改系统Python或替换8T环境包。
- 结果：Qwen1.5-0.5B完成MindSpore Ascend模型加载；health为`context_verified`、`bf16_loader_compat=true`；非流式8-token生成HTTP 200/12秒，SSE 8-token生成HTTP 200/5秒并收到`[DONE]`。加载前后系统RAM与NPU内存见 [16GB 8T MindSpore Validation](16-8t-16gb-mindspore-validation.md)。
- 清理：worker停止，8090释放；NPU记账从14144降至5355 MB，系统RAM used从10062降至1288 MB。

## CASE9-ISS-20261101-01：8T MindSpore切换后的NPU内存累积与串口硬件事件

- 状态：`mitigated`
- 判定层级：`device / driver / runtime cleanup`
- 测试环境：16GB Ascend310B4 / 8T，CANN 8.0.0，driver 25.2.0，统一`ascend` Python 3.9.25；COM6串口115200持续可用。
- 症状：历史MindSpore/ACL测试后会出现SSH超时或用户感知的“死机”。本次重启后以串口监控，开机干净NPU记账为2224/15610 MB；Qwen1.5加载、32-token与256-token生成均完成，SSH和串口未中断。
- 串口设备证据：MindSpore启动前已出现APEI `recoverable`硬件事件和MATA RAS `error_code=0x50e`；LPM `0x80E3A203` / bbox `0xa6193215`约每5分钟出现，空闲和MindSpore `launch_queue`负载期间都可见。没有Kernel panic、OOM、watchdog/lockup或device reset证据，不能把单次LPM事件写成死机根因。
- 可重复机制：同一boot会话中，NPU记账从2224 MB基线上升；Qwen1.5停止后仍约5304 MB，随后Qwen2.5仅初始化context并被HBM门拒绝后仍约7234 MB。无业务进程时记账仍累积，导致后续模型可用HBM减少；严格8192 MB门阻止了继续加载和潜在失稳。
- 根因状态：`hypothesis`。当前证据支持“框架/驱动清理不完全导致同一boot内存累积”是直接风险机制；板端MATA RAS是独立的硬件/驱动风险线索。尚未复现真正整板死机，不能确定两者谁是最终根因。
- 修复：统一worker的`close()`新增释放模型引用、Python GC、调用`mindspore.ms_memory_recycle()`与`mindspore.hal.empty_cache()`；注册SIGTERM优雅停机，health新增`memory_recycle_status`。本地回归15项通过；板端日志确认收到SIGTERM并记录`ms_memory_recycle+hal.empty_cache`，但干净重启后的加载→停止对照仍从2266升至4976 MB，残留约2710 MB。
- 当前结论：worker生命周期修复已生效，但设备记账仍未回到基线；根因继续标为`hypothesis`，下一层应检查MindSpore/CANN/驱动的设备内存生命周期，不能继续通过重复模型切换扩大风险。
- 证据路径：板端`/home/HwHiAiUser/Documents/ascend-edge-chat-ms-8t/reports/serial-debug-qwen15-20261101T1512/`；256-token请求实际完成256、`length`。Qwen2.5-0.5B资产通过校验，但首次启动因可用HBM 7576 MB低于8192 MB门而在权重加载前停止。

## CASE9-ISS-20261101-05：20T 2048全上下文FP16 PyACL OM输出损坏

- 状态：`observed-degraded`
- 判定层级：`task-quality / text integrity`
- 测试环境：Ascend310B1 / 20T，CANN 8.0.0、driver 25.2.0，统一`ascend` Python 3.9.25；Qwen2.5-0.5B static 2048 B1 OM，FP16全上下文合同，3个`[1,2048] int64`输入和约622MB logits输出。
- 结果：纯PyACL控制器在统一环境加载、描述符验证和health均通过。8-token loopback API返回HTTP 200，耗时59.358秒。
- 症状：回答为` Holden Holden Holden 纲纲 radiation�`，含重复无关词和U+FFFD替换字符。
- 根因状态：`unknown`。执行路径通过但文本质量/完整性失败，可能涉及静态2048导出、输出选择、精度或tokenizer合同；不是当前B1 static-KV候选的替代品。
- 清理：8091已释放；停止后系统可用RAM约22.9GiB、NPU记账6090/23673MB。
- 下一步：不将该OM加入浏览器目录；若继续研究，先建立相同prompt的MindSpore/PyACL logits数值比较和tokenizer对照。

## CASE9-ISS-20261101-04：8T Qwen1.5 1.8B首次生成连续内存分配失败

- 状态：`observed-fail`
- 判定层级：`runtime / memory allocation`
- 测试环境：Ascend310B4 / 8T，CANN 8.0.0、driver 25.2.0，统一`ascend` Python 3.9.25，MindSpore 2.4.10、MindNLP 0.4.1；模型`Qwen1.5-1.8B-Chat@e482ee3f73c375a627a16fdf66fd0c8279743ca6`，七项固定revision资产从hf-mirror下载且哈希通过。
- 最小复现：模型达到health ready；提交8-token中文问候，HTTP返回503，worker fail-closed。
- 原始错误：`MindSpore generation failed: Alloc failed, size:632186368, stream_id:0`。加载期HBM检查显示约8382 MB设备记账余量，但首次生成需要额外连续约603 MiB分配。
- 内存证据：失败时`MemAvailable`降至约930 MiB、匿名页约9.6 GiB；优雅停止后可用RAM恢复约13.7 GiB、匿名页约927 MiB，8090释放且NPU记账回到1957/15610 MB。
- 根因状态：`confirmed`为当前组合的生成期内存分配不足；不是下载完整性、模型加载失败、设备节点丢失或系统死机。未采用降低安全门或CPU回退。
- 下一步：该模型不作为16GB 8T正常服务候选；若需继续，应评估量化/更低内存图或在20T上独立验证。

## CASE9-ISS-20261101-03：8T Qwen2.5 1.5B长输出文本替换字符

- 状态：`observed-degraded`
- 判定层级：`text integrity / generation`
- 测试环境：Ascend310B4 / 8T，CANN 8.0.0、driver 25.2.0，统一`ascend` Python 3.9.25，MindSpore 2.4.10，MindNLP 0.4.1；模型`Qwen2.5-1.5B-Instruct@989aa7980e4cf806f80c7fef2b1adb7bc71aa306`，七项资产哈希通过。
- 结果：中文和英文32/64/96-token长文均HTTP 200、无U+FFFD、无角色标记并保持worker健康。中文128-token长文重复两次均完成128 token和`finish_reason=length`，且每次恰有1个U+FFFD；英文128-token长文完成128 token、无U+FFFD。
- 资源证据：128-token运行期间NPU可达高占用；SIGTERM优雅停止后8090释放、NPU回到2320/15610 MB、系统匿名页和可用内存恢复。
- 根因状态：`hypothesis`。异常随中文长输出预算出现而可重复，英文同预算未复现；当前更像中文tokenizer/解码或文本序列化边界问题，不是模型加载、设备崩溃或一般性内存不足结论。中文128以上预算不作为正常聊天推荐预算；英文128在本轮完整通过。
- 下一步：保留中英文32/64为当前干净预算；若需提高中文预算，应收集出错位置附近的token id、UTF-8字节和SSE增量后再定位。

## CASE9-ISS-20261101-02：8T DeepSeek-R1 1.5B MindSpore TBE主进程退出

- 状态：`observed-fail`
- 判定层级：`model-load / TBE`
- 测试环境：Ascend310B4 / 8T，16GB系统/NPU内存，CANN 8.0.0，driver 25.2.0，统一`ascend` Python 3.9.25、MindSpore 2.4.10、MindNLP 0.4.1；模型`DeepSeek-R1-Distill-Qwen-1.5B@0a28897fe71fdd30de350b667ae588601a85990f`，五项资产哈希通过。
- 最小复现：启动同一Case9 MindSpore worker，使用8-token预算和B4实验profile；未达到health ready。
- 症状：加载期记录`Free memory size is less than half of total memory size`，随后出现8次`TBE Subprocess[task_distribute] ... main process disappeared!`；resource tracker报告30个遗留semaphore。没有成功HTTP请求。
- 设备证据：worker已退出，8090已释放，NPU记账回到1571/15610 MB；没有将该模型失败写成整板死机或硬件不支持。
- 根因状态：`unknown`。失败发生在MindSpore/TBE加载层，需在独立干净boot下用更完整的TBE/plog证据继续定位；不得通过CPU回退或跳过TBE取得通过结果。
- 下一步：继续验证其他独立候选；DeepSeek重试前保留原始worker日志，收集TBE/plog和同版本20T对照。

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
