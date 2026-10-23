# TBE DSL入口与CPU调试契约

`add_tbe/` 包含两个独立入口：`add.py` 描述并编译广播加法，`run.py` 则使用 TBE
调试环境验证较小的等价表达式。后者不是 `add.py` 的单元测试，也不会导入
`add.py` 中的 `add()` 或 `add_compute()`。

## `add.py`入口

函数层次如下：

| 符号 | 职责 |
| --- | --- |
| `add_compute(input_x, input_y, output_z, kernel_name)` | 将两个输入广播到共同 shape，调用 `dsl.vadd`，注册为 static 计算 |
| `add(input_x, input_y, output_z, kernel_name)` | 读取字典参数、校验部分 dtype、创建 placeholder、自动调度并编译 |
| `if __name__ == "__main__"` | 用 `(5, 6, 7)` 和 `float16` 触发编译 |

运行方式：

```bash
cd samples/chapter7/add_tbe
python add.py
```

需要 CUDA 工具环境的 `tbe` 模块、TVM 接口和 CCE 编译后端：

```python
import tbe
from tbe import tvm
from tbe import dsl
from tbe.common.utils import shape_util
```

成功后由 `dsl.build()` 产生编译产物，预期目录是 `kernel_meta/`。源码没有指定
绝对输出目录，因此实际位置跟随当前工作目录和 CANN 配置。仓库当前没有提交
`kernel_meta/`，所以“会生成 `add.o` 与 `add.json`”属于正文和 API 预期，不是本次
检出中的 observed 结果。

## `add.py`参数与校验

`add()` 接收三个字典和一个 kernel name：

| 参数 | 必读字段 | 是否实际使用 |
| --- | --- | --- |
| `input_x` | `shape`、`dtype` | 是 |
| `input_y` | `shape` | 是；dtype 未显式核对 |
| `output_z` | 无 | 否，仅作为 API 形状保留 |
| `kernel_name` | 字符串 | 传给 compute 和 build 配置 |

支持的 dtype 字符串为 `float16`、`float32`、`int32`，比较前调用 `.lower()`。
实现根据广播后的最大 shape 计算总元素数，大于 `2147483648` 时抛出
`RuntimeError("the shape is too large to calculate")`。

广播使用：

```python
shape_x, shape_y, shape_max = shape_util.broadcast_shapes(
    shape_x,
    shape_y,
    param_name_input1="input_x",
    param_name_input2="input_y",
)
```

若两个输入在最后一维都为 1，代码会移除该尾维，目的是减少无语义变化的调度维度。
两个 placeholder 随后以广播后的 shape 和 `input_x.dtype` 创建。因此即使
`input_y.dtype` 不同，本函数也不会先给出清晰错误，而可能在更后层失败。

## `run.py`入口

```bash
cd samples/chapter7/add_tbe
python run.py
```

关键行为：

1. 用默认参数 `(2, 3, 4)`、`float32` 调用 `addtest()`。
2. 在 `debug()` 和 CPU context 中生成均匀分布随机输入。
3. 构造 `data_c = data_a + data_b`。
4. 将 `data_c` 的中间 tensor 信息写到 `samplefile.txt`。
5. 用 `assert_allclose` 比较 `data_c` 与 NumPy 的 `a + b`，容差为
   `[1e-7, 1e-7]`。
6. 构造 `data_d = data_c + data_b`，即 `a + 2b`。
7. 通过自定义 `build()` 使用 `tvm.target.Target("c", host="llvm")` 构建 CPU
   target，运行后再与 `a + 2b` 比较。

`run.py` 覆盖同名模块 `build()`，把旧式 `target`/`target_host` 调用改成新式
`Target("c", host="llvm")`。这是 CANN API 版本漂移的适配，不代表 CCE 后端已经
验证。

## I/O契约

输入字典的稳定字段是：

| 字段 | 示例 | 用途 |
| --- | --- | --- |
| `shape` | `(2, 3, 4)` | 创建随机数据与 placeholder |
| `format` | `"ND"` | 字典约定，脚本计算逻辑不读取 |
| `ori_shape` | `(2, 3, 4)` | 字典约定，脚本计算逻辑不读取 |
| `ori_format` | `"ND"` | 字典约定，脚本计算逻辑不读取 |
| `dtype` | `"float32"` | NumPy 数组与 TVM placeholder dtype |

输出边界：

- `add.py`：编译产物位于工作目录相关位置，没有数据推理输出文件。
- `add_tbe/run.py`：标准输出打印验证信息，并写 `samplefile.txt`。
- 随机输入没有固定 seed，因此每次数值和张量文件内容可以不同。
- 成功退出码为 `0`；未捕获异常导致非零退出码。

`samplefile.txt` 是调试副产物，不是稳定规范，也没有清理或存在性检查。脚本使用
普通 `open(..., "w")` 且不通过 context manager 关闭文件，运行目录必须可写。

## CPU与NPU边界

三个 `run.py` 的 `build()` 明确指定 `Target("c", host="llvm")`，而 `debug()`
提供 CPU 调试 context。因此它们回答的是“TBE DSL 表达式和 CPU 编译路径是否能在
当前 Python 环境运行”，不回答以下问题：

- `add.py` 的 CCE 编译是否成功。
- 生成的 AI Core 指令是否正确。
- Ascend C Kernel 是否可编译或执行。
- 310B 上的数值是否与 NumPy 一致。
- 算子延迟、吞吐或硬件利用率。

`add_tbe/run.py` 最终表达式为 `a + 2b`，而 `add.py` 和两个 C++ Kernel 的目标
都是 `x + y`。这也是 CPU 调试脚本不能当作算子端到端验收的原因之一。
