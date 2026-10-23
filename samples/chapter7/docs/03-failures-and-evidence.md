# 失败模式与证据边界

本章样例的失败可能来自 Python 解释器、TBE/CANN API 版本、CCE 编译、Ascend C
工程缺失或 device runtime。文档应报告失败发生在哪一层，并保留对应输入和日志。

## 当前可复现检查

本地 scoped Python 文件已完成 AST 语法解析：

```bash
python -c "import ast, pathlib; \
files=list(pathlib.Path('samples/chapter7').rglob('*.py')); \
[ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; \
print('syntax-ok', len(files))"
```

该命令不导入 TBE，也不证明样例可运行。对本章 4 个 Python 文件，解析结果为
`observed-pass`。

检查 TBE 是否位于当前解释器：

```bash
python -c "import tbe; print(tbe.__file__)"
```

本地开发解释器返回：

```text
ModuleNotFoundError: No module named 'tbe'
```

本地缺少 TBE 不构成样例失败，也不证明 310B 不支持样例。它只说明当前工作站不能
执行 TBE CPU 调试或 CCE 编译。板端必须在 `ascend` 环境、匹配 CANN 环境和正确
解释器中重跑。

## TBE故障

### 导入失败

`ModuleNotFoundError: No module named 'tbe'` 首先检查：

- 是否激活已记录的板端 Python 环境。
- 是否 source 匹配的 CANN `set_env.sh`。
- 当前解释器的 `sys.path` 是否包含该 CANN 附带的 TBE。
- 是否存在多个 CANN/Python 安装互相遮蔽。

不要用 `pip install --user` 临时补包，也不要把另一个 CANN 版本的 `tbe` 目录硬
拼到 `PYTHONPATH`。先记录解释器、导入路径和 CANN 版本。

### API版本漂移

样例显式处理了两类新旧 API 差异：

- `tvm.target.Target("cce", host="cce")` 替代旧式 CCE target 调用。
- 自定义 `build()` 使用 `Target("c", host="llvm")` 替代分离的 target 参数。

若不同 CANN 版本仍出现参数签名、`_Testing` 私有符号或 deprecated warning，需在
新的版本适配分支中处理。不能删除 `_Testing` 调用后假设验证仍然发生。

### CPU调试通过但CCE失败

`run.py` 只构建 `c`/`llvm` target。可能出现 Python CPU 调试成功，但
`python add.py` 在 CCE 自动调度或代码生成阶段失败。这是两个独立门禁：

- TBE DSL 表达式调试：CPU target。
- 算子编译：CCE target 与目标 SoC。

后者还需要检查 CANN 算子包、SoC 版本和编译器日志。

### 随机输入与副产物

`run.py` 使用未固定 seed 的随机数组。小 shape 下通常会快速通过，但不能用于
可复现性能或边界覆盖。`add_tbe/run.py` 还会覆盖当前目录的 `samplefile.txt`；
只读目录会在此步失败。

## Kernel与Tiling故障

### 缺少头文件

C++ 文件只包含：

```cpp
#include "kernel_operator.h"
```

本地或普通 C++ 编译器找不到该头文件时，只能说明没有加载 CANN Ascend C 开发环境。
不能把普通 `g++` 编译错误当成 Kernel 逻辑错误。

### 缺少Host和Tiling结构

Kernel 使用 `GET_TILING_DATA` 并访问 `tilingData.totalLength` 与
`tilingData.tileNum`。当前目录没有对应的 tiling 结构定义、Host 计算与序列化、
算子注册或构建目标。单独编译 `.cpp` 不能生成可加载算子。

### 整数除法丢失尾部

两个 Kernel 都用：

```cpp
blockLength = totalLength / GetBlockNum();
tileLength = blockLength / tileNum / BUFFER_NUM;
```

整数除法不保留余数，循环也固定为 `tileNum * BUFFER_NUM`。当数据长度不能整除时，
尾部不会被计算；当 `tileLength == 0` 时，Kernel 没有显式拒绝。失败可能表现为
输出尾部未写、断言失败、非法搬运或设备异常，而不是清晰的参数错误。

### Tiling key不匹配

入口只有 `TILING_KEY_IS(1)` 分支。Host 生成其他 key 时 Kernel 正常返回但不执行
计算。验证程序若只检查进程退出码，会得到假阳性；必须同时检查输出内容。

### Python脚本不是Kernel测试

`add_ascendc/run.py` 和 `mat_add_tiling/run.py` 都通过 TBE DSL 构建 CPU target，
最终比较 `a + 2b`。C++ Kernel 的语义是 `x + y`，且没有被加载。所谓“等价功能
验证”与实际验证表达式不一致，因此文档不能把这两个脚本列为 C++ Kernel 的
`observed-pass`。

## 证据等级

| 项目 | 等级 | 依据 |
| --- | --- | --- |
| 6 个 Python 文件语法有效 | `observed-pass` | AST 解析完成 |
| Python 入口、shape 和最终表达式 | `observed` | 来自实际源码 |
| TBE CPU debug 执行 | `blocked` | 本地缺少 `tbe`，未上板 |
| `python add.py` CCE 编译 | `untested` | 未执行 CANN/CCE |
| Ascend C Kernel 编译 | `blocked` | 缺少完整 Host/tiling/构建资产 |
| Kernel 板端数值正确 | `untested` | 未加载或执行 Kernel |
| 310B 性能与 Profiling | `untested` | 未采集任何硬件数据 |

完成板端验证时，应分别记录：

- 板卡型号、计算档位、健康状态和 `npu-smi` 快照。
- CANN、driver、firmware、Python、TBE 与 NumPy 版本。
- TBE/CANN 导入路径。
- 完整运行命令、退出码、标准输出和错误日志。
- 编译器生成物的字节数与 SHA-256。
- 确定性输入、期望输出、数值误差和边界 shape。
- 若声明性能，还要给出 warmup、迭代次数、重复次数、计时方法和原始报告路径。

只有这些门禁分别通过后，才能把“语法有效”“CPU DSL 验证通过”“Kernel 编译
成功”“NPU 数值通过”和“性能达标”写成不同等级的 observed 结果。
