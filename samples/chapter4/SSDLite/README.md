# MobileNet-SSDLite320 (PyTorch)

本项目是一个基于 `timm MobileNet` 主干网络的 `SSDLite320` 目标检测实现，训练与验证流程面向 **多卡 GPU** 场景做了优化。

## 1. 当前实现概览

### 模型
- 输入尺寸：`320 x 320`
- 检测头：`SSDLite` 风格，使用 `Depthwise 3x3 + Pointwise 1x1`
- 激活函数：`ReLU6`
- 归一化：`BatchNorm2d(eps=0.001, momentum=0.03)`
- 额外特征层：`1x1 -> depthwise 3x3(stride=2) -> 1x1`
- 初始化：卷积层使用 `normal_(mean=0, std=0.03)`

对应代码：`ssd/model.py`

### 先验框（Default Boxes）
- 特征层尺寸：`[20, 10, 5, 3, 2, 1]`
- 宽高比：每层 `[[2, 3]]`
- scale 范围：`min_ratio=0.2`, `max_ratio=0.95`
- 生成方式：与 torchvision `DefaultBoxGenerator` 的 ratio/scale 思路对齐

对应代码：`ssd/utils.py` 中 `dboxes320_coco()`

### 训练策略（当前默认）
- 优化器：`SGD(momentum=0.9)` + Tencent trick（BN 和 bias 不做 weight decay）
- 学习率调度：`CosineAnnealingLR`
- 混合精度：`torch.amp`（GPU 自动启用）
- Warmup：默认 `100` iter（可设为 0 关闭）
- 冻结策略：前 `2` 个 epoch 冻结 backbone，仅训练检测头
- 早停：`patience=10`，`min_delta=1e-4`
- 每轮验证：默认 `eval_interval=1`
- 保存策略：
  - 每轮保存：`checkpoints/ssd_{backbone}_{epoch}.pth`
  - 最优保存：`checkpoints/ssd_{backbone}_best.pth`

对应代码：`ssd/train.py`

---

## 2. 环境要求

推荐使用你已创建的 conda 环境：`torch`。

### 激活环境
```bash
conda activate torch
```

### 安装依赖
```bash
pip install -r requirements.txt
```

### VS Code 解释器
项目已配置：`.vscode/settings.json`
- `python.defaultInterpreterPath=/home/zhong/anaconda3/envs/torch/bin/python`

---

## 3. 训练命令

训练入口：`train_cuda.py`

### 默认训练（单卡）
```bash
python train_cuda.py --device cuda --backbone mobilenetv3
```

### 从最新 checkpoint 续训
```bash
python train_cuda.py --device cuda --backbone mobilenetv3 --restart
```

### 快速 smoke test（先跑 10 轮）
```bash
python train_cuda.py --device cuda --backbone mobilenetv3 --epochs 10 --patience 3
```

---

## 4. 当前默认参数（train_cuda.py）

- `--batch-size 32`
- `--epochs 50`
- `--lr 1.5e-3`
- `--momentum 0.9`
- `--weight-decay 4e-5`
- `--num-workers 4`
- `--warmup-iters 100`
- `--freeze-backbone-epochs 2`
- `--patience 10`
- `--min-delta 1e-4`
- `--eval-interval 1`
- `--num-classes 81`（COCO 80 类 + background）
- `--pretrained-backbone` 默认开启（使用 timm 预训练权重）
- `--backbone mobilenetv3`

---

## 参数推荐表（单卡，RTX 5090D 32GB）

| 场景 | 建议参数 | 说明 |
|---|---|---|
| 大显存高吞吐（推荐） | `--batch-size 64 --epochs 40 --lr 2.5e-3 --warmup-iters 100 --freeze-backbone-epochs 1 --patience 8 --num-workers 8` | 32GB 显存优先吃满吞吐，通常训练更快 |
| 稳定均衡（建议起点） | `--batch-size 48 --epochs 50 --lr 2.0e-3 --warmup-iters 100 --freeze-backbone-epochs 2 --patience 10 --num-workers 8` | 稳定性与速度平衡，适合作为默认正式训练 |
| 快速验证配置（先排错） | `--batch-size 32 --epochs 10 --lr 1.5e-3 --warmup-iters 50 --freeze-backbone-epochs 1 --patience 3 --eval-interval 1 --num-workers 4` | 用于快速验证训练链路与指标走势 |

推荐先跑“快速验证配置”，确认 loss 与 mAP 正常后，再切“稳定均衡”或“高吞吐”做正式训练。

示例命令（大显存高吞吐）：
```bash
python train_cuda.py --device cuda --backbone mobilenetv3 --batch-size 64 --epochs 40 --lr 2.5e-3 --warmup-iters 100 --freeze-backbone-epochs 1 --patience 8 --num-workers 8
```

## 5. 导出 ONNX

训练结束后会自动导出：
- `models/ssd_{backbone}.onnx`

也可手动调用 `ssd/train.py` 中 `export_onnx_model()`。

---

## 6. 常见问题

### Q1: 想更快收敛，怎么调？
- 保持 `--pretrained-backbone` 开启
- 可把 `--epochs` 先设成 `30` 做第一轮
- 数据量较小时可将 `--warmup-iters` 降到 `50` 或关闭

### Q2: 显存不足？
- 先降 `--batch-size` 到 `24` 或 `16`
- 保持 AMP（默认 GPU 已启用）

### Q3: 如何查看训练效果？
- TensorBoard 日志目录：`logs/{backbone}`
- 验证可视化结果：`viz_results/epoch_x/`

---

## 7. 主要代码文件

- `train_cuda.py`：训练入口与参数
- `ssd/train.py`：训练/验证/早停/保存逻辑
- `ssd/model.py`：MobileNet + SSDLite320 模型定义
- `ssd/utils.py`：编码解码、IoU、Default Boxes
- `inference_cuda.py` / `inference_cpu.py`：推理脚本

