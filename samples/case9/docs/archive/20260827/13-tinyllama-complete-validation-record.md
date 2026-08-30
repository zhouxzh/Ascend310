# TinyLlama 完整验证归档

## 归档范围与最终判定

本文把 `docs/09` 的历史板 `192.168.8.178` 和 `docs/11` 的当前板
`192.168.1.90` 放在同一张审计表中，但不合并两块板的运行数据。两块板均为
Ascend310B4/8T；IP、进程、Python 环境、源文件同步和报告目录均独立。

TinyLlama 预编译 OM 已在两块板上完成 descriptor、真实 ACL execute 和文本 API 的
实验性验证；它**没有通过中文质量门**，因此不接纳为中文聊天模型，也不以此恢复音频
或小智。`.178` 还完成了 10 轮资源观察但发现 NPU/HugePages 增长风险；`.90` 只完成
单次 smoke 和协议回归，不能继承 `.178` 的稳定性结论。

统一最终判定：

```text
artifact_verified       = 两块板通过
descriptor_verified      = 两块板通过
acl_smoke_passed         = 两块板通过
npu_generation_passed    = 两块板通过（实验性）
api_passed               = 两块板通过各自隔离/最终端口
chinese_quality_passed   = 未通过，not-admitted
audio_loop_passed        = 未完成
xiaozhi_device_passed    = 未执行
```

`api_passed` 只说明 OpenAI JSON/SSE 和网关协议可用，不等于模型语言质量、数值一致性、
长期稳定性或产品可用性。

## 固定模型和运行契约

| 项目 | 固定值 |
| --- | --- |
| 来源 | `wan-zutao/tiny-llama-manual-reset` |
| 源 revision | `114a158718411d8b0a252806ca14144c01a7e3db` |
| OM | `tiny-llama.om`, `1,493,077,371` bytes |
| OM SHA-256 | `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967` |
| tokenizer ZIP | `709,459` bytes |
| tokenizer ZIP SHA-256 | `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` |
| 服务模型名 | `tiny-llama-1.1b-acl-om` |
| 运行方式 | 原生 ACL、NumPy、无 Torch，batch 1、greedy |
| 输入 | `input_ids [1,1] int64`; `attention_mask [1,1025] int64`; `position_ids [1,1] int64`; packed KV `[22,2,1,4,1024,64] float16` |
| 输出 | logits `float32 [1,1,32000]`、更新 KV 和 attention 辅助输出；以 OM descriptor 为准 |
| 当前服务上限 | 代码审核后收紧为 `max_tokens <= 8`；早期 `.178` 报告的 API 表仍记录 `<=32`，两者不能混写 |

该契约只适用于 TinyLlama。下一款中文静态 ONNX 模型必须新建模型专属 descriptor、
tokenizer、KV 布局和服务，不得只替换模型名或词表大小。

## 历史板 `.178` 验证

### 环境身份

来源为 [`docs/09-tinyllama-acl-om-validation-record.md`](09-tinyllama-acl-om-validation-record.md)
和早期 [`docs/03-local-chat-validation.md`](03-local-chat-validation.md)。

| 项目 | 实测值 |
| --- | --- |
| 地址/主机 | `HwHiAiUser@192.168.8.178`, `orangepiaipro` |
| 芯片 | `Ascend310B4 / 8T` |
| CANN | toolkit `8.0.0`，组件记录 `7.6.0.1.220:8.0.0` |
| ACL 环境 | `case9-acl-om`, Python `3.9.25`, `acl` 可导入 |
| 禁止包 | `torch`, `torch_npu`, `torchaudio`, `mindtorch`, `transformers`, `vllm`, `mindie`, `onnxruntime` 等未用于本流程；洁净环境仍未建立 |
| 健康栏 | `npu-smi Health: Alarm`，仅诊断记录 |
| 最终服务 | TinyLlama `127.0.0.1:8080`；历史隔离验证曾用 `8081` |
| 网关 | `127.0.0.1:7861`，上游为 `http://127.0.0.1:8080/v1` |

### 门禁结果

| 门 | 结果 | 原始证据 |
| --- | --- | --- |
| 工件和 tokenizer 完整性 | `passed` | `~/case9-tinyllama/reports/tokenizer-check-20260821T075109Z.txt`、`acl-descriptor-20260821T075022Z.txt` |
| OM descriptor | `passed`；4 个静态输入、3 个输出 | `~/case9-tinyllama/contracts/tiny-llama-om-descriptor.json` |
| ACL 单 token/smoke | `passed`；真实 `acl.mdl.execute` | `~/case9-tinyllama/reports/smoke-20260821T080910Z.txt` |
| NPU 生成 | `passed`；10/10 生成 token 在词表范围且 logits 有限 | smoke 报告和 `npu-smi` 快照 |
| JSON/SSE API | `passed` | `~/case9-tinyllama/reports/api-final-8080-20260821T104134Z.json` |
| 网关转发 | `passed`；鉴权 JSON/SSE 到 8080 | 同上 |
| 数值一致性 | `not-run` | 未生成外部 ONNX CPU top-k/余弦参考 |
| 10 轮观察 | `passed-with-risk` | `~/case9-tinyllama/reports/stability-10-20260821T081314Z.tsv` |
| 中文质量 | `failed/not-admitted` | `~/case9-tinyllama/reports/http-chinese-probe-20260821T081944Z.jsonl` |

### `.178` 性能和资源边界

单轮 direct smoke 约 `23.624-25.852 s`，HTTP 探测约 `11.167-15.041 s` 的首 token
范围未被单独拆分为正式 p50/p95。10 轮全部退出码为 0，采样子进程 RSS 约
`784-804 KB`、文件描述符为 `3`；NPU 内存从第 1 轮 `7643 -> 7643 MB` 到第 10 轮
`7644 -> 7675 MB`，HugePages `771 -> 784`。因此只能写作“无崩溃但有残余增长风险”，
不能宣称长期稳定。

### `.178` 中文和音频边界

中文探测出现英文回退和 `U+FFFD` 替换字符，G8 标为 `failed/not-admitted`。后续
流式修复把完整 tokenizer 解码作为稳定的单个 delta，解决了协议层重复/部分 BPE 的
问题，但没有修复模型中文能力；协议修复不能改写中文质量结果。

`local_app` 的一轮文本 WebSocket 回归通过并触发了回复 TTS，但没有执行
`ptt_start`、C922 采集或 ASR；早期独立音频检查只证明 PulseAudio 设备可采样/播放，
不构成 10 条中文语音闭环。

## 当前板 `.90` 验证

### 环境身份

来源为 [`docs/11-code-review-optimization-and-board-192-168-1-90.md`](11-code-review-optimization-and-board-192-168-1-90.md)。

| 项目 | 实测值 |
| --- | --- |
| 地址/主机 | `HwHiAiUser@192.168.1.90`, `orangepiaipro` |
| 芯片 | `Ascend310B4 / 8T`, `npu-smi 25.2.0` |
| CANN | `/usr/local/Ascend/ascend-toolkit/latest`, `8.0.0` |
| Tiny 环境 | `case9-acl-om`, Python `3.9.16`, NumPy `1.26.4`, tokenizers `0.19.1` |
| 文字环境 | `case9-local-chat`, Python `3.9.16` |
| user-site 边界 | 既有 `mindspore==2.4.10`、`onnxruntime==1.19.2`、`sentencepiece`；通过 `PYTHONNOUSERSITE=1` 隔离，未删除 |
| base 边界 | `base` 中既有 `torch==2.1.0`、`torch_npu==2.1.0.post2`、`torchaudio==2.1.0`，没有用于 TinyLlama 运行时 |
| 健康栏 | `Alarm`，仅诊断记录 |

### 门禁结果

| 门 | 结果 | 原始证据 |
| --- | --- | --- |
| 工件完整性 | `passed`；OM/tokenizer bytes 和 SHA 与 manifest 匹配 | `/home/HwHiAiUser/case9-review-20260822/reports/` 中 G0 记录 |
| 环境隔离 | `passed (isolated condition)`；专用环境无 Torch/NPU 推理框架/音频推理包 | `docs/11` 的 G1 表和环境报告 |
| OM descriptor | `passed`；4 输入、3 输出 | `reports/tinyllama-acl-contract.json` |
| ACL smoke/NPU | `passed`；中文 prompt、8 token greedy，`finish_reason=length` | `reports/20260822T111328Z-tinyllama-smoke.log` |
| TinyLlama API | `passed`；`127.0.0.1:8081` JSON/SSE | `docs/11` G7 记录 |
| 网关转发 | `passed`；`127.0.0.1:7861` | `docs/11` G7 记录 |
| 文字页面 | `passed`；`0.0.0.0:7863` health/config/static/SSE/history/clear | `docs/11` G7 记录 |
| Python/Web 回归 | `104` 通过、`1` 跳过；Tiny 专用回归 `26` 通过 | `20260822T2103Z-web-tests.log`、`20260822T2050Z-tinyllama-tests.log` |
| 中文质量 | 未接纳；探测观察为 `我是�����`，英文请求为 `Sure, I'd be` | `reports/20260822T2105Z-live-final.log` |
| 10 轮资源稳定性 | `not-run`；只有单次 smoke 前后设备快照 | `docs/11` 当前边界 |

`.90` 的服务刻意留在 `127.0.0.1:8081`，没有切换或覆盖 `8080`；网关和文字页面分别
使用 `7861`、`7863`。页面地址是 `http://192.168.1.90:7863/`，页面没有鉴权，
只适合可信实验网络。

### `.90` 中文失败的解释

本板已观察到中文输出失败，但尚未完成正式的 10 条中文探测和人工“可理解”计数；
因此状态应写为“已观察失败/未接纳”，不能写成“中文质量通过”，也不能写成“完全
未测试”。英文输出和 NPU 设备内存变化只能证明执行链路。`npu-smi` smoke 前后约
`5728 MB -> 5738 MB` 的变化是一次诊断快照，不足以推出稳定性。

## 源码版本边界

`.178` 报告记录的是旧同步源；当前工作树中的 runtime、service 和两个启动脚本 SHA
已经不同。完整对照表见 [`docs/12`](12-case9-evidence-index.md)。因此：

- `.178` 的 10 轮和最终 8080 结果不能直接证明当前源码在 `.90` 的同等行为；
- `.90` 的协议回归不能回填 `.178` 缺少的数值一致性或中文质量；
- 重新部署时必须同时保存源文件 SHA、manifest SHA、OM SHA 和测试报告路径；
- 若源 SHA 变化，应将后续运行视为新的验证批次，而不是覆盖旧报告。

## 未完成项和停止条件

以下项目仍未通过或未执行：

1. 外部 ONNX CPU 数值参考、top-k/余弦比较；
2. `.90` 连续 10 轮进程、FD、RSS、NPU 内存和 HugePages 观察；
3. 10 条中文语音 PTT、ASR、LLM、TTS 闭环及 p50/p95；
4. XiaoZhi 服务端无 Torch 依赖审核、启动、模拟协议和真实 ESP32 设备闭环；
5. 新的中文 ONNX 静态输入模型的独立 contract、ATC、OM、ACL 和中文质量门。

任何新模型门失败时，保留工件和日志并标为 `blocked`；不安装 Torch、Torch-NPU、
Torchaudio、MindSpore、vLLM、ONNX Runtime，不切换 CPU、云端或其他模型。TinyLlama
当前只可作为实验性英文/协议链路候选，不能作为中文聊天产品或小智后端的正式模型。
