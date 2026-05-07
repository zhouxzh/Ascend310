# 案例 6：智能小车视觉感知

基于昇腾 310B 的智能小车视觉感知系统，结合**经典 CV 车道线检测**与
**ResNet18 驾驶场景分类**，为自动驾驶提供道路理解能力。

## 项目说明

本项目演示**经典 CV + 深度学习**的混合感知工作流：

| 阶段 | 位置 | 操作 |
| :--- | :--- | :--- |
| 1 | 工作站 / 昇腾设备 | 导出 ResNet18 场景分类模型 (ONNX → OM) |
| 2 | 昇腾 310B NPU / CPU | 驾驶场景分类推理 |
| 3 | CPU | 经典 CV 车道线检测 (OpenCV) |
| 4 | 浏览器 | Gradio Web 界面展示感知结果 |

## 目录结构

```text
case6/
├── app.py                 # Gradio Web 界面入口
├── lane_detector.py        # 经典 CV 车道线检测 (OpenCV)
├── scene_classifier.py     # NPU/CPU 双后端场景分类 (ResNet18)
├── config.py               # 配置常量
├── prepare_models.py       # ONNX 导出 & OM 转换
├── setup.sh                # 一键环境安装
├── requirements.txt        # Python 依赖
├── data/                   # 运行时数据目录
├── models/                 # 模型文件目录
└── README.md
```

## 快速开始

### 1. 环境安装

```bash
bash setup.sh
```

### 2. 准备模型

setup.sh 已自动执行此步骤。如需手动重做：

```bash
# ONNX 导出 (可在开发机上运行)
python3 prepare_models.py --onnx-only

# ONNX → OM 转换 (需在昇腾设备上运行)
python3 prepare_models.py
```

### 3. 启动服务

```bash
python3 app.py
```

打开 http://127.0.0.1:7860，上传道路图像查看车道线和场景分类结果。

### 4. 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接
```

## 运行模式

### NPU 模式（默认）

在昇腾 310B 设备上自动检测并使用 OM 模型进行场景分类推理。

### CPU 回退

没有 Ascend 310B 时自动回退到 PyTorch ResNet18。车道线检测始终在 CPU
上运行（纯 OpenCV）。

## 依赖说明

| 包 | 用途 | 必需？ |
| :--- | :--- | :--- |
| gradio | Web 界面 | ✓ |
| torch + torchvision | ResNet18 模型 / CPU 推理 | ✓ |
| opencv-python | 车道线检测 + 图像预处理 | ✓ |
| numpy | 数值计算 | ✓ |
| onnx | ONNX 模型导出 | 仅 prepare_models.py |
| acl (CANN) | NPU 推理 | 仅昇腾设备 |

## 使用建议

1. 先以 CPU 模式启动，体验车道线检测和场景分类
2. 使用清晰包含车道线的道路图像进行测试
3. 在「道路感知」页签上传图像，查看车道线叠加和场景标签
4. 在「系统信息」页签查看模型状态和检测参数
5. 不同场景（高速、城市、交叉口）测试场景分类效果
6. 在昇腾设备上运行，体验 NPU 加速效果
