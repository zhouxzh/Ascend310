# Ascend Face Attendance System

基于华为 Ascend 310B NPU 的高性能人脸识别考勤系统。支持本地摄像头自动打卡、网页端手动打卡、用户管理等功能。

## 功能特性

*   **高性能推理**: 利用 Ascend 310B NPU (ACL) 加速人脸检测 (RetinaFace) 和人脸识别 (ArcFace)。
*   **双模式打卡**:
    *   **设备端自动打卡**: 后台实时分析本地 USB 摄像头画面，自动识别并记录考勤。
    *   **网页端手动打卡**: 支持浏览器摄像头拍照或上传图片进行打卡。
*   **用户管理**: 支持照片注册（上传或摄像头抓拍）、用户列表查询、删除。
*   **实时反馈**: 考勤成功后提供 UI 动画反馈。
*   **数据存储**: 使用 SQLite 本地存储用户特征值和考勤记录。

## 技术栈

*   **硬件**: Huawei Ascend 310B (OrangePi AIpro 等)
*   **后端**: Python Flask, PyACL (Ascend Computing Language), SQLite, OpenCV
*   **前端**: HTML5, Bootstrap 5, JavaScript
*   **模型**: RetinaFace (Detection), ArcFace (Recognition) -> 转换为 OM 格式

## 目录结构

```
.
├── app.py                 # Flask 主程序
├── ascend_inference.py    # Ascend NPU 推理引擎封装
├── camera.py              # 本地摄像头管理与自动打卡逻辑
├── database.py            # SQLite 数据库操作
├── prepare_models.py      # 模型下载与转换脚本
├── models/                # 存放 ONNX 和 OM 模型
├── static/                # 静态资源
├── templates/             # HTML 模板
├── uploads/               # 临时图片存储
└── README.md              # 说明文档
```

## 快速开始

### 1. 环境准备

确保已安装 Ascend CANN Toolkit，并配置好环境变量。

```bash
# 检查 NPU 状态
npu-smi info

# 检查 ATC 工具
which atc
```

安装 Python 依赖：

```bash
pip install flask opencv-python-headless numpy<2.0 requests
# 注意：Ascend 环境通常已预装 acl 库，无需 pip 安装
```

### 2. 模型准备

运行脚本自动下载并转换模型（RetinaFace & ArcFace）：

```bash
python3 prepare_models.py
```
*该脚本会自动下载 `buffalo_s.zip`，解压并使用 `atc` 命令转换为 `.om` 格式。*

### 3. 摄像头权限

确保当前用户有权限访问 USB 摄像头 (`/dev/video0`)：

```bash
sudo chmod 666 /dev/video0
# 或者将用户加入 video 组
sudo usermod -aG video $USER
```

### 4. 启动系统

```bash
python3 app.py
```

启动成功后，访问：http://127.0.0.1:5000

## 使用说明

1.  **用户注册**:
    *   进入 "User Management" 页面。
    *   选择 "Upload Photo" 上传照片，或 "Device Camera" 使用本地摄像头抓拍。
    *   输入姓名并提交。

2.  **考勤打卡**:
    *   **自动**: 只要您在本地摄像头前，系统每2秒检测一次，识别成功自动记录。
    *   **手动**: 进入 "Attendance Log"，点击 "Manual Check-in" 使用浏览器摄像头打卡。

3.  **查看记录**:
    *   "Attendance Log" 页面会实时刷新显示最新的考勤记录。

## 常见问题

*   **摄像头无法打开**: 请检查 `/dev/video0` 权限，或确认摄像头未被其他程序占用。
*   **模型转换失败**: 请检查 `atc` 命令是否可用，以及内存是否充足。脚本已启用 `TE_PARALLEL_COMPILER=1` 优化内存。
*   **ImportError: numpy.float_**: 请确保安装了兼容的 numpy 版本 (`pip install "numpy<2.0"`).
