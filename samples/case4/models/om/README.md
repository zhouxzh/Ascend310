# Ascend OM 资产

Palm NanoDet 的 OM 文件放在本目录根部，并通过文件名中的 `_8t` 或 `_20t` 区分算力档位。
COCO 通用检测器的对比 OM 不在本目录；见独立仓库 [zhouxzh/ascend310b-coco-detector-benchmarks](https://huggingface.co/zhouxzh/ascend310b-coco-detector-benchmarks)。

文件名、字节数、SHA-256、来源修订和再分发状态见仓库根目录的 `om_manifest.json`。复制或下载完成后，在项目根目录执行：

```bash
python -m palmprint_workbench.tools.verify_assets --strict
```

参考 ONNX 不是板端推理的必需文件；只有执行导出或 `--require-onnx-contract` 契约检查时才需要它。该目录中的 OM 二进制不会提交到源码 Git 仓库；公开资产包必须先完成许可证审核和远端哈希复核。
