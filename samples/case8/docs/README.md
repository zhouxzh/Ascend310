# case8 工程文档

case8 是教材中的一个实践案例：在 Ascend 310B 上使用 YOLOv10 和 HaGRID
完成实时手势检测，再通过 WebRTC 将带检测框的视频发送到浏览器。

## 📚 推荐阅读顺序

| 文档 | 内容 |
| --- | --- |
| [00 文档说明](00-document-guide.md) | 学习目标、阅读顺序和证据约定 |
| [01 项目设计](01-project-design.md) | 任务理论、系统设计和数据流 |
| [02 程序解析](02-program-analysis.md) | 源码模块、接口、生命周期和错误处理 |
| [03 模型、数据集与转换](03-model-dataset-and-conversion.md) | HaGRID、模型契约、ONNX、ATC 和 OM |
| [04 部署与测试](04-deployment-and-testing.md) | 板端环境、部署命令和验收方法 |
| [05 问题与版本兼容性](05-known-issues-and-version-compatibility.md) | CANN VENC 实测结果和已知限制 |

## 🔗 代码入口

- 服务端：[`scripts/webrtc_om_app.py`](../scripts/webrtc_om_app.py)
- NPU 推理：[`hagrid_yolo/`](../hagrid_yolo/)
- CANN VENC、DVPP 和 V4L2：[`webrtc_app/`](../webrtc_app/)
- 浏览器前端：[`web/`](../web/)
- ONNX 导出：[`weights/export_yolo_to_onnx.py`](../weights/export_yolo_to_onnx.py)
- ONNX 到 OM：[`scripts/atc_convert.sh`](../scripts/atc_convert.sh)

## 🧭 文档边界

`samples/case8/README.md` 只负责快速部署和启动；`src/experiment/case8.md`
负责教材叙述；本目录负责工程设计、源码解析、模型转换、板端操作和实测
证据。模型权重、ONNX、OM、数据集和运行报告不属于 Git 中的文档资产。

文档中的结论使用以下标签：

- `observed-pass`：指定软硬件组合下实际通过。
- `observed-fail`：指定软硬件组合下实际失败。
- `documented`：来自供应商资料或项目输入资料。
- `inferred`：由多个观测推断，尚未独立确认。
- `untested`：尚未执行，不能写成支持或修复。

后续只需更新对应主题的文档：设计改 `01`，源码改 `02`，模型改 `03`，
部署和测试改 `04`，新故障或新环境改 `05`。
