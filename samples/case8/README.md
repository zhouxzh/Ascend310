# 案例 8：手势识别

基于昇腾 310B 的实时手势识别系统，使用 **HaGRIDv2 + YOLOv10** 进行
34/48 类手势检测。

> 完整的教程内容（背景知识、网络架构详解、性能分析等）请阅读
> [`src/experiment/case8.md`](../../src/experiment/case8.md)。
> 本 README 仅包含代码使用说明。

## 目录结构

```text
case8/
├── hagrid_yolo/               # 可复用 Python 包
│   ├── backends/              # onnx_backend.py / acl_backend.py
│   ├── camera.py              # 摄像头采集 + 推理循环
│   ├── detector.py            # YOLO 检测器封装
│   ├── metadata.py            # 标签 / 输入尺寸 / 设备解析
│   ├── preprocess.py          # letterbox 缩放 + NCHW 转换
│   ├── postprocess.py         # 检测框解码 + NMS
│   └── visualization.py       # 检测框与标签绘制
├── webrtc_app/                # CANN VENC / DVPP JPEGD / V4L2 采集
├── scripts/                   # CLI 入口
│   ├── infer_onnx_camera.py   # ONNX 摄像头 / 图片测试
│   ├── infer_om_camera.py     # OM 摄像头 / 图片测试
│   ├── webrtc_om_app.py       # WebRTC H.264 推流主程序
│   ├── gradio_om_app.py       # 兼容入口 → webrtc_om_app.py
│   └── atc_convert.sh         # ONNX → OM 批量转换
├── web/                       # WebRTC 浏览器前端
├── weights/                   # PyTorch 权重 (.pt) + 导出脚本
├── models/                    # 生成的 ONNX / OM / 标签 / 元数据
└── requirements.txt
```

## 模型说明

本案例使用 HaGRIDv2 官方预训练的 YOLOv10 模型，共四个：

| 模型文件 | 任务 | 类别数 | 输入尺寸 |
| :--- | :--- | ---: | :--- |
| `YOLOv10n_gestures.pt` | 手势分类（小模型） | 34 | 640×640 |
| `YOLOv10x_gestures.pt` | 手势分类（大模型） | 34 | 640×640 |
| `YOLOv10n_hands.pt` | 手势分类（小模型） | 34 | 640×640 |
| `YOLOv10x_hands.pt` | 手部检测含左右手区分（大模型） | 48 | 640×640 |

完整的 34 类手势标签表见教程文档。

## 快速开始

本案例涉及两台机器：**PC/GPU 工作站**（导出 ONNX）和**昇腾 310B 开发板**
（ATC 转换、OM 推理、WebRTC 推流）。以下步骤标注了执行位置。

### 1. 在 PC / GPU 工作站上导出 ONNX

```bash
# 进入项目目录
cd samples/case8

# 安装导出依赖
pip install numpy==1.26.4 onnx==1.14.1 onnxruntime==1.15.1 opencv-python==4.8.0.76
pip install torch==2.10.0 torchvision==0.25.0 --extra-index-url https://download.pytorch.org/whl/cu128
pip install ultralytics==8.4.60

# 导出模型（建议从小模型开始）
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models \
  --imgsz 640 --batch 1 --opset 13 --device cpu
```

如果 ATC 转换时报算子不支持，重新导出时改用 `--opset 17`。

> 验证：`ls models/*.onnx models/*_labels.txt models/*_metadata.json`

### 2. 将 ONNX 文件同步到昇腾 310B

```bash
# 在 PC 上执行，将 models/ 目录同步到 310B（替换 your-board 为实际主机名或 IP）
rsync -av models/*.onnx models/*_labels.txt models/*_metadata.json \
  your-board:~/Documents/Ascend310/samples/case8/models/
```

### 3. 在昇腾 310B 上安装运行时依赖

```bash
# SSH 到 310B，激活 CANN 环境和 conda 虚拟环境
ssh your-board
source /usr/local/Ascend/ascend-toolkit/set_env.sh
conda activate npu

cd ~/Documents/Ascend310/samples/case8
pip install -r requirements.txt
```

### 4. ONNX CPU 验证（可选，建议先做）

在 ATC 转换前用 ONNX Runtime 验证模型和后处理流程是否正确：

```bash
# 有显示器时（去掉 --no-window 可看到 OpenCV 画面）
python scripts/infer_onnx_camera.py --no-window --max-frames 30

# 单张图片冒烟测试
python scripts/infer_onnx_camera.py \
  --model models/YOLOv10n_gestures.onnx \
  --source ../images/example.jpeg \
  --once --save models/onnx_test.jpg
```

> 验证：脚本结束时应打印 `Processed 30 frames ... inferences ...`。
> 昇腾 310B 的 CPU 较弱，用 `YOLOv10x` 会非常慢，仅用 `YOLOv10n` 验证。

### 5. ATC 转换为 OM

```bash
# 确保 CANN 环境已加载
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/case8

# 批量转换 models/ 下所有 ONNX 文件
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh

# 或仅转换单个文件
SOC_VERSION=Ascend310B4 \
  bash scripts/atc_convert.sh models/YOLOv10n_gestures.onnx models/YOLOv10n_gestures
```

> 验证：输出末尾应有 `ATC run success`，且 `ls models/*.om` 能看到对应的
> `.om` 文件。

### 6. OM NPU 推理

```bash
# 纯模型基准测试（合成输入，不接摄像头，仅测 NPU 推理耗时）
python scripts/infer_om_camera.py --benchmark-runs 20 --print-model-info

# 摄像头实时检测（有显示器时去掉 --no-window）
python scripts/infer_om_camera.py --no-window --max-frames 60

# 指定摄像头参数和模型
python scripts/infer_om_camera.py \
  --model models/YOLOv10x_gestures.om \
  --source /dev/video0 \
  --camera-width 1280 --camera-height 720 --camera-fps 30
```

> 验证：基准测试应打印 `[OM] benchmark runs=20, avg=... ms`；摄像头测试
> 应打印 `Processed 60 frames ...`。

### 7. WebRTC 远程预览

```bash
python scripts/webrtc_om_app.py
```

终端会打印可访问的 URL。在**同一局域网**的浏览器中打开打印的局域网地址
（如 `http://192.168.1.100:8080`），即可看到带检测框的实时视频。页面支持
切换模型和调节参数。

```bash
# 常用启动选项
python scripts/webrtc_om_app.py \
  --model models/YOLOv10n_gestures.om \
  --source /dev/video0 \
  --camera-width 1280 --camera-height 720 --camera-fps 30 \
  --infer-every-n 2 --bitrate-kbps 4000
```

> 验证：`curl http://127.0.0.1:8080/health` 应返回 JSON，其中
> `"status": "ok"`，`"encoder"` 字段指示当前使用的编码器。

## 脚本用法速查

### infer_onnx_camera.py

| 选项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--model` | `models/YOLOv10n_gestures.onnx` | ONNX 模型路径 |
| `--source` | `/dev/video0` | 摄像头设备或图片路径 |
| `--conf` / `--iou` | `0.25` / `0.45` | 置信度 / NMS 阈值 |
| `--once` | `False` | 单张图片推理 |
| `--no-window` | `False` | SSH 无窗口模式 |
| `--max-frames` | `0`（无限） | 最大处理帧数 |

### infer_om_camera.py

在 ONNX 选项基础上增加 `--device-id`、`--benchmark-runs`、`--print-model-info`。

### webrtc_om_app.py

| 选项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--model` | `models/YOLOv10n_gestures.om` | OM 模型路径 |
| `--source` | `/dev/video0` | 摄像头设备 |
| `--camera-width / height / fps` | `1280 / 720 / 30` | 采集参数 |
| `--bitrate-kbps` | `4000` | H.264 编码码率 |
| `--camera-backend` | `opencv` | `opencv` 或 `dvpp` |
| `--infer-every-n` | `1` | 推理间隔 |
| `--no-hardware-encode` | `False` | 回退 CPU 编码 |

## 常见问题

**摄像头打不开？** 先确认设备节点：`ls /dev/video*`，再尝试切换：
`--source /dev/video1`。权限问题：`sudo usermod -aG video $USER` 后重新
登录。

**WebRTC 页面打开但视频黑屏？** 确认浏览器打开的是脚本打印的局域网地址
（`192.168.x.x`），而非 `127.0.0.1`。`127.0.0.1` 只能从开发板本地访问。

**推理延迟偏高？** 先确认加载的是 `YOLOv10n` 而非 `YOLOv10x`。再用
`v4l2-ctl --device=/dev/video0 --list-formats-ext` 检查摄像头是否以 MJPG
模式运行，YUYV 高分辨率下采集本身会很慢。

**ATC 报算子不支持？** 尝试用 `--opset 17` 重新导出 ONNX，再转换。

## 参考

- 完整教程：`src/experiment/case8.md`
- HaGRIDv2 论文：[arXiv:2412.01508](https://arxiv.org/abs/2412.01508)
- HaGRID 仓库：[github.com/hukenovs/hagrid](https://github.com/hukenovs/hagrid)
- YOLOv10 论文：[arXiv:2405.14458](https://arxiv.org/abs/2405.14458)
- YOLOv10 仓库：[github.com/THU-MIG/yolov10](https://github.com/THU-MIG/yolov10)
