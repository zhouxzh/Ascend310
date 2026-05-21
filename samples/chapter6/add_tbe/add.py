import tbe
from tbe import tvm
from tbe import dsl
from tbe.common.utils import para_check
from tbe.common.utils import shape_util

from functools import reduce

SHAPE_SIZE_LIMIT = 2147483648

# 实现Add算子的计算逻辑

@tbe.common.register.register_op_compute("add",op_mode="static")
def add_compute(input_x, input_y, output_z, kernel_name="add"):
    shape_x = shape_util.shape_to_list(input_x.shape) # 将shape转换为list
    shape_y = shape_util.shape_to_list(input_y.shape) # 将shape转换为list
    shape_x, shape_y, shape_max = shape_util.broadcast_shapes(shape_x, shape_y,param_name_input1="input_x",param_name_input2="input_y")   # shape_max取shape_x与shape_y的每个维度的大值
    shape_size = reduce(lambda x, y: x * y, shape_max[:])      
    if shape_size > SHAPE_SIZE_LIMIT:
        raise RuntimeError("the shape is too large to calculate")

    input_x = dsl.broadcast(input_x, shape_max)       # 将input_x的shape广播为shape_max
    input_y = dsl.broadcast(input_y, shape_max)       # 将input_y的shape广播为shape_max
    res = dsl.vadd(input_x, input_y)        # 执行input_x + input_y

    return res          # 返回计算结果的tensor

# 算子定义函数
def add(input_x, input_y, output_z, kernel_name="add"):
    # 获取算子输入tensor的shape与dtype
    shape_x = input_x.get("shape")      
    shape_y = input_y.get("shape")
    check_tuple = ("float16", "float32", "int32")
    input_data_type = input_x.get("dtype").lower()
    if input_data_type not in check_tuple:
        raise RuntimeError("only support %s while dtype is %s" %
                           (",".join(check_tuple), input_data_type))
    # shape_max取shape_x与shape_y的每个维度的最大值
    shape_x, shape_y, shape_max = shape_util.broadcast_shapes(shape_x, shape_y,param_name_input1="input_x",param_name_input2="input_y")  
    if shape_x[-1] == 1 and shape_y[-1] == 1 and shape_max[-1] == 1: 
        # 如果shape的长度等于1，就直接赋值，如果shape的长度不等于1，做切片，将最后一个维度舍弃（按照内存排布，最后一个维度为1与没有最后一个维度的数据排布相同，例如2*3=2*3*1，将最后一个为1的维度舍弃，可提升后续的调度效率）。
        shape_x = shape_x if len(shape_x) == 1 else shape_x[:-1]   
        shape_y = shape_y if len(shape_y) == 1 else shape_y[:-1]
        shape_max = shape_max if len(shape_max) == 1 else shape_max[:-1]
  
    # 使用TVM的placeholder接口对第一个输入tensor进行占位，返回一个tensor对象
    data_x = tvm.placeholder(shape_x, name="data_1", dtype=input_data_type)
    # 使用TVM的placeholder接口对第二个输入tensor进行占位，返回一个tensor对象
    data_y = tvm.placeholder(shape_y, name="data_2", dtype=input_data_type)

    # 调用compute实现函数
    res = add_compute(data_x, data_y, output_z, kernel_name)  
    # 自动调度
    with tvm.target.Target("cce", host="cce"):
        schedule = dsl.auto_schedule(res)
    # 编译配置
    config = {"name": kernel_name,
              "tensor_list": (data_x, data_y, res)}
    dsl.build(schedule, config)
    
# 算子调用
if __name__ == '__main__':
    input_output_dict = {"shape": (5, 6, 7),"format": "ND","ori_shape": (5, 6, 7),"ori_format": "ND", "dtype": "float16"}
    add(input_output_dict, input_output_dict, input_output_dict, kernel_name="add")