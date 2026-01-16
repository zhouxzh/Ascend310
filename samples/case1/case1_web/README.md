# 智能人脸识别考勤系统

基于深度学习的实时人脸识别考勤系统，支持摄像头实时识别和Web界面管理，专为Ascend 310 NPU优化。

## 🌟 主要特性

- **实时人脸识别**: 基于USB摄像头的实时人脸检测和识别
- **Web管理界面**: 现代化的Web界面，支持用户注册、管理和考勤记录查看
- **智能考勤**: 自动记录考勤信息，支持可配置的重复打卡间隔
- **高精度算法**: 使用MTCNN进行人脸检测，MobileFaceNet进行特征提取
- **NPU加速**: 专为Ascend 310 NPU优化，提供高性能推理
- **多种注册方式**: 支持摄像头实时注册和图片上传注册

## 📋 系统要求

### 硬件要求
- Ascend 310B 开发板
- USB摄像头
- 至少4GB内存

### 软件要求
- **操作系统**: Ubuntu 20.04 或兼容版本
- **CANN工具包**: 版本7.0或更高
- **Python**: 版本3.8.x
- **浏览器**: Chrome、Firefox、Safari等现代浏览器

## 🚀 快速开始

### 1. 环境准备

1. **克隆项目**:
   ```bash
   git clone <项目地址>
   cd case1_camera
   ```

2. **安装Python依赖**:
   ```bash
   pip install -r requirements.txt
   ```

3. **准备模型文件**:
   
   确保以下模型文件存在：
   - `models/mobilefacenet.om` (Ascend NPU模型)
   - `models/mobilefacenet.onnx` (ONNX模型，备用)

   如果没有模型文件，请运行：
   ```bash
   python download_mobilefacenet.py
   python convert_model.py
   ```

### 2. 模型转换

人脸识别模型需要转换为`.om`格式才能在Ascend NPU上运行。使用Ascend张量编译器(ATC)进行转换：

```bash
atc --model=models/mobilefacenet.onnx \
    --framework=5 \
    --output=models/mobilefacenet \
    --input_format=NCHW \
    --input_shape="actual_input_1:1,3,112,112" \
    --soc_version=Ascend310 \
    --log=info
```

**参数说明**:
- `--model`: 输入ONNX模型路径
- `--framework`: 框架类型 (5表示ONNX)
- `--output`: 输出`.om`模型路径和名称
- `--input_format`: 输入数据格式
- `--input_shape`: 输入张量形状
- `--soc_version`: Ascend处理器版本
- `--log`: 日志级别

### 3. 启动Web服务

```bash
python app.py
```

服务启动后，在浏览器中访问: http://localhost:5000

## 📖 使用指南

### 人脸注册

#### 方法一：Web界面注册
1. 访问 "用户注册" 页面
2. 选择 "上传图片识别" 或 "摄像头识别"
3. 输入人员姓名
4. 上传3-5张不同角度的清晰人脸照片或使用摄像头拍摄
5. 点击 "注册人脸" 完成注册

#### 方法二：命令行注册
```bash
# 使用摄像头注册
python register_face.py

# 批量注册（从文件夹）
python web_register.py "张三" --folder /path/to/photos
```

### 实时人脸识别

1. 访问 "人脸识别" → "摄像头识别" 页面
2. 点击 "启动摄像头" 开始实时识别
3. 系统会自动检测和识别人脸
4. 识别成功的用户会显示姓名和考勤状态
5. 考勤信息会实时显示在页面上

### 考勤管理

#### 查看考勤记录
- 访问 "考勤记录" 页面查看所有打卡记录
- 显示统计信息：今日打卡人数、总打卡次数、注册人员数
- 支持按时间排序和筛选

#### 配置考勤参数
- 访问 "考勤配置" 页面
- 设置重复打卡间隔（0.1-24小时）
- 配置其他考勤参数

### 用户管理

- 访问 "用户管理" 页面
- 查看所有注册用户
- 删除不需要的用户
- 查看用户注册的人脸图片

## 🏗️ 项目结构

```
case1_camera/
├── app.py                    # Flask Web应用主文件
├── main.py                   # 命令行版本主程序
├── register_face.py          # 命令行人脸注册工具
├── web_register.py          # Web版人脸注册工具
├── convert_model.py         # 模型转换脚本
├── download_mobilefacenet.py # 模型下载脚本
├── templates/               # HTML模板文件
│   ├── base.html           # 基础模板
│   ├── index.html          # 主页
│   ├── camera_live.html    # 实时摄像头识别
│   ├── camera_register.html # 摄像头注册
│   ├── recognize.html      # 图片识别
│   ├── register.html       # 图片注册
│   ├── attendance.html     # 考勤记录
│   ├── attendance_config.html # 考勤配置
│   └── user_management.html # 用户管理
├── models/                  # 模型文件目录
│   ├── mobilefacenet.om    # NPU模型文件
│   ├── mobilefacenet.onnx  # ONNX模型文件
│   └── mobilefacenet.pt    # PyTorch模型文件
├── datasets/               # 人脸数据集目录
├── uploads/                # 上传文件临时目录
├── utils/                  # 工具模块
│   ├── acl_resource.py     # ACL资源管理
│   ├── model_processor.py  # 模型处理
│   ├── mtcnn_acl.py       # MTCNN ACL版本
│   └── mtcnn_onnx.py      # MTCNN ONNX版本
├── requirements.txt        # Python依赖
├── attendance.csv         # 考勤记录文件
└── attendance_config.json # 考勤配置文件
```

## 🔧 配置说明

在 `app.py` 中可以修改以下配置：

```python
# 模型路径
FACE_RECOGNITION_MODEL_PATH = 'models/mobilefacenet.om'

# 数据集路径
DATASET_PATH = 'datasets'

# 相似度阈值 (0.0-1.0)
SIMILARITY_THRESHOLD = 0.7

# 上传文件大小限制
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB

# 考勤间隔配置
ATTENDANCE_INTERVAL_HOURS = 8  # 默认8小时
```

## 🛠️ 开发说明

### 核心算法

1. **人脸检测**: 使用MTCNN算法检测图像中的人脸
2. **特征提取**: 使用MobileFaceNet提取512维人脸特征向量
3. **相似度计算**: 使用余弦相似度计算人脸特征的匹配程度
4. **阈值判断**: 根据设定阈值判断是否为同一人

### API接口

- `POST /start_camera`: 启动摄像头
- `POST /stop_camera`: 停止摄像头
- `GET /video_feed`: 视频流
- `GET /get_recognition_results`: 获取识别结果
- `POST /register_face`: 注册人脸
- `POST /recognize_face`: 识别人脸
- `GET /attendance_config`: 获取考勤配置
- `POST /attendance_config`: 更新考勤配置

## 🔍 故障排除

### 常见问题

1. **摄像头无法启动**
   - 检查USB摄像头连接
   - 确认摄像头驱动正常
   - 检查是否被其他程序占用

2. **模型加载失败**
   - 检查模型文件是否存在
   - 确认Ascend NPU驱动正确安装
   - 验证模型转换是否成功

3. **人脸识别准确率低**
   - 调整相似度阈值
   - 确保注册图片质量良好
   - 检查光照条件

4. **Web服务无法访问**
   - 检查防火墙设置
   - 确认端口5000未被占用
   - 查看控制台错误信息

### 性能优化

- 使用NPU加速可显著提升推理速度
- 适当调整图像分辨率以平衡速度和精度
- 定期清理临时文件和日志

## 📄 许可证

本项目仅供学习和研究使用。

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个项目。

---

**注意**: 本系统需要在配置了Ascend 310 NPU的环境中运行以获得最佳性能。如果没有NPU环境，系统会自动回退到CPU模式运行。