# case8 文档说明

## 🎯 学习目标

读者完成本案例后，应能理解并复现以下过程：

1. 使用 HaGRID 手势模型完成静态目标检测。
2. 将 PyTorch 权重导出为 ONNX，并在 310B 上转换为 OM。
3. 使用 PyACL 加载 OM，在摄像头帧上完成 NPU 推理。
4. 通过 CANN VENC 或用户明确选择的 CPU `libx264` 编码 H.264。
5. 使用 WebRTC 在浏览器中查看带检测框的实时视频。

## 📖 阅读路径

先阅读 [项目设计](01-project-design.md)，建立整体概念；再阅读
[程序解析](02-program-analysis.md)，对照源码理解运行流程；模型转换见
[模型、数据集与转换](03-model-dataset-and-conversion.md)；需要上板运行时
阅读 [部署与测试](04-deployment-and-testing.md)；遇到硬编问题时查看
[问题与版本兼容性](05-known-issues-and-version-compatibility.md)。

## 🧩 文档与源码的关系

| 内容 | 权威来源 |
| --- | --- |
| 教材叙述 | [`src/experiment/case8.md`](../../../src/experiment/case8.md) |
| 快速部署 | [`samples/case8/README.md`](../README.md) |
| 运行服务 | [`scripts/webrtc_om_app.py`](../scripts/webrtc_om_app.py) |
| 模型推理 | [`hagrid_yolo/`](../hagrid_yolo/) |
| 编码和采集 | [`webrtc_app/`](../webrtc_app/) |
| 模型导出和转换 | [`weights/`](../weights/)、[`scripts/atc_convert.sh`](../scripts/atc_convert.sh) |

文档解释代码，不复制代码实现。源码变化后只更新受影响的段落，并保留
原有测试结论的环境范围。

## 🧾 证据约定

实测结果必须同时写出板卡、系统日期、驱动、固件、CANN、模型和命令。一次
测试失败只说明该软硬件组合失败，不能直接推广为所有 310B 或所有 CANN
版本都不支持。

CPU 编码路径和 CANN VENC 路径是两个明确的用户选择。CPU 路径通过并不代
表硬件编码通过；CANN VENC 失败时程序保持 fail-closed，不把 CPU 成功当作
硬编修复。
