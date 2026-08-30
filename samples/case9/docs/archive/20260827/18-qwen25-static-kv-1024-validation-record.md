# Qwen2.5 静态 KV 1024 验证记录

## 记录边界

本文是 Qwen2.5-0.5B-Instruct 固定 1024、FP32 split StaticCache 候选的逐门记录。
它只记录已经执行的证据；预期契约、板端命令和回滚边界见
[`17-qwen25-static-kv-1024-porting-plan.md`](17-qwen25-static-kv-1024-porting-plan.md)。
音频、ASR/TTS 和 XiaoZhi 不在本批次。

截至 2026-08-25，本地控制机图导出、ONNX 检查和单元测试已执行；板端 192.168.1.90
随后完成了 ONNX 原子传输、ATC、OM descriptor、ACL smoke、NPU 生成、隔离 API/UI、
同协议性能对照和中文探测。候选的 p50/p95 改善为 48.79%/48.70%，中文探测为 8/10；
在保留回滚证据后，已受控提升到正式 `8080 -> 7861 -> 7865`。音频、ASR/TTS 和
XiaoZhi 仍暂停。

## 早期传输阻断与后续恢复

控制机已将单文件 ONNX 按 16 MiB 分片准备为 76 个临时文件，分片总字节数与
`1,261,082,122` 一致。板端候选目录已创建，tokenizer、控制机契约、检查报告和运行时代码的小文件
曾成功同步；ONNX 只留下少量早期测试分片，未形成可验收的完整文件。对单个约 16 MiB 分片的
`scp`、原始 SSH 管道和反向 SSH HTTP 隧道均出现连接复位；随后板端 ICMP/SSH 短时不可达。
因此当时没有写入 ONNX 原子目标，也没有执行 `inspect`、`atc` 或 `smoke`。这是早期
失败批次的历史记录，不能覆盖本记录后面的成功批次。

已向旧 Qwen 服务 PID `27022`（8082）和 `26870`（8083）发送过明确的 `SIGTERM`，但之后连接
复位，未取得最终进程状态；不能据此声称旧服务已停止或候选资源已释放。未发送其他进程的停止信号，
未删除板端模型、日志、报告或系统 CANN 文件。恢复测试前应先重新 SSH 核对进程、磁盘和 `npu-smi`，
再从 G0 `check` 门重新开始。

随后恢复 SSH，并通过 WSL `rsync --partial --append-verify` 完成大文件原子传输；
后续 `G0 -> G6` 和正式端口提升证据见下文。旧 8082 OM、日志和性能报告保留在
`~/case9-qwen25`，不与当前 1024 静态 KV 工件混用。

## 固定身份和契约

| 字段 | 值 |
| --- | --- |
| 模型 ID | `qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om` |
| 控制机环境 | `C:\Users\zhoux\anaconda3\envs\sci-agent` |
| checkpoint | `C:\Users\zhoux\case9-qwen25-build\model` |
| source revision | `modelscope:13448952dbdab7a1627d0680ecd207535d889a23` |
| 设备目标 | `Ascend310B4 / 8T`，`192.168.1.90` |
| cache | 48 个命名 tensor，layer -> key,value；每个 `float32 [1,2,1024,64]` |
| token cache 输出 | 48 个 `float32 [1,1,2,64]` |
| mask | `int64 [1,1024]` |
| logits | `float32 [1,1,151936]` |
| 解码 | batch 1、greedy、`temperature=0`、最多 80 token；至少 16 token 后首个句末停止 |

控制机本轮版本快照：`Python 3.13.14`、`torch 2.13.0`、`transformers 4.46.3`、
`onnx 1.20.1`、`onnxruntime 1.24.2`、`tokenizers 0.20.3`、`numpy 2.3.5`；
FastAPI/Uvicorn `0.128.8/0.39.0` 仅用于网关协议回归。上述包均未安装到板端
`case9-acl-om` 环境。

控制机当前源文件 SHA-256 如下；运行时的 ACL context 修复和 provision 的隔离 ATC
依赖/推理采样修复均已同步并在板端核对：

| 文件 | SHA-256 |
| --- | --- |
| `qwen25_kv_acl_contract.py` | `9f9fbb62d6e74c595fab4ae13ce3b08f6c6748f74944e56f8b24abc60a018f1a` |
| `qwen25_kv_acl_runtime.py` | `7371271f23bf3b830a4713b6a4aa85e3c10631055b02b927ba1b0a52f65f480c` |
| `qwen25_kv_acl_service.py` | `7146b566d1583da6341ce8422029af6e30dde28173b693bf6025f3e5d6092e02` |
| `app.py` | `f4cb662b2dc61828fdcf2de43ea293ac4cf8933b1329c204ba58d972e7670bd3` |
| `config.py` | `f1ec4729e8c43e43bc3cbad7948c93b97455bd392561fd27fb41574c32ca9dd1` |
| `text_chat_app.py` | `705d510ce41f12e4a50514fb1ebde44e331c440ca1683a9b677d6ff67eb5eab8` |
| `local_session.py` | `6dcfe0c8c03bd8f89ec8f9dc082a87f1b39df3fbed90d6841f6b92c273e79b25` |
| `qwen25_kv_tokenizer.py` | `262a42aec55f23b0ec5b19ee49505595a15dc2e76e2fd11ef7b3ac4b2838c5e8` |
| `tools/export_qwen25_static_onnx.py` | `bbb4c5bf4e5c326a57d5737e62001f495a4a5f0e56b3c5dee775000c8b011e38` |
| `tools/inspect_qwen25_static_onnx.py` | `43007cc949edcb2de49570dc571667f3a0541bd883a6beb415b9f7ceb1b4274b` |
| `scripts/provision_qwen25_kv102_board.sh` | `e1595a9e9a44dabc5645fb092ce831a61a38be7427b40f47f335bca8058a9711` |
| `scripts/run_qwen25_kv_acl_service.sh` | `053d001b1b71539aa1c02e4784db9c12843b8d16c6cdb08a88f29a62b52d719b` |
| `scripts/serve_qwen25_kv_acl.py` | `ed4ca538a3825ab59f72f1f1adf10ade76b7a5a7234929db77ef3500877e286f` |
| `scripts/run_qwen25_kv102_gateway.sh` | `b8064f8f7991a6d38f87df14cd3614b19fce51a08eafd063bff59a4d6e177eaf` |
| `scripts/run_qwen25_kv102_text_chat.sh` | `90df22feee4e25428040a3f4ebf99626bc037d6ac0bdbdaa30b6ddcad3991fd8` |

板端已同步并核对控制机契约：
`contracts/qwen25-static-kv-1024-fp32-contract.json` SHA-256
`fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498`；tokenizer 与配置的
SHA-256 分别为 `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` 和
`5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583`。

本轮通过小文件 `scp` 同步了候选运行时、契约、检查器、服务入口、provision 脚本和两个
候选启动器到 `/home/HwHiAiUser/case9-qwen25-kv1024/src/`；ONNX 使用 WSL
`rsync --partial --append-verify` 传输到 `.part`，验收字节数和 SHA-256 后原子改名。
最新运行时和 provision 脚本 hash 已在板端重新核对。候选服务运行期间，旧 8082 基线
PID `32734` 被明确停止以释放 NPU 大页；其 OM、日志和报告均保留。

Qwen tokenizer JSON 实测词表为 151,665，而模型 logits 维度为 151,936；这是保留 token
布局差异。运行时允许 tokenizer 词表小于模型词表，但会校验 EOS/PAD/对话特殊 token
均在模型范围内，禁止生成越界 ID。

## 门禁状态

| 门 | 状态 | 证据 |
| --- | --- | --- |
| G0 板端环境 | `passed` | `check` 于 `2026-08-23T10:57:34Z` 实测：`aarch64`、Ascend310B4/8T、CANN runtime/compiler/OPP `8.0.0`、Python 3.9.16、`acl import: ok`；活动环境禁止包扫描为空，user-site 仍报告既有 `mindspore`/`onnxruntime`；磁盘可用 168 GB、内存 available 10 GiB。`npu-smi Health: Alarm` 仅作诊断记录，不单独阻断 |
| G1 ONNX 导出/完整性 | `passed` | 板端 `1,261,082,122` bytes；SHA-256 `b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e`；路径 `~/case9-qwen25-kv1024/artifacts/qwen25-static-kv-1024-v2.onnx` |
| G1 ONNX checker/静态契约 | `passed` | 控制机 inspector：51 inputs/49 outputs、动态维度 0、无 external initializer；板端 `inspect` 以合同和完整文件 hash 复核 |
| G2 ATC/OM | `passed` | 首次失败原因是 CANN TBE 缺少既有 `decorator.py`；未安装包，使用 `/usr/local/miniconda3/lib/python3.9/site-packages/decorator.py` 的隔离 symlink 重试成功。日志 `~/case9-qwen25-kv1024/logs/atc-retry1-20260823T110028Z.log`；OM `1,266,010,586` bytes，SHA-256 `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| G2 ACL descriptor | `passed` | `51` inputs/`49` outputs；base 输入为 `int64 [1,1]`、`int64 [1,1024]`、`int64 [1,1]`；48 cache 输入为 `float32 [1,2,1024,64]`、每个 524,288 bytes；logits 为 `float32 [1,1,151936]`、607,744 bytes；48 token cache 输出为 `float32 [1,1,2,64]`、512 bytes。完整合同 `~/case9-qwen25-kv1024/contracts/qwen25-static-kv-1024-v2-om-contract.json` |
| G3 ACL smoke/NPU 生成 | `passed` | `2026-08-23T11:10:40Z` smoke 返回 `你好！`、2 tokens；报告 `~/case9-qwen25-kv1024/reports/20260823T111040Z-acl-smoke.txt`；NPU before/during/after 快照同批保存，推理时设备内存约 6.0 -> 7.6 GB，随后回落 |
| G4 JSON/SSE | `passed` | 候选 `8084` 的 JSON/SSE 和连续 10 轮请求全部 HTTP 200。候选 76-token 结果保留作历史句末策略证据；刷新后的正式 ACL PID `65088`/`127.0.0.1:8080` 在省略上限和显式 `max_tokens=32` 时均返回 22 token、`finish_reason=stop`，文本以 `千问。` 结束，健康状态为 `ready=true`、`device_cache_update=true`、`restart_required=false`。旧 PID `58403` 的 32-token/23-event 记录仅作为历史修复批次保留 |
| G4 网关/UI | `passed` | 正式网关 PID `62247`/`127.0.0.1:7861` 连续转发 ACL 增量并发送终端 `finish_reason=stop` 和 `[DONE]`；文字 UI PID `64080`/`0.0.0.0:7865` 连续发送增量，`done` 事件携带完整 `我是Qwen...千问。` 文本。旧 PID `58467/54265` 的 32-token/23-delta 记录仅作为历史修复批次保留 |
| G5 CPU 数值参考 | `informational_pass` | `compare-hello-v2.json` 与中英文 `compare-multilingual-v2.json`：51 inputs/49 outputs；8 个逐 token 对照均通过，max abs diff `0.403751`、min cosine `0.999589`、min top-5 overlap `0.8`、next-token 不一致数 0；不宣称 bitwise 一致 |
| G5 中文质量/稳定性 | `quality_pass (8/10); stability_observed` | 10 条中文 SSE 探测报告 `~/case9-qwen25-kv1024/reports/20260823T-chinese-probe-10.json`，2 条含替换字符（计算、静态 KV），其余 8 条人工可理解；首 token p50/p95 `11.02/11.90 s`，总耗时 p50/p95 `14.52/15.40 s`。连续 10 轮均 HTTP 200，进程 RSS 约 820 MB，NPU 内存约 6.45 GB；未把稳定性观察升级为正式稳定性门 |
| G6 同协议性能 | `passed` | 同一中文 prompt、`max_tokens=2`、temperature 0、1 次预热 + 5 次测量：候选 8084 总耗时 p50/p95 `11,139.7/11,164.9 ms`，2048 基线 8082 为 `21,751.4/21,761.3 ms`；p50 改善 `48.79%`、p95 改善 `48.70%`。报告 SHA：候选 `342b46e2b24f448fd4535cce555a56f09ae33dfc6f1779856a83c464a5a5ef3c`，基线 `13489ffb88ac274d91b1a57c99e61004e8b600c0c772a9f42a62e845ffdb2b0e` |
| G7 正式入口提升 | `passed` | 仅停止已核对的旧/候选 PID；刷新后的正式 ACL `65088`、网关 `62247`、UI `64080` 在 `8080/7861/7865` 健康。Bearer 鉴权、JSON、SSE 和 UI 请求均通过，未向浏览器暴露网关密钥。旧 `58403/58467/54265` 进程及其报告保留作历史修复证据 |

本次 G0 原始快照保留在板端：
`~/case9-qwen25-kv1024/reports/npu-before-20260823T094415Z.txt` 和
`~/case9-qwen25-kv1024/reports/system-20260823T094415Z.txt`。

早期收尾复核（2026-08-23T10:16:11Z）曾观察到 TCP/22 短时超时；该状态已恢复，后续
命令均在重新建立的 SSH 会话中执行并留下原始报告。

正式提升前的旧 `7865` 页面和 `7866` 网关已按 PID 精确停止；其源代码、日志和旧
`8082` 工件保留用于回滚，不把旧端口存活视为当前模型证据。

## 控制机与板端执行记录

已完成：

* 静态 wrapper 使用 Transformers `StaticCache` 语义，sample forward 返回 49 个输出：
  1 个 logits 和 48 个 token cache；logits 为 `[1,1,151936]`，cache 为 `[1,1,2,64]`。
* Qwen2.5 静态图、契约、服务、导出和数值比较相关回归当前为 `46 passed`；完整命令为：
  `python -m pytest -q tests/test_qwen25_static_kv_graph.py tests/test_qwen25_kv_acl_service.py tests/test_qwen25_static_export.py tests/test_qwen25_acl_service.py tests/test_qwen25_last_logits_contract.py tests/test_qwen25_last_logits_optimizer.py tests/test_qwen25_static_kv_compare.py`。
* 实际单文件工件位于控制机 `C:\Users\zhoux\case9-qwen25-build\onnx-static-kv-1024\qwen25-static-kv-1024-v2.onnx`；板端复制后以相同 bytes/SHA-256 原子验收。
* CANN 8.0 的 Python ACL binding 未导出 `ACL_MEMCPY_DEVICE_TO_DEVICE` 符号，但已核对
  本板 CANN 8.0 头文件的 ABI 值为 `3`。运行时只在 H2D/D2H 枚举值也符合预期时使用该
  回退值；D2D 复制失败会禁用该路径并回退到主机 cache，已成功后再次失败则 fail-closed。
  候选 `8084` 和修复后的正式 `8080` 都已执行成功的设备端 cache 更新，正式 `/health`
  为 `device_cache_update=true`。
* 历史修复批次中，网页仅发送 `message`/`stream`，网关会把未设置的 `max_tokens` 交给
  ACL 服务旧默认值 `8`，造成回复在数个汉字后正常结束而看起来像卡住；随后曾临时将
  1024 上下文 Qwen 上游和 ACL 默认值改为 `32`，runtime 执行超时为 `96 s`。这些
  `32/96` 数值和当时的 `58403/58467/54265` PID 只保留作历史修复证据，TinyLlama
  的独立 8-token 限制不受影响。
* 当前正式限制是 ACL `max_tokens=80`、ACL 执行超时 `240 s`、网关上游/流超时 `270 s`、
  文字 UI LLM 超时 `300 s`，并在至少 16 token 后于首个完整句末停止。候选 PID `61313`
  和旧正式 PID `62188` 的 76-token/三句话结果属于阈值修改前的历史证据；刷新后的
  正式 PID `65088` 已返回 22 token 的完整首句。旧批次中没有完整 finish 证据的 64/80
  token 运行不标为句末停止结果。
* ONNX Runtime 的近似差异来自 CPU FP16/算子实现，不是接口契约失败。单条 `你好` 报告
  `compare-hello-v2.json` SHA-256 为 `f0a7e7948ca2643381426681bdd1b11acb3dd96da59d2e2e66dfde99d9f10506`；
  追加中英文各两条 prompt、每条两步的 `compare-multilingual-v2.json` 也通过（4 prompts、8 steps、
  failure_count=0、max abs diff `0.403751`、min cosine `0.999589`、min top-5 overlap `0.8`、
  next-token 不一致数 0），其 SHA-256 为 `b346a7622daacc81b6f7f225b30f85b15d964817317f0937f9e7a72f441256f1`。
  比较应使用 next-token、top-k overlap 和 cosine，不能只用严格 top-5 顺序或单一最大误差判定。
* Python 编译和 `git diff --check` 已通过；前端 `npm test`（4/4）和 `npm run build`
  已通过。前端结果只证明静态资源和协议回归，不替代板端 ACL/NPU/UI 硬件验收。
* 最新本地回归（`sci-agent`）中，排除仅适用于板端的 Windows 音频前置测试后为
  `166 passed, 1 skipped`；静态 KV/ACL 聚焦集合为 `46 passed`。包含音频测试的全量命令
  在 Windows 因缺少 `paplay` 有 1 项环境失败，这不表示 ACL/NPU 失败；音频仍需在板端
  按独立门禁执行。ACL context 和 tokenizer BOS 校验后的运行时已在板端加载，并完成
  JSON/SSE 和连续 10 轮请求。
* 默认 token 和 D2D ABI 回退修复后，`py_compile` 通过；Qwen 静态 KV、ACL 服务、
  last-logits 与网关的相关集合为 `76 passed in 9.81 s`，其中服务与网关定向集合为
  `38 passed`。这些是控制机协议/代码回归，不替代上述板端实测。

待执行的控制机命令示例：

```powershell
& 'C:\Users\zhoux\anaconda3\envs\sci-agent\python.exe' tools/export_qwen25_static_onnx.py `
  --model 'C:\Users\zhoux\case9-qwen25-build\model' `
  --output 'C:\Users\zhoux\case9-qwen25-build\onnx-static-kv-1024\qwen25-static-kv-1024-v2.onnx' `
  --source-revision 'modelscope:13448952dbdab7a1627d0680ecd207535d889a23' `
  --report 'C:\Users\zhoux\case9-qwen25-build\onnx-static-kv-1024\export-report-v2.json'

& 'C:\Users\zhoux\anaconda3\envs\sci-agent\python.exe' tools/inspect_qwen25_static_onnx.py `
  --model 'C:\Users\zhoux\case9-qwen25-build\onnx-static-kv-1024\qwen25-static-kv-1024-v2.onnx' `
  --output 'C:\Users\zhoux\case9-qwen25-build\onnx-static-kv-1024\contract-v2.json' `
  --report 'C:\Users\zhoux\case9-qwen25-build\onnx-static-kv-1024\inspect-report-v2.json' `
  --source-revision 'modelscope:13448952dbdab7a1627d0680ecd207535d889a23'
```

## 最新板端执行批次

本节覆盖早期 SSH 传输阻断之后的实际执行，时间均为 2026-08-23 UTC。所有模型和报告
仍只保留在板端：

```text
board: HwHiAiUser@192.168.1.90 (orangepiaipro, Ascend310B4 / 8T)
root: ~/case9-qwen25-kv1024
onnx: artifacts/qwen25-static-kv-1024-v2.onnx
onnx_bytes: 1261082122
onnx_sha256: b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e
om: artifacts/qwen25-static-kv-1024-v2.om
om_bytes: 1266010586
om_sha256: f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8
cann: 8.0.0
atc_log: logs/atc-retry1-20260823T110028Z.log
om_lock: artifacts/qwen25-static-kv-1024-v2.om.lock.json
descriptor: contracts/qwen25-static-kv-1024-v2-om-contract.json
acl_smoke: reports/20260823T111040Z-acl-smoke.txt
npu_snapshots: reports/20260823T111040Z-smoke-before.txt,
  reports/20260823T111040Z-smoke-during.txt,
  reports/20260823T111040Z-smoke-after.txt
candidate_acl_service: PID 61313, 127.0.0.1:8084 (句末停止复核；日志保留)
candidate_gateway: PID 61990, 127.0.0.1:7867 (省略 max_tokens 复核；日志保留)
candidate_text_ui: PID 52853, 0.0.0.0:7868 (较早隔离 UI 证据；本轮句末复核以 ACL/网关为准，已停止)
formal_acl_service: PID 65088, 127.0.0.1:8080
formal_gateway: PID 62247, 127.0.0.1:7861
formal_text_ui: PID 64080, 0.0.0.0:7865
current_acl_max_tokens: 80
current_acl_execution_timeout_seconds: 240
current_gateway_timeout_seconds: 270
current_text_ui_llm_timeout_seconds: 300
candidate_sentence_stop: prompt=你是谁？, request_max_tokens=80, prompt_tokens=32, completion_tokens=76, finish_reason=stop, text_suffix=回答。
candidate_gateway_default_max: max_tokens omitted, completion_tokens=76, finish_reason=stop
candidate_sse_max32: complete first sentence, finish_reason=stop, DONE event present
formal_default_max: max_tokens omitted, completion_tokens=22, finish_reason=stop, text_suffix=千问。
formal_max32: request_max_tokens=32, prompt_tokens=32, completion_tokens=22,
  finish_reason=stop, text_suffix=千问。
formal_gateway_max32_sse: gateway_pid=62247, text_suffix=千问。，
  terminal_finish_reason=stop, done_event=true
formal_ui_default_max: max_tokens omitted, http_status=200, first_sentence_stop=true, text_suffix=千问。
formal_health_after_request: ready=true, device_cache_update=true, restart_required=false
post_npu_snapshot: Ascend310B4, Health=Alarm, memory=8284/15610MB, hugepages=647/647
chinese_probe: reports/20260823T-chinese-probe-10.json (8/10 understandable)
stability_probe: reports/20260823T1125-continuous-10.json (10/10 HTTP 200)
performance_probe: reports/20260823T-candidate-benchmark-1warmup-5.json and
  reports/20260823T-baseline-8082-benchmark-1warmup-5.json
formal_promotion: logs/formal-promotion-20260823.json
formal_npu_during_request: logs/formal-npu-during-20260823T121716Z.txt (AICore 69--93% observed)
formal_npu_response: logs/formal-8080-npu-request-20260823T121716Z.json
formal_report_sha256: `78bd9e0de61fb992bcc0e5e87b22f16ff95d0f3cff2d73e2c629be59fa90224f`
formal_npu_report_sha256: `6a5d96230a60c725f52a89cbe19d071f494ffbb7b597c724578bef37cc6aa22c`
formal_runtime_sha256: `fc81c65d02bfeb84241cf31e08728719177a419300ccb6f3eec8eda3d66d7f52`
formal_service_sha256: `7146b566d1583da6341ce8422029af6e30dde28173b693bf6025f3e5d6092e02`
formal_gateway_sha256: `f4cb662b2dc61828fdcf2de43ea293ac4cf8933b1329c204ba58d972e7670bd3`
formal_config_sha256: `f1ec4729e8c43e43bc3cbad7948c93b97455bd392561fd27fb41574c32ca9dd1`
formal_text_ui_sha256: `705d510ce41f12e4a50514fb1ebde44e331c440ca1683a9b677d6ff67eb5eab8`
formal_runtime_refresh_report: `logs/formal-npu-during-runtime-refresh-20260823T122323Z.txt` (AICore 76--77% observed)
formal_runtime_refresh_report_sha256: `e7ce0c80995d18f29f8cee51f5eeaebd2699e8e356009e38db889719af614552`
formal_fix_acl_log: `logs/qwen25-static-kv-1024-formal-fix-8080-20260823T213411+0800.log`
formal_fix_acl_log_sha256: `3fbe34f1ce87b0ffc70e5dd2ca6a277d5557b06905a194acd1749a762c1f34aa`
formal_fix_gateway_log: `logs/qwen25-static-kv-1024-gateway-7861-fix-20260823T213411+0800.log`
formal_fix_gateway_log_sha256: `42bcfda1d56be1df6f9bd8b70aeb3f6920bf926dae21bfc08db7be739127314a`
formal_fix_validation: `logs/qwen25-static-kv-1024-formal-fix-validation.json`
formal_fix_validation_sha256: `69f8bb58187265b541914fe4de4d5f92c3844369bab2121c1ffe48d4c36c60fd`
formal_fix_ui_validation: `logs/qwen25-static-kv-1024-formal-fix-ui-validation.json`
formal_fix_ui_validation_sha256: `3801d9060ed48457490de91175b8fc9dd565b739493db6d981af5558a6b5efd1`
formal_fix_device_cache_update: true after the first repaired formal request
```

正式 ACL 进程使用与候选相同的无 Torch 环境和工件，启动命令固定为：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
nohup python scripts/serve_qwen25_kv_acl.py --host 127.0.0.1 --port 8080 \
  --root "$HOME/case9-qwen25-kv1024" \
  --om "$HOME/case9-qwen25-kv1024/artifacts/qwen25-static-kv-1024-v2.om" \
  --tokenizer "$HOME/case9-qwen25-kv1024/artifacts/tokenizer.json" \
  --tokenizer-config "$HOME/case9-qwen25-kv1024/artifacts/tokenizer_config.json" \
  --contract "$HOME/case9-qwen25-kv1024/contracts/qwen25-static-kv-1024-v2-om-contract.json" \
  --max-tokens 80 > "$HOME/case9-qwen25-kv1024/logs/qwen25-static-kv-1024-fp32-service-8080.log" 2>&1 &
```

网关和文字 UI 分别以 `UPSTREAM_BASE_URL=http://127.0.0.1:8080/v1`、
`UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`、
`RAG_ENABLED=false`、`MAX_CONCURRENT_REQUESTS=1` 启动；`GATEWAY_API_KEY` 只从板端
进程环境继承。没有把密钥、模型或报告复制进仓库。

ATC 的首轮失败日志 `logs/atc-20260823T105821Z.log` 保留，原因是隔离环境没有
`decorator`；没有安装任何包。重试只在候选运行目录创建 `run/atc-pythonpath/decorator.py`
符号链接，链接到 CANN/Miniconda 已有的单文件依赖，并把该文件 SHA-256 写入 OM lock。
这不是把系统 site-packages 或 Torch 环境加入服务运行时。

HTTP 服务最初在 worker 线程中创建 ACL device buffer 时返回 `107002`。诊断证明独立
进程分配完整 51/49 buffers 成功，根因是 worker 线程未在分配前绑定 ACL context。运行时
已修复为在 `begin_request()` 的 dataset/buffer 创建前调用 `acl.rt.set_context()`；本地
相关回归 `25 passed`，板端 JSON/SSE 和 10 轮请求随后全部通过。

候选运行期间为释放 NPU 大页，只停止了明确识别的候选 PID `50887`、`51364` 和旧基线
PID `32734`；旧 8082 的 OM、日志和报告仍保留在 `~/case9-qwen25`。当前可供浏览器
测试的正式地址是：

```text
http://192.168.1.90:7865/
```

正式端口已完成受控提升；正式 ACL 请求期间 `npu-smi` 观察到 AICore `69--93%`，证明
当前入口仍在真实 NPU 执行。同协议性能 p50/p95 分别改善 48.79%/48.70%，中文探测达到
8/10。回滚时只停止 `formal-acl-8080.pid`、`formal-gateway-7861.pid` 和
`formal-text-ui-7865.pid` 中仍匹配命令行的 PID，再恢复已保留的旧配置；不安装 Torch、Torch-NPU、Transformers、
ONNX Runtime、MindSpore、vLLM 或云端替代方案。

## 2026-08-23 句末停止修复复核

为处理“输出几个字后像卡住”的复现，runtime 将句末停止阈值从
`max_tokens - 16` 改为固定的 16 个生成 token。这样默认 80-token 请求不会为了
后续重复句子继续执行；显式 32-token 请求仍遵守调用方硬上限，耗尽上限时通过
`finish_reason=length` 报告，而不是静默提交半句。修改后的
`qwen25_kv_acl_runtime.py` SHA-256 为
`7371271f23bf3b830a4713b6a4aa85e3c10631055b02b927ba1b0a52f65f480c`，
`scripts/provision_qwen25_kv102_board.sh` 默认值同步为 80，SHA-256 为
`e1595a9e9a44dabc5645fb092ce831a61a38be7427b40f47f335bca8058a9711`。

板端正式 ACL 仅重启了命令行完全匹配的旧 PID `64277`，新 PID 为 `65088`；未重启
网关 `62247` 或文字 UI `64080`。新 ACL 使用 `case9-acl-om`、CANN 8.0 和
`acl_om`，`/health` 仍为 `ready=true`、`device_cache_update=true`。对
`你是谁？` 的默认 80-token JSON 请求返回 22 token、`finish_reason=stop`、文本后缀
`千问。`；正式 UI `/api/chat` 连续发送增量，并以包含完整文本的 `done` 事件收尾。
板端仍报告 Ascend310B4、Health Alarm；该状态仅作诊断记录。
本次 ACL 日志为
`~/case9-qwen25-kv1024/logs/qwen25-static-kv-1024-sentence-stop-8080.log`，SHA-256
`14af20f6757405121f26948ce93e3b68586fd7163af88d9f1a6be001bbb259f1`；请求后
`npu-smi` 记录设备内存 `13844/15610 MB`、HugePages `647/647`。

## 2026-08-24 正式服务恢复与回归

上一次受控重启命令被中断后，`8080/7861/7865` 曾短暂全部停止；没有删除模型、
OM、报告或系统 CANN。随后仅按正式链路重新启动三项服务，当前进程为：

```text
ACL      PID 4255  127.0.0.1:8080  --max-tokens 80
Gateway  PID 4726  127.0.0.1:7861
Text UI  PID 4833  0.0.0.0:7865
```

三端健康检查均通过。ACL `/health` 明确返回
`max_tokens=80`、`sentence_stop_min_tokens=16`、`device_cache_update=true`、
`restart_required=false`。板端运行时 SHA-256 为
`7371271f23bf3b830a4713b6a4aa85e3c10631055b02b927ba1b0a52f65f480c`。

真实 NPU 回归结果：

* ACL JSON，显式 `max_tokens=32`：`completion_tokens=22`、`finish_reason=stop`，
  内容为 `我是Qwen，一个由阿里云开发的超大规模语言模型，我叫通义千问。`，本次测量
  总耗时约 28.6 s。
* Gateway SSE：连续转发 22-token 增量，终端 chunk 为 `finish_reason=stop`，随后
  发送 `[DONE]`。
* Text UI `/api/chat`：默认请求同样在首个完整句末结束，最终事件为
  `done(text=完整首句, finish_reason=stop)`；浏览器不会把中间 delta 缺失误显示为
  成功的半句。

本次日志和摘要哈希：ACL
`qwen25-static-kv-1024-health-policy-8080.log` / SHA-256
`412170c8a4b9714f52c8d26f556a9092596ca84182e2c34e8360a6eb2f1b0d3b`；网关日志 SHA-256
`fef8503e512302d8ebf72bddaafd10a3aac2f35613e47be0f075b79b869995d0`；UI 日志 SHA-256
`943a450e833668ad63ec09a0c421022558de278987cc1cb1a8e168c4fdd17326`。请求后
`npu-smi` 为 Ascend310B4、Health Alarm、设备内存 `5712/15610 MB`、HugePages
`647/647`。Health Alarm 仍只作诊断记录。

## 2026-08-25 OS_PANIC 重启诊断与正式链路恢复

开发板在 `2026-08-25 21:24:20 +08:00` 左右重新启动。当前内核命令行包含
`reboot_reason=OS_PANIC`、`dump_data_addr` 和 `dump_data_len`；这不是三个 Python
服务正常退出的证据。重启后的内核日志还记录了：

```text
[DRV_LPM_FAULT] receive fault=0x80E3A203
[bbox] LPM exception id=0xa6193215, reboot_pri=0x2, from_module=lpm
[MATA RAS EVENT INFO] error_code=0x50e
[fpdc] received a safety event ... 0xfc30050e
```

因此本次只能把根因归类为板端 OS/Ascend 驱动或硬件 LPM/RAS 异常；受限用户权限无法
读取完整 crash dump，不能进一步指定硬件部件。`Health: Alarm` 同样只作诊断字段，
不单独阻断本次 API 验证。

重启后没有发现 case9 对应的 systemd service、systemd user service、cron 或桌面自动启动
配置。原服务进程随重启消失，随后在同一 CANN/`case9-acl-om` 环境中手动恢复：

```text
ACL      PID 3974  127.0.0.1:8080  --max-tokens 80
Gateway  PID 4266  127.0.0.1:7861  RAG_ENABLED=false
Text UI  PID 4443  0.0.0.0:7865
```

当前链路为 `192.168.1.90:7865 -> 127.0.0.1:7861 -> 127.0.0.1:8080`。ACL `/health`
返回 `ready=true`、`descriptor_validated=true`、`device_cache_update=true`、
`restart_required=false`、`max_tokens=80`、`sentence_stop_min_tokens=16`。
板端当前运行时源码 SHA-256 为
`7371271f23bf3b830a4713b6a4aa85e3c10631055b02b927ba1b0a52f65f480c`。

本次真实 NPU/API 复测（提示词 `你是谁？`）如下：

* Gateway SSE，调用方 `max_tokens=32`：连续收到 22-token 增量、终止
  `finish_reason=stop` 和 `[DONE]`；原始响应为
  `reports/20260825-gateway-sse-max32.txt`，SHA-256 为
  `f362756bce9a51ef2f8e87d6178714dccea5f0cc99815ea6a685fbefb6e099ad`。
* Text UI JSON：HTTP 成功，返回完整首句
  `我是Qwen，一个由阿里云开发的超大规模语言模型，我叫通义千问。`，
  `finish_reason=stop`；原始响应为 `reports/20260825-text-ui-json.txt`，SHA-256 为
  `e2b38758631a52406131b2b282a30c68ff18d3a0462efd5ffb752efa7a1df5ad`。
* Text UI SSE：重启后的浏览器流仍发送 `delta`，并以包含完整文本的 `done` 事件收尾；
  原始响应为 `reports/20260825-text-ui-sse.txt`，SHA-256 为
  `a64215e1943106a7e005215a302dbb75c1590c88cd60cff21794c3aef6f2e1b8`。
* 端到端 SSH 测量的单次请求约 30 s（22 token），仍是串行 ACL/NPU 的实测速度，
  不能把 SSE 的分块到达误解为每个 token 都即时完成。

恢复后启动日志哈希为：ACL
`dd98694f0d79647aa2729094622b658e1496e27195eaf1200917f92adf6bfbf2`，Gateway
`1cea4c7077d57409a69e55a832ad4c53c20928ca5f6d035707eecc21707827d2`，Text UI
`0ef7138b0a894f00b975afaf09b799dc7c08b531823edfa6b49e7b6dadcf0c22`。复测后
`npu-smi` 仍显示 `Ascend310B4`、`Health=Alarm`、设备内存
`8196/15610 MB`、HugePages `647/647`（21:35 快照）；未将 Alarm 当作应用失败。

本节不宣称服务具备开机自启或故障自动拉起能力；配置 systemd 守护和重启策略属于
后续独立变更，当前不自动安装。

## 2026-08-25 OM 回传本地副本

按用户明确要求，已将板端生成的 OM 从
`~/case9-qwen25-kv1024/artifacts/qwen25-static-kv-1024-v2.om` 同步到控制机：

```text
artifacts/qwen25-kv-1024/board/qwen25-static-kv-1024-v2.om
bytes: 1266010586
sha256: f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8
```

同步采用 WSL `rsync --partial --append-verify`。第一次传输因网络中断在约 58% 停止，
恢复网络后从断点续传完成；本地 SHA-256 与板端重新读取的 SHA-256 一致。OM lock、
OM descriptor contract、ATC 日志和 ACL smoke 文本也同步到被 `.gitignore` 忽略的
board provenance 目录。详细路径、文件哈希和边界见
[`docs/19-qwen25-om-local-copy-record.md`](19-qwen25-om-local-copy-record.md)。

本地副本只证明文件传输完整性，不改变板端 CANN/ACL/NPU 验收结论，也不能在 Windows
控制机直接执行该 OM；正式服务仍使用板端原路径。

## 2026-08-25 第二次重启与可复现归档

在上述恢复后，开发板又于约 `2026-08-25 22:26:43 +08:00` 重启。`22:40:25` 的只读快照
显示 `Ascend310B4`、aarch64、CANN `8.0.0`、Python `3.9.16` 和 ACL 可导入，但
`8080`、`7861`、`7865` 均已停止监听，ACL、网关和文字 UI 进程也不存在。没有据此
声称服务具备自动恢复能力；模型、源码和历史报告仍留在板端。

为便于更换开发板后直接重做，已在控制机建立完整的被 Git 忽略复现包：
`repro/qwen25-kv1024-20260825/`。其中包括 `1,261,082,122` bytes 的单文件 ONNX、
`1,266,010,586` bytes 的 OM、`988,097,824` bytes 的 Qwen checkpoint、tokenizer、
两套精确源码快照、控制机/板端环境锁、ATC/ACL/NPU/API/UI 报告和全量
`SHA256SUMS.txt`。操作、前置条件、禁止包清单和新板启动/停止命令见
[`docs/20-qwen25-kv1024-reproducibility-bundle.md`](20-qwen25-kv1024-reproducibility-bundle.md)
和复现包自身的 `README.md`。
