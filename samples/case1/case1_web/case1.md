# 案例1：智能人脸识别打卡机
---
## 项目简介

本项目旨在利用昇腾310B的强大AI算力，构建一个功能完整、响应迅速的智能人脸识别打卡系统。系统通过USB摄像头实时捕捉视频流，检测画面中的人脸，并与预先注册的员工/学生人脸数据库进行比对，完成身份验证和自动记录考勤。

该项目不仅是一个功能性的应用，更是一个端到端的AI实践案例，涵盖了从硬件选型、软件环境搭建、数据准备、模型训练与优化，到最终在边缘设备上部署的全过程。

## 内容大纲

### 硬件准备

- **核心计算单元**: 昇腾310B开发者套件
- **图像采集**: USB摄像头 (推荐罗技C920或同等规格)
- **显示设备 (可选)**: HDMI显示器，用于实时预览或UI展示
- **外设**: 键盘、鼠标
- **电源**: 为昇腾310B开发板提供稳定供电
- **连接线**: HDMI线, USB-C数据线等

*附：硬件连接示意图*
> (此处可插入一张图片，清晰展示所有硬件的连接方式)

### 软件环境

- **操作系统**: Ubuntu 20.04 或 openEuler（Windows 10/11 亦可运行Web版）
- **CANN版本**: 7.0或更高
- **Python版本**: 3.8.x
- **主要依赖库**:
    - `opencv-python`: 用于图像和视频处理
    - `numpy`: 用于数值计算
    - `scikit-learn`: 用于评估模型性能
    - `onnxruntime`: 用于运行ONNX模型
    - `PyQt5` (可选): 用于构建图形用户界面
    - `Flask` + `Bootstrap` (可选): 提供Web界面与交互
    - `Font Awesome` (可选): 丰富界面图标


> 提示：在Windows环境中，安装完成后可直接运行 `python app.py` 启动Web界面。

### 数据集准备

- **数据集来源**:
    1.  **公开数据集**: 如LFW (Labeled Faces in the Wild)、CASIA-WebFace等。
    2.  **自建数据集**: 推荐！使用摄像头为每位用户（员工/学生）拍摄多张、多角度、不同光照和表情的人脸照片。
- **数据组织结构**:
  ```
  datasets/
  ├── zhang_san/
  │   ├── 001.jpg
  │   ├── 002.jpg
  │   └── ...
  ├── li_si/
  │   ├── 001.jpg
  │   ├── 002.jpg
  │   └── ...
  └── ...
  ```
- **采集与注册方式**：
    - 脚本版：运行 `python register_face.py`，按提示录入姓名并采集多张人脸。
    - Web版：运行 `python web_register.py`，在浏览器完成拍摄与提交。

### 模型训练

- **模型选择**:
    - **人脸检测**: MTCNN 或 RetinaFace
    - **人脸识别**: ArcFace, CosFace, 或 MobileFaceNet (推荐，因其轻量高效)
- **训练流程**:
    1.  使用预处理好的数据集进行模型训练。
    2.  调整超参数（学习率、批大小等）以获得最佳性能。
    3.  在验证集上评估模型准确率、召回率等指标。
- **模型导出**: 将训练好的PyTorch或TensorFlow模型转换为昇腾亲和的ONNX格式。

### 模型部署

- **模型转换**: 使用ATC (Ascend Tensor Compiler) 工具将ONNX模型转换为昇腾310B支持的`.om`离线模型。
  ```bash
  atc --model=./models/mobilefacenet.onnx --framework=5 --output=./models/mobilefacenet --input_format=NCHW --input_shape="data:1,3,112,112" --soc_version=Ascend310B1
  ```
- **部署代码 (`main.py`)**:
    1.  初始化CANN和ACL (Ascend Computing Language) 资源。
    2.  加载`.om`离线模型。
    3.  循环读取摄像头帧。
    4.  对每一帧进行人脸检测和识别推理。
    5.  将识别结果与数据库比对，输出姓名。
    6.  在画面上绘制矩形框和姓名，并记录打卡时间。

#### Web应用（可选）
- 依赖安装：`pip install -r requirements.txt`
- 启动：`python app.py`
- 访问：浏览器打开 `http://localhost:5000`
- 主要页面与接口：
  - 页面：
    - `/` 首页
    - `/recognize` 图片识别
    - `/camera_live` 摄像头实时识别
    - `/camera_register` 摄像头注册
    - `/user_management` 用户管理
    - `/attendance` 考勤记录
    - `/attendance_config_page` 考勤配置页面
  - API：
    - `POST /start_camera` 启动摄像头
    - `POST /stop_camera` 停止摄像头
    - `GET /video_feed` 视频流
    - `GET /get_recognition_results` 获取最新识别结果
    - `GET /get_users` 获取用户列表
    - `GET/POST /attendance_config` 获取/更新考勤配置



### 用户手册

1.  **硬件组装**: 参照`2.1`节的连接图连接好所有硬件。
2.  **环境配置**: 在项目根目录执行 `pip install -r requirements.txt` 安装依赖。
3.  **人脸注册**: 
    - 脚本版：运行 `python register_face.py`，根据提示输入姓名并采集人脸图片。
    - Web版：运行 `python web_register.py`，在浏览器中完成拍摄与提交。
4.  **启动系统**: 运行`main.py`（脚本版）或运行`app.py`（Web版）启动人脸识别打卡程序。
5.  **查看记录与配置**: 打卡记录保存在项目根目录的`attendance.csv`；考勤配置保存在`attendance_config.json`，可在“考勤配置”页面修改再次打卡间隔（对应`ATTENDANCE_INTERVAL_HOURS`）。

## 数据与文件存储位置

- 用户人脸数据：`datasets/<姓名>/`（每个用户一个文件夹，存放多张人脸图片）
- 临时上传目录：`uploads/`（Web版自动创建）
- 考勤记录：`attendance.csv`（项目根目录）
- 考勤配置：`attendance_config.json`（项目根目录）

> 说明：上述文件名与路径由`app.py`中的常量`ATTENDANCE_FILE`与`ATTENDANCE_CONFIG_FILE`控制，可按需调整。

## 配置与参数

- `FACE_RECOGNITION_MODEL_PATH`: 识别模型路径（默认 `models/mobilefacenet.om`）
- `DATASET_PATH`: 数据集目录（默认 `datasets`）
- `SIMILARITY_THRESHOLD`: 识别相似度阈值（默认 `0.7`）
- `ATTENDANCE_INTERVAL_HOURS`: 再次打卡间隔小时数（默认 `8`）
- `UPLOAD_FOLDER`: 上传目录（默认 `uploads`）
- `MAX_CONTENT_LENGTH`: 上传大小限制（默认 `16MB`）

> 提示：阈值过低可能导致误识别，过高可能导致漏识别，可结合实际数据调整。

## 源代码

> (此处未来可替换为GitHub仓库链接或详细的文件树)

### 模型与转换
- 预训练权重下载：`python models/download_mobilefacenet.py`
- PyTorch转ONNX：`python models/convert_model.py`（读取 `models/mobilefacenet.pt` 输出 `models/mobilefacenet.onnx`）
- ONNX转OM（示例）：
  ```bash
  atc --model=./models/mobilefacenet.onnx \
      --framework=5 \
      --output=./models/mobilefacenet \
      --input_format=NCHW \
      --input_shape="data:1,3,112,112" \
      --soc_version=Ascend310B1
  ```
> 注意：`main.py` 与 `app.py` 默认使用 `models/mobilefacenet.om`，如不存在需先完成转换。

## 效果演示

> (此处可插入系统运行时的截图或GIF动图，例如摄像头实时识别人脸的画面)

## 故障排除

- 摄像头无法启动：检查设备连接与占用情况，尝试重启应用；确认访问 `/start_camera` 返回成功。
- 模型加载失败：确认 `models/mobilefacenet.om` 是否存在且可读；检查 CANN/ACL 环境变量与驱动安装。
- 识别效果不佳：提高图像质量与采集数量；适当调整 `SIMILARITY_THRESHOLD`；保证注册图片为正脸、清晰光照。
- 无法打卡或间隔提示：查看 `attendance_config.json` 是否存在，或在“考勤配置”页面重设 `ATTENDANCE_INTERVAL_HOURS`。

## 性能建议
- 将摄像头分辨率设置为 `640x480`（已在代码中默认设置）以降低处理压力。
- 在 Web 识别中使用识别冷却时间（代码默认 `2s`）减少重复计算与抖动。
- 数据集每人建议采集 10+ 张不同角度与光照的人脸图片提升鲁棒性。