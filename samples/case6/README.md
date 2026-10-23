# 案例 6：智能小车

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

### 环境安装

```bash
bash setup.sh
```

`setup.sh` 只使用已有的 conda `ascend` Python 3.9 环境，并设置
`PYTHONNOUSERSITE=1`；它不执行 `sudo apt`、不回退 base，也不使用
`pip --user`。系统级 OpenCV/PortAudio 等依赖由操作者按开发板镜像预先安装。

### 准备模型

setup.sh 已自动执行此步骤。如需手动重做：

```bash
# ONNX 导出 (可在开发机上运行)
python3 prepare_models.py --onnx-only

# ONNX → OM 转换 (需在昇腾设备上运行)
python3 prepare_models.py
```

脚本在 8T 上默认设置 `TE_PARALLEL_COMPILER=1`，避免 ATC 并行编译子进程
耗尽内存后触发 OOM killer。需要提高并行度时，应由操作者显式覆盖该环境变量。

### 启动服务

```bash
python3 app.py
```

打开 http://127.0.0.1:7860，上传道路图像查看车道线和场景分类结果。

### 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接；无认证，仅限受控实验
```

## 运行模式

### NPU 模式

只有在 `models/resnet18_scene.om` 存在、`import acl` 成功且 ACL/OM 初始化通过时，场景分类才使用 NPU。任何一步失败都会回退 CPU；页面“系统信息”会显示实际后端。

### CPU 回退

没有可用的 Ascend 运行时或 OM 时自动回退到 PyTorch ResNet18。若没有经过训练的 PTH，当前脚本会使用 ImageNet backbone 加随机初始化的 5 类 head，分类结果没有质量意义。车道线检测始终在 CPU 上运行（纯 OpenCV）。

## 工程文档

完整的架构、流水线、模型转换、页面操作、验证限制和问题台账见 [docs/README.md](docs/README.md)。

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
