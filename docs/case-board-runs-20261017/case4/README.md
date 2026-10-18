# case4 双板调试记录

## 运行

- 部署目录：`/home/HwHiAiUser/Ascend310/cases/case4`
- 启动：`python -m palmprint_workbench.api --host 0.0.0.0 --port 7860`
- profile：`manual_test`
- active 模型：`ccnet`，ONNX/OM 字节数和 SHA-256 均在板端校验通过

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| `/api/health` | `status=ok`，`runtime_importable=true` | `status=ok`，`runtime_importable=true` |
| 模型资产 | CCNet ONNX/OM ready | CCNet ONNX/OM ready |
| 前端 bundle | ready | ready |
| 模板库 | ready，空库 | ready，空库 |
| 摄像头预览 | 失败：选择了 `/dev/video0`，无帧 | 成功：C922，1280×720 |
| 推理烟测 | 未初始化 NPU | 摄像头自动 ROI 约 2022.9ms；模板库为空，未匹配 |
| 页面截图 | 通过（UI + 摄像头错误） | 通过（UI + 实时画面） |

两板服务、模型资产校验和页面都通过。8T 的 USB 摄像头实际在 `/dev/video2`，但页面自动选择了 ISP `/dev/video0`，因此 8T 预览失败；这是摄像头设备选择问题，不是模型或 API 加载失败。20T 的自动识别完成了 ROI/推理 attempt，但模板库为空，不能记录为身份匹配成功。没有执行数据集评测、准确率或模板注册。

## 截图

- [8T 掌纹识别工作台](board-8t.png)
- [20T 掌纹识别工作台（无掌纹 ROI 截图）](board-20t-safe.png)

截图只用于 UI/摄像头状态证据，不包含身份标签或数据集评测结论。

## 证据

- [8T 服务日志](board-8t.log)
- [20T 服务日志](board-20t.log)
- [health/bootstrap 原始结果](capture.json)
- [安全 UI 截图元数据](capture-safe.json)
