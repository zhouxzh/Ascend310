# Ascend 人脸打卡系统

基于华为 Ascend 310B NPU 的高性能人脸识别考勤系统。

## 系统架构

### 核心模块

1. **[app.py](app.py)** - Flask Web 服务器，提供 REST API 和页面路由
2. **[ascend_inference.py](ascend_inference.py)** - Ascend NPU 推理引擎，封装人脸检测和识别
3. **[camera.py](camera.py)** - 摄像头管理，实现自动打卡逻辑
4. **[database.py](database.py)** - SQLite 数据库操作

## 详细代码分析

### 1. 数据库层 (database.py)

#### 数据表结构

**users 表** - 存储用户信息
- `id`: 主键，自增
- `name`: 用户姓名
- `embedding`: 人脸特征向量 (BLOB，512维 float32)
- `created_at`: 创建时间

**attendance 表** - 存储考勤记录
- `id`: 主键
- `user_id`: 外键，关联 users.id
- `timestamp`: 打卡时间
- `type`: 打卡类型 ('manual' 手动 / 'camera_auto' 自动)
- `image_path`: 图片路径

#### 核心函数

```python
init_db()              # 初始化数据库表
add_user(name, embedding)  # 添加用户，返回 user_id
get_users()            # 获取所有用户（含 embedding）
delete_user(user_id)   # 删除用户及其考勤记录
add_attendance(user_id, type, image_path)  # 记录考勤
get_attendance()       # 获取考勤记录（JOIN users 表）
```

### 2. Ascend 推理引擎 (ascend_inference.py)

#### 类结构

**AscendSystem** - NPU 设备管理
- 初始化 ACL 运行时环境
- 创建 context 和 stream
- 管理设备资源生命周期

**AscendModel** - 模型加载与推理
- 加载 `.om` 模型文件
- 管理输入/输出 buffer（设备内存）
- 执行推理：Host → Device → Execute → Device → Host

**FaceSystem** - 人脸系统封装
- 加载人脸检测模型 (`face_detection.om`)
- 加载人脸识别模型 (`face_recognition.om`)
- 提供高层接口：`detect()` 和 `get_embedding()`

#### 人脸检测流程

```
输入图像 (H×W×3 BGR)
    ↓
preprocess_det()  # 缩放到 640×640，归一化 (x-127.5)/128
    ↓
det_model.execute()  # NPU 推理，输出 9 个 tensor
    ↓
decode_bbox()  # 解码 anchor-based 检测结果
    ↓
NMS 过滤  # 非极大值抑制，阈值 0.4
    ↓
返回人脸框列表 [[x1,y1,x2,y2], ...]
```

**检测模型输出解析**：
- 输出 0-2: 三个尺度的分类分数 (stride 8/16/32)
- 输出 3-5: 三个尺度的边界框偏移 (l, t, r, b)
- 输出 6-8: 关键点（本项目未使用）

**Anchor 生成**：
- 在 640×640 图像上，stride=8/16/32 生成网格
- 每个网格点 2 个 anchor
- 总计 (80×80 + 40×40 + 20×20) × 2 = 16800 个 anchor

**边界框解码** (SCRFD 格式)：
```python
x1 = anchor_x - left * stride
y1 = anchor_y - top * stride
x2 = anchor_x + right * stride
y2 = anchor_y + bottom * stride
```

#### 人脸识别流程

```
人脸图像 (裁剪后)
    ↓
preprocess_rec()  # 缩放到 112×112，归一化
    ↓
rec_model.execute()  # NPU 推理
    ↓
返回 512 维特征向量 (float32)
```

**相似度计算**：
```python
similarity = cosine_similarity(emb1, emb2)
           = dot(emb1, emb2) / (norm(emb1) * norm(emb2))
```
阈值设为 0.5，超过则认为是同一人。

### 3. 摄像头管理 (camera.py)

#### VideoCamera 类

**初始化**：
- 打开 `/dev/video0` 摄像头
- 设置分辨率 640×480
- 启动后台线程持续读取帧

**后台线程 (update 方法)**：
```
循环 (30 FPS):
    读取摄像头帧 → 保存到 last_frame
    ↓
    每 2 秒触发一次:
        process_attendance(frame)  # 自动打卡逻辑
```

**自动打卡逻辑 (process_attendance)**：
```
1. 人脸检测 → 选择最大人脸
2. 裁剪人脸区域
3. 提取特征向量
4. 遍历数据库所有用户，计算相似度
5. 如果最高相似度 > 0.5:
   - 打印识别结果
   - 写入考勤记录 (type='camera_auto')
```

**Web 流式传输**：
- `get_frame()`: 返回 JPEG 编码的帧（用于 `/video_feed` 路由）
- `get_snapshot()`: 返回原始帧（用于用户注册拍照）

### 4. Web 服务器 (app.py)

#### 路由结构

**页面路由**：
- `/` → index.html (主页)
- `/users_page` → users.html (用户管理)
- `/attendance_page` → attendance.html (考勤记录)

**API 路由**：

| 路由 | 方法 | 功能 |
|------|------|------|
| `/api/users` | GET | 获取用户列表 |
| `/api/users` | POST | 添加用户 |
| `/api/users/<id>` | DELETE | 删除用户 |
| `/api/camera/capture` | POST | 从设备摄像头抓拍 |
| `/api/clockin` | POST | 手动打卡 |
| `/api/attendance` | GET | 获取考勤记录 |
| `/video_feed` | GET | 视频流 (MJPEG) |

#### 关键流程

**用户注册流程**：
```
1. 接收图片 (上传文件 或 摄像头抓拍)
2. 人脸检测 → 选择最大人脸
3. 裁剪人脸 → 提取 512 维特征
4. 特征向量转 bytes 存入数据库
5. 返回 user_id
```

**手动打卡流程**：
```
1. 接收图片 (文件上传 或 base64)
2. 人脸检测 → 裁剪
3. 提取特征向量
4. 遍历数据库用户，计算余弦相似度
5. 找到最高相似度 > 0.5:
   - 记录考勤 (type='manual')
   - 返回匹配结果
6. 否则返回 match=False
```

## 完整工作流程

### 启动流程

```
python3 app.py
    ↓
database.init_db()  # 初始化数据库
    ↓
get_face_system()  # 初始化 Ascend NPU
    ├─ AscendSystem() → 初始化 ACL
    ├─ 加载 face_detection.om
    └─ 加载 face_recognition.om
    ↓
get_video_camera()  # 初始化摄像头
    └─ 启动后台线程 (自动打卡)
    ↓
Flask 启动 (0.0.0.0:5000)
```

### 用户注册流程

```
用户上传照片 / 摄像头抓拍
    ↓
POST /api/users
    ↓
FaceSystem.detect(image)  # NPU 检测人脸
    ↓
选择最大人脸框 → 裁剪
    ↓
FaceSystem.get_embedding(face)  # NPU 提取特征
    ↓
database.add_user(name, embedding_bytes)
    ↓
返回 user_id
```

### 自动打卡流程

```
摄像头后台线程 (每 2 秒)
    ↓
读取当前帧
    ↓
FaceSystem.detect(frame)
    ↓
有人脸? → 裁剪最大人脸
    ↓
FaceSystem.get_embedding(face)
    ↓
遍历数据库用户:
    计算 cosine_similarity(current_emb, db_emb)
    ↓
    找到最高相似度 > 0.5?
        ↓
        database.add_attendance(user_id, 'camera_auto', 'local_camera')
        ↓
        打印识别结果
```

### 手动打卡流程

```
用户在网页点击打卡 (浏览器摄像头 / 上传)
    ↓
POST /api/clockin (image_base64 或 file)
    ↓
FaceSystem.detect(image) → 裁剪人脸
    ↓
FaceSystem.get_embedding(face)
    ↓
遍历数据库用户，计算相似度
    ↓
最高相似度 > 0.5?
    ├─ 是: database.add_attendance() → 返回 match=True
    └─ 否: 返回 match=False
```

## 技术细节

### NPU 推理优化

1. **内存管理**：使用 `acl.rt.malloc` 在设备上预分配 buffer，避免重复分配
2. **数据传输**：Host → Device 使用 `acl.rt.memcpy`，模式 1 (H2D)
3. **异步执行**：通过 stream 管理推理任务（本项目为同步模式）

### 性能特点

- **检测速度**：640×640 图像，Ascend 310B 约 10-20ms
- **识别速度**：112×112 人脸，约 5-10ms
- **自动打卡间隔**：2 秒（可调整 `check_interval`）
- **视频流帧率**：30 FPS

### 相似度阈值

- **当前阈值**：0.5 (余弦相似度)
- **调整建议**：
  - 提高阈值 (0.6-0.7) → 更严格，减少误识别
  - 降低阈值 (0.4-0.5) → 更宽松，提高召回率

## 快速开始

### 1. 环境准备

```bash
# 检查 NPU
npu-smi info

# 安装依赖
pip install flask
```

### 2. 模型准备

**基本用法**（自动判断，只做必要的步骤）：
```bash
export TE_PARALLEL_COMPILER=1      # 限制算子最大并行编译进程数
export MAX_COMPILE_CORE_NUMBER=1   # 限制图编译占用的 CPU 核数
python3 prepare_models.py
```

**高级选项**：
```bash
# 只下载模型，不转换
python3 prepare_models.py --download-only

# 只转换模型（假设 ONNX 已存在）
python3 prepare_models.py --convert-only

# 强制重新下载和转换
python3 prepare_models.py --force
```

脚本会自动：
- 检测已有文件（ZIP/ONNX/OM）
- 下载 buffalo_s.zip（约 86MB）
- 解压 ONNX 模型
- 转换为 OM 格式

生成文件：
- `models/face_detection.om` (SCRFD 检测模型)
- `models/face_recognition.om` (ArcFace 识别模型)

### 3. 摄像头权限

```bash
sudo chmod 666 /dev/video0
```

### 4. 启动系统

```bash
python3 app.py
```

访问：http://127.0.0.1:5000

## 使用说明

1. **注册用户**：进入 User Management，上传照片或使用摄像头抓拍
2. **自动打卡**：站在摄像头前，系统每 2 秒自动识别
3. **手动打卡**：进入 Attendance Log，点击 Manual Check-in
4. **查看记录**：Attendance Log 页面显示所有打卡记录

## 常见问题

**Q: 摄像头无法打开？**
A: 检查 `/dev/video0` 权限，或确认设备未被占用

**Q: 识别率低？**
A: 调整阈值 (app.py:185, camera.py:110)，或重新注册清晰照片

**Q: 模型转换失败？**
A: 确保 `atc` 命令可用，内存充足（建议 4GB+）

**Q: 重复打卡？**
A: 自动打卡每 2 秒触发一次，可在数据库层添加去重逻辑（检查最近 1 分钟是否已打卡）
