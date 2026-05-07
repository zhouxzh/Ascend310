# 案例 7：智能相册

基于昇腾 310B 的智能照片管理和检索系统，使用 ResNet50 提取图像特征，FAISS 进行向量相似度搜索，OpenCV 进行人脸计数。

## 项目说明

本项目演示**批量照片索引 + 向量检索**的边缘 AI 工作流：

| 阶段 | 位置 | 操作 |
| :--- | :--- | :--- |
| 1 | 工作站 / 昇腾设备 | 导出 ResNet50 特征提取模型 (ONNX → OM) |
| 2 | 昇腾 310B NPU / CPU | 批量提取照片特征向量，构建 FAISS 索引 |
| 3 | 浏览器 | Gradio Web 界面浏览、搜索、管理照片 |

## 目录结构

```text
case7/
├── app.py                 # Gradio Web 界面入口
├── feature_extractor.py   # NPU/CPU 双后端特征提取
├── photo_index.py         # FAISS 索引管理 + 人脸检测
├── config.py              # 配置常量
├── prepare_models.py      # ONNX 导出 & OM 转换
├── setup.sh               # 一键环境安装
├── requirements.txt       # Python 依赖
├── data/                  # FAISS 索引 + 元数据
├── models/                # 模型文件目录
├── photos/                # 默认照片目录 (可自定义)
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

打开 http://127.0.0.1:7860，在「管理」页签中索引照片文件夹，然后浏览和搜索。

### 4. 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接
```

## 运行模式

### NPU 模式（默认）

在昇腾 310B 设备上自动检测并使用 OM 模型推理，批量索引时可加速。

### CPU 回退

没有 Ascend 310B 时自动回退到 PyTorch ResNet50，功能不受影响。

## 依赖说明

| 包 | 用途 | 必需？ |
| :--- | :--- | :--- |
| gradio | Web 界面 + 照片画廊组件 | ✓ |
| torch + torchvision | ResNet50 模型 / CPU 推理 | ✓ |
| opencv-python | 图像预处理 + 人脸检测 | ✓ |
| numpy | 数值计算 | ✓ |
| faiss-cpu | 向量相似度搜索 | ✓ |
| onnx | ONNX 模型导出 | 仅 prepare_models.py |
| acl (CANN) | NPU 推理 | 仅昇腾设备 |

## 使用建议

1. 先以 CPU 模式启动，体验照片索引和搜索功能
2. 准备一个有 50-200 张照片的文件夹用于测试
3. 在「管理」页签中索引照片，观察进度条
4. 在「浏览」页签中按人脸筛选照片
5. 在「搜索」页签上传照片，体验相似搜索效果
6. 在昇腾设备上运行，对比 NPU 加速效果
