import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.datasets import fetch_california_housing
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
from tqdm.auto import tqdm, trange
import random

# 设置随机种子以确保实验结果的可重现性
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
# 指定使用昇腾 NPU 设备
device = torch.device('npu:0')

# 加载加州房价数据集：包含8个特征，目标值为房屋中位数价格
california = fetch_california_housing()
X = california.data  # 特征矩阵 (样本数, 8)
y = california.target.reshape(-1, 1) # 目标值 (样本数, 1)

# 数据标准化：将特征和目标值缩放到均值为0、方差为1的分布，有助于模型收敛
scaler_X = StandardScaler()
scaler_y = StandardScaler()
X_scaled = scaler_X.fit_transform(X)
y_scaled = scaler_y.fit_transform(y)

# 划分数据集：80% 用于训练，20% 用于验证
X_train, X_val, y_train, y_val = train_test_split(
    X_scaled, y_scaled, test_size=0.2, random_state=42
)

# 构建 PyTorch 数据管道
batch_size = 32
train_dataset = TensorDataset(
    torch.FloatTensor(X_train), 
    torch.FloatTensor(y_train)
)
val_dataset = TensorDataset(
    torch.FloatTensor(X_val), 
    torch.FloatTensor(y_val)
)

# DataLoader 负责按批次(Batch)提供数据并支持洗牌(Shuffle)
train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=batch_size)

# 定义双层感知机模型
class DoubleLayerPerceptron(nn.Module):
    def __init__(self, input_dim, hidden_dim=64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim), # 输入层到隐藏层
            nn.ReLU(),                        # 激活函数，引入非线性
            nn.Linear(hidden_dim, 1)          # 隐藏层到输出层（回归任务输出维度为1）
        )
    def forward(self, x):
        return self.net(x)

# 实例化模型并迁移至 NPU
model = DoubleLayerPerceptron(X_train.shape[1]).to(device)
# 回归任务常用的均方误差损失函数
criterion = nn.MSELoss()
# 随机梯度下降优化器
optimizer = optim.SGD(model.parameters(), lr=0.0001)

# 单个 Epoch 的训练逻辑
def train_epoch(model, loader, criterion, optimizer):
    model.train()
    total_loss = 0
    for batch_x, batch_y in tqdm(loader, desc="Train", leave=False):
        # 将数据搬运到 NPU 显存
        batch_x = batch_x.to(device)
        batch_y = batch_y.to(device)
        
        optimizer.zero_grad()           # 清空梯度
        outputs = model(batch_x)        # 前向传播
        loss = criterion(outputs, batch_y) # 计算损失
        loss.backward()                 # 反向传播计算梯度
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0) # 梯度裁剪防止梯度爆炸
        optimizer.step()                # 更新权重
        total_loss += loss.item() * batch_x.size(0)
    return total_loss / len(loader.dataset)

# 验证逻辑
def validate(model, loader, criterion):
    model.eval() # 设置为评估模式
    total_loss = 0
    with torch.no_grad(): # 禁用梯度计算，节省内存和计算资源
        for batch_x, batch_y in tqdm(loader, desc="Val", leave=False):
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            total_loss += loss.item() * batch_x.size(0)
    return total_loss / len(loader.dataset)

# 执行训练过程
epochs = 50
train_losses = []
val_losses = []

t = trange(epochs, desc="Epochs")
for epoch in t:
    train_loss = train_epoch(model, train_loader, criterion, optimizer)
    val_loss = validate(model, val_loader, criterion)
    train_losses.append(train_loss)
    val_losses.append(val_loss)
    # 在进度条上实时显示损失和学习率
    t.set_postfix(train_loss=f"{train_loss:.4f}", val_loss=f"{val_loss:.4f}", lr=f'{optimizer.param_groups[0]["lr"]:.6f}')

print(f"\nValidation Loss: {val_losses[-1]:.4f}")

# 评估阶段：获取验证集的预测值并还原标准化
model.eval()
all_predictions = []
all_targets = []

with torch.no_grad():
    for batch_x, batch_y in val_loader:
        batch_x = batch_x.to(device)
        predictions = model(batch_x)
        all_predictions.extend(predictions.cpu().numpy())
        all_targets.extend(batch_y.cpu().numpy())

# 将标准化后的数据逆转回原始价格单位
all_predictions = scaler_y.inverse_transform(np.array(all_predictions))
all_targets = scaler_y.inverse_transform(np.array(all_targets))

# 计算回归评估指标
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

r2 = r2_score(all_targets, all_predictions) # 决定系数，越接近1越好
mae = mean_absolute_error(all_targets, all_predictions) # 平均绝对误差
rmse = np.sqrt(mean_squared_error(all_targets, all_predictions)) # 均方根误差

print(f"\n最终评估指标:")
print(f"R² Score: {r2:.4f}")
print(f"MAE: ${mae:.2f}")
print(f"RMSE: ${rmse:.2f}")

# 结果可视化
import matplotlib.pyplot as plt

fig, axes = plt.subplots(2, 2, figsize=(12, 10))

# 1. 损失下降曲线：观察模型是否收敛或过拟合
axes[0, 0].plot(train_losses, label='Train Loss')
axes[0, 0].plot(val_losses, label='Val Loss')
axes[0, 0].set_xlabel('Epoch')
axes[0, 0].set_ylabel('Loss')
axes[0, 0].set_title('Training and Validation Loss')
axes[0, 0].legend()
axes[0, 0].grid(True)

# 2. 预测值 vs 真实值散点图：点越靠近红虚线表示预测越准
axes[0, 1].scatter(all_targets, all_predictions, alpha=0.3)
axes[0, 1].plot([all_targets.min(), all_targets.max()], 
                [all_targets.min(), all_targets.max()], 'r--', lw=2)
axes[0, 1].set_xlabel('True Price')
axes[0, 1].set_ylabel('Predicted Price')
axes[0, 1].set_title('True vs Predicted Prices')
axes[0, 1].grid(True)

# 3. 残差图：检查误差是否随机分布（理想状态应均匀分布在0附近）
residuals = all_targets - all_predictions
axes[1, 0].scatter(all_predictions, residuals, alpha=0.3)
axes[1, 0].axhline(y=0, color='r', linestyle='--')
axes[1, 0].set_xlabel('Predicted Price')
axes[1, 0].set_ylabel('Residuals')
axes[1, 0].set_title('Residual Plot')
axes[1, 0].grid(True)

# 4. 误差分布直方图：观察误差是否符合正态分布
axes[1, 1].hist(residuals, bins=50, edgecolor='black')
axes[1, 1].set_xlabel('Error')
axes[1, 1].set_ylabel('Frequency')
axes[1, 1].set_title('Error Distribution')
axes[1, 1].grid(True)

plt.tight_layout()
plt.savefig('california_housing_mlp_results.png')