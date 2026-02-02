import time
import numpy as np
import onnxruntime

from datasets import load_dataset
import tqdm

data_dir = './data' # 假设 data_dir 已定义，或者根据实际情况保留原变量

# 加载数据集 (指定 cache_dir)
dataset = load_dataset('zh-plus/tiny-imagenet', split='valid', cache_dir=data_dir) # 只加载 valid 集

# ONNX Runtime 初始化
onnx_model_path = "model/resnet18_tiny_imagenet.onnx" # 请确保当前路径下有该模型文件
session = onnxruntime.InferenceSession(onnx_model_path, providers=['CPUExecutionProvider'])
print(f"当前运行设备 (Providers): {session.get_providers()}") # 打印以确认 CPU 是否生效

input_name = session.get_inputs()[0].name

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
    
    # 推理
    outputs = session.run(None, {input_name: input_tensor})
    
    # 获取预测结果: argmax 获取概率最大的类别索引
    predicted_label = np.argmax(outputs[0])
    
    if predicted_label == label:
        correct_count += 1
    
    total_images += 1
    # if total_images >= 100: break # 可选：用于快速测试

end_time = time.time()
duration = end_time - start_time
fps = total_images / duration
accuracy = correct_count / total_images * 100

print(f"推理完成。")
print(f"总图片数: {total_images}")
print(f"总耗时: {duration:.4f} 秒")
print(f"推理帧率: {fps:.2f} FPS")
print(f"正确率: {accuracy:.2f}%")