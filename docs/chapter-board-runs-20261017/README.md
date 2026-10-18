# chapter2–9 双板原始运行记录

本目录保存 2026-10-17 chapter2–9 双板测试的原始 stdout/stderr、环境快照和 `summary.tsv`。结论和差异解释见 [`../ascend310b-chapter-board-comparison-20261017.md`](../ascend310b-chapter-board-comparison-20261017.md)。

## 目录

| 目录 | 内容 |
| --- | --- |
| `8t/run1/`、`20t/run1/` | 第一轮 chapter2–9 主矩阵 |
| `8t/run2/`、`20t/run2/` | chapter3 长预算、chapter5 选定子集、chapter2 帮助 |
| `8t/chapter9/`、`20t/chapter9/` | Tiny-ImageNet 数据下载、FP32/FP16 对比和性能脚本 |
| `tool-execution/` | 新插件工具执行的任务、HF/GitHub/Huawei 下载、ATC、`msame`、Python 和代理验证结果 |

`tool-execution/tool-matrix.json` 是早期一轮原始记录，包含当时 Python PATH 和 GitHub 默认代理尚未修正的失败；`tool-smoke2.json` 是修正后的 Python、HF 和 GitHub proxy 复测，`full-matrix.txt` 是通过新任务工具重跑的 chapter 主矩阵；chapter2 的真实 ATC/`msame` 结果在 `tool-matrix.json`，chapter9 的修正后结果在 `chapter9-task-tool.txt`。

每个批次都有 `summary.tsv`；第一轮另有 `meta.txt`，记录板端 Python、`npu-smi` 和 CANN 版本。原始日志保留失败信息，不把 `N/A`、`blocked` 或 `untested` 改写成通过。
