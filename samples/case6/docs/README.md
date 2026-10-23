# Case 6 文档索引

本目录记录智能小车视觉感知案例的代码结构、运行方式、模型转换、证据边界和测试问题。`samples/case6/README.md` 是快速运行入口，`src/experiment/case6.md` 是教材正文，本目录是代码工程文档。

| 文档 | 内容 |
| --- | --- |
| [01-architecture.md](01-architecture.md) | 组件职责、数据流和信任边界 |
| [02-lane-and-scene-pipeline.md](02-lane-and-scene-pipeline.md) | 车道线 CV 与 ResNet18 场景分类流水线 |
| [03-model-conversion-and-npu.md](03-model-conversion-and-npu.md) | PTH、ONNX、ATC、OM 和 NPU 后端 |
| [04-web-ui-and-operation.md](04-web-ui-and-operation.md) | Gradio 页面、参数、启动和停止 |
| [05-validation-and-limitations.md](05-validation-and-limitations.md) | 本地检查、板端验收和证据边界 |
| [06-troubleshooting.md](06-troubleshooting.md) | 统一测试问题台账 |

## 代码入口

```text
app.py                  Gradio Web 入口
lane_detector.py        OpenCV 车道线
scene_classifier.py     ResNet18 NPU/CPU 分类器
prepare_models.py       PTH/ONNX/OM 模型准备
config.py               路径、类别、预处理和 CV 参数
setup.sh                当前脚本化环境安装
```
