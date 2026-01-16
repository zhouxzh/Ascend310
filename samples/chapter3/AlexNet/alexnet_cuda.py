import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader
from torchvision import datasets
import matplotlib.pyplot as plt
from tqdm.auto import tqdm, trange
from torchvision import transforms

device = torch.device("cuda")

class AlexNet(nn.Module):
    def __init__(self, num_classes=10):
        super(AlexNet, self).__init__()
        self.features = nn.Sequential(
            # Layer 1
            nn.Conv2d(3, 96, kernel_size=11, stride=4, padding=1),
            nn.ReLU(inplace=True),
            nn.AvgPool2d(kernel_size=3, stride=2),
            # Layer 2
            nn.Conv2d(96, 256, kernel_size=5, padding=2),
            nn.ReLU(inplace=True),
            nn.AvgPool2d(kernel_size=3, stride=2),
            # Layer 3
            nn.Conv2d(256, 384, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            # Layer 4
            nn.Conv2d(384, 384, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            # Layer 5
            nn.Conv2d(384, 256, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.AvgPool2d(kernel_size=3, stride=2),
        )
        
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(6400, 4096),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5),
            nn.Linear(4096, 4096),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5),
            nn.Linear(4096, num_classes),
        )

    def forward(self, x):
        x = self.features(x)
        x = self.classifier(x)
        return x
  
def load_data(script_dir, batch_size=4):
    """
    加载 CIFAR-10 数据集并进行预处理
    """
    # 引入 transforms 用于图像变换

    # 数据存储路径
    data_root = os.path.join(script_dir, "data")
    
    # 定义预处理流程：
    # 1. Resize: 将 CIFAR-10 的 32x32 图像调整为 224x224，以匹配 AlexNet 输入要求
    # 2. ToTensor: 将图像数据转换为 Tensor (C, H, W)，并将像素值归一化到 [0, 1]
    # 3. Normalize: 对 RGB 三个通道进行标准化
    transform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        transforms.Lambda(lambda x: x.half())
    ])
    
    # 使用 torchvision.datasets 下载并加载 CIFAR-10 数据
    # 传入 transform 参数，DataLoader 读取数据时会自动进行 Resize 和 ToTensor
    train_ds = datasets.CIFAR10(data_root, train=True, download=True, transform=transform)
    test_ds = datasets.CIFAR10(data_root, train=False, download=True, transform=transform)
    
    # 构建 DataLoader
    # 相比原代码一次性加载到内存，这里使用 lazy loading 方式，避免 Resize 后占用过多内存
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False)
    return train_loader, val_loader

def train_and_eval(train_loader, val_loader, epochs=10, lr=0.01):
    """
    执行模型训练与验证循环
    """
    # 初始化模型并迁移至 NPU 设备
    model = AlexNet().half().to(device)
    # 定义损失函数：交叉熵损失，适用于多分类问题
    criterion = nn.CrossEntropyLoss()
    # 定义优化器：随机梯度下降 (SGD)
    optimizer = optim.SGD(model.parameters(), lr=lr)

    train_acc_history = []
    val_acc_history = []
    train_loss_history = []
    val_loss_history = []

    # 使用 trange 展示总进度条
    t = trange(epochs, desc="Epochs")
    for epoch in t:
        # --- 训练阶段 ---
        model.train() # 设置模型为训练模式（启用 Dropout, BatchNorm 等）
        correct = 0; total = 0
        running_loss = 0.0
        for inputs, labels in train_loader:
            # 数据迁移至 NPU，保持 float32
            inputs = inputs.to(device); labels = labels.to(device)
            
            # 前向传播
            outputs = model(inputs)
            # 计算损失
            loss = criterion(outputs, labels)
            
            # 反向传播与优化
            optimizer.zero_grad() # 清空梯度
            loss.backward()       # 计算梯度
            optimizer.step()      # 更新参数
            
            # 统计损失与精度
            running_loss += loss.item() * labels.size(0)
            # 获取预测结果（最大概率对应的索引）
            preds = outputs.argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
        
        # 计算本 epoch 的平均训练精度和损失
        train_acc = correct / total
        train_loss = running_loss / total
        train_acc_history.append(train_acc)
        train_loss_history.append(train_loss)

        # --- 验证阶段 ---
        model.eval() # 设置模型为评估模式
        correct_v = 0; total_v = 0
        running_loss_v = 0.0
        with torch.no_grad(): # 验证时不计算梯度，节省显存和计算资源
            for inputs, labels in val_loader:
                inputs = inputs.to(device); labels = labels.to(device)
                outputs = model(inputs)
                loss_v = criterion(outputs, labels)
                running_loss_v += loss_v.item() * labels.size(0)
                preds = outputs.argmax(dim=1)
                correct_v += (preds == labels).sum().item()
                total_v += labels.size(0)
        
        # 计算本 epoch 的平均验证精度和损失
        val_acc = correct_v / total_v
        val_loss = running_loss_v / total_v
        val_acc_history.append(val_acc)
        val_loss_history.append(val_loss)
        t.set_postfix(train_acc =f"{train_acc:.4f}", val_loss=f"{val_loss:.4f}", val_acc=f"{val_acc:.4f}", lr=f'{optimizer.param_groups[0]["lr"]:.6f}')

    # 返回训练/验证指标历史
    return train_acc_history, val_acc_history, train_loss_history, val_loss_history

def plot_metrics(script_dir, train_acc, val_acc, train_loss, val_loss):
    """
    绘制训练和验证的精度与损失曲线
    """
    plt.figure(figsize=(12, 5))
    
    # 绘制精度曲线
    plt.subplot(1, 2, 1)
    plt.plot(train_acc, label='Train Accuracy')
    plt.plot(val_acc, label='Validation Accuracy')
    plt.title('Accuracy over Epochs')
    plt.xlabel('Epoch')
    plt.ylabel('Accuracy')
    plt.legend()
    
    # 绘制损失曲线
    plt.subplot(1, 2, 2)
    plt.plot(train_loss, label='Train Loss')
    plt.plot(val_loss, label='Validation Loss')
    plt.title('Loss over Epochs')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.legend()
    
    # 保存图片
    save_path = os.path.join(script_dir, "training_metrics_cuda_fp16.png")
    plt.savefig(save_path)
    print(f"Metrics plot saved to {save_path}")

if __name__ == "__main__":
    # 获取当前脚本目录，方便保存文件
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 加载数据
    train_loader, val_loader = load_data(script_dir, batch_size=16)
    # 开始训练
    train_acc_history, val_acc_history, train_loss_history, val_loss_history = train_and_eval(
        train_loader, val_loader, epochs=20, lr=0.01
    )
    # 绘制结果
    plot_metrics(script_dir, train_acc_history, val_acc_history, train_loss_history, val_loss_history)
