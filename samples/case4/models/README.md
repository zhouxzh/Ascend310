# 模型资产目录

这是运行时模型的挂载点，不是源码包的二进制分发目录。

- `registry.json` 是唯一的生产模型注册表；当前只准入 `ccnet`，运行精度固定为 `mixed_fp16`。
- `om/` 保存板端使用的 Ascend OM 文件。OM 文件默认被 Git 忽略；请按根目录的 `om_manifest.json` 校验字节数和 SHA-256 后再放入此目录。
- `onnx/`、`checkpoints/` 和 `upstream/` 保存手掌、掌纹模型的离线导出或审计资产，不会被生产 API 直接加载；板端只下载 OM 时可以不提供 ONNX。
- Palm NanoDet 的 ONNX 保留在 `onnx/` 根目录；对应的 8T 和 20T OM 保留在 `om/` 根目录，并在文件名中标明算力档位与精度。
- COCO 通用检测器不属于本案例。相关 ONNX、OM 和评测入口见独立仓库 [zhouxzh/ascend310b-coco-detector-benchmarks](https://huggingface.co/zhouxzh/ascend310b-coco-detector-benchmarks)。
- `offline_models` 中的静态 CompNet 和 EDCC 是研究基线，`offline_only=true`，不能通过 API、模板或前端选择器启用。

模型、权重和数据集的再分发必须遵守各自许可证。源码包不包含这些二进制资产；受控资产包和下载地址以 `om_manifest.json` 及编号文档为准。
