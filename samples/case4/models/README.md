# 模型资产目录

这是运行时模型的挂载点，不是源码包的二进制分发目录。

- `registry.json` 是唯一的生产模型注册表；当前只准入 `ccnet`，运行精度固定为 `mixed_fp16`。
- `om/` 保存板端使用的 Ascend OM 文件。OM 文件默认被 Git 忽略；请按根目录的 `om_manifest.json` 校验字节数和 SHA-256 后再放入此目录。
- `onnx/`、`checkpoints/` 和 `upstream/` 仅供离线导出或审计，不会被生产 API 加载；板端只下载 OM 时可以不提供 ONNX。
- `offline_models` 中的静态 CompNet 和 EDCC 是研究基线，`offline_only=true`，不能通过 API、模板或前端选择器启用。

模型、权重和数据集的再分发必须遵守各自许可证。源码包不包含这些二进制资产；受控资产包和下载地址以 `om_manifest.json` 及编号文档为准。
