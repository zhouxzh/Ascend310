# Case9 历史测试结果归档

## 记录目的

本文按时间和候选归档前序测试，明确每项证据属于协议、静态检查、工件完整性、真实
ACL/NPU、音频 I/O、质量还是计划状态。历史结果用于解释决策和失败原因，不自动升级
为当前模型或当前板的验收结论。

特别注意：`192.168.8.178` 是旧开发板，`192.168.1.90` 是当前开发板。两者虽然都
报告为 Ascend310B4/8T，仍必须分别引用报告、源文件 SHA、进程和端口。跨板复制结果
会破坏证据边界。

## 时间线

| 时间 | 板卡/范围 | 结果类别 | 结论和文档 |
| --- | --- | --- | --- |
| 2026-08-20 | 替换板；网关协议 | `passed`（协议） | 受控 stub 验证 `/health`、Bearer 鉴权、JSON/SSE 和局域网访问；没有真实 LLM、ATC、ACL 或 NPU 推理。见 [`docs/01`](01-board-gateway-acceptance.md)。 |
| 2026-08-21 | `.178`；本地音频基础检查 | `passed`（设备 I/O） | C922 采集 16,000 样本、RMS 41.5，USB sink 静音播放通过；没有 10 条中文 PTT 闭环。见 [`docs/03`](03-local-chat-validation.md)。 |
| 2026-08-21 | `.178`；Qwen GGUF/llama.cpp | `artifact-passed / build-failed` | Qwen2.5 GGUF 哈希通过；llama.cpp CANN 在 CANN 8.0 缺头文件，未生成 `llama-server`。见下文。 |
| 2026-08-21 | `.178`；Qwen1.5 通用 ONNX | `blocked` | 文件内容完整，但 51 输入/49 输出、动态 KV 和未放行 `Sigmoid` 不满足 ACL contract；未执行 ATC/OM/ACL。见 [`docs/07`](07-acl-om-validation-record.md)。 |
| 2026-08-21 | `.178`；TinyLlama 预编译 OM | `api-passed / Chinese not-admitted` | descriptor、真实 ACL execute、NPU 生成、8080 JSON/SSE 和 7861 网关通过；中文探测失败，10 轮观察有内存增长。见 [`docs/09`](09-tinyllama-acl-om-validation-record.md)。 |
| 2026-08-21 | `.178`；文字页面 | `passed`（页面/SSE） | 页面和文字请求返回完整 delta；不代表中文质量、音频或小智。见 [`docs/10`](10-text-chat-ui.md)。 |
| 2026-08-22 | `.90`；代码审核和隔离复核 | `passed`（实验链路） | 独立 `case9-acl-om` 在 8081 完成 Tiny smoke/API，7861 网关和 7863 文字页回归通过；中文质量未接纳，10 轮稳定性未执行。见 [`docs/11`](11-code-review-optimization-and-board-192-168-1-90.md)。 |
| 2026-08-23 | `.90`；Qwen2.5 静态 FP16 ONNX | `contract/ATC/ACL/API/isolated-gateway-passed` | 外部 CPU 导出、静态图检查、Ascend310B4 ATC、OM descriptor、真实单 token 生成、8082 JSON/SSE 和 7864 隔离网关通过；中文质量和稳定性未完成。见 [`docs/15`](15-qwen25-static-onnx-validation-record.md)。 |
| 2026-08-23 | `.90`；Qwen2.5 last-logits 优化候选 | `optimization/ATC/ACL/API-passed; speedup-unproven` | 通过公开输出收窄、独立 8083 descriptor、ACL/NPU、JSON/SSE 和原图 token 对照；固定 2048 主体计算仍保留，中文质量、稳定性和正式切换未完成。见 [`docs/16`](16-qwen25-optimization-research-and-last-logits-validation.md)。 |
| 2026-08-23 | `.90`；Qwen2.5 静态 KV 1024 FP32 候选 | `artifact/ATC/ACL/NPU/API/UI/formal-passed; Chinese 8/10` | 板端完成原子传输、Ascend310B4 ATC、OM descriptor、真实 ACL/NPU、隔离 JSON/SSE、网关/UI、同协议性能对照和正式 `8080 -> 7861 -> 7865` 提升；p50/p95 改善 48.79%/48.70%。早期传输复位作为历史失败保留。见 [`docs/18`](18-qwen25-static-kv-1024-validation-record.md)。 |

## 结果分类规则

| 标签 | 含义 | 不能推出的结论 |
| --- | --- | --- |
| `artifact-verified` | 文件大小、来源/revision 和 SHA-256 匹配 | 不能推出 ONNX 契约、ATC、OM 或推理正确 |
| `static/protocol-passed` | Python/前端/HTTP/SSE 契约测试通过 | 不能推出 NPU 执行或语言质量 |
| `acl-smoke-passed` | 板端 ACL 初始化、模型加载和至少一次真实 execute 成功 | 不能推出中文能力、长时稳定性或数值一致性 |
| `api-passed` | JSON/SSE 和网关转发符合接口契约 | 不能推出模型适合中文或小智设备 |
| `failed` / `blocked` | 门禁明确失败或被前置条件阻断 | 不能自动切换其他后端来填补结果 |
| `not-run` | 尚未执行或没有原始证据 | 不能解释为通过 |

## 网关与前端历史结果

### 受控网关 stub

`docs/01` 记录了一个仅监听 loopback 的标准库上游 `127.0.0.1:18080` 和网关
`127.0.0.1:7861`。`/health`、带 Bearer 的 `/v1/models`、未知模型拒绝、JSON 转发、
SSE 两个 delta 及 `[DONE]` 均通过；上游返回的是合成文本，不运行任何模型。因此这
只是网关协议证据，不能被写作“LLM 已部署”。

### 文字页面

前端在 Windows 上完成 `npm ci`、`npm test`（4/4）和 `npm run build`。`.178` 的
文字页面冒烟观察到 `start`、一个完整 `delta` 和 `done`，约 26.5 秒；`.90` 后续在
`0.0.0.0:7863` 完成 health/config/static、SSE、history/clear 和 104 个 Web/协议
测试（1 个跳过）。页面无浏览器鉴权，只适合实验网络；网关 token 保留在板端。

这些结果证明页面和代理协议，不证明中文生成、音频、ASR/TTS 或小智协议。

## 音频、ASR 和 TTS 历史结果

在旧板 `.178` 的 `case9-local-chat` 环境中，基础设备和运行时检查记录为：

| 检查 | 实测结果 | 证据边界 |
| --- | --- | --- |
| sherpa-onnx | `1.13.6`，Python `3.9.25`，Zipformer 和 Huayan runtime 可导入 | 不代表 10 条中文识别质量 |
| C922 麦克风 | PulseAudio 转换为 16 kHz、单声道；1 秒得到 `16,000` 样本，RMS `41.5` | 只证明一次采样设备 I/O |
| USB 喇叭 | 22.05 kHz 单声道静音 PCM 交给 `paplay` 返回成功 | 没有听感或连续播放质量指标 |
| 原始 PCM | 未写 WAV、日志或数据库 | 隐私边界通过；不提供音频样本 |
| 10 条中文 PTT | `待执行` | 不能用静态测试或文本 API 代替 |
| p50/p95 | `未完成` | 没有 ASR 完成、LLM 首 token、TTS 首音频和总时延分位数 |

`.178` 的 `local_app` 文本回归曾播放文本回复的 TTS，但没有 `ptt_start`、麦克风采集
或 ASR；`.90` 本轮没有启动音频服务。音频路径必须等新的 LLM 通过后单独验收，且不能
把设备采样成功写成语音聊天闭环。

## Qwen GGUF 与 llama.cpp 历史结果

### Qwen2.5 GGUF 工件

控制器/旧板记录了 Qwen2.5-0.5B Q4_0：`428,730,208` bytes，revision
`12145bd1d629190a4d44254073650877954d02c9`，SHA-256
`7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed`。文件完整性通过，
但没有对应的 310B4 ACL/NPU 推理证据；它只是历史候选，不能替代 OM 服务。

### llama.cpp CANN 构建

固定源码 revision 为 `d9b6be07d0864ab09417b17ba36f9788087dd22c`。板端 CANN `8.0.0`
编译失败，缺少：

```text
aclnnop/aclnn_recurrent_gated_delta_rule.h: No such file or directory
```

没有生成 `llama-server`，也没有执行 CANN 初始化、ACL/OM 推理、NPU 生成或 OpenAI
completion。该结果是构建失败证据，不是 CPU 回退的许可；后续方案不得把它重新标为
当前首选。

## Qwen1.5 通用 ONNX ACL/OM 历史结果

固定候选为 `onnx-community/Qwen1.5-0.5B-Chat-ONNX`，revision
`6d413dd9a252749e0760902c93331e3e4e65b73c`：

| 工件 | bytes | SHA-256 | 内容完整性 |
| --- | ---: | --- | --- |
| `onnx/model_fp16.onnx` | `928,499,243` | `1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262` | 通过 |
| `tokenizer.json` | `11,418,266` | `bcfe42da0a4497e8b2b172c1f9f4ec423a46dc12907f4349c55025f670422ba9` | 通过 |

Hugging Face 直连超时后，板端通过 ModelScope 对象存储取得相同字节；传输 provenance
与 canonical HF URL 分开记录，不能绕过 revision/哈希审计。

ONNX 检查结果：

- 图有 `51` 个输入，包括 `48` 个 `past_key_values`；
- 图有 `49` 个输出，包括 `48` 个 `present`；
- 输入/输出存在动态或符号维度；
- logits 实际为 `float32`，不是旧首轮 contract 预期的静态输出；
- 操作审计发现未放行的 `ai.onnx:Sigmoid`；
- 未发现 external initializer，但这不改变 contract 失败。

因此 ONNX contract 门失败，ATC、OM 加载、ACL smoke、NPU 推理和 OpenAI API 均未执行。
不能把“文件下载成功”或“静态检查脚本运行”写成 Qwen 已部署。旧脚本的
`install-runtime` 默认禁用；禁止为了绕过该门安装 Torch、MindSpore、ONNX Runtime 或
自定义 OPP。

## TinyLlama 历史结果摘要

TinyLlama 的完整双板归档见 [`docs/13`](13-tinyllama-complete-validation-record.md)，
这里仅保留历史测试结论：

| 板卡 | 端口 | ACL/NPU | JSON/SSE/网关 | 中文 | 稳定性 |
| --- | --- | --- | --- | --- | --- |
| `.178` | Tiny 最终 `8080`，网关 `7861` | 真实 execute 通过 | 通过 | 英文回退和 `U+FFFD`，`failed/not-admitted` | 10/10 无崩溃，但 NPU/HugePages 增长，`passed-with-risk` |
| `.90` | Tiny 隔离 `8081`，网关 `7861`，文字页 `7863` | 真实 smoke 通过 | 通过 | 观察到 `我是�����`，正式中文质量门未接纳 | 10 轮未执行，仅单次前后快照 |

`.178` 报告的早期 API 表写有 `max_tokens<=32`；代码审核后为控制单请求延迟，当前
服务和 `.90` 网关统一为 `max_tokens<=8`。两条记录必须按源 SHA 和时间分别引用，
不能用新限制改写旧实验，也不能用旧限制推断当前接口。

## Qwen2.5 静态 ONNX 批次（2026-08-23）

这是一轮独立于 TinyLlama 和旧 Qwen1.5 的新候选验证，目标板为
`192.168.1.90`（Ascend310B4/8T，CANN `8.0.0`）。模型在控制机的 `sci-agent` 环境
以 CPU、FP16、batch 1、固定序列长度 2048 导出；开发板只使用无 Torch 的
`case9-acl-om` 和原生 ACL。ModelScope 传输 revision 为
`13448952dbdab7a1627d0680ecd207535d889a23`，不能将其写成已核实的 Hugging Face
canonical commit。

| 门 | 当前结果 | 工件/报告身份 |
| --- | --- | --- |
| tokenizer/config 完整性 | `passed`（控制机） | `tokenizer.json` 7,031,645 bytes，SHA `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`；`tokenizer_config.json` 7,305 bytes，SHA `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583` |
| ONNX 静态 contract | `passed` | `qwen25-static-2048.onnx` 1,269,330,329 bytes，SHA `9887d67ad36179ef8d451a1226adc35011ad7093e65b1b49cf2ab1888163c43f`；3 个 `int64 [1,2048]` 输入，`float16 [1,2048,151936]` logits，opset 17，无动态维度/外部 initializer/未放行算子 |
| ATC | `passed` | `--soc_version=Ascend310B4`，CANN `8.0.0`；完整重试日志保留在板端 `~/case9-qwen25/logs/atc-qwen25-static-2048-retry2.log` |
| OM descriptor | `passed` | `qwen25-static-2048.om` 1,407,111,161 bytes，SHA `dc17b153ee1e76b3d31e971617d1cfdc7c56f366226212a61377a2c85e1d92b8`；实际输出名 `/Cast:0:logits`，dtype/shape 与 contract 一致 |
| ACL/NPU 生成 | `passed (limited)` | 中文 prompt 单 token 实测输出 `我是`，约 21 s；板端 `acl-smoke-2048-rerun.log` 和前后 `npu-smi` 快照 |
| OpenAI JSON/SSE、网关、文字 UI | `passed-isolated` | ACL 服务 `127.0.0.1:8082` 的 JSON/SSE、临时网关 `127.0.0.1:7864` 的鉴权转发，以及 `7865 -> 7866 -> 8082` 文字 SSE 闭环通过；正式 `7861` 未切换。原始报告见 `docs/15` |
| 中文质量/10 轮稳定性 | `not-run` | 不能由一次单 token smoke 推断 |

FP32 导出曾产生超过 2.5 GB 的 external-data 图并在 checker 阶段阻断，因此没有把它
当作候选工件；当前批次只承认上述单文件 FP16 图。详细命令和门禁顺序见
[`docs/15-qwen25-static-onnx-validation-record.md`](15-qwen25-static-onnx-validation-record.md)。

控制机另有一个独立的 128-token FP16 图做过 ONNX Runtime CPU 与 Transformers FP16
参考比较（cosine `0.9999947548`，prompt 24 tokens 的 top-5 相同）。它不是板端
2048-token 工件，不能回填 2048 图的数值一致性门；开发板仍未安装 ONNX Runtime。

## XiaoZhi 历史状态

`xiaozhi-esp32-server` 从未在这两块板上安装或启动，未执行设备注册、OTA、WebSocket、
Opus、ASR、TTS 或真实 ESP32 语音闭环。暂停原因是：

1. 上游默认依赖包含 `torch` 和 `torchaudio`，与 310B 无 Torch 约束冲突；
2. 设备当时不可用，无法把模拟协议结果称为真机结果；
3. 本地 LLM 的中文质量尚未通过，不能把失败模型接入小智。

小智只能在本地模型、无 Torch 语音依赖、服务配置加载和真实设备协议分别通过后恢复。
不允许以网关 stub、TinyLlama 英文输出或文字页面通过替代上述门禁。

## 源码和报告版本边界

`.178` 的 TinyLlama 报告绑定旧同步源 SHA；当前工作树的 runtime、service 和启动脚本
已发生变化。逐文件对照见 [`docs/12`](12-case9-evidence-index.md)。历史报告应只用于
描述当时的代码和工件，不应被重写成当前源码的测试报告。新一轮测试必须把以下字段
写进同一报告：

```text
board_ip / chip_tier / cann_version
source_file_sha256
model_or_om_sha256
tokenizer_sha256
exact_command / exit_code
port_and_pid
raw_report_path
```

## 历史未完成项与后续边界

下表保留历史候选的未完成门禁，或记录当前明确暂停的后续工作。已经正式提升的
Qwen2.5 静态 KV 1024 条目不再视为未完成；它的回滚工件和进一步优化边界仍单独保留。

| 项目 | 当前状态 | 允许的下一步 |
| --- | --- | --- |
| Qwen2.5 中文静态 ONNX 模型 | contract/ATC/descriptor/单 token ACL/API/隔离网关已通过；中文质量和稳定性未完成 | 运行独立中文探测集、10 轮资源观察；不覆盖旧 Qwen 条目 |
| Qwen2.5 静态 KV 1024 FP32 候选 | `artifact/ATC/descriptor/ACL/NPU/API/UI/formal-passed`; 中文探测 8/10，性能 p50/p95 改善 48.79%/48.70%；已正式部署 | 正式 `8080 -> 7861 -> 7865` 持续运行；候选 `~/case9-qwen25-kv1024`、8084/7867/7868、OM `1,266,010,586` bytes、SHA `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` 和回滚报告均保留。后续只做独立质量/性能改进，不得复用 1024 last-logits 工件 |
| Qwen2.5 静态 KV 1024 在 20T/310B1 | `artifact/ATC/descriptor/ACL/API/性能通过（实验性）`; JSON 5/5，p50/p95 `7751.579/7751.770 ms`，相对 8T 静态 KV 历史基线改善 30.415%/30.570%；dirty base 限定 | 独立 B1 OM `1,266,009,438` bytes，SHA `6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609`；证据见 `docs/21` 和 `repro/qwen25-kv1024-20260825/reports/board20t/`。base 中既有 Torch/MindSpore 未删除，未通过干净环境生产门；不切换正式 `8080 -> 7861 -> 7865` |
| ONNX 数值参考 | `not-run` | 仅在外部构建/控制机执行，板端不装 Torch |
| `.90` Tiny 10 轮稳定性 | `not-run` | 重新记录 PID、FD、RSS、NPU 内存和 HugePages |
| 本地音频闭环 | `not-run`/待模型通过 | 10 条中文 PTT，记录人工可理解计数和 p50/p95 |
| XiaoZhi | 暂停 | 先完成无 Torch 依赖审计和设备可用性确认 |

所有失败日志、哈希和 NPU 快照必须保留；不得删除旧报告、切换 CPU/云端、安装被禁
止框架或把历史候选重新标为已通过。
