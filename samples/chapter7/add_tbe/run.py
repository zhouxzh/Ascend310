# run.py
from tbe import tvm
from tbe import dsl
from tbe.common.utils import para_check
from tbe.common.utils import shape_util
# 引入testing模块相关接口
from tbe.common.testing.testing import *
from tbe.common.testing.testing import _Testing
import numpy as np


def build(inputs, args=None, name="default_function"):
    """覆盖 testing 模块的 build()，将 target 和 target_host 合并为新版 API。"""
    _Testing.build(inputs, args=args,
                   target=tvm.target.Target("c", host="llvm"),
                   target_host=None, name=name,
                   tiling_keys=None, binds=None, evaluates=None)


@para_check.check_input_type(dict, dict, dict, str)
def addtest(input_a, input_b, output_d, kernel_name="addtest"):
    # 进入DSL调试模式，并选择CPU作为运行平台
    with debug():
        # 获取算子运行的上下文
        ctx = get_ctx()

        # 获取输入数据的shape与dtype
        shape_a = shape_util.scalar2tensor_one(input_a.get("shape"))
        shape_b = shape_util.scalar2tensor_one(input_b.get("shape"))
        data_type = input_a.get("dtype").lower()

        # 使用numpy定义输入golden数据大小
        a = tvm.nd.array(np.random.uniform(size=shape_a).astype(data_type), ctx)
        b = tvm.nd.array(np.random.uniform(size=shape_b).astype(data_type), ctx)
        # 使用numpy将输出d初始化为全0
        d = tvm.nd.array(np.zeros(shape_a, dtype=data_type), ctx)

        # 调用TVM的placeholder接口对输入tensor进行占位，并返回一个tensor对象
        data_a = tvm.placeholder(shape_a, name="data_1", dtype=data_type)
        data_b = tvm.placeholder(shape_b, name="data_2", dtype=data_type)
        # 调用DSL计算接口实现data_a + data_b
        data_c = dsl.vadd(data_a, data_b)

        # 中间Tensor数据验证
        sample = open('samplefile.txt', 'w')
        # 将中间tensor data_c存入文件samplefile.txt
        print_tensor(data_c, ofile=sample)
        # 检查中间tensor data_c的值是否正确
        assert_allclose(data_c, desired=a.asnumpy() + b.asnumpy(), tol=[1e-7, 1e-7])
        print("The value of data_c is the same as the expected value.")

        # 继续自定义DSL的逻辑撰写,调用DSL接口实现：data_d = data_c + data_b
        data_d = dsl.vadd(data_c, data_b)
        # 调用TVM的create_schedule接口，为算子创建调度实例对象，入参为输出tensor的OP列表。
        s = tvm.create_schedule(data_d.op)

        # 编译生成算子,data_a,data_b,data_d是占位的输入输出列表，AddTest是我们自定义算子的名称
        build(s, [data_a, data_b, data_d], name="AddTest")

        # 执行算子,将a,b,d按顺序代入编译出来的DSL算子AddTest
        run(a, b, d)  # AddTest(a, b, d)

        # 将输出数据d的值打印出来,并预期结果进行比较，看是否相符
        print("d:", d)
        tvm.testing.assert_allclose(d.asnumpy(), a.asnumpy() + b.asnumpy() + b.asnumpy())
        print("The actual output is the same as the expected output.")

# 编写入口函数，调用addtest函数
if __name__ == "__main__":
    input_output_dict = {"shape": (2, 3, 4),"format": "ND","ori_shape": (2, 3, 4),"ori_format": "ND", "dtype":"float32"}
    addtest(input_output_dict, input_output_dict, input_output_dict, kernel_name="addtest")