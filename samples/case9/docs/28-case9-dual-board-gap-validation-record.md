# Case9 双板模型缺口验证记录

_记录版本：1.1｜日期：2026-08-30｜状态：`completed`（缺口证据已归档；部分组合明确为 `blocked`/`not-run`）_

本文是补测账本。表中的“已有证据”来自此前独立批次；“缺口批次”均在产生原始
报告后填写。`completed` 只表示本轮组合盘点和证据归档闭环，不表示每个模型都可用、
中文质量通过或正式准入。空白或 `not-run` 不代表模型不能运行。

## 1. 板卡身份

| 板卡 | 当前 IP | 历史 IP | SoC/算力 | 备注 |
| --- | --- | --- | --- | --- |
| 8T | `192.168.1.90` | `192.168.8.178` | `Ascend310B4 / 8T` | 同一块板，IP 变化不自动产生新性能批次 |
| 20T | `192.168.1.95` | `192.168.8.210` | `Ascend310B1 / 20T` | `.210` 不作为当前连接入口；需以 `.95` 的原始快照为准 |

两板均曾记录 `Health: Alarm`；该字段仅为诊断信息，不单独判定成功或失败。

## 2. 模型和组合矩阵

### 2.1 ONNX→OM→ACL

| 组合 | 工件身份 | 已有证据 | 缺口批次状态 |
| --- | --- | --- | --- |
| Qwen2.5 Static-KV / B4 `.90` | ONNX SHA `b4870df5...d1a3c0e`；B4 OM SHA `f6650e52...1140eb8` | 完整 ACL、JSON/SSE、长输出、稳定性和性能；历史采集地址 `.178` | `passed`（当前身份只读复核；报告 `repro/case9-dual-board-gap-20260830/identity-input/board8t-qwen25-current-identity.json`，SHA `738e2788...d2474`；不重跑历史性能） |
| Qwen2.5 Static-KV / B1 `.95` | 历史 B1 ONNX SHA `b4870df5...d1a3c0e`；OM SHA `6bca884f...6298609` | 历史 B1 完整批次，采集地址为 `.210` | `blocked`（当前身份报告 `repro/case9-dual-board-gap-20260830/identity-input/board20t-qwen25-current-identity.json`，SHA `2f556e06...b751eb`；当前板无 ONNX/OM/contract/lock，未执行 ACL load） |
| B4 OM 在 B1、B1 OM 在 B4 | 同上 | 仅有历史跨 SoC compatibility 说明 | `not-run`（本批次不执行跨 SoC 互载，不作为 native 结果） |

历史性能参考：B4 总耗时 p50/p95 `8693.731/8707.133 ms`、吞吐 `0.230 token/s`；
B1 总耗时 p50/p95 `6486.422/6506.085 ms`、吞吐 `0.308 token/s`。详细报告见
[`01-qwen25-dual-board-validation.md`](01-qwen25-dual-board-validation.md)。这些数值
不是本缺口批次新测量，也不应与 MindNLP 服务数字直接排名。

### 2.2 MindNLP/MindSpore

| 模型 | 8T `.90` | 20T `.95` |
| --- | --- | --- |
| Qwen1.5-0.5B-Chat | 已有完整批次：总耗时 p50/p95 `1412.236/1603.883 ms`，吞吐 `1.420 token/s`；`experimental_dirty_base` | `.95` 缺口 `passed`，9/9 机器门；总耗时 p50/p95 `1329.830/1440.076 ms`，首事件 p50/p95 `661.419/754.165 ms`，吞吐 p50/p95 `1.505/1.619 token/s`；报告见下表，人工质量待审，仍为 `experimental_dirty_base` |
| TinyLlama-1.1B-Chat | 已有批次：总耗时 p50/p95 `3114.857/3185.731 ms`，吞吐 `0.642 token/s`；长输出含 `U+FFFD`，`blocked` | `.95` 缺口 `failed`，8/9 机器门；总耗时 p50/p95 `1939.938/2003.937 ms`，首事件 p50/p95 `1938.973/2003.336 ms`，吞吐 p50/p95 `1.031/1.044 token/s`；32/48 token UTF-8 失败、中文机器质量 7/10，保持 `blocked` |
| DeepSeek-R1-Distill-Qwen-1.5B FP16 | `.90` 缺口 `passed`，9/9 机器门；总耗时 p50/p95 `3938.489/4008.768 ms`，首事件 p50/p95 `3932.103/4002.231 ms`，吞吐 p50/p95 `0.510/0.517 token/s`；报告见下表，人工质量待审，`experimental_dirty_base` | 完整临时 API：总耗时 p50/p95 `2484.751/2557.242 ms`，吞吐 `0.805 token/s`；中文质量未通过，`blocked` |

上表的 MindNLP 性能批次不是完全同一 prompt/token 数，跨模型数值只作各自记录。
DeepSeek 8T 与 20T 的 4-token 直接对照使用相同协议，20T p50 延迟约低 22.9%、吞吐
约高 31.8%；该对照与本轮 64-token 长输出均不能替代人工质量签字或正式准入。

### 2.3 缺口批次的 64-token 长输出

以下数值来自各自 `long-output.json`，不是估算值。`finish_reason=stop` 表示模型在
64-token 上限前结束；`length` 表示达到上限。

| 板卡/模型 | `max_tokens` | 实际 completion tokens | `finish_reason` | UTF-8 | `valid_for_budget` | 记录 |
| --- | ---: | ---: | --- | --- | --- | --- |
| `.95` Qwen1.5 | 64 | 55 | `stop` | `true` | `true` | `repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/long-output.json` |
| `.95` TinyLlama | 64 | 64 | `length` | `true` | `true` | `repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/long-output.json` |
| `.90` DeepSeek | 64 | 64 | `length` | `true` | `true` | `repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/long-output.json` |

TinyLlama 同一批次的 32 和 48 token 行分别为 `utf8_valid=false`、
`valid_for_budget=false`；不能因为 64-token 行恢复为有效就清除失败状态。

## 3. 已有报告索引

| 证据 | 路径 |
| --- | --- |
| Qwen2.5 双板 ACL | [`repro/qwen25-kv1024-dual-board-20260827/reports/usage-perf/`](../repro/qwen25-kv1024-dual-board-20260827/reports/usage-perf/) |
| Qwen2.5 当前身份（`.90`/`.95`） | [`repro/case9-dual-board-gap-20260830/identity-input/`](../repro/case9-dual-board-gap-20260830/identity-input/) |
| 8T Qwen1.5 | [`repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/`](../repro/mindspore-chat-20260829/reports/board8t/qwen-full-20260829b/) |
| 8T TinyLlama | [`repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/`](../repro/mindspore-chat-20260829/reports/board8t/tiny-full-20260829b/) |
| 20T Qwen1.5 缺口 | [`repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/`](../repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/) |
| 20T TinyLlama 缺口 | [`repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/`](../repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/) |
| 8T DeepSeek 缺口 | [`repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/`](../repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/) |
| 20T DeepSeek API | [`repro/deepseek-r1-20t-20260830/reports/board20t/api/reopen-20260830T0536Z/`](../repro/deepseek-r1-20t-20260830/reports/board20t/api/reopen-20260830T0536Z/) |
| MindNLP 综合说明 | [`24-mindspore-chat-validation-record.md`](24-mindspore-chat-validation-record.md) |

报告目录属于 Git 忽略的复现资产；若本地不存在某个板端原始文件，不得用文档数字
代替文件证据。

## 4. 缺口批次登记表

以下表格由 `run_case9_gap_acceptance.sh` 或人工执行后填写；已执行项引用原始报告，
有意未执行的兼容性组合保持 `not-run`，禁止把历史报告冒充当前板结果。

| run_id | 板卡/IP | 模型/路线 | G0-G8 汇总 | 性能报告 | 原始报告/日志 | 当前状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `deepseek-8t-gap-20260830b` | `.90` / `192.168.1.90` | DeepSeek / MindNLP | 9/9 机器门；JSON/SSE/长输出/稳定性/协议通过；中文机器 10/10，人工待审 | `repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/performance.json`；总耗时 p50/p95 `3938.489/4008.768 ms`，吞吐 `0.510/0.517 token/s` | `repro/case9-dual-board-gap-20260830/reports/board8t/deepseek-r1-qwen-1.5b-mindspore/deepseek-8t-gap-20260830/acceptance.json`（68,211 bytes，SHA `6f04a998980dbf2c872f0c470178af6e028d48da3c8af36d21fab08b30f43ab1`）；64 tokens 实际 64，`length`，UTF-8 有效 | `passed` + `experimental_dirty_base` |
| `qwen20-gap-20260830` | `.95` / `192.168.1.95` | Qwen1.5 / MindNLP | 9/9 机器门；JSON/SSE/长输出/稳定性/协议通过；中文机器 10/10，人工待审 | `repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/performance.json`；总耗时 p50/p95 `1329.830/1440.076 ms`，吞吐 `1.505/1.619 token/s` | `repro/case9-dual-board-gap-20260830/reports/board20t/qwen1.5-0.5b-mindspore/qwen20-gap-20260830/acceptance.json`（68,315 bytes，SHA `978864c6ddab8d7944d318748abaa1a00145c2ce6d5dcb6b6b1b40014b62e1c4`）；64 tokens 实际 55，`stop`，UTF-8 有效 | `passed` + `experimental_dirty_base` |
| `tiny20-gap-20260830` | `.95` / `192.168.1.95` | TinyLlama / MindNLP | 8/9 机器门；JSON/SSE/稳定性/协议通过，长输出失败；中文机器 7/10 | `repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/performance.json`；总耗时 p50/p95 `1939.938/2003.937 ms`，吞吐 `1.031/1.044 token/s` | `repro/case9-dual-board-gap-20260830/reports/board20t/tinyllama-1.1b-mindspore/tiny20-gap-20260830/acceptance.json`（66,436 bytes，SHA `b160e2d36d734a7f94c6e92722e97eb02c26f5de468480ac625808b54f0d97db`）；64 tokens 实际 64，`length`，UTF-8 有效；32/48 行无效 | `failed` / `blocked` |
| `identity-20260830T105928Z` | `.90` / `192.168.1.90` | Qwen2.5 OM 当前身份 | 只读身份、ACL 导入、descriptor 和工件哈希通过；不执行推理 | 不重跑历史性能 | `identity-input/board8t-qwen25-current-identity.json`（2,074 bytes，SHA `738e2788...d2474`） | `passed`（identity-only） |
| `identity-20260830T105959Z` | `.95` / `192.168.1.95` | Qwen2.5 OM 当前身份 | 工件搜索和环境快照完成；当前 ONNX/OM/contract/lock 缺失，未执行 ACL load | 不重跑 | `identity-input/board20t-qwen25-current-identity.json`（1,676 bytes，SHA `2f556e06...b751eb`） | `blocked` |
| `cross-soc-20260830` | `.90` ↔ `.95` | Qwen2.5 B4/B1 跨 SoC 互载 | 有意不执行；仅保留历史 compatibility 说明 | 不适用 | 本记录第 2.1 节及历史跨板文档 | `not-run` |

### 4.1 每个缺口批次必须填写的字段

```text
run_id / UTC start / UTC end
board_ip / hostname / soc / compute_tier
cann / python / mindspore / mindnlp / package pollution
model revision / tokenizer revision / artifact bytes / sha256
worker pid / command / exit code
load_seconds / first_event_ms / total_p50_ms / total_p95_ms / tokens_per_second
json / sse / long_output / stability / quality / protocol statuses
npu_before / npu_during / npu_after / rss / fd / hugepages
failure_or_rollback / report_paths / sha256 verification
```

## 5. 质量、协议和准入边界

机器协议门（HTTP 200、schema、SSE、资源和错误码）与中文质量门独立记录。TinyLlama
已有的 `U+FFFD` 是质量/长输出失败证据，不因 NPU 执行成功而解除 `blocked`；DeepSeek
和 Qwen1.5 的机器 `10/10` 只代表编码/协议检查通过，不代表中文回答正确。三个
MindNLP 缺口结果共享 dirty `base`，最多标为 `experimental_dirty_base`，需要人工审核
和干净环境复核后才能考虑 `admitted`。

任何缺口组合若遇到 SSH 超时、ACL/MindSpore load 失败、LPM fault、进程泄漏或工件
哈希不符，状态写为 `blocked` 或具体失败状态，并保留原始输出。不得填写估算 token/s，
不得把另一块板或另一模型的结果继承过来。

## 6. 当前完成判定

截至本记录日期，本轮缺口证据已完成归档，状态为 `completed`。完成范围包括：

1. Qwen1.5/20T、TinyLlama/20T、DeepSeek/8T 均有真实 acceptance、性能和 64-token
   长输出报告；TinyLlama 的失败门原样保留为 `blocked`；
2. Qwen2.5 `.90` 当前身份为 `passed`（只读），`.95` 因当前工件缺失为 `blocked`；
3. 跨 SoC 互载明确为 `not-run`，历史 `.210` 只作 provenance；
4. 每个缺口数字都有报告路径和 SHA-256，教程矩阵区分 B4/B1 native、跨 SoC
   compatibility、MindNLP dirty-base 和中文质量；复现包 `SHA256SUMS.txt` 与
   `bundle-manifest.json` 的 `197/197` 项已重新生成并通过当前文件校验；
5. 正式 `8080 -> 7861 -> 7865` 入口未被本批次测试脚本自动切换。

仍未完成且不属于本轮证据闭环的事项：人工中文质量签字、干净环境准入、Qwen2.5 `.95`
工件恢复、跨 SoC 互载和正式网关/UI 切换。它们必须另行批准，不能从本记录的机器门
结果推导。
