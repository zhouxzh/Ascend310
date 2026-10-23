# 第7章 算子开发样例

本目录配套[第7章 算子开发实战](../../src/book/chapter7.md)，收录 TBE DSL 与
Ascend C 两类加法示例。三个子目录的用途不同，不能把 Python 脚本的 CPU 调试
结果当成 Ascend C 内核的板端执行结果。

详细接口与证据边界见[样例文档索引](docs/README.md)。

## 样例目录

| 目录 | 入口 | 当前用途 |
| --- | --- | --- |
| [`add_tbe/`](add_tbe/) | `add.py` | 用 TBE DSL 描述广播加法，目标后端为 `cce`，调用 `dsl.build()` 编译算子 |
| [`add_tbe/`](add_tbe/) | `run.py` | 在 TBE debug/CPU 目标上验证中间结果 `x + y` 和最终结果 `x + 2y` |
| [`add_ascendc/`](add_ascendc/) | `add_custom.cpp` | Ascend C 向量加法 Kernel，计算 `z = x + y` |
| [`add_ascendc/`](add_ascendc/) | `run.py` | TBE DSL 的 CPU 调试脚本，不对应、也不加载 `add_custom.cpp` |
| [`mat_add_tiling/`](mat_add_tiling/) | `mat_add_custom.cpp` | 使用双缓冲与 tiling 元数据的 Ascend C 加法 Kernel |
| [`mat_add_tiling/`](mat_add_tiling/) | `run.py` | TBE DSL 随机数据验证，不对应、也不加载 C++ Kernel |

两个 C++ Kernel 目录都没有 `CMakeLists.txt`、算子原型、`.ini`、Host 侧 Tiling
实现或实际编译脚本。当前不能按仓库内容直接构建 `.o`、`.om` 或可执行文件。

## 前置环境

Python 入口依赖 CANN 提供的 `tbe` 包及其 `tvm`、`dsl`、`testing` 模块，还需要
NumPy。仓库统一板端环境名为 `ascend`，但本样例没有记录已经在某个精确
CANN/driver/firmware 组合上通过的 TBE 结果。

建议的板端 shell 形态：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/chapter7
```

`PYTHONNOUSERSITE=1` 隔离用户 site，符合仓库的统一环境规则。若当前板端的 TBE
只能由某个已验证 CANN 安装提供，应先记录它的导入路径和版本，再运行样例；不能
临时改回用户 site 掩盖环境错误。

## TBE DSL编译入口

```bash
cd add_tbe
python add.py
```

`add.py` 的直接入口使用 shape `(5, 6, 7)`、dtype `float16` 和 kernel name
`add`。成功时预期在当前目录生成 CST 编译产物，通常位于 `kernel_meta/`；实际
文件名、目录结构与 CANN 版本有关，不能把正文中的 `add.o`、`add.json` 当成已经
在仓库验证过的事实。

## TBE DSL CPU调试

```bash
cd add_tbe
python run.py

cd ../add_ascendc
python run.py

cd ../mat_add_tiling
python run.py
```

三个脚本都在 `debug()` 中创建随机 NumPy 数组，并用 `llvm` CPU target 运行 TBE
DSL。它们在第一阶段验证 `data_c = a + b`，随后把 `data_c + b` 构造成最终输出，
因此第二阶段实际验证的是 `a + 2b`，不是单个 `a + b` Kernel。

`add_tbe/run.py` 还会在当前目录写出 `samplefile.txt`。其余两个脚本没有该副作用。

这些脚本成功时的退出码为 `0`，并打印阶段校验信息。断言失败、TBE 导入失败、
旧版 API 不兼容或 CPU target 构建失败时，异常未被捕获，进程以非零退出码结束。

## 输入输出契约

TBE 字典输入至少需要以下字段：

```python
{
    "shape": (2, 3, 4),
    "format": "ND",
    "ori_shape": (2, 3, 4),
    "ori_format": "ND",
    "dtype": "float32",
}
```

`add.py` 的算子函数读取 `shape` 和 `dtype`，支持的 dtype 声明为
`float16`、`float32`、`int32`。广播后的元素总数不得超过
`2147483648`。`add.py` 只检查第一个输入的 dtype；两个输入的实际 dtype 是否一致
没有在该函数中显式校验。

Ascend C Kernel 的运行时契约与 Python 不同：

- `add_custom` 和 `mat_add_custom` 都把输入解释为扁平 `float32` 数组。
- Kernel 从 `GM_ADDR tiling` 解包 `totalLength` 与 `tileNum`。
- 只有 `TILING_KEY_IS(1)` 时执行 `Process()`。
- `add_custom` 输出 `z = x + y`；`mat_add_custom` 输出扁平数据上的
  `c = a + b`。
- C++ 代码没有对 M、N、shape 或非整除尾部做边界检查。

## 当前状态

- `observed-pass`：本章 4 个 Python 文件通过 AST 语法解析。
- `observed-pass`：本地 fixture/命令名称与源码一致。
- `untested`：三个 `run.py` 的 TBE CPU 调试执行，因为本地 Python 环境没有
  `tbe`。
- `untested`：`add.py` 的 CCE 编译。
- `blocked`：C++ Kernel 的实际编译；当前目录缺少 CANN 头文件环境与完整 Host
  构建工程。
- `untested`：任何 310B NPU 执行、数值对齐、算子性能或 Profiling。

故障分类见[失败模式与证据边界](docs/03-failures-and-evidence.md)。
