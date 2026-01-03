import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.datasets import fetch_california_housing
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
from tqdm.auto import tqdm, trange

# ==========================================
# 1. 环境配置与随机种子设置
# ==========================================
# 设置随机种子以确保实验结果的可复现性
np.random.seed(42)
torch.manual_seed(42)

# 指定计算设备。'npu' 表示使用华为昇腾 AI 处理器
# 如果在没有 NPU 的环境下运行，可改为 'cuda' 或 'cpu'
device = torch.device('npu')

# ==========================================
# 2. 数据加载与预处理
# ==========================================
# 加载加州房价数据集（回归任务）
california = fetch_california_housing()
X = california.data  # 特征矩阵：包含 8 个特征（如人均收入、房龄等）
y = california.target.reshape(-1, 1)  # 目标值：房价，调整为 (N, 1) 形状

# 数据标准化：神经网络对输入特征的量级很敏感
# 使用 StandardScaler 使数据符合均值为 0，方差为 1 的分布
scaler_X = StandardScaler()
scaler_y = StandardScaler()
X_scaled = scaler_X.fit_transform(X)
y_scaled = scaler_y.fit_transform(y)

# 划分数据集：80% 用于训练，20% 用于验证
X_train, X_val, y_train, y_val = train_test_split(
    X_scaled, y_scaled, test_size=0.2, random_state=42
)

# ==========================================
# 3. 构建 PyTorch 数据加载器 (DataLoader)
# ==========================================
batch_size = 32

# 将 NumPy 数组转换为 PyTorch 张量 (FloatTensor)
train_dataset = TensorDataset(
    torch.FloatTensor(X_train), 
    torch.FloatTensor(y_train)
)
val_dataset = TensorDataset(
    torch.FloatTensor(X_val), 
    torch.FloatTensor(y_val)
)

# DataLoader 负责按批次 (Batch) 提供数据，并在训练集上开启打乱 (Shuffle)
train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=batch_size)

# ==========================================
# 4. 定义模型结构
# ==========================================
# 单层感知机：本质上是一个线性回归模型 (y = Wx + b)
class SingleLayerPerceptron(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        # 定义一个全连接层，输入维度为特征数，输出维度为 1
        self.fc = nn.Linear(input_dim, 1)
        
    def forward(self, x):
        # 前向传播逻辑
        return self.fc(x)

# 实例化模型并迁移到指定的计算设备 (NPU)
model = SingleLayerPerceptron(X_train.shape[1]).to(device)

# 定义损失函数：均方误差 (MSE)，适用于回归任务
criterion = nn.MSELoss()

# 定义优化器：随机梯度下降 (SGD)，学习率设为 0.0001
optimizer = optim.SGD(model.parameters(), lr=0.0001)

# ==========================================
# 5. 训练与验证函数定义
# ==========================================
def train_epoch(model, loader, criterion, optimizer):
    """单轮训练逻辑"""
    model.train() # 设置为训练模式
    total_loss = 0
    for batch_x, batch_y in tqdm(loader, desc="Train", leave=False):
        # 将数据迁移到 NPU
        batch_x = batch_x.to(device)
        batch_y = batch_y.to(device)
        
        # 梯度清零
        optimizer.zero_grad()
        # 前向传播
        outputs = model(batch_x)
        # 计算损失
        loss = criterion(outputs, batch_y)
        # 反向传播
        loss.backward()
        # 梯度裁剪：防止梯度爆炸
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        # 更新参数
        optimizer.step()
        
        total_loss += loss.item() * batch_x.size(0)
    return total_loss / len(loader.dataset)

def validate(model, loader, criterion):
    """验证逻辑"""
    model.eval() # 设置为评估模式
    total_loss = 0
    with torch.no_grad(): # 验证阶段不需要计算梯度
        for batch_x, batch_y in tqdm(loader, desc="Val", leave=False):
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            total_loss += loss.item() * batch_x.size(0)
    return total_loss / len(loader.dataset)

# ==========================================
# 6. 执行训练循环
# ==========================================
epochs = 50
train_losses = []
val_losses = []

# 使用 trange 展示总进度条
t = trange(epochs, desc="Epochs")
for epoch in t:
    train_loss = train_epoch(model, train_loader, criterion, optimizer)
    val_loss = validate(model, val_loader, criterion)
    
    train_losses.append(train_loss)
    val_losses.append(val_loss)
    
    # 在进度条右侧实时显示当前损失和学习率
    t.set_postfix(train_loss=f"{train_loss:.4f}", val_loss=f"{val_loss:.4f}", lr=f'{optimizer.param_groups[0]["lr"]:.6f}')

print(f"\nFinal Validation Loss: {val_losses[-1]:.4f}")

# ==========================================
# 7. 模型评估与指标计算
# ==========================================
model.eval()
all_predictions = []
all_targets = []

# 获取验证集上的所有预测结果
with torch.no_grad():
    for batch_x, batch_y in val_loader:
        batch_x = batch_x.to(device)
        predictions = model(batch_x)
        # 将结果移回 CPU 并转为 NumPy
        all_predictions.extend(predictions.cpu().numpy())
        all_targets.extend(batch_y.cpu().numpy())

# 逆标准化：将预测值和真实值还原回原始的房价单位（10万美元）
all_predictions = scaler_y.inverse_transform(np.array(all_predictions))
all_targets = scaler_y.inverse_transform(np.array(all_targets))

# 计算回归常用指标
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

r2 = r2_score(all_targets, all_predictions) # 决定系数，越接近 1 越好
mae = mean_absolute_error(all_targets, all_predictions) # 平均绝对误差
rmse = np.sqrt(mean_squared_error(all_targets, all_predictions)) # 均方根误差

print(f"\n最终评估指标:")
print(f"R² Score: {r2:.4f}")
print(f"MAE: ${mae:.2f}")
print(f"RMSE: ${rmse:.2f}")

# ==========================================
# 8. 结果可视化
# ==========================================
import matplotlib.pyplot as plt

fig, axes = plt.subplots(2, 2, figsize=(12, 10))

# 1. 损失曲线：观察模型是否收敛
axes[0, 0].plot(train_losses, label='Train Loss')
axes[0, 0].plot(val_losses, label='Val Loss')
axes[0, 0].set_xlabel('Epoch')
axes[0, 0].set_ylabel('Loss')
axes[0, 0].set_title('Training and Validation Loss')
axes[0, 0].legend()
axes[0, 0].grid(True)

# 2. 预测值 vs 真实值：散点越接近对角线表示预测越准
axes[0, 1].scatter(all_targets, all_predictions, alpha=0.3)
axes[0, 1].set_xlabel('True Price')
axes[0, 1].set_ylabel('Predicted Price')
axes[0, 1].set_title('True vs Predicted Prices')
axes[0, 1].grid(True)

# 3. 残差图：检查误差是否随机分布（理想状态下应无明显模式）
residuals = all_targets - all_predictions
axes[1, 0].scatter(all_predictions, residuals, alpha=0.3)
axes[1, 0].set_xlabel('Predicted Price')
axes[1, 0].set_ylabel('Residuals')
axes[1, 0].set_title('Residual Plot')
axes[1, 0].grid(True)

# 4. 误差分布直方图：检查误差是否符合正态分布
axes[1, 1].hist(residuals, bins=50, edgecolor='black')
axes[1, 1].set_xlabel('Error')
axes[1, 1].set_ylabel('Frequency')
axes[1, 1].set_title('Error Distribution')

plt.tight_layout()
plt.savefig('california_housing_linear_network_results.png')