# run.py - Ascend C 向量加法示例验证
# 对应章节: chapter6 第4节
# add_custom.cpp 中的 Ascend C 内核通过 TBE 算子框架编译；
# 本脚本使用 TBE DSL 进行等价的功能验证。
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


shape = (2, 3, 4)
data_type = "float32"

with debug():
    ctx = get_ctx()
    shape_a = shape_util.scalar2tensor_one(shape)

    a = tvm.nd.array(np.random.uniform(size=shape_a).astype(data_type), ctx)
    b = tvm.nd.array(np.random.uniform(size=shape_a).astype(data_type), ctx)
    d = tvm.nd.array(np.zeros(shape_a, dtype=data_type), ctx)

    data_a = tvm.placeholder(shape_a, name="data_1", dtype=data_type)
    data_b = tvm.placeholder(shape_a, name="data_2", dtype=data_type)
    data_c = dsl.vadd(data_a, data_b)

    assert_allclose(data_c, desired=a.asnumpy() + b.asnumpy(),
                    tol=[1e-7, 1e-7])
    print("The value of data_c is the same as the expected value.")

    data_d = dsl.vadd(data_c, data_b)
    s = tvm.create_schedule(data_d.op)
    build(s, [data_a, data_b, data_d], name="AddTest")
    run(a, b, d)

    print("d:", d)
    tvm.testing.assert_allclose(
        d.asnumpy(), a.asnumpy() + b.asnumpy() + b.asnumpy())
    print("The actual output is the same as the expected output.")
    print("\n---")
    print("Ascend C kernel source: see add_custom.cpp")
    print("Kernel compilation requires the TBE operator registration framework.")
