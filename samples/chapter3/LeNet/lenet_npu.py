import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader
from torchvision import datasets
import matplotlib.pyplot as plt
from tqdm.auto import tqdm, trange

# 指定使用昇腾 NPU 设备
# 在昇腾 AI 处理器上运行 PyTorch 代码时，需要指定设备为 "npu"
device = torch.device("npu")

class LeNet(nn.Module):
    """
    经典的 LeNet-5 网络结构实现
    LeNet-5 是一个简单的卷积神经网络，包含两个卷积层和三个全连接层
    """
    def __init__(self):
        super().__init__()
        # 第一层卷积：输入通道1（灰度图），输出通道6，卷积核5x5
        # padding默认是0，stride默认是1
        self.conv1 = nn.Conv2d(1, 6, kernel_size=5)
        # 池化层：2x2 窗口，步长为2，用于下采样，减少特征图尺寸
        self.pool = nn.AvgPool2d(2, 2)
        # 第二层卷积：输入通道6，输出通道16，卷积核5x5
        self.conv2 = nn.Conv2d(6, 16, kernel_size=5)
        # 全连接层：输入特征 16*4*4，输出120
        # 这里的 16*4*4 是经过两次卷积和池化后的特征图大小展平后的维度
        # 原始 28x28 -> conv1(5x5) -> 24x24 -> pool(2x2) -> 12x12
        # -> conv2(5x5) -> 8x8 -> pool(2x2) -> 4x4
        # 最终特征图大小为 16通道 * 4 * 4
        self.fc1 = nn.Linear(16 * 4 * 4, 120)
        # 全连接层：输入120，输出84
        self.fc2 = nn.Linear(120, 84)
        # 输出层：输入84，输出10（对应 MNIST 的10个类别 0-9）
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        # 卷积 -> ReLU 激活 -> 池化
        # x shape: [batch, 1, 28, 28] -> [batch, 6, 12, 12]
        x = torch.relu(self.conv1(x))
        x = self.pool(x)
        # 卷积 -> ReLU 激活 -> 池化
        # x shape: [batch, 6, 12, 12] -> [batch, 16, 4, 4]
        x = torch.relu(self.conv2(x))
        x = self.pool(x)
        # 展平特征图为一维向量，用于全连接层
        # x shape: [batch, 16, 4, 4] -> [batch, 16*4*4]
        x = x.view(x.size(0), -1)
        # 全连接层 -> ReLU
        x = torch.relu(self.fc1(x))
        x = torch.relu(self.fc2(x))
        # 输出层，通常配合 CrossEntropyLoss 使用，不需要 softmax
        x = self.fc3(x)
        return x

def plot_metrics(script_dir, train_acc_history, test_acc_history, train_loss_history, test_loss_history):
    """
    绘制并保存训练过程中的精度和损失曲线
    """
    plt.figure(figsize=(10, 4))
    # Accuracy subplot (左图：精度曲线)
    plt.subplot(1, 2, 1)
    plt.plot(train_acc_history, label="Train Acc")
    plt.plot(test_acc_history, label="Test Acc")
    plt.xlabel("Epoch"); plt.ylabel("Accuracy"); plt.title("Accuracy"); plt.legend()
    # Loss subplot (右图：损失曲线)
    plt.subplot(1, 2, 2)
    plt.plot(train_loss_history, label="Train Loss")
    plt.plot(test_loss_history, label="Test Loss")
    plt.xlabel("Epoch"); plt.ylabel("Loss"); plt.title("Loss"); plt.legend()
    plt.tight_layout()
    # 保存图片到脚本所在目录
    out_path = os.path.join(script_dir, "accuracy_loss_curve.png")
    plt.savefig(out_path, bbox_inches="tight")
    print(f"Saved accuracy & loss curve to: {out_path}")

def load_data(script_dir, batch_size=256):
    """
    加载 MNIST 数据集并进行预处理
    """
    # 数据存储路径
    data_root = os.path.join(script_dir, "data")
    # 使用 torchvision.datasets 下载并加载 MNIST 数据
    train_ds = datasets.MNIST(data_root, train=True, download=True)
    test_ds = datasets.MNIST(data_root, train=False, download=True)
    
    # 预处理：
    # 1. unsqueeze(1): 增加通道维度 [N, H, W] -> [N, 1, H, W]
    # 2. to(torch.float16): 转换为半精度浮点数，NPU 上 float16 计算性能更好
    # 3. div_(255.0): 像素值归一化到 [0, 1] 区间
    x_train = train_ds.data.unsqueeze(1).to(torch.float16).div_(255.0)
    y_train = train_ds.targets.to(torch.int64)
    x_test = test_ds.data.unsqueeze(1).to(torch.float16).div_(255.0)
    y_test = test_ds.targets.to(torch.int64)
    
    # 构建 DataLoader，用于批量加载数据
    # shuffle=True 表示训练时打乱数据顺序
    train_loader = DataLoader(TensorDataset(x_train, y_train), batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(TensorDataset(x_test, y_test), batch_size=batch_size, shuffle=False)
    return train_loader, test_loader

def train_and_eval(train_loader, test_loader, epochs=10, lr=0.01):
    """
    执行模型训练与验证循环
    """
    # 初始化模型并迁移至 NPU 设备
    # .half() 将模型参数转换为 float16，利用 NPU 的 Tensor Core 加速
    model = LeNet().half().to(device)
    # 定义损失函数：交叉熵损失，适用于多分类问题
    criterion = nn.CrossEntropyLoss()
    # 定义优化器：随机梯度下降 (SGD)
    optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9)

    train_acc_history = []
    test_acc_history = []
    train_loss_history = []
    test_loss_history = []

    # 使用 trange 展示总进度条
    t = trange(epochs, desc="Epochs")
    for epoch in t:
        # --- 训练阶段 ---
        model.train() # 设置模型为训练模式（启用 Dropout, BatchNorm 等）
        correct = 0; total = 0
        running_loss = 0.0
        for inputs, labels in train_loader:
            # 数据迁移至 NPU 并转为半精度，标签保持 int64 或 int32
            inputs = inputs.to(device)
            labels = labels.to(device)
            
            # 前向传播
            outputs = model(inputs)
            # 计算损失时将输出转回 float32 以保证数值稳定性（混合精度训练的常见做法）
            loss = criterion(outputs.float(), labels)
            
            # 反向传播与优化
            optimizer.zero_grad() # 清空梯度
            loss.backward()       # 计算梯度
            optimizer.step()      # 更新参数
            
            # 统计损失与精度
            running_loss += loss.item() * labels.size(0)
            # 获取预测结果（最大概率对应的索引）
            preds = outputs.float().argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
        
        # 计算本 epoch 的平均训练精度和损失
        train_acc = correct / total
        train_loss = running_loss / total
        train_acc_history.append(train_acc)
        train_loss_history.append(train_loss)

        # --- 测试阶段 ---
        model.eval() # 设置模型为评估模式
        correct_t = 0; total_t = 0
        running_loss_t = 0.0
        with torch.no_grad(): # 验证时不计算梯度，节省显存和计算资源
            for inputs, labels in test_loader:
                inputs = inputs.to(device).half(); labels = labels.to(device)
                outputs = model(inputs)
                loss_t = criterion(outputs.float(), labels)
                running_loss_t += loss_t.item() * labels.size(0)
                preds = outputs.float().argmax(dim=1)
                correct_t += (preds == labels).sum().item()
                total_t += labels.size(0)
        
        # 计算本 epoch 的平均测试精度和损失
        test_acc = correct_t / total_t
        test_loss = running_loss_t / total_t
        test_acc_history.append(test_acc)
        test_loss_history.append(test_loss)
        t.set_postfix(train_acc =f"{train_acc:.4f}", val_loss=f"{test_acc:.4f}", lr=f'{optimizer.param_groups[0]["lr"]:.6f}')

    # 返回训练/测试指标历史
    return train_acc_history, test_acc_history, train_loss_history, test_loss_history

if __name__ == "__main__":
    # 获取当前脚本目录，方便保存文件
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 加载数据
    train_loader, test_loader = load_data(script_dir, batch_size=256)
    # 开始训练
    train_acc_history, test_acc_history, train_loss_history, test_loss_history = train_and_eval(
        train_loader, test_loader, epochs=10, lr=0.01
    )
    # 绘制结果
    plot_metrics(script_dir, train_acc_history, test_acc_history, train_loss_history, test_loss_history)