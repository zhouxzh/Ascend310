# 案例 9 测试问题记录

> 本文件是案例 9 的唯一测试问题台账。详细测量和边界见 [验证与限制](04-validation-and-limitations.md) 与 [板端语音终端计划](08-board-voice-terminal-plan.md)。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-10-18；摄像头问题归档于 2026-10-20 |
| 开发板 | Ascend310B1 / 20T；摄像头问题另含 8T 对照 |
| SoC / 档位 | 20T；8T 结论分开记录 |
| CANN / 驱动 | CANN 8.0.0 / driver 25.2.0 |
| Python / 框架 | Python 3.9.2；MindSpore 2.4.10；MindNLP 0.4.1 |
| 模型 / revision | Qwen2-0.5B、Qwen2-1.5B、Qwen2.5、TinyLlama 等教学 profile；revision 见各条目 |
| 代码提交 | 以验收时部署目录为准 |
| 输入与命令 | OpenAI JSON/SSE；浏览器 UI；USB UAC 麦克风测试 |
| 原始证据 | `run/chatbot-acceptance-20261018-20t-spaced2.json` 等板端 ignored evidence |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CHATBOT-ISS-20261018-01` | `mitigated` | `major` | 2026-10-18 | 无间隔验收触发 HTTP 429 | 加请求间隔后通过 |
| `CHATBOT-ISS-20261018-02` | `resolved` | `major` | 2026-10-18 | Qwen2 tokenizer 缺少 ChatML 模板，出现角色标记续写 | 修正模板后合同检查通过 |
| `CHATBOT-ISS-20261018-03` | `open` | `major` | 2026-10-18 | 中文提问返回英文道歉，语义质量未通过该次期望 | 质量评审仍待完成 |
| `CHATBOT-ISS-20261020-01` | `open` | `major` | 2026-10-20 归档 | SN0002 UVC/UAC 摄像头麦克风读取 I/O error | 视频可用；麦克风不可用 |

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

## 关联文档

- [验证与限制](04-validation-and-limitations.md)
- [教学模型目录](07-teaching-model-catalog.md)
- [板端语音终端计划](08-board-voice-terminal-plan.md)
