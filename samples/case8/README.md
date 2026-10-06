# 案例 8：实时手势识别

本案例在 Orange Pi AI Pro 8T（Ascend 310B4）上运行 HaGRID YOLOv10 手势
检测模型：摄像头帧由 ACL/OM 在 NPU 上推理，结果绘制后通过 H.264/WebRTC
发送到浏览器。

## 目录

```text
case8/
├── hagrid_yolo/       # 预处理、检测器、ACL/ONNX 后端和后处理
├── webrtc_app/        # CANN VENC、DVPP JPEGD、V4L2 采集
├── scripts/            # ONNX/OM 推理、ATC 和 WebRTC 入口
├── web/                # 浏览器页面
├── weights/            # 权重导出脚本（权重文件不提交）
├── models/             # ONNX/OM/标签/元数据（模型文件不提交）
└── docs/               # 教程配套工程文档
```

详细设计和程序解析从 [工程文档入口](docs/README.md) 开始；教材说明见
[`src/experiment/case8.md`](../../src/experiment/case8.md)。

## 环境

- Ascend 310B4 / 8T 开发板和 USB 摄像头
- CANN、驱动和固件已在板端安装
- Python 环境包含 `acl`、OpenCV、aiortc、PyAV 和 NumPy
- 模型输入为静态 `1x3x640x640`

当前已验证的运行范围、CANN VENC 限制和系统版本对照见
[问题与版本兼容性](docs/05-known-issues-and-version-compatibility.md)。

## 快速部署

在 PC 或 GPU 工作站导出 ONNX：

```bash
cd samples/case8
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models --imgsz 640 --batch 1 --opset 13 --device cpu
```

同步项目和模型伴随文件到开发板：

```bash
rsync -av samples/case8/ \
  HwHiAiUser@192.168.1.100:/home/HwHiAiUser/Documents/case8/
```

在开发板上加载 CANN 并转换 OM：

```bash
ssh HwHiAiUser@192.168.1.100
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Documents/case8
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
```

先做 OM 推理冒烟：

```bash
python scripts/infer_om_camera.py \
  --benchmark-runs 20 --warmup-runs 5 --print-model-info
```

启动 WebRTC 服务：

```bash
python scripts/webrtc_om_app.py
```

浏览器打开服务打印的地址，默认是 `http://192.168.1.100:8080`。

## 编码器行为

网页默认选择 `CANN VENC`。选择该模式后，ACL 不可用、VENC 通道创建失败或
单帧编码失败都会明确报错并停止连接，程序不会自动回退到 CPU。

只有在明确测试 CPU 编码时，才选择网页中的 `CPU libx264`，或使用：

```bash
python scripts/webrtc_om_app.py --no-hardware-encode
```

该选项只替换 H.264 编码，OM 推理仍然在 NPU 上执行。

## 常用参数

```bash
python scripts/webrtc_om_app.py \
  --model models/YOLOv10n_gestures.om \
  --source /dev/video0 \
  --camera-width 1280 --camera-height 720 --camera-fps 30 \
  --camera-backend opencv --camera-fourcc MJPG \
  --infer-every-n 1 --bitrate-kbps 4000
```

模型尺寸不是运行参数；它由 OM 模型契约决定。摄像头分辨率、推理间隔、码率、
置信度和 IoU 可以按测试需要调整。

## 服务检查

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/models
curl http://127.0.0.1:8080/stats
```

`/health` 的 `status=ok` 只表示 HTTP 服务在线，不能代替 CANN VENC 视频
连接验收。完整测试顺序见 [部署与测试](docs/04-deployment-and-testing.md)。

## 文档入口

- [项目设计](docs/01-project-design.md)
- [程序解析](docs/02-program-analysis.md)
- [模型、数据集与转换](docs/03-model-dataset-and-conversion.md)
- [部署与测试](docs/04-deployment-and-testing.md)
- [问题与版本兼容性](docs/05-known-issues-and-version-compatibility.md)
