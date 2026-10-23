# Case 2 文档索引

本目录记录目标检测与跟踪案例的代码契约、模型转换、运行方式、证据边界和问题台账。快速运行入口见 `samples/case2/README.md`，教材正文见 `src/experiment/case2.md`。

| 文档 | 内容 |
| --- | --- |
| [01-architecture.md](01-architecture.md) | 检测/跟踪组件、数据流和边界 |
| [02-detection-pipeline.md](02-detection-pipeline.md) | SSD 预处理、backend、解码、NMS 和绘制 |
| [03-tracking-pipeline.md](03-tracking-pipeline.md) | IoU+Kalman 轻量跟踪、轨迹生命周期和参数 |
| [04-model-download-and-conversion.md](04-model-download-and-conversion.md) | Hugging Face 下载、ONNX、ATC 和 SoC |
| [05-runtime-and-operation.md](05-runtime-and-operation.md) | 摄像头/视频、显示、保存和计时 |
| [06-validation-and-troubleshooting.md](06-validation-and-troubleshooting.md) | 验证边界和统一问题台账 |

## 代码入口

```text
scripts/detection_app.py          检测
scripts/tracking_app.py           检测 + 跟踪
scripts/download_models.py        模型下载
scripts/convert_onnx_to_om.py     ONNX → OM
ssdlite/                          backend、decoder、预处理/后处理
tracking/                         Kalman 与轻量跟踪器
utils/                            OpenCV 运行时、输入和绘制
```
