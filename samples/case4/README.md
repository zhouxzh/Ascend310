# 案例 4：智能掌纹识别机

基于昇腾 310B 的掌纹识别系统，使用 **GhostNet 1.0x** 进行深度特征提取，
**FAISS** 进行向量检索，支持掌纹注册与身份验证。

## 项目说明

本案例演示**开集生物特征验证**的完整工作流：

| 阶段 | 操作 | 硬件 |
| :--- | :--- | :--- |
| 1 | 掌纹 ROI 检测 + CLAHE 增强 | USB 摄像头 |
| 2 | GhostNet 1280 维特征提取 | Ascend 310B (NPU) / CPU |
| 3 | FAISS 余弦相似度检索 | CPU |
| 4 | 阈值判定 + 结果展示 | Gradio 仪表盘 |

## 目录结构

```text
case4/
├── app.py                 # Gradio 仪表盘入口
├── palm_preprocessor.py   # 掌纹 ROI 检测 + CLAHE 增强
├── palm_extractor.py      # GhostNet 特征提取 (NPU/CPU 双后端)
├── palm_index.py          # FAISS 注册 / 验证 / 管理
├── ghostnet.py            # GhostNet 1.0x 独立实现
├── train.py               # 对比学习 Siamese 训练
├── prepare_models.py      # ONNX 导出 + ATC OM 转换
├── config.py              # 配置常量
├── setup.sh               # 一键环境安装
├── requirements.txt       # Python 依赖
├── data/                  # 运行时数据 (FAISS 索引 + 元数据)
├── models/                # 模型文件 (.pth / .onnx / .om)
└── README.md
```

## 快速开始

### 1. 环境安装

```bash
bash setup.sh
```

### 2. 训练模型

需要掌纹数据集（如 PolyU、IITD），按以下结构组织：

```
PolyU/
  001/  img1.bmp  img2.bmp  ...
  002/  img1.bmp  img2.bmp  ...
  ...
```

```bash
python3 train.py --data-dir /path/to/PolyU
```

### 3. 准备模型

```bash
# ONNX 导出 (可在开发机上运行)
python3 prepare_models.py --onnx-only

# ONNX → OM 转换 (需在昇腾设备上运行)
python3 prepare_models.py
```

### 4. 启动服务

```bash
python3 app.py
```

打开 http://127.0.0.1:7860，使用摄像头进行掌纹注册和验证。

### 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接
```

## 运行模式

### NPU 模式（默认）

在昇腾 310B 设备上自动检测并使用 OM 模型进行掌纹特征提取。

### CPU 回退

没有 Ascend 310B 时自动回退到 PyTorch GhostNet。

## 与已有案例的区别

| | 案例4 | 案例7 | 案例8 |
|---|---|---|---|
| 模型 | GhostNet (独立实现) | ResNet50 (torchvision) | MobileNetV3-Small |
| 训练范式 | 对比学习 Siamese | 无训练 | 分类 CrossEntropy |
| 任务类型 | 开集验证 (1:1) | 开集检索 (1:N) | 闭集分类 (N选1) |

## 软件依赖

| 包 | 用途 | 必需？ |
| :--- | :--- | :--- |
| gradio | Web 仪表盘 | ✓ |
| torch + torchvision | GhostNet / CPU 推理 | ✓ |
| opencv-python | 掌纹预处理 + 摄像头采集 | ✓ |
| numpy | 数值计算 | ✓ |
| faiss-cpu | 向量检索 | ✓ |
| onnx | ONNX 模型校验 | 仅 prepare_models.py |
| acl (CANN) | NPU 推理 | 仅昇腾设备 |

## 硬件依赖

| 组件 | 用途 | 必需？ |
| :--- | :--- | :--- |
| USB 摄像头 (≥5MP) | 掌纹图像采集 | ✓ |
| 近红外 LED (可选) | 增强纹理对比度 | 可选 |
| Ascend 310B | AI 推理加速 | CPU 可回退 |
| 3D 打印外壳 (可选) | 手掌定位 + 遮光 | 可选 |

## 使用建议

1. 确保摄像头环境光线均匀，背景简洁（推荐深色背景）
2. 手掌自然张开，手指微微分开，放置在摄像头下方 15-20 cm 处
3. 注册时采集 3-5 张不同角度/位置的掌纹，提高验证准确率
4. 验证阈值默认为 0.75，可根据实际使用场景调整
5. 定期备份 `data/` 目录下的 FAISS 索引和元数据
6. 训练时确保数据集中每个用户至少 5 张掌纹图像
