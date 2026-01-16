#!/usr/bin/env python3
"""
下载或创建MobileFaceNet ONNX模型
"""

import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

def download_file(url: str, filepath: str) -> bool:
    """
    下载文件
    
    Args:
        url: 下载链接
        filepath: 保存路径
        
    Returns:
        是否下载成功
    """
    try:
        print(f"正在下载: {url}")
        print(f"保存到: {filepath}")
        
        # 创建目录
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        # 下载文件
        urllib.request.urlretrieve(url, filepath)
        
        # 检查文件是否存在且大小大于0
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            print(f"下载成功: {filepath}")
            return True
        else:
            print(f"下载失败: 文件为空或不存在")
            return False
            
    except urllib.error.URLError as e:
        print(f"网络错误: {e}")
        return False
    except Exception as e:
        print(f"下载失败: {e}")
        return False

def create_simple_mobilefacenet_onnx(filepath: str) -> bool:
    """
    创建一个简化的MobileFaceNet ONNX模型
    用于演示和测试目的
    """
    try:
        import torch
        import torch.nn as nn
        import torch.onnx
        
        print("创建简化的MobileFaceNet模型...")
        
        class SimpleMobileFaceNet(nn.Module):
            def __init__(self):
                super(SimpleMobileFaceNet, self).__init__()
                # 简化的网络结构
                self.features = nn.Sequential(
                    # 第一层卷积
                    nn.Conv2d(3, 32, 3, stride=2, padding=1),
                    nn.BatchNorm2d(32),
                    nn.ReLU(inplace=True),
                    
                    # 深度可分离卷积块
                    nn.Conv2d(32, 32, 3, stride=1, padding=1, groups=32),
                    nn.BatchNorm2d(32),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(32, 64, 1),
                    nn.BatchNorm2d(64),
                    nn.ReLU(inplace=True),
                    
                    # 下采样
                    nn.Conv2d(64, 64, 3, stride=2, padding=1, groups=64),
                    nn.BatchNorm2d(64),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(64, 128, 1),
                    nn.BatchNorm2d(128),
                    nn.ReLU(inplace=True),
                    
                    # 全局平均池化
                    nn.AdaptiveAvgPool2d((1, 1))
                )
                
                # 分类器
                self.classifier = nn.Sequential(
                    nn.Dropout(0.2),
                    nn.Linear(128, 512),
                    nn.BatchNorm1d(512)
                )
            
            def forward(self, x):
                x = self.features(x)
                x = x.view(x.size(0), -1)
                x = self.classifier(x)
                # L2归一化
                x = torch.nn.functional.normalize(x, p=2, dim=1)
                return x
        
        # 创建模型实例
        model = SimpleMobileFaceNet()
        model.eval()
        
        # 创建示例输入
        dummy_input = torch.randn(1, 3, 112, 112)
        
        # 导出为ONNX
        torch.onnx.export(
            model,
            dummy_input,
            filepath,
            export_params=True,
            opset_version=11,
            do_constant_folding=True,
            input_names=['input0'],
            output_names=['output0'],
            dynamic_axes={
                'input0': {0: 'batch_size'},
                'output0': {0: 'batch_size'}
            }
        )
        
        print(f"简化MobileFaceNet模型创建成功: {filepath}")
        return True
        
    except ImportError:
        print("PyTorch未安装，无法创建模型")
        return False
    except Exception as e:
        print(f"创建模型失败: {e}")
        return False

def main():
    """主函数"""
    models_dir = "models"
    model_filename = "mobilefacenet.onnx"
    model_path = os.path.join(models_dir, model_filename)
    
    print("=== MobileFaceNet ONNX模型下载器 ===")
    
    # 检查模型是否已存在
    if os.path.exists(model_path):
        print(f"模型已存在: {model_path}")
        file_size = os.path.getsize(model_path) / (1024 * 1024)  # MB
        print(f"文件大小: {file_size:.2f} MB")
        
        choice = input("是否重新下载? (y/N): ").strip().lower()
        if choice not in ['y', 'yes']:
            print("使用现有模型")
            return
    
    # 尝试下载预训练模型的URL列表
    download_urls = [
        # 这些是示例URL，实际使用时需要替换为有效的下载链接
        "https://github.com/foamliu/MobileFaceNet/releases/download/v1.0/mobilefacenet.pt",
        # 可以添加更多备用下载链接
    ]
    
    success = False
    
    # 尝试从各个URL下载
    for url in download_urls:
        if url.endswith('.pt'):
            # PyTorch模型需要转换
            pt_path = os.path.join(models_dir, "mobilefacenet.pt")
            if download_file(url, pt_path):
                print("PyTorch模型下载成功，需要手动转换为ONNX格式")
                print("请参考README.md中的转换说明")
                success = True
                break
        else:
            # 直接下载ONNX模型
            if download_file(url, model_path):
                success = True
                break
    
    # 如果下载失败，创建简化模型
    if not success:
        print("\\n下载失败，尝试创建简化的MobileFaceNet模型...")
        if create_simple_mobilefacenet_onnx(model_path):
            print("\\n注意: 这是一个简化的演示模型，性能可能不如预训练模型")
            print("建议手动下载预训练的MobileFaceNet模型以获得更好的效果")
        else:
            print("\\n无法创建模型，请手动下载MobileFaceNet ONNX模型")
            print("参考链接:")
            print("- https://github.com/foamliu/MobileFaceNet")
            print("- https://huggingface.co/models?search=mobilefacenet")
    
    print("\\n完成!")

if __name__ == "__main__":
    main()