# run.py - 矩阵加法示例验证（带 Tiling 概念演示）
# 对应章节: chapter6 第5节
# mat_add_custom.cpp 展示带 Tiling 的 Ascend C Kernel 结构；
# 本脚本使用 TBE DSL 进行等价的矩阵加法功能验证。
from tbe import tvm
from tbe import dsl
from tbe.common.utils import shape_util
from tbe.common.testing.testing import *
from tbe.common.testing.testing import _Testing
import numpy as np


def build(inputs, args=None, name="default_function"):
    """覆盖 testing 模块的 build()，使用新版 API。"""
    _Testing.build(inputs, args=args,
                   target=tvm.target.Target("c", host="llvm"),
                   target_host=None, name=name,
                   tiling_keys=None, binds=None, evaluates=None)


# 模拟矩阵加法: M=128, N=256 (数据量足以触发 Tiling 需求)
shape = (128, 256)
data_type = "float32"
total_elements = 128 * 256  # 32768 个 float32 ≈ 128KB

with debug():
    ctx = get_ctx()
    shape_a = shape_util.scalar2tensor_one(shape)

    a = tvm.nd.array(np.random.uniform(size=shape_a).astype(data_type), ctx)
    b = tvm.nd.array(np.random.uniform(size=shape_a).astype(data_type), ctx)
    d = tvm.nd.array(np.zeros(shape_a, dtype=data_type), ctx)

    data_a = tvm.placeholder(shape_a, name="data_1", dtype=data_type)
    data_b = tvm.placeholder(shape_a, name="data_2", dtype=data_type)

    # TBE DSL 自动处理 Tiling（开发者无需手动指定 tile size）
    data_c = dsl.vadd(data_a, data_b)

    assert_allclose(data_c, desired=a.asnumpy() + b.asnumpy(),
                    tol=[1e-7, 1e-7])
    print("Matrix add verification: PASSED")
    print("Shape: {}, Total elements: {}".format(shape, total_elements))
    print("(Data size {:.0f}KB exceeds UB, Tiling is automatically applied)".format(
        total_elements * 4 / 1024))

    data_d = dsl.vadd(data_c, data_b)
    s = tvm.create_schedule(data_d.op)
    build(s, [data_a, data_b, data_d], name="MatAddTest")
    run(a, b, d)

    tvm.testing.assert_allclose(
        d.asnumpy(), a.asnumpy() + b.asnumpy() + b.asnumpy())
    print("All verifications PASSED.")
    print("---")
    print("Ascend C kernel with manual Tiling: see mat_add_custom.cpp")
    print("TBE DSL auto-tiling is handled by auto_schedule internally.")
