# 模型下载与转换

## 下载

`scripts/download_models.py` 默认使用 `hf-mirror.com`：

```bash
python scripts/download_models.py
python scripts/download_models.py --onnx
python scripts/download_models.py --om
python scripts/download_models.py --all
```

默认下载 `ssd320_mobilenetv3_large_100.onnx`；`--repo`/`--endpoint` 可指定其他 HF 镜像。脚本会枚举仓库 siblings、匹配 SSD 文件名，并把部分旧名称映射到 `ssd300_`/`ssd320_`。

当前下载器只做 HTTP 下载和文件名映射，没有内置 SHA-256 验证。使用发布资产前必须按仓库模型 manifest 或外部记录补充校验。

## ONNX → OM

在配置好 CANN/ATC 的板端执行：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
```

20T / Ascend310B1 必须显式传：

```bash
python scripts/convert_onnx_to_om.py --soc-version Ascend310B1
```

默认 `DEFAULT_SOC_VERSION=Ascend310B4`，不要把这个默认值当成目标板事实。

常用参数：

```text
--input-shape auto                 根据文件名推断 300/320
--input-name input
--input-shape input:1,3,320,320
--no-input-shape
--dry-run
--force
--extra-arg=--precision_mode=allow_fp32_to_fp16
```

`infer_input_size()` 只看文件名：`resnet`/`ssd300` → 300，`mobilenet`/`ssd320` → 320。无法识别时必须手工给 `--input-shape` 或 `--no-input-shape`。

转换后必须检查：

- ATC 退出码；
- 目标目录实际存在 `.om`；
- OM 大小和 SHA-256；
- NPU backend 能加载；
- 输出 prior 数和 decoder 规格一致。
