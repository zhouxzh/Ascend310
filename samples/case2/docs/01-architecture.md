# 系统架构

案例 2 有两条可执行链路：

```text
视频文件/摄像头
  ├─ detection_app.py：SSD 检测 → 类别 NMS → 标注
  └─ tracking_app.py：SSD 检测 → 检测框转轨迹输入
                         → Kalman 预测 + IoU/中心距关联 → 轨迹 ID
```

## 组件职责

| 组件 | 文件 | 职责 |
| --- | --- | --- |
| 检测入口 | `scripts/detection_app.py` | 参数、backend、视频源、检测、显示/保存 |
| 跟踪入口 | `scripts/tracking_app.py` | 在检测上加轨迹关联和绘制 |
| 下载 | `scripts/download_models.py` | 从 HF mirror 下载 ONNX/OM |
| 转换 | `scripts/convert_onnx_to_om.py` | 生成 ATC 命令并转换 OM |
| Backend 基类 | `ssdlite/backend_base.py` | 预处理、输出识别、解码、NMS、计时 |
| CPU backend | `ssdlite/cpu_backend.py` | ONNX Runtime CPU |
| NPU backend | `ssdlite/npu_backend.py` | PyACL OM 加载和执行 |
| 解码 | `ssdlite/decoder.py` | SSD prior、框解码、Softmax、IoU/NMS |
| 跟踪 | `tracking/deepsort.py` | 中心 Kalman、IoU/中心距关联、轨迹 |
| 运行时 | `utils/opencv_runtime.py` | V4L2、MJPEG、FPS、计时和后端创建 |
| 输入契约 | `utils/preprocessing.py` | 模型发现、标签、摄像头 profile |
| 绘制 | `utils/postprocessing.py` | 检测框、轨迹、颜色和状态叠加 |

## 关键边界

- 检测入口默认 `--device cpu`，跟踪入口默认 `--device npu`，两者不同。
- CPU backend 接受 `.onnx`，NPU backend 接受 `.om`；模型选择按文件名前缀匹配。
- NPU backend 是 `strict_ssd=True`，对输出形状更严格；CPU 允许通用解码路径。
- 车道线、目标跟踪和绘制都在 CPU；只有神经网络 backend 可能是 NPU。
- 没有独立 Web 服务，界面是 OpenCV 窗口；`--no-display` 用于无头运行。
