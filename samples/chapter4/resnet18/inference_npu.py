import time
import numpy as np
import acl
from datasets import load_dataset
import tqdm

import ctypes

data_dir = './data' # 假设 data_dir 已定义，或者根据实际情况保留原变量

# 加载数据集 (指定 cache_dir)
dataset = load_dataset('zh-plus/tiny-imagenet', split='valid', cache_dir=data_dir) # 只加载 valid 集

# ACL 初始化与资源管理类
class AclResource:
    def __init__(self, device_id=0, model_path="model/resnet18_tiny_imagenet.om"):
        self.device_id = device_id
        self.model_path = model_path
        self.model_id = None
        self.context = None
        self.stream = None
        self.input_dataset = None
        self.output_dataset = None
        self.model_desc = None
        
    def init(self):
        # ACL 初始化
        ret = acl.init()
        if ret != 0: raise RuntimeError("acl init failed")
        ret = acl.rt.set_device(self.device_id)
        if ret != 0: raise RuntimeError("set device failed")
        self.context, ret = acl.rt.create_context(self.device_id)
        if ret != 0: raise RuntimeError("create context failed")
        
        # 加载模型
        self.model_id, ret = acl.mdl.load_from_file(self.model_path)
        if ret != 0: raise RuntimeError(f"load model failed, path: {self.model_path}")
        
        # 获取模型描述
        self.model_desc = acl.mdl.create_desc()
        ret = acl.mdl.get_desc(self.model_desc, self.model_id)
        
        print(f"ACL Resource Init Success. Device: {self.device_id}")

    def execute(self, input_numpy):
        # 准备输入数据 (Host -> Device)
        # 获取模型输入大小
        input_size = acl.mdl.get_input_size_by_index(self.model_desc, 0)
        
        # 申请 Device 输入内存
        dev_in_ptr, ret = acl.rt.malloc(input_size, 2) # 2: ACL_MEM_MALLOC_HUGE_FIRST
        
        # 拷贝数据 (Host -> Device)
        # 确保输入是 contiguous 且 float32 (C Type bytes)
        # 使用 tobytes + bytes_to_ptr 避免 numpy_to_ptr 可能引发的 ImportError 兼容性问题
        if input_numpy.nbytes != input_size:
            print(f"Warning: Input size mismatch. Model expects {input_size}, got {input_numpy.nbytes}")
            
        input_bytes = input_numpy.tobytes()
        input_ptr = acl.util.bytes_to_ptr(input_bytes)
        # acl.rt.memcpy (dst, dest_max, src, count, kind)
        acl.rt.memcpy(dev_in_ptr, input_size, input_ptr, input_size, 1) # 1: ACL_MEMCPY_HOST_TO_DEVICE
        
        # 组装 Input Dataset
        self.input_dataset = acl.mdl.create_dataset()
        input_data_buffer = acl.create_data_buffer(dev_in_ptr, input_size)
        acl.mdl.add_dataset_buffer(self.input_dataset, input_data_buffer)

        # 准备输出数据
        self.output_dataset = acl.mdl.create_dataset()
        output_size = acl.mdl.get_output_size_by_index(self.model_desc, 0)
        dev_out_ptr, ret = acl.rt.malloc(output_size, 2)
        output_data_buffer = acl.create_data_buffer(dev_out_ptr, output_size)
        acl.mdl.add_dataset_buffer(self.output_dataset, output_data_buffer)

        # 执行推理
        ret = acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        if ret != 0: print("Model execute failed")

        # 取回结果 (Device -> Host)
        host_out_buffer, ret = acl.rt.malloc_host(output_size)
        acl.rt.memcpy(host_out_buffer, output_size, dev_out_ptr, output_size, 2) # 2: ACL_MEMCPY_DEVICE_TO_HOST
        
        # 转换为 numpy (假设输出是 float32, batch=1, class=200)
        out_array = np.frombuffer(ctypes.string_at(host_out_buffer, output_size), dtype=np.float32)
        
        # 清理单次推理资源
        acl.rt.free(dev_in_ptr)
        acl.rt.free(dev_out_ptr)
        acl.rt.free_host(host_out_buffer)
        acl.destroy_data_buffer(input_data_buffer)
        acl.destroy_data_buffer(output_data_buffer)
        acl.mdl.destroy_dataset(self.input_dataset)
        acl.mdl.destroy_dataset(self.output_dataset)
        
        return out_array

    def release(self):
        acl.mdl.destroy_desc(self.model_desc)
        acl.mdl.unload(self.model_id)
        acl.rt.destroy_context(self.context)
        acl.rt.reset_device(self.device_id)
        acl.finalize()

# 实例化并初始化 ACL 资源
# 请确保当前路径下有对应的 .om 模型文件
om_model_path = "model/resnet18_tiny_imagenet.om" 
acl_resource = AclResource(model_path=om_model_path)
acl_resource.init()

# 自定义数据预处理函数 (替代 torchvision)
def preprocess(image):
    # 将 PIL Image 转换为 numpy 数组
    img_data = np.array(image).astype('float32') / 255.0
    
    # 获取图像尺寸，如果不是 RGB 三通道 (例如灰度图)，需要转换
    if len(img_data.shape) == 2:
        img_data = np.stack([img_data]*3, axis=-1)
    
    # 归一化参数
    mean = np.array([0.485, 0.456, 0.406], dtype='float32')
    std = np.array([0.229, 0.224, 0.225], dtype='float32')
    
    # 归一化: (image - mean) / std
    img_data = (img_data - mean) / std
    
    # 调整维度: HWC -> CHW (3, 64, 64)
    img_data = img_data.transpose(2, 0, 1)
    
    # 增加 Batch 维度: (1, 3, 64, 64)
    img_data = np.expand_dims(img_data, axis=0)
    
    # 确保内存连续，这对 C 侧指针拷贝很重要
    if not img_data.flags['C_CONTIGUOUS']:
        img_data = np.ascontiguousarray(img_data)
        
    return img_data

# 推理计数和计时
total_images = 0
correct_count = 0
start_time = time.time()

print("开始推理...")

# 逐张图片推理
for item in tqdm.tqdm(dataset):
    image = item['image'] # 获取 PIL 图像
    label = item['label'] # 获取真实标签
    
    # 预处理
    input_tensor = preprocess(image)
    
    # 推理 (使用 pyACL)
    # output 是扁平的一维数组，直接使用
    outputs = acl_resource.execute(input_tensor)
    
    # 获取预测结果: argmax 获取概率最大的类别索引
    predicted_label = np.argmax(outputs)
    
    if predicted_label == label:
        correct_count += 1
    
    total_images += 1
    # if total_images >= 100: break # 可选：用于快速测试

end_time = time.time()
duration = end_time - start_time
fps = total_images / duration
accuracy = correct_count / total_images * 100

# 释放 ACL 资源
acl_resource.release()

print(f"推理完成。")
print(f"总图片数: {total_images}")
print(f"总耗时: {duration:.4f} 秒")
print(f"推理帧率: {fps:.2f} FPS")
print(f"正确率: {accuracy:.2f}%")