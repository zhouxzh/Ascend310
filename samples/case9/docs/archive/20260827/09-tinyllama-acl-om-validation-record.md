# TinyLlama ACL/OM 验证记录

## 记录规则

这是 TinyLlama 的板端验证记录。未填写或标为 `not-run` 的项目表示尚未执行，不能
解释为通过。每条证据必须注明命令、退出码、时间（UTC）、原始日志路径和工件 SHA-256。
禁止用静态 descriptor、协议测试或英文输出替代真实 NPU 推理证据。

当前状态：`api-passed`（最终协议报告：`20260821T104134Z`）。板端已完成真实
ACL/NPU 生成、8080 JSON/SSE 服务和 7861 网关转发；中文质量未通过，不能把该模型
作为中文聊天模型接纳。

## 实验身份

| 字段 | 记录 |
| --- | --- |
| 板端地址 | `192.168.8.178` |
| NPU 型号/档位 | `Ascend310B4 / 8T` |
| 主机名 | `orangepiaipro` |
| `uname -a` | `Linux orangepiaipro 5.10.0+ #32 SMP Thu Sep 25 17:54:23 CST 2025 aarch64` |
| CANN toolkit/runtime/ATC | `/usr/local/Ascend/ascend-toolkit/latest`; installed/running `7.6.0.1.220:8.0.0` |
| Python/conda 环境 | `/home/HwHiAiUser/.conda/envs/case9-acl-om/bin/python`, `3.9.25`；本环境沿用了历史 ACL/ONNX 安装，未作为洁净重建环境宣称 |
| `npu-smi` | `25.2.0`, `310B4`, `Alarm`（不单独阻断） |
| 驱动/内核版本文件 | `/usr/local/Ascend/driver/{version.info,version.cfg}` 未找到；驱动版本证据 `not-available`，不能宣称完整驱动版本验收 |
| 运行目录 | `~/case9-tinyllama` |
| 服务端口 | `127.0.0.1:8080`（最终实测；8081 为隔离验证端口） |
| 网关上游 | `http://127.0.0.1:8080/v1`, model `tiny-llama-1.1b-acl-om` |
| 最终进程 | TinyLlama PID `35906`；网关 PID `35118`；均仅监听 loopback |
| 最终网关限制 | `UPSTREAM_TIMEOUT_SECONDS=60`；`MAX_CONCURRENT_REQUESTS=1` |
| 网关环境备份 | `/home/HwHiAiUser/case9-xiaozhi-gateway/.env.before-tinyllama-20260821T080703Z` |

板端模块盘点显示 `torch`、`torch_npu`、`torchaudio`、`mindtorch`、`torchvision`、`xformers`、
`transformers`、`vllm`、`mindie`、`qwen_ascend_llm` 和 `onnxruntime` 均不存在。历史环境中
预先存在 `onnx==1.16.2`、`protobuf==4.25.3`、`typing-extensions==4.12.2` 等
遗留 ABI/检查工具；本次 TinyLlama 流程没有安装、导入或调用它们，也没有删除。最终扫描中
`sentencepiece`、`mindspore` 以及所有硬禁止包均不存在，所以 G1 的“无硬禁止包”通过，但
“仅 NumPy/tokenizers 的洁净环境”证据仍为 `not-established`。

## 固定工件

| 工件 | 来源/revision | 预期 bytes | 预期 SHA-256 | 实测 | 状态 |
| --- | --- | ---: | --- | --- | --- |
| `tiny-llama.om` | [ManualReset](https://gitee.com/wan-zutao/tiny-llama-manual-reset), `114a158718411d8b0a252806ca14144c01a7e3db` | `1493077371` | `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967` | `同预期` | `artifact-verified; descriptor-verified; acl-smoke-passed` |
| `tokenizer.zip` | 同上 | `709459` | `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` | `同预期` | `artifact-verified` |
| `tokenizer.json` | 从 tokenizer ZIP 解出 | `1,842,767` | `bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e` | `同预期内容` | `artifact-verified` |
| `tokenizer.model` | 从 tokenizer ZIP 解出 | `499,723` | `9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347` | `同预期内容` | `artifact-verified` |
| `special_tokens_map.json` | 从 tokenizer ZIP 解出 | `414` | `6fa06efa2785e450051989a6f8fb4416b10149ded485ddd3f127a40734f5cfd0` | `同预期内容` | `artifact-verified` |
| `tokenizer_config.json` | 从 tokenizer ZIP 解出 | `932` | `bcdc6f267b05e1afd27fa622a62fea649bcb941e6dc705d2835883a0746192da` | `同预期内容` | `artifact-verified` |
| `tiny-llama.onnx` | 同上；仅 ATC 分支 | `1487421772` | `待板端计算` | `待记录` | `optional/not-run` |

模型、tokenizer、ONNX、OM、锁文件和报告必须保留在板端，不提交到 Git。

## 运行时源文件证据

以下 SHA-256 是同步到 `~/case9-tinyllama/source/` 后在目标板重新计算的值；模型和运行日志不
进入 Git：

| 文件 | 板端 SHA-256 |
| --- | --- |
| `tinyllama_acl_contract.py` | `7c553063cfb3cc9e0301d3c7311393455e2901215b2a7ad5d051e59599841343` |
| `tinyllama_acl_service.py` | `b73d297815e8e544a7dc0baef15abf2876fe81fba3dbe84a4f33afedca0627a6` |
| `tinyllama_acl_runtime.py` | `6e2bd4ec8314f179415eba7a24edb526b6933de6dd20b399731548f589b96ce6` |
| `tinyllama_tokenizer.py` | `ba995928c3861337b083f22e26720b630e3e3d9505d59c0752046ed42e85a88f` |
| `scripts/run_tinyllama_acl_service.sh` | `a94860b00d89753c27bb7477c0f807bbadb6f0326a47f9c562b1746d40bfe6eb` |
| `scripts/provision_tinyllama_board.sh` | `9875bd31a47f66d62425f49b3e66d66ea13378072eac64552cc633919d9fef8a` |
| `local_model_manifest.json` | `ed80c4efd69ba8d41b8c2d18ef4293e52189166c9d31ddd7b714a29143b39081` |
| `requirements-tinyllama-acl-om.txt` | `8486d2fcf212a42985e09057649962972f1e3cd3e160ecf933e67bf5ed8fa24f` |
| local_app 文本探测报告 | `22d442c0e2504ae0f439b5ea427556bf447ffaa60f28febf3caddd5f41e78d8a` |

网关同步源也已核对：`app.py` `5ebe160dc193e1c1c59298fa8d5e3dd5feb72bcc615037a3c72711ff72b1c751`、
`config.py` `f9ffe15f2b6d6f680a526c43b082cee9482d55ad42aa1a21b6247340d21cd6c7`、
`retrieval.py` `0cdea0b0b5fcdfacb4d9acec9493e95803d95d20c1251a61ca7a5a41633f9caa`、
`upstream.py` `cbb9fd55ff64f82868682267b069d7a9f45ae448b8002d8375d0be8dc773f4e9`；
目标板与控制机哈希相同。

2026-08-21T12:15Z 起，TinyLlama 流式实现改为先完成 token 序列解码，再发送一个
tokenizer 稳定的 `delta.content`；不再用累计字符串前缀猜测增量。这样避免了部分
BPE/UTF-8 解码产生的 `U+FFFD` 和重复片段。此次修复的单次页面探测原始响应保留在
`/tmp/case9-text-chat-smoke-final-20260821T1225Z.sse`；它证明协议增量语义，不能
替代 G8 中文探测集，G8 仍按历史探测结果标为 `failed/not-admitted`。

## 验收门

| 门 | 命令/证据 | 结果 | 原始报告 |
| --- | --- | --- | --- |
| G0 来源和清单 | `local_model_manifest.json`、revision、URL、下载日志 | `passed` | `~/case9-tinyllama/reports/tokenizer-check-20260821T075109Z.txt` |
| G1 板端环境 | `bash scripts/provision_tinyllama_board.sh check` | `passed-with-contamination`；B4/CANN/ACL/硬禁止包通过，洁净环境未建立；driver version `not-available` | `~/case9-tinyllama/reports/20260821T103258Z-tinyllama-environment.log`、`...-npu-smi.log`；模块盘点见本节 |
| G1 无 Torch/推理框架 | `import acl`；扫描 Torch、Transformers、vLLM、MindIE、ONNX Runtime | `passed`；辅助历史包未使用 | `~/case9-tinyllama/logs/tinyllama-service-final-hardening.log`、`~/case9-tinyllama/reports/20260821T103258Z-tinyllama-environment.log` |
| G2 OM 完整性 | 字节数、SHA-256、非 LFS pointer、锁文件 | `passed` | `~/case9-tinyllama/reports/acl-descriptor-20260821T075022Z.txt` |
| G2 tokenizer 完整性 | ZIP 成员和解出文件 | `passed` | `~/case9-tinyllama/reports/tokenizer-check-20260821T075109Z.txt` |
| G3 OM descriptor | ACL init/device/context/stream/load/get_desc/unload/reset/finalize 均返回 0；真实 execute 在 G4 完成 | `passed` | `~/case9-tinyllama/reports/acl-descriptor-20260821T075022Z.txt`; JSON `~/case9-tinyllama/contracts/tiny-llama-om-descriptor.json` |
| G4 ACL 单 token | `bash scripts/provision_tinyllama_board.sh smoke`；真实 `acl.mdl.execute` | `passed` | `~/case9-tinyllama/reports/smoke-20260821T080910Z.txt` |
| G4 NPU 证据 | smoke 前/后及最终 API 后 `npu-smi info` | `passed` | smoke report、stability snapshots、`~/case9-tinyllama/reports/npu-final-post-api-20260821T104322Z.log` |
| G5 JSON API | `GET /v1/models`、非流式 completion | `passed` | `~/case9-tinyllama/reports/api-final-8080-20260821T104134Z.json` |
| G5 SSE API | `stream=true`、`data: [DONE]` | `passed` | `~/case9-tinyllama/reports/api-final-8080-20260821T104134Z.json` |
| G5 数值一致性 | 外部 ONNX CPU top-k/余弦参考 | `not-run`；控制机未安装/执行板端 Torch，不能用协议结果替代 | 未生成 |
| G6 网关 | `7861` 鉴权转发到 TinyLlama，含 provider 字段适配 | `passed` | `~/case9-tinyllama/reports/api-final-8080-20260821T104134Z.json` |
| G7 稳定性 | 连续 10 轮，进程/FD/RSS/NPU 内存观察 | `passed-with-risk` | `~/case9-tinyllama/reports/stability-10-20260821T081314Z.tsv` |
| G8 中文质量 | 中文探测集，人工可理解性 | `failed/not-admitted` | `~/case9-tinyllama/reports/http-chinese-probe-20260821T081944Z.jsonl` |

任一 G0-G5 硬门失败时，状态改为 `blocked`，停止后续服务接入；不得自动切换 CPU、云端、
Torch、MindSpore 或其他模型。

## Descriptor 契约摘录

完成 `inspect` 后，将实际值复制到下表，并与 `tinyllama-acl-contract.json` 保持一致：

| 项目 | 预期/实测 |
| --- | --- |
| `model.model_id` | `tiny-llama-1.1b-acl-om` |
| `model.family` | `tinyllama` |
| `model.num_layers` | 预期 `22` |
| `model.num_kv_heads` | 预期 `4` |
| `model.head_dim` | 预期 `64` |
| `model.max_sequence_length` | 预期 `1024` |
| `acl_om.execution_mode` | `kv_cache_token` |
| `acl_om.input_order_verified` | 必须为 `true` |
| 输入 shape/dtype | `input_ids [1,1] int64`; `attention_mask [1,1025] int64`; `position_ids [1,1] int64`; packed KV `[22,2,1,4,1024,64] float16` |
| logits output index | `0` (`/Cast:0`, `float32 [1,1,32000]`) |
| KV output indices | `1` (`/model/Reshape_1:0`, `float16 [22,2,1,4,1,64]`) |

目标板 descriptor 已确认的辅助输出为 `/model/Reshape_2:0` attention scores
`float16 [22,1,32,1,1025]`；它不能作为 logits 或 KV cache。

如果实际 descriptor 与上游 README 的静态 KV 形状不同，以 descriptor 为准；不能通过修改
contract JSON 来绕过检查。

## ACL/NPU 结果

### Smoke

```text
prompt: 你好（不写入隐私文本）
max_tokens: 8
temperature: 0 / greedy
device_id: 0
historical smoke command timeout: 300 s; current runtime request budget: 50 s best-effort;
current smoke wrapper default: 60 s plus `timeout --kill-after=5s`
```

| 指标 | 结果 |
| --- | --- |
| ACL init/device/context/stream | `通过；各步骤 rc=0，真实 execute 已运行` |
| OM load/descriptor | `通过；输入 4 项、输出 3 项` |
| logits 是否有限 | `通过；10/10 生成` |
| token ID 是否在词表范围 | `通过；10/10 生成` |
| 输出文本 | `已留在板端报告；中文质量单独失败` |
| 首 token 延迟 | `未单独拆分；HTTP 探测约 11.167-15.041 s` |
| 总延迟 | `direct smoke 约 23.624-25.852 s/轮` |
| pre/post `npu-smi` | `通过；310B4 Alarm 仅作诊断记录` |
| 资源释放 | `无崩溃/无进程残留；内存增长为残余风险` |

descriptor JSON（原始 descriptor）：`~/case9-tinyllama/contracts/tiny-llama-om-descriptor.json`；文本报告：
`~/case9-tinyllama/reports/acl-descriptor-20260821T075022Z.txt`。运行服务前必须由
`provision_tinyllama_board.sh inspect` 生成带 `source_artifact`/`source_revision` 绑定的
runtime contract。该门证明 ACL 初始化、模型加载、descriptor 读取和真实
`acl.mdl.execute` 生成均成功；中文能力另由探测集判定，不能从英文输出推断中文可用。

### 稳定性观察

完成 10 轮后记录进程 PID、RSS、打开文件描述符、NPU 内存和每轮耗时。该观察不是首轮
硬门，但任何崩溃、设备复位、ACL 句柄泄漏或无法释放都必须保留完整日志并标记风险。
本次实测 10/10 轮退出码为 0，每轮耗时 `23.624-25.852 s`，采样子进程 RSS
`784-804 KB`、文件描述符 `3`。NPU 内存从第 1 轮 `7643 -> 7643 MB`、第 10 轮
`7644 -> 7675 MB`，HugePages 从 `771 -> 784`；存在残余资源增长，保留为风险，
不作为本轮 API 通过的硬阻断。原始 TSV 和每轮 NPU 快照仅保留在板端：
`~/case9-tinyllama/reports/stability-10-20260821T081314Z.tsv`。

## API 和网关结果

| 请求 | 结果 | 备注 |
| --- | --- | --- |
| `GET http://127.0.0.1:8080/v1/models` | `passed` | 最终端口，loopback；8081 为历史隔离端口；最终报告 `~/case9-tinyllama/reports/api-final-8080-20260821T104134Z.json` |
| JSON `POST /v1/chat/completions` | `passed` | batch 1、`max_tokens<=32`；JSON/SHA 摘要留在最终报告 |
| SSE `POST /v1/chat/completions` | `passed` | `text/event-stream`、单个完整 `delta.content`、`data: [DONE]`；历史报告 `~/case9-tinyllama/reports/api-final-8080-20260821T104134Z.json`，修复后页面响应见 `/tmp/case9-text-chat-smoke-final-20260821T1225Z.sse` |
| case9 gateway `7861` | `passed` | 鉴权 JSON/SSE 转发至 8080；provider-only 字段请求返回 200，非法 greedy 参数返回 400；最终报告同上 |
| `local_app` 文本聊天 | `passed` | 临时 `127.0.0.1:7862` WebSocket 文本回归通过；报告 `~/case9-tinyllama/reports/local-app-text-probe-20260821T090044Z.json`。该路径按现有实现播放了文本回复的 TTS，但未执行 `ptt_start`、麦克风采集或 ASR；服务已在探测后停止 |

最终 API 探测后的设备快照：`~/case9-tinyllama/reports/npu-final-post-api-20260821T104322Z.log`。

已确认 `UPSTREAM_BASE_URL=http://127.0.0.1:8080/v1`、
`UPSTREAM_MODEL=tiny-llama-1.1b-acl-om`，网关 token 没有发送到浏览器。网关公开的
`/v1/models` ID 是 `case9-rag`，completion 响应保留上游的
`tiny-llama-1.1b-acl-om` model 字段；这属于当前代理响应契约，不影响转发验收。原网关环境备份为
`/home/HwHiAiUser/case9-xiaozhi-gateway/.env.before-tinyllama-20260821T080703Z`。
XiaoZhi 服务端和真实设备闭环不属于本记录。此次 local_app 回归只证明文本 WebSocket
到网关的链路；ASR、麦克风采集、独立音频质量和持续音频运行仍未验收。

## ATC 分支记录

默认不执行。若获得单独批准，必须填写：

```text
authorization: <ticket/operator/date>
build host/container: <record>
CANN/ATC version: <record>
ASCEND_CUSTOM_OPP_PATH: <isolated path>
exact command: <record>
ATC exit code: <record>
OM bytes/SHA-256: <record>
failure or pass evidence: <log path>
```

自定义 OPP 不得覆盖系统 CANN；ATC 失败时状态仍为 `blocked`。

## 最终状态

只允许使用以下状态：

```text
not-started | artifact-verified | descriptor-verified | acl-smoke-passed
| npu-generation-passed | api-passed | blocked
```

`api-passed` 只表示 ACL API 和网关协议通过，不代表中文质量、数值一致性或环境洁净度通过。
中文质量、数值参考和稳定性必须单独报告；任何未执行项目都不能填 `passed`。

当前最终状态：`api-passed`。G4 ACL execute、最终 G5 JSON/SSE 和 G6 网关报告均通过；
数值一致性仍为 `not-run`；G7 为 `passed-with-risk`（NPU 内存/巨页有观察到的增长）；
G8 为 `failed/not-admitted`，因为中文探测出现英文回退和 U+FFFD 乱码。请求级 50 秒预算
是 best-effort，且 `finish_reason` 以是否达到 token 上限近似判断，EOS 恰好落在上限的边界
仍需单独测试。不得把 TinyLlama 接纳为中文聊天模型，也不得以此结果恢复音频或 XiaoZhi。
