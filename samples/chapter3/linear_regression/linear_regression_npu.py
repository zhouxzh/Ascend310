"""线性回归（V ≈ R·I + b）示例，使用华为昇腾 NPU 设备训练。
- 数据：合成电压/电流数据，单位 A/V，含高斯噪声（约 5mV）。
- 目标：拟合电阻 R（斜率）与偏置 b（截距）。
- 设备：默认 npu:0，可切换为 CPU。
- 训练：SGD 优化，MSE 损失，小批量训练并通过 tqdm 展示进度。
- 输出：打印估计的 R、b，并保存训练损失曲线为 training_loss.png。
"""

import torch  # PyTorch 主库
from tqdm import tqdm  # 进度条显示
import matplotlib.pyplot as plt  # 绘图
import torch_npu  # NPU 支持库（确保已安装与环境就绪）

device = torch.device('npu:0')  # 选择 NPU 设备（Ascend），需要正确的驱动/环境
torch.manual_seed(0)  # 固定随机种子以确保可复现

# 使用合成数据（无自有数据）
N = 1024  # 样本数
R_true, b_true = 120.0, 0.02  # 真实电阻（Ω）与偏置（V），用于生成数据的“地真值”
I = torch.linspace(0.0, 1.0, N).unsqueeze(1)  # 电流张量 [N,1]，范围 0~1 A
noise_std = 0.005  # 噪声标准差 ≈ 5 mV
noise = noise_std * torch.randn(N, 1)  # 加性高斯噪声，与 V 同形状
V = R_true * I + b_true + noise  # 生成电压张量 [N,1]，单位 V

I = I.to(device)  # 将数据移至计算设备（NPU/CPU）
V = V.to(device)
batch_size = 16  # 小批量大小，影响梯度估计方差与训练稳定性

# 线性模型 V ≈ R·I + b（1 输入 -> 1 输出，带偏置）
model = torch.nn.Linear(1, 1, bias=True).to(device)  # 权重即电阻 R，偏置即 b
opt = torch.optim.SGD(model.parameters(), lr=1e-2)  # 随机梯度下降，学习率 1e-2
loss_fn = torch.nn.MSELoss()  # 均方误差：衡量预测电压与真实电压的差异

# 使用小批量训练循环，加入 tqdm 进度条并记录 loss
num_epochs = 50  # 训练轮数
epoch_losses = []  # 记录每个 epoch 的平均损失，便于绘图

with tqdm(range(num_epochs), desc="Epochs") as pbar:  # 进度条显示每个 epoch
    for _ in pbar:
        epoch_loss = 0.0  # 累计当前 epoch 的损失
        n_batches = 0  # 实际批次数（用于计算平均损失）
        perm = torch.randperm(I.shape[0], device=device)  # 打乱索引以实现随机小批量
        for start in range(0, I.shape[0], batch_size):  # 遍历每个批次起点
            end = start + batch_size  # 批次终点
            idx = perm[start:end]  # 当前批次的随机样本索引
            I_b = I[idx].to(torch.float32)  # NPU 通常以 float32 计算，确保类型一致
            V_b = V[idx].to(torch.float32)
            pred = model(I_b)  # 前向计算：V̂ = R·I + b
            loss = loss_fn(pred, V_b)  # 计算当前批次的 MSE 损失
            opt.zero_grad()  # 清空上一轮梯度
            loss.backward()  # 反向传播，计算参数梯度
            opt.step()  # 参数更新（SGD）
            epoch_loss += loss.item()  # 累加标量损失
            n_batches += 1  # 批次数 +1
        avg_loss = epoch_loss / max(n_batches, 1)  # 当前 epoch 的平均损失
        epoch_losses.append(avg_loss)  # 记录以便后续绘图
        pbar.set_postfix(loss=f"{avg_loss:.6f}")  # 在进度条尾部显示当前损失

R = model.weight.detach().item()  # 提取斜率（电阻，单位 Ω），不跟踪梯度
b = model.bias.detach().item()    # 提取截距（偏置，单位 V），不跟踪梯度
print("device =", device)  # 打印使用的设备
print("R(Ω) =", R, " b(V) =", b)  # 打印拟合结果，便于与真值对比

# 绘制训练损失下降曲线并保存图片
plt.figure(figsize=(6, 4))  # 设定图像尺寸
plt.plot(epoch_losses, label="Train MSE")  # 绘制每个 epoch 的平均损失
plt.xlabel("Epoch")  # 横轴：训练轮数
plt.ylabel("Loss")  # 纵轴：均方误差
plt.title("Training Loss")  # 标题
plt.grid(True)  # 开启网格，便于阅读
plt.legend()  # 图例显示
plt.tight_layout()  # 自动布局，防止标签遮挡
plt.savefig("linear_regression_training_loss.png")  # 保存为 PNG 文件