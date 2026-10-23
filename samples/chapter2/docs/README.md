# 第2章样例文档索引

本索引指向 ResNet50 快速推理样例的工程说明。首次操作从
[样例 README](../README.md)开始，再按任务查阅主题文档。

| 文档 | 内容 |
| --- | --- |
| [ResNet50快速推理流程与接口契约](01-resnet-inference-pipeline.md) | 代码入口、依赖、命令、预处理与后处理 I/O、代理输入输出 |
| [运行边界与故障定位](02-runtime-boundaries-and-failures.md) | CPU/NPU 分界、常见失败、证据等级与未验证项 |

配套源文件：

- [CPU 预处理](../sample_resnet_quick_start/src/make_bin_resnet224_float32.py)
- [Top-K 后处理](../sample_resnet_quick_start/src/postprocess_resnet50.py)
- [上游样例历史说明](../sample_resnet_quick_start/README.md)
- [第2章正文](../../../src/book/chapter2.md)

`sample_resnet_quick_start/README.md` 是从上游样例沿用下来的说明，其中引用的
`scripts/sample_run.sh`、`../cpp/readme_img/` 等路径不在当前目录树中。需要运行
本篇样例时，以本索引和顶层 README 中的现有入口为准。
