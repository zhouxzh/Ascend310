# 20T MindSpore 聊天模型验收记录（2026-10-16）

目标板为 `192.168.1.101`，实测 `Ascend310B1 / 20T`，CANN `8.0.0`，Python `3.9.2`，MindSpore `2.4.10`，MindNLP `0.4.1`。测试使用现有 `base` dirty-base 环境，未安装、升级或删除任何包；Torch 系列包只作为既有环境污染记录，适配服务没有导入它们。正式 `8080 -> 7861 -> 7865` 未修改。

> **历史记录更正：** 本文记录的是 30 秒客户端超时批次。该客户端断开会触发
> fail-closed 清理，后续 TBE `main process disappeared` 不能单独视为模型崩溃。最新
> `case9-ms240-cann80` 隔离环境、300 秒 worker watchdog 和 330 秒客户端复测见
> [54](54-mindspore-watchdog-timeout-analysis-20261016.md) 和
> [56](56-mindspore-20t-isolated-rerun-20261016.md)，后者覆盖本表中的 Qwen1.5、
> Qwen2 和 Qwen2.5 状态。

## 下载源与镜像边界

板端不能稳定直连 `huggingface.co`，所以本批次的 Hugging Face 工件下载器和
MindNLP worker 默认使用 `HF_ENDPOINT=https://hf-mirror.com`。固定 revision、HTTP
大小和 SHA-256 校验仍然是硬门；镜像只改变传输入口，不改变模型来源或工件内容。

已在 `192.168.1.101` 上用 Qwen2.5-0.5B 的 `config.json` 做了镜像传输探测：
镜像返回 200，文件大小为 659 字节，SHA-256 为
`18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45`。

MiniCPM3 不属于 Hugging Face 下载路径。官方示例（[Ascend MiniCPM3 示例](https://www.hiascend.com/developer/techArticles/20250612-1)）使用 MindNLP 的
`mirror='modelers'`，其文件 URL 形态为 `modelers.cn/.../media/<file>`。因此
`HF_ENDPOINT` 不能替代 Modelers；当前 Profile 的 revision 仍为可变的 `main`，
没有可锁定的 Modelers revision 和权重哈希，本批次没有下载或加载 MiniCPM3。
这次 `blocked` 是工件来源/可复现性门，不是对 MiniCPM3 在 20T 上兼容性的否定。

## 批次结论

| Profile | 工件/加载 | 生成与协议 | 当前判定 |
| --- | --- | --- | --- |
| Qwen1.5-0.5B | 已复用完整报告，并完成当前地址短回归 | health、models、JSON、SSE、协议短回归通过；完整性能沿用 2026-10-15：首 token p50 655.5 ms、总耗时 p50 1295.4 ms、约 1.544 token/s | `experimental_dirty_base`，人工质量待审 |
| TinyLlama-1.1B | 工件校验和加载通过 | 32 token 输出出现 `U+FFFD`，后续 64 token 超时并进入 `model_unavailable` | `blocked` |
| DeepSeek-R1-Distill-Qwen-1.5B | 五个工件校验、worker 加载通过 | 首次 JSON 生成触发 TBE `task_distribute` 子进程崩溃；服务退出 | `blocked` |
| Qwen2.5-0.5B | 六个工件 SHA-256 通过，worker 加载、health/models 通过 | 首个 JSON 生成触发同样 TBE 崩溃，后续请求 503 | `blocked` |
| Qwen2.5-1.5B | 七个工件 SHA-256 通过，worker 加载、health/models 通过 | 2-token JSON/SSE 与 8/16 token 通过；32 token 超时后 TBE 崩溃 | `blocked` |
| Qwen3-0.6B | 未下载权重 | MindNLP 0.4.1 无 Qwen3 loader | `blocked`（loader） |
| Qwen3-1.7B | 未下载权重 | MindNLP 0.4.1 无 Qwen3 loader | `blocked`（loader） |
| MiniCPM3-4B | 专用类可见，但未下载权重 | 官方来源是 Modelers；当前 revision 为可变 `main`，未形成固定 Modelers 工件锁 | `blocked`（artifact provenance；不代表模型永久不兼容） |

## 原始证据

每个运行目录保留在板端 `~/case9-mindspore-chat/`：

- Qwen1.5 短回归：`reports/mindspore-chat/qwen1p5-20t-short-20261016c/acceptance.json`
- TinyLlama 完整报告：`reports/mindspore-chat/tinyllama-20t-full-20261016/acceptance.json`
- DeepSeek 完整报告：`reports/mindspore-chat/deepseek-20t-full-20261016b/acceptance.json`
- Qwen2.5-0.5B：`reports/mindspore-chat/qwen25-05-20t-full-20261016c/acceptance.json`
- Qwen2.5-1.5B：`reports/mindspore-chat/qwen25-15-20t-full-20261016/acceptance.json`
- Qwen3 loader 报告：`run/mindspore-20t-campaign/qwen3-06-20t-loader-20261016/acceptance.json` 与 `qwen3-17-20t-loader-20261016/acceptance.json`
- MiniCPM3 获取日志：`run/mindspore-20t-campaign/minicpm3-20t-full-20261016/fetch.json`、`/tmp/minicpm3-20t-full-20261016.log`
- MiniCPM3 Modelers 边界复测：`run/mindspore-20t-campaign/minicpm3-20t-modelers-boundary-20261016b/`；只验证了
  “mutable main 不能进入固定锁”的拒绝路径，未下载权重。

## 失败边界

多个模型在同一环境、同一 SoC 的首个或较长生成阶段出现 TBE `task_distribute` 主进程消失，并伴随 30 个 semaphore 泄漏警告。该观察支持“当前 MindSpore 2.4.10/MindNLP 0.4.1/CANN 8.0 与这些模型/生成路径组合存在运行时阻断”的结论，但不外推为硬件或模型永久不支持。后续若要继续，应先保留本批次证据，单独研究 CANN/MindSpore 版本配套；本批次不升级环境、不切换 CPU、不恢复正式入口。

`Health: Alarm` 已记录在 `npu-smi` 快照中，不是本批次单独阻断原因。所有 worker 在批次结束后均停止，端口 `8090` 不保留常驻服务。
