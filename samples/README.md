# samples 配套代码说明

本目录存放书稿章节和实践案例的配套代码、脚本、模型准备工具与工程资产。

- `chapter*` 目录对应 `src/book/` 下的理论章节。
- `case*` 目录对应 `src/experiment/` 下的实践案例。
- 每个案例目录中的 `README.md` 提供更详细的运行步骤、依赖说明和文件解释。
- 涉及 CANN、PyACL、ATC、OM 推理、DVPP、NPU 硬件能力的脚本需要在真实 Ascend 310B 设备上运行；普通开发机适合阅读代码、编辑文档、做语法检查或运行不依赖昇腾硬件的 CPU 路径。

## 章节配套代码

| 目录 | 配套内容 | 说明 |
|---|---|---|
| [`chapter2/`](chapter2/) | 第 2 章 CANN 异构计算架构与 ATC 模型转换 | 包含 ResNet50 图片分类快速入门样例，展示输入预处理、模型文件、推理输出与后处理的基础流程。 |
| [`chapter3/`](chapter3/) | 第 3 章 PyTorch 与 `torch_npu` 迁移训练 | 包含线性回归、加州房价预测、LeNet、AlexNet、ResNet、VGG 等训练/对比样例，以及 NPU 算子兼容性测试。 |
| [`chapter4/`](chapter4/) | 第 4 章 PyACL 应用开发与模型推理 | 包含 ACL 环境验证、ResNet18 推理、SSD300 目标检测、MobileNet-SSDLite320 推理评估与 ONNX 到 OM 转换辅助脚本。 |
| [`chapter5/`](chapter5/) | 第 5 章 DVPP 媒体处理 | 包含 VENC、VDEC、VPC、JPEG 硬件编解码最小样例和性能测试，并提供 WebRTC 推流综合案例。 |
| [`chapter6/`](chapter6/) | 第 6 章 自定义算子开发 | 包含 TBE DSL 向量加法、Ascend C 向量加法、带 Tiling 的矩阵加法样例，用于理解算子工程结构和运行流程。 |
| [`chapter7/`](chapter7/) | 第 7 章 性能分析与优化 | 包含 ResNet18 推理分段计时、ACL Buffer 复用、CPU 预处理、DVPP VPC resize、Queue Pipeline 和 msprof 采集样例。 |

## 章节目录细分

| 目录 | 内容 |
|---|---|
| [`chapter2/sample_resnet_quick_start/`](chapter2/sample_resnet_quick_start/) | AscendCL ResNet50 图片分类快速上手工程，含 `src/` 源码、`model/` 模型目录、`data/` 测试数据目录和推理输出目录。 |
| [`chapter3/linear_regression/`](chapter3/linear_regression/) | 线性回归训练样例，提供 CPU 与 NPU 版本以及训练损失曲线。 |
| [`chapter3/california_housing/`](chapter3/california_housing/) | 加州房价回归预测样例，包含线性网络和 MLP 版本。 |
| [`chapter3/LeNet/`](chapter3/LeNet/) | MNIST 手写数字识别样例，包含 LeNet NPU 训练脚本和训练曲线。 |
| [`chapter3/AlexNet/`](chapter3/AlexNet/) | AlexNet 在 CUDA/NPU、FP16/FP32 路径上的训练与测试样例。 |
| [`chapter3/ResNet/`](chapter3/ResNet/) | ResNet 训练对比样例，包含 CUDA 与 NPU 版本及训练曲线。 |
| [`chapter3/VGG/`](chapter3/VGG/) | VGG 训练对比样例，包含 CUDA 与 NPU 版本及训练曲线。 |
| [`chapter3/test/`](chapter3/test/) | `torch_npu` 算子和网络层兼容性测试。 |
| [`chapter4/check_ascend_device/`](chapter4/check_ascend_device/) | PyACL 初始化、设备查询和去初始化的最小环境验证脚本。 |
| [`chapter4/resnet18/`](chapter4/resnet18/) | ResNet18 Tiny-ImageNet 训练、CPU 推理和 PyACL NPU 推理样例。 |
| [`chapter4/SSD/`](chapter4/SSD/) | SSD300 目标检测样例，包含训练、CPU/CUDA/NPU 推理和模型工具。 |
| [`chapter4/SSDLite/`](chapter4/SSDLite/) | MobileNet-SSDLite320 部署样例，包含模型下载、ONNX Runtime CPU 校验、PyACL NPU 推理、COCO 评估和可视化报告。 |
| [`chapter5/venc/`](chapter5/venc/) | DVPP VENC 硬件视频编码最小样例和性能测试。 |
| [`chapter5/vdec/`](chapter5/vdec/) | DVPP VDEC 硬件视频解码最小样例和性能测试。 |
| [`chapter5/vpc/`](chapter5/vpc/) | DVPP VPC 图像处理样例，覆盖 resize、crop+resize 和 CPU 对比测试。 |
| [`chapter5/jpeg/`](chapter5/jpeg/) | DVPP JPEG 编解码闭环验证样例。 |
| [`chapter5/WebRTC/`](chapter5/WebRTC/) | 基于 aiortc 的 WebRTC 视频发送端综合案例，集成 V4L2 采集、DVPP JPEGD、CANN VENC、H.264/H.265 与浏览器接收页面。 |
| [`chapter6/add_tbe/`](chapter6/add_tbe/) | TBE DSL 向量加法算子样例。 |
| [`chapter6/add_ascendc/`](chapter6/add_ascendc/) | Ascend C 向量加法算子样例。 |
| [`chapter6/mat_add_tiling/`](chapter6/mat_add_tiling/) | 带 Tiling 的矩阵加法 Ascend C 算子样例。 |
| [`chapter7/`](chapter7/) | 性能优化案例教学样例，覆盖本地 CPU/模拟测试和 Ascend 310B 硬件 Profiling。 |

## 实践案例配套代码

| 目录 | 配套案例 | 说明 |
|---|---|---|
| [`case1/`](case1/) | 案例 1：智能人脸识别考勤系统 | Flask Web 服务、摄像头采集、SQLite 考勤数据库、RetinaFace/ArcFace 推理封装和模型准备脚本。 |
| [`case2/`](case2/) | 案例 2：目标检测与多目标跟踪 | MobileNet-SSD/SSDLite 检测、DeepSORT 跟踪、OpenCV 视频输入输出、CPU/NPU 后端和模型转换脚本。 |
| [`case3/`](case3/) | 案例 3：智能电子琴 | MIDI 键盘程序和电子琴结构件 CAD/STEP/STL 文件，配合手势交互与音乐输出实验。 |
| [`case4/`](case4/) | 案例 4：智能掌纹识别机 | 掌纹 ROI 预处理、GhostNet 特征提取、FAISS 向量检索、Gradio 界面、训练与模型转换脚本。 |
| [`case5/`](case5/) | 案例 5：智能数据采集仪 | STM32 低速传感、FPGA 振动数据、频谱特征处理、故障分类、异常检测、数据记录和 Gradio 展示。 |
| [`case6/`](case6/) | 案例 6：智能小车视觉感知 | OpenCV 车道线检测、ResNet18 驾驶场景分类、CPU/NPU 双后端和 Gradio Web 界面。 |
| [`case7/`](case7/) | 案例 7：智能相册 | ResNet50 图像特征提取、FAISS 相似检索、人脸计数、照片索引管理和 Gradio 浏览界面。 |
| [`case8/`](case8/) | 案例 8：手势识别 | MobileNetV3-Small 静态手势分类，包含训练、ONNX/OM 准备、Ascend 推理封装和实时 Web 界面。 |
| [`case9/`](case9/) | 案例 9：边缘智能聊天机器人 | 文本嵌入模型推理、FAISS RAG 知识库、对话管理、语音输入输出和可选 OpenAI 兼容 LLM 接口。 |

## 常见文件含义

| 文件或目录 | 说明 |
|---|---|
| `README.md` | 当前样例或案例的详细说明。 |
| `app.py` | Web 或 Gradio 应用入口。 |
| `prepare_models.py` | 模型下载、ONNX 导出或 ONNX 到 OM 转换辅助脚本；涉及 ATC 的步骤应在 Ascend 设备上执行。 |
| `ascend_inference.py`、`*_backend.py` | AscendCL/PyACL 推理封装或不同推理后端实现。 |
| `config.py` | 路径、模型名、类别、阈值等配置。 |
| `requirements.txt`、`setup.sh` | Python 依赖和环境安装脚本。 |
| `models/`、`weights/` | 模型文件目录，通常存放 `.pth`、`.onnx`、`.om` 等文件。 |
| `data/`、`outputs/`、`reports/` | 测试数据、运行输出、评估报告或可视化结果。 |
