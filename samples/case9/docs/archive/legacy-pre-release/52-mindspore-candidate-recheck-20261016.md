# MindSpore 候选模型复核记录（2026-10-16）

本记录针对当前 20T 开发板 `192.168.1.101`（`Ascend310B1 / 20T`）执行。环境为开发板现有
`base` dirty-base：Python 3.9.2、MindSpore 2.4.10、MindNLP 0.4.1、CANN 8.0.0。
没有安装、升级或删除推理框架；正式入口 `8080 -> 7861 -> 7865` 未修改。

> **2026-10-16 更正。** 本文最初的 Qwen2/Qwen2.5 `TBE task_distribute`
> 记录发生在验收客户端 30 秒超时并主动断开之后。候选服务按 fail-closed 规则结束
> worker，随后 TBE 子进程才报告 `main process disappeared`。该现象不能单独归因于
> 模型、TBE 或显存不足。本文件保留原始日志作为历史证据；修正后的 300 秒服务
> watchdog、330 秒客户端重测及最终判断见
> [54](54-mindspore-watchdog-timeout-analysis-20261016.md) 与
> [56](56-mindspore-20t-isolated-rerun-20261016.md)。

## 复核范围

本批次逐个复核 8 个原生 MindSpore Profile。每个模型单独启动 8090 worker，完成后停止并释放
worker，再开始下一个模型。工件放在板端独立目录 `~/case9-mindspore-chat/recheck-20261016/`，
原始验收 JSON 已同步到 `repro/mindspore-20t-recheck-20261016/reports/`。

## 结果摘要

| Profile | 复核结果 | 证据边界 |
| --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | `experimental_isolated` | 超时修正后完成 JSON、SSE、64-token、稳定性、质量结构检查和 30 次性能，吞吐 p50 `1.531 token/s`；人工质量待审。 |
| `deepseek-r1-qwen-1.5b-mindspore` | `blocked` | 模型加载和 health/models 通过；首次生成后设备内存池出现 `Expand block failed`，接口进入 503。 |
| `tinyllama-1.1b-mindspore` | `blocked` | 加载、JSON/SSE 短请求通过；持续生成触发 TBE `task_distribute main process disappeared`，随后 503。 |
| `qwen2-0.5b-instruct-mindspore` | `blocked` | 超时修正后通过 JSON/SSE、64-token、稳定性和性能；中文加法探测产生 `U+FFFD`，不可激活。 |
| `qwen2-1.5b-instruct-mindspore` | `blocked` | 更正模型根目录后完成工件、Ascend、JSON/SSE、64-token、稳定性和性能；中文加法探测产生 `U+FFFD`，不可激活。 |
| `qwen2.5-0.5b-mindspore` | `experimental_isolated` | 超时修正后完成 JSON/SSE、64-token、稳定性、机器质量和性能；吞吐 p50 `1.531 token/s`，人工质量待审。 |
| `qwen2.5-1.5b-mindspore` | `blocked` | 低占用复测通过 JSON/SSE、64-token、稳定性和性能；中文加法探测产生 `U+FFFD`。一次高占用运行的 `Alloc failed` 单独保留。 |
| `qwen3-0.6b-mindspore` | `blocked` | MindNLP 0.4.1 没有 Qwen3 loader；未下载权重。 |
| `qwen3-1.7b-mindspore` | `blocked` | MindNLP 0.4.1 没有 Qwen3 loader；未下载权重。 |
| `minicpm3-4b-mindspore` | `blocked` | MiniCPM3 类可见，但当前 Modelers revision 是可变 `main`，没有固定 revision 和权重 SHA-256；未下载权重。 |

## 关键观察

### Qwen1.5

Qwen1.5 是本批次唯一通过完整当前地址机器门的 MindSpore 模型，但仍受共享 dirty-base 和人工质量
审核约束，不能自动提升为正式生产模型。

### DeepSeek

DeepSeek 不是 loader 缺失，也不是工件损坏。工件加载、NPU context 和服务启动通过；生成阶段出现：

```text
Expand block failed, expand size : 0.
```

随后请求返回 503。该结果只说明当前 `DeepSeek + MindSpore 2.4.10 + MindNLP 0.4.1 + CANN 8.0 +
Ascend310B1` 组合未通过生成门，不能外推为模型或硬件永久不支持。

### TinyLlama 与 Qwen2.5

这几个模型都能加载并完成部分短请求，但在连续解码阶段出现底层 TBE 子进程退出：

```text
TBE Subprocess[task_distribute] raise error[], main process disappeared!
```

服务随后 fail-closed 并返回 503。`resource_tracker` 的 semaphore 警告是进程异常后的清理副作用。
Qwen2.5 日志另外出现滑动窗口注意力在 eager 路径未实现的警告；这是需要后续单独隔离的运行时路径，
不是本记录中已经证明的唯一根因。

## Qwen3 与 MiniCPM3 的边界

Qwen3 的复核停在 loader 门，避免在当前环境下载大体积权重或升级 MindNLP。MiniCPM3 的专用
`MiniCPM3Tokenizer`/`MiniCPM3ForCausalLM` 类可以被发现，但 Modelers 的可变 `main` 不满足本项目的
可复现工件规则，因此本批次没有把“类可见”误写成“模型可运行”。

## 原始证据

- `repro/mindspore-20t-recheck-20261016/reports/qwen15-acceptance.json`
- `repro/mindspore-20t-recheck-20261016/reports/deepseek-acceptance.json`
- `repro/mindspore-20t-recheck-20261016/reports/tinyllama-acceptance.json`
- `repro/mindspore-20t-recheck-20261016/reports/qwen25-05-acceptance.json`
- `repro/mindspore-20t-recheck-20261016/reports/qwen25-15-acceptance.json`
- `repro/mindspore-20t-recheck-20261016/reports/qwen3-06-acceptance.json`
- `repro/mindspore-20t-recheck-20261016/reports/qwen3-17-acceptance.json`

板端完整日志仍保留在对应的 `~/case9-mindspore-chat/run/mindspore-20t-campaign/` 和
`~/case9-mindspore-chat/reports/mindspore-chat/` 目录中。

## 当前可用性结论

在当前隔离环境和当前 20T 板上，Qwen1.5-0.5B、Qwen1.5-1.8B 与 Qwen2.5-0.5B 具备完整可运行机器证据，
状态为 `experimental_isolated`。Qwen2-0.5B 与 Qwen2.5-1.5B 因可见 UTF-8 文本损坏保持
`blocked`；Qwen2-1.5B 已在同样探测中复现该文本损坏。DeepSeek、TinyLlama、Qwen3 与 MiniCPM3 保留各自的前置阻断。任何模型都没有
自动替换正式 Qwen2.5 ACL 入口。
