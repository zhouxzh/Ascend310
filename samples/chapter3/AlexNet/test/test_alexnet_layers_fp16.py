import torch
import torch.nn as nn
import pytest
import sys
import os
import copy

# 尝试检测设备
try:
    import torch_npu
    device = torch.device("npu")
except ImportError:
    raise RuntimeError("未找到 torch_npu，请切换到安装了 CANN 的环境运行此代码。")
except Exception as e:
    raise RuntimeError(f"初始化 NPU 设备失败: {e}，请检查 CANN 环境配置。")

print(f"Running tests on device: {device}")

def init_weights(m):
    """显式初始化权重函数"""
    if isinstance(m, (torch.nn.Conv2d, torch.nn.Linear)):
        torch.nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
        if m.bias is not None:
            torch.nn.init.constant_(m.bias, 0)

def create_layer_pair(module):
    """辅助函数：创建 CPU (FP32) 和 NPU (FP16) 版本的层，并同步权重"""
    # 初始化权重
    module.apply(init_weights)
    module.eval()
    
    # 创建 NPU 版本
    npu_module = copy.deepcopy(module)
    npu_module = npu_module.half().to(device)
    npu_module.eval()
    
    return module, npu_module

def compare_outputs(cpu_out, npu_out, rtol=1e-2, atol=1e-2):
    """辅助函数：对比 CPU (FP32) 和 NPU (FP16) 的输出"""
    npu_out = npu_out.cpu().float() # 转回 float32 进行对比
    assert cpu_out.shape == npu_out.shape, f"Shape mismatch: CPU {cpu_out.shape} vs NPU {npu_out.shape}"
    assert torch.allclose(cpu_out, npu_out, rtol=rtol, atol=atol), \
        f"Content mismatch! Max diff: {(cpu_out - npu_out).abs().max()}"

def test_layer1():
    """测试第一层: Conv -> ReLU -> Pool"""
    # 单独定义网络层
    conv1, conv1_npu = create_layer_pair(nn.Conv2d(3, 96, kernel_size=11, stride=4, padding=1))
    relu1, relu1_npu = create_layer_pair(nn.ReLU())
    pool1, pool1_npu = create_layer_pair(nn.AvgPool2d(kernel_size=3, stride=2))

    input_cpu = torch.randn(1, 3, 224, 224)
    input_npu = input_cpu.half().to(device)
    
    # Conv1
    out_cpu = conv1(input_cpu)
    out_npu = conv1_npu(input_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu1(out_cpu)
    out_npu = relu1_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    # Pool1
    out_cpu = pool1(out_cpu)
    out_npu = pool1_npu(out_npu)
    compare_outputs(out_cpu, out_npu)

def test_layer2():
    """测试第二层: Conv -> ReLU -> Pool"""
    # 单独定义网络层
    conv2, conv2_npu = create_layer_pair(nn.Conv2d(96, 256, kernel_size=5, padding=2))
    relu2, relu2_npu = create_layer_pair(nn.ReLU())
    pool2, pool2_npu = create_layer_pair(nn.AvgPool2d(kernel_size=3, stride=2))

    # 模拟上一层的输出
    input_cpu = torch.randn(1, 96, 26, 26)
    input_npu = input_cpu.half().to(device)
    
    # Conv2
    out_cpu = conv2(input_cpu)
    out_npu = conv2_npu(input_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu2(out_cpu)
    out_npu = relu2_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    # Pool2
    out_cpu = pool2(out_cpu)
    out_npu = pool2_npu(out_npu)
    compare_outputs(out_cpu, out_npu)

def test_layer3():
    """测试第三层: Conv -> ReLU"""
    conv3, conv3_npu = create_layer_pair(nn.Conv2d(256, 384, kernel_size=3, padding=1))
    relu3, relu3_npu = create_layer_pair(nn.ReLU())

    input_cpu = torch.randn(1, 256, 12, 12)
    input_npu = input_cpu.half().to(device)
    
    # Conv3
    out_cpu = conv3(input_cpu)
    out_npu = conv3_npu(input_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu3(out_cpu)
    out_npu = relu3_npu(out_npu)
    compare_outputs(out_cpu, out_npu)

def test_layer4():
    """测试第四层: Conv -> ReLU"""
    conv4, conv4_npu = create_layer_pair(nn.Conv2d(384, 384, kernel_size=3, padding=1))
    relu4, relu4_npu = create_layer_pair(nn.ReLU())

    input_cpu = torch.randn(1, 384, 12, 12)
    input_npu = input_cpu.half().to(device)
    
    # Conv4
    out_cpu = conv4(input_cpu)
    out_npu = conv4_npu(input_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu4(out_cpu)
    out_npu = relu4_npu(out_npu)
    compare_outputs(out_cpu, out_npu)

def test_layer5():
    """测试第五层: Conv -> ReLU -> Pool"""
    conv5, conv5_npu = create_layer_pair(nn.Conv2d(384, 256, kernel_size=3, padding=1))
    relu5, relu5_npu = create_layer_pair(nn.ReLU())
    pool3, pool3_npu = create_layer_pair(nn.AvgPool2d(kernel_size=3, stride=2))

    input_cpu = torch.randn(1, 384, 12, 12)
    input_npu = input_cpu.half().to(device)
    
    # Conv5
    out_cpu = conv5(input_cpu)
    out_npu = conv5_npu(input_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu5(out_cpu)
    out_npu = relu5_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    # Pool3
    out_cpu = pool3(out_cpu)
    out_npu = pool3_npu(out_npu)
    compare_outputs(out_cpu, out_npu)

def test_classifier():
    """测试全连接层"""
    flatten, flatten_npu = create_layer_pair(nn.Flatten())
    fc1, fc1_npu = create_layer_pair(nn.Linear(6400, 4096))
    relu6, relu6_npu = create_layer_pair(nn.ReLU())
    dropout1, dropout1_npu = create_layer_pair(nn.Dropout(0.5))
    fc2, fc2_npu = create_layer_pair(nn.Linear(4096, 4096))
    relu7, relu7_npu = create_layer_pair(nn.ReLU())
    dropout2, dropout2_npu = create_layer_pair(nn.Dropout(0.5))
    fc3, fc3_npu = create_layer_pair(nn.Linear(4096, 10))

    input_cpu = torch.randn(1, 256, 5, 5)
    input_npu = input_cpu.half().to(device)
    
    # Flatten
    out_cpu = flatten(input_cpu)
    out_npu = flatten_npu(input_npu)
    compare_outputs(out_cpu, out_npu)
    
    # FC1
    out_cpu = fc1(out_cpu)
    out_npu = fc1_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu6(out_cpu)
    out_npu = relu6_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = dropout1(out_cpu)
    out_npu = dropout1_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    # FC2
    out_cpu = fc2(out_cpu)
    out_npu = fc2_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = relu7(out_cpu)
    out_npu = relu7_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    out_cpu = dropout2(out_cpu)
    out_npu = dropout2_npu(out_npu)
    compare_outputs(out_cpu, out_npu)
    
    # FC3
    out_cpu = fc3(out_cpu)
    out_npu = fc3_npu(out_npu)
    compare_outputs(out_cpu, out_npu)

def test_full_model():
    """测试整个模型的完整前向传播"""
    # 在函数内部定义 AlexNet，确保独立性
    class LocalAlexNet(nn.Module):
        def __init__(self):
            super(LocalAlexNet, self).__init__()
            self.features = nn.Sequential(
                nn.Conv2d(3, 96, kernel_size=11, stride=4, padding=1),
                nn.ReLU(),
                nn.AvgPool2d(kernel_size=3, stride=2),
                nn.Conv2d(96, 256, kernel_size=5, padding=2),
                nn.ReLU(),
                nn.AvgPool2d(kernel_size=3, stride=2),
                nn.Conv2d(256, 384, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.Conv2d(384, 384, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.Conv2d(384, 256, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.AvgPool2d(kernel_size=3, stride=2),
            )
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(6400, 4096),
                nn.ReLU(),
                nn.Dropout(0.5),
                nn.Linear(4096, 4096),
                nn.ReLU(),
                nn.Dropout(0.5),
                nn.Linear(4096, 10),
            )

        def forward(self, x):
            x = self.features(x)
            x = self.classifier(x)
            return x

    cpu_model, npu_model = create_layer_pair(LocalAlexNet())

    input_cpu = torch.randn(1, 3, 224, 224)
    input_npu = input_cpu.half().to(device)
    
    out_cpu = cpu_model(input_cpu)
    out_npu = npu_model(input_npu)
    compare_outputs(out_cpu, out_npu)
