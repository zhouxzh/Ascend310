# Ascend C Kernel与Tiling边界

`add_ascendc/add_custom.cpp` 与 `mat_add_tiling/mat_add_custom.cpp` 展示了
Ascend C 的 Global Memory、Unified Buffer、双缓冲队列和 tiling 元数据用法。
当前目录只提供 Kernel 源文件，尚未提供让这些 Kernel 成为可构建算子的工程闭环。

## 共同入口契约

两个文件都导出 `extern "C" __global__ __aicore__` Kernel：

| 目录 | Kernel 符号 | 数学语义 |
| --- | --- | --- |
| `add_ascendc/` | `add_custom` | 扁平 float32 数据上的 `z = x + y` |
| `mat_add_tiling/` | `mat_add_custom` | 扁平 float32 数据上的 `c = a + b` |

参数集合均为：

```cpp
GM_ADDR x, GM_ADDR y, GM_ADDR z, GM_ADDR workspace, GM_ADDR tiling
```

其中 `workspace` 在当前实现中未使用。`tiling` 由 `GET_TILING_DATA` 解包，
`Init()` 读取以下两个字段：

```cpp
tilingData.totalLength
tilingData.tileNum
```

入口只在 `TILING_KEY_IS(1)` 时调用 `Process()`。这些宏、tiling 结构体定义、
Kernel 注册信息和 tiling key 的生成逻辑均不在目录中，必须由 CANN 算子工程提供。

## 数据流

两个 Kernel 的结构相同：

1. `Init()` 按 `GetBlockNum()` 把 `totalLength` 平均分给 AI Core。
2. 每个 Core 把两个输入 GlobalTensor 绑定到自己的偏移区间。
3. `pipe.InitBuffer()` 为两个输入队列和一个输出队列分配双缓冲。
4. `Process()` 执行 `tileNum * BUFFER_NUM` 次循环。
5. 每次循环执行 `CopyIn -> Compute -> CopyOut`。
6. `CopyIn` 用 `DataCopy` 搬入 UB，`Compute` 调用 `Add`，`CopyOut` 写回 Global
   Memory。

这里的“矩阵加法”没有读取 M 或 N。`mat_add_custom.cpp` 仍把数据当作一维元素
序列处理；矩阵语义只由外部调用者传入的 `totalLength` 和内存布局保证。

## Shape与dtype约束

C++ Kernel 把地址显式转换为 `__gm__ float*`，因此当前实现的实际计算 dtype 是
`float32`。即使 TBE 示例声明支持 `float16` 或 `int32`，也不能把这个声明套用到
C++ Kernel。

索引区间由整数除法计算：

```cpp
blockLength = totalLength / GetBlockNum();
tileLength = blockLength / tileNum / BUFFER_NUM;
```

因此要求：

- `totalLength` 能被 AI Core 数量整除。
- 每个 Core 的 `blockLength` 能被 `tileNum * BUFFER_NUM` 整除。
- `tileLength` 至少为 1，且 `tileLength * sizeof(float)` 不超过该缓冲区可用容量。
- 传入的 tiling 结构确实提供 `totalLength` 与 `tileNum`。

源码没有检查余数。不能整除时，末尾元素不会进入循环；商为 0 时还会产生零长度
搬运或无效 UB 分配。生产实现必须由 Host tiling 函数和 Kernel 两侧共同保证边界。

## `add_ascendc`路径

[`add_custom.cpp`](../add_ascendc/add_custom.cpp) 是简洁的双缓冲向量加法示例。
它没有独立 Host 调用、算子原型、信息库或构建文件。

同目录 [`run.py`](../add_ascendc/run.py) 使用 TBE DSL 和 CPU `llvm` target
验证 `a + 2b`。它没有：

- 导入或编译 `add_custom.cpp`。
- 包含 `kernel_operator.h`。
- 生成 `add_custom.o` 或 OM。
- 在 NPU 上执行 `add_custom`。
- 比较 C++ 输出，因为 C++ Kernel 没有运行。

因此该脚本只能说明 TBE debug API 的表达式校验路径，不能说明 Ascend C 向量加法
实现正确。

## `mat_add_tiling`路径

[`mat_add_custom.cpp`](../mat_add_tiling/mat_add_custom.cpp) 的结构与向量加法相同，
差别主要是注释和命名。所谓 tiling 由外部 `tileNum` 控制，但目录中没有 Host 侧
根据目标 UB 容量、core 数量和对齐约束计算 `tileNum` 的代码。

[`run.py`](../mat_add_tiling/run.py) 使用 shape `(128, 256)`、`float32`，共 32768
个元素、131072 字节，并打印“超过 UB、自动 tiling”的固定说明。该说明来自脚本，
脚本没有查询板卡 UB 容量，也没有检查 TBE 自动调度结果。它同样验证最终表达式
`a + 2b`，不验证 C++ 的 `a + b`。

文件头注释中的“对应章节：chapter6 第4节/第5节”与当前配套正文为第7章不一致。
这是源码中的历史注释漂移；文档以目录结构和实际代码为准。

## 缺失的构建链

要使 C++ Kernel 成为可验证算子，至少还需要 CANN 版本匹配的：

- `kernel_operator.h` 与 Ascend C 编译器环境。
- 能生成 `totalLength`、`tileNum` 和 tiling key 的 Host tiling 函数。
- 算子原型与 `.ini`/JSON 注册信息。
- 编译入口，例如正文提到的 CMake 或算子工程脚本。
- 单算子调用程序、输入输出分配、数值基准和失败退出协议。

这些文件不在当前 `add_ascendc/` 或 `mat_add_tiling/` 中。本文不提供未经验证的
`ccec` 命令来暗示已经可以编译；应先在固定 CANN 版本上补齐完整工程，记录命令、
日志和生成物哈希，再执行板端验收。
