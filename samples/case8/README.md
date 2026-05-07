# 案例 8：手势识别

基于昇腾 310B 的实时手势识别系统，使用 MobileNetV3-Small 对 10 种静态手势进行分类。

## 项目说明

本项目演示**训练 → 转换 → 部署**的完整边缘 AI 工作流：

| 阶段 | 位置 | 操作 |
| :--- | :--- | :--- |
| 1 | 工作站 (GPU/CPU) | 在 HaGRID 数据集上训练 MobileNetV3-Small |
| 2 | 工作站 | 导出 ONNX，ATC 转换为 OM 离线模型 |
| 3 | Ascend 310B NPU | 实时 OM 推理 + Gradio Web 界面 |

## 目录结构

```text
case8/
├── app.py                 # Gradio Web 界面入口
├── ascend_inference.py    # Ascend NPU 推理封装 + CPU 回退
├── config.py              # 配置常量
├── train.py               # 模型训练脚本
├── prepare_models.py      # ONNX 导出 & OM 转换
├── setup.sh               # 一键环境安装
├── requirements.txt       # Python 依赖
├── data/
│   └── gesture_labels.json  # 手势类别映射
├── models/                   # 模型文件目录
└── README.md
```

## 快速开始

### 1. 环境安装

```bash
bash setup.sh
```

### 2. 准备模型

```bash
# 方式 A：下载预训练模型（推荐，跳过训练）
python3 prepare_models.py --download

# 方式 B：自行训练（需要 HaGRID 数据集）
python3 train.py
python3 prepare_models.py
```

### 3. 启动服务

```bash
python3 app.py
```

打开 http://127.0.0.1:7860，在浏览器中授权摄像头即可使用。

### 4. 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接
```

## 手势列表

| 手势 | 中文 | 置信度阈值 |
| :--- | :--- | :--- |
| 👍 like | 点赞 | ≥70% |
| 👎 dislike | 不喜欢 | ≥70% |
| ✊ fist | 握拳 | ≥70% |
| ✋ palm | 手掌 | ≥70% |
| ✌️ peace | 剪刀手 | ≥70% |
| 👌 ok | OK | ≥70% |
| 🤘 rock | 摇滚 | ≥70% |
| 📞 call | 打电话 | ≥70% |
| 🛑 stop | 停止 | ≥70% |
| 🫥 no_gesture | 无手势 | ≥70% |

## 运行模式

### NPU 模式（默认）

在昇腾 310B 设备上自动检测并使用 OM 模型推理。

### CPU 回退

没有 Ascend 310B 时自动回退到 PyTorch MobileNetV3-Small，功能不受影响。

## 依赖说明

| 包 | 用途 | 必需？ |
| :--- | :--- | :--- |
| gradio | Web 界面 + 摄像头组件 | ✓ |
| torch + torchvision | 模型定义 / CPU 推理 | ✓ |
| opencv-python | 图像预处理 | ✓ |
| numpy | 数值计算 | ✓ |
| Pillow | 训练数据加载 | 仅 train.py |
| acl (CANN) | NPU 推理 | 仅昇腾设备 |

## 使用建议

1. 先以 CPU 模式启动，体验手势识别功能
2. 阅读 [config.py](config.py) 了解可调整的参数
3. 在设置面板中调整置信度阈值以平衡灵敏度和误识别
4. 用手势物体（或打印图片）测试不同手势的识别效果
5. 在昇腾设备上运行，观察 NPU 推理加速效果
6. 使用 `python3 train.py` 训练自己的专属手势模型
