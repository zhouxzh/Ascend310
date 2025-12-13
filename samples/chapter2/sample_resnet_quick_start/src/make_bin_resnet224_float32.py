from PIL import Image
import numpy as np

# 加载图像并转换为 RGB
img = Image.open('data/dog1_1024_683.jpg').convert('RGB')
# 使用双线性插值缩放到 256x256
img = img.resize((256, 256), Image.BILINEAR)
# 计算 224x224 中心裁剪的坐标
left = (256 - 224) // 2
top = (256 - 224) // 2
# 执行中心裁剪
img = img.crop((left, top, left + 224, top + 224))

# 转换为 [0, 1] 范围的 float32 数组
arr = np.array(img).astype(np.float32) / 255.0
# 定义归一化常量
mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
# 按通道进行归一化
arr = (arr - mean) / std

# 调整为 NCHW 格式并添加批次维度
arr = arr.transpose(2, 0, 1)[None, :, :, :]
# 保存为二进制文件
arr.tofile('data/dog1_224_float32.bin')
# 打印缓冲区大小（字节数）
print('bytes:', arr.nbytes)