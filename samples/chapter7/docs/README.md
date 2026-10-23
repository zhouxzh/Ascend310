# 第7章样例文档索引

本索引把 TBE DSL、Ascend C Kernel 和验证证据分开说明，避免把 Python CPU 调试
与 NPU Kernel 执行混为同一个结果。

| 文档 | 内容 |
| --- | --- |
| [TBE DSL入口与CPU调试契约](01-tbe-dsl-workflow.md) | Python 入口、依赖、命令、随机输入、输出与校验语义 |
| [Ascend C Kernel与Tiling边界](02-ascend-c-kernels-and-tiling.md) | 两个 Kernel 的结构、I/O、tiling 算术、缺失构建链 |
| [失败模式与证据边界](03-failures-and-evidence.md) | 环境、API、shape、ABI 故障以及当前可复现结论 |

样例入口：

- [TBE 加法实现](../add_tbe/add.py)
- [TBE 调试脚本](../add_tbe/run.py)
- [Ascend C 向量加法](../add_ascendc/add_custom.cpp)
- [Ascend C 向量调试脚本](../add_ascendc/run.py)
- [带 Tiling 的 Ascend C 加法](../mat_add_tiling/mat_add_custom.cpp)
- [矩阵加法调试脚本](../mat_add_tiling/run.py)
- [第7章正文](../../../src/book/chapter7.md)

正文给出的标准 Ascend C 工程结构包含 `CMakeLists.txt`、`.ini` 和 Host 侧文件，
当前样例目录并未包含这些文件。主题文档以目录中的真实源码为准，不把正文构想当作
已经存在的构建资产。
