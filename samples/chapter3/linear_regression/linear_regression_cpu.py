import torch
from torch.utils.data import TensorDataset, DataLoader
from tqdm import tqdm
import matplotlib.pyplot as plt

# 仅使用 CPU
device = torch.device('cpu')

torch.manual_seed(0)

# 使用合成数据（无自有数据）
N = 1024
R_true, b_true = 120.0, 0.02  # 真实电阻与偏置
I = torch.linspace(0.0, 1.0, N).unsqueeze(1)  # [N,1] 电流(A)
noise_std = 0.005  # 约 5mV 噪声
noise = noise_std * torch.randn(N, 1)
V = R_true * I + b_true + noise  # [N,1] 电压(V)

I = I.to(torch.float32).to(device)
V = V.to(torch.float32).to(device)

# 使用 DataLoader
dataset = TensorDataset(I, V)
batch_size = 16
loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

# 自定义 1x1 线性层，避免使用 F.linear/matmul
class LinearNoMatmul(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.randn(1))  # w
        self.bias = torch.nn.Parameter(torch.zeros(1))    # b
    def forward(self, x):
        # x: [N,1] -> 元素级乘加，不走 matmul
        x = x.reshape(-1)
        y = x * self.weight + self.bias
        return y.unsqueeze(1)

# 线性模型 V ≈ R·I + b
# model = torch.nn.Linear(1, 1, bias=True).to(device)
model = LinearNoMatmul().to(device)
opt = torch.optim.SGD(model.parameters(), lr=1e-2)
loss_fn = torch.nn.MSELoss()


# 使用小批量训练循环，加入 tqdm 进度条并记录 loss
num_epochs = 50
epoch_losses = []

with tqdm(range(num_epochs), desc="Epochs") as pbar:
    for _ in pbar:
        epoch_loss = 0.0
        n_batches = 0
        for I_b, V_b in loader:
            I_b = I_b.to(torch.float32).to(device)
            V_b = V_b.to(torch.float32).to(device)
            pred = model(I_b)
            loss = loss_fn(pred, V_b)
            opt.zero_grad()
            loss.backward()
            opt.step()
            epoch_loss += loss.item()
            n_batches += 1
        avg_loss = epoch_loss / max(n_batches, 1)
        epoch_losses.append(avg_loss)
        pbar.set_postfix(loss=f"{avg_loss:.6f}")

# 绘制训练损失下降曲线并保存图片
plt.figure(figsize=(6, 4))
plt.plot(epoch_losses, label="Train MSE")
plt.xlabel("Epoch")
plt.ylabel("Loss")
plt.title("Training Loss")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig("training_loss.png")

R = model.weight.detach().item()  # 斜率=电阻(Ω)
b = model.bias.detach().item()    # 截距=偏置(V)
print("device =", device)
print("R(Ω) =", R, " b(V) =", b)