# MobileNet-SSDLite320 Ascend 310B 推理样例

本目录提供 MobileNet-SSDLite320 在 Ascend 310B 上的推理、评估和 ONNX -> OM 转换流程。
本样例面向已经拿到 ONNX 模型后的部署阶段，不包含训练代码，也不要求使用者本地存在训练仓库。
ONNX 模型的训练代码在 [zhouxzh/SSDLite320-MobileNet](https://github.com/zhouxzh/SSDLite320-MobileNet)，如果需要了解训练策略、导出流程和 CUDA 评估细节，请到该 GitHub 仓库查找。

模型可以通过两种方式准备：

1. 直接下载已经发布的 ONNX 模型，这是推荐的快速上手方式。
2. 使用自己训练或从其他来源导出的 ONNX，但模型接口必须符合下文的约定。

公共代码使用 numpy/Pillow/ONNX Runtime/PyACL，不依赖 torch、torchvision 或 torch_npu。

## 目录结构

| 路径 | 说明 |
|---|---|
| `scripts/inference_cpu.py` | 使用 ONNX Runtime CPUExecutionProvider 校验 ONNX 接口、后处理和 COCO mAP |
| `scripts/inference_npu.py` | 在 Ascend 310B 上使用 PyACL 加载 `.om` 模型并评估 |
| `scripts/download_models.py` | 从 Hugging Face 下载 `ssd320_*` ONNX/OM 模型 |
| `scripts/convert_onnx_to_om.py` | 在 Ascend 310B 设备上调用 ATC 转换 ONNX 到 OM |
| `ssdlite320/config.py` | 常量、默认路径和模型路径解析 |
| `ssdlite320/backends.py` | ONNX Runtime CPU 后端和 AscendCL/PyACL NPU 后端 |
| `ssdlite320/data.py` | Hugging Face val parquet 加载、图像预处理和 GT 提取 |
| `ssdlite320/postprocess.py` | numpy 版 default boxes、decode、softmax、NMS |
| `ssdlite320/eval.py` | CPU/NPU 共用评估流程、COCO mAP、可视化和 CSV 报告 |
| `weights/` | 放置下载或自备的 `ssd320_{backbone}.onnx` |
| `models/` | 推荐放置 ATC 转换得到的 `ssd320_{backbone}.om` |
| `outputs/` | 预测结果和可视化输出 |

## 模型接口

推理和转换脚本按以下约定解析模型。若使用自备 ONNX，请先确认模型满足这些接口要求：

- 输入名：`input`
- 输入尺寸：`1,3,320,320`
- 输出名：`boxes` 和 `scores`
- 模型文件名：`weights/ssd320_{backbone}.onnx`
- OM 文件名：`models/ssd320_{backbone}.om`
- Default Boxes：`min_ratio=0.1`，`max_ratio=0.9`

推理脚本默认使用 `mobilenetv4_conv_small`，也可以通过 `--backbone` 或 `--model` 指定其他模型。

## 环境依赖

进入样例目录后安装 Python 依赖：

```bash
cd samples/chapter4/SSDLite
pip install -r requirements.txt
```

普通开发机可以完成模型下载和 ONNX Runtime CPU 校验。ATC 转换和 NPU 推理必须在 Ascend 310B 设备上执行，并且设备上需要已经安装 CANN。

`acl` Python 包来自 Ascend CANN 环境，不在 `requirements.txt` 中安装。在 Ascend 310B 设备上运行转换或 NPU 推理前，需要加载 CANN 环境变量：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
```

## 获取 ONNX 模型

### 方式一：下载公开模型

推荐先下载默认 ONNX 模型：

```bash
cd samples/chapter4/SSDLite
python scripts/download_models.py
```

默认会从 Hugging Face 仓库 `zhouxzh/SSDLite320` 下载 `ssd320_mobilenetv4_conv_small.onnx`，并保存到 `weights/` 目录。
脚本默认使用 `https://hf-mirror.com` 作为下载端点；如果需要使用 Hugging Face 官方源，可以指定：

```bash
python scripts/download_models.py --endpoint https://huggingface.co
```

下载全部 ONNX 模型：

```bash
python scripts/download_models.py --onnx
```

下载指定模型文件：

```bash
python scripts/download_models.py ssd320_mobilenetv1_100h.onnx ssd320_mobilenetv1_125.om
```

按 backbone 下载对应模型。默认会下载该 backbone 的 ONNX 和 OM；也可以加 `--onnx` 或 `--om` 只下载其中一种格式：

```bash
python scripts/download_models.py mobilenetv4_conv_small
python scripts/download_models.py --onnx mobilenetv4_conv_small
python scripts/download_models.py --om mobilenetv4_conv_small
```

### 方式二：使用自备 ONNX

如果你已经有自己训练或其他来源导出的 ONNX，请先确认它满足“模型接口”中的输入、输出和尺寸约定，然后放到 `weights/`：

```bash
cd samples/chapter4/SSDLite
mkdir -p weights
cp /path/to/ssd320_mobilenetv4_conv_small.onnx weights/
```

文件名需要与 `--backbone` 参数对应。例如 `--backbone mobilenetv4_conv_small` 会默认查找 `weights/ssd320_mobilenetv4_conv_small.onnx`。如果文件名不同，CPU 校验时可以使用 `--model /path/to/model.onnx`；转换 OM 时可以把 ONNX 路径作为位置参数传给 `scripts/convert_onnx_to_om.py`，或者把文件改名为脚本默认格式。

## ONNX CPU 校验

CPU 路径只用于校验 ONNX 模型接口、numpy 后处理和评估流程，不作为设备推理结果：

```bash
python scripts/inference_cpu.py --backbone mobilenetv4_conv_small --max-samples 50
```

只生成可视化图、不跑完整 COCO 评估：

```bash
python scripts/inference_cpu.py --backbone mobilenetv4_conv_small --skip-eval --num-visualizations 10
```

脚本默认读取本地 `data/val-*.parquet` 验证集分片；如果本地没有这些文件，会先通过 `huggingface_hub` 下载匹配的验证集分片，再从本地 parquet 文件加载。不使用官方原版 COCO JSON。

每次完成评估后，都会单独创建一个带时间戳的 CSV 表格：

```text
reports/{provider}_eval_YYYYMMDD_HHMMSS_{backbone}.csv
```

表格会记录时间、backbone、模型路径、CPU/NPU provider、mAP、AP50、AP75、small/medium/large AP、FPS 和预测 JSON 路径，便于对比多个模型。
如果需要指定表格路径，可以使用 `--report-file /path/to/result.csv`；否则不会追加到旧表。
默认预测 JSON 会保存到 `outputs/val_results/{provider}_eval_YYYYMMDD_HHMMSS/` 下，避免多次运行互相覆盖。

若需要边下载边读取，可显式启用 streaming：

```bash
python scripts/inference_cpu.py --backbone mobilenetv4_conv_small --streaming
```

不要把 `--data-files` 设为空；空值会让 `datasets` 按默认规则解析整个仓库，可能重新触发 train+val 的大体积缓存。

## 转换 OM

ATC 转换需要在 Ascend 310B 设备上执行，并先加载 CANN 环境变量。本地开发机不运行 ATC。
转换前需要先确保 `weights/` 下已有 ONNX 文件。

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/chapter4/SSDLite
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
```

转换脚本默认读取 `weights/*.onnx`，输出到 `models/*.om`。可用 `--dry-run` 查看实际 ATC 命令。

如果只转换某一个 ONNX 文件：

```bash
python scripts/convert_onnx_to_om.py weights/ssd320_mobilenetv4_conv_small.onnx --soc-version Ascend310B4
```

## NPU 推理

NPU 推理需要在 Ascend 310B 设备上执行，并确保 `models/` 下已有转换后的 `.om` 文件：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/inference_npu.py --backbone mobilenetv4_conv_small --device 0
```

如果模型不在默认目录：

```bash
python scripts/inference_npu.py --model /path/to/ssd320_mobilenetv4_conv_small.om --device 0
```

## 评估报告与模型规模

本目录的 CPU/NPU 评估报告由脚本按运行时间生成，不作为仓库固定文件发布。需要复现实测结果时，在准备好 ONNX/OM 模型和验证集后运行：

```bash
python scripts/inference_cpu.py --all
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/inference_npu.py --all --device 0
```

生成的 CSV 会保存到 `reports/`，记录 backbone、provider、mAP、AP50、AP75、small/medium/large AP、`fps_total` 和 `fps_inference` 等字段。`fps_total` 包含数据读取、预处理、推理、解码/NMS、COCO 结果写入等评估流程开销；`fps_inference` 在本脚本中包含单样本预处理、模型执行、输出整理和 decode/NMS，不含 COCO 汇总和 JSON 写盘。310B CPU 使用 ONNX Runtime `CPUExecutionProvider` 跑 ONNX，310B NPU 使用 PyACL 跑 ATC 转换后的 OM。

所有 ONNX 模型的静态规模如下。输入均为 `input:1x3x320x320`，输出均为 `boxes:1x4x3234` 和 `scores:1x81x3234`。
参数量统计 ONNX initializer 中的浮点权重元素数；`MACs` 统计 ONNX 图中 `Conv`、`Gemm`、`MatMul` 的理论乘加量；`FLOPs` 按 `1 MAC = 2 FLOPs` 换算。
该统计不包含 Python 侧预处理、decode、NMS、COCO 评估，也不代表 ATC 编译后在 NPU 上的实际融合算子耗时。

| Backbone | Params (M) | Param size (MiB) | ONNX size (MiB) | MACs (G) | FLOPs (G) | Nodes | Conv ops |
|---|---:|---:|---:|---:|---:|---:|---:|
| `mobilenetv4_hybrid_large` | 36.977 | 141.05 | 141.35 | 5.412 | 10.824 | 1017 | 180 |
| `mobilenetv4_conv_large` | 31.802 | 121.31 | 121.40 | 4.556 | 9.113 | 306 | 137 |
| `mobilenetv3_large_150d` | 13.632 | 52.00 | 52.10 | 1.733 | 3.466 | 350 | 122 |
| `mobilenetv4_hybrid_medium` | 10.311 | 39.33 | 39.51 | 2.143 | 4.285 | 628 | 155 |
| `mobilenetv4_conv_medium` | 8.950 | 34.14 | 34.21 | 1.807 | 3.615 | 265 | 112 |
| `mobilenetv1_125` | 7.262 | 27.70 | 27.76 | 2.042 | 4.084 | 242 | 63 |
| `mobilenetv1_100` | 5.213 | 19.89 | 19.94 | 1.361 | 2.723 | 242 | 63 |
| `mobilenetv1_100h` | 5.213 | 19.89 | 19.94 | 1.361 | 2.723 | 242 | 63 |
| `mobilenetv2_140` | 4.852 | 18.51 | 18.58 | 1.182 | 2.365 | 297 | 87 |
| `mobilenetv3_large_100` | 4.714 | 17.98 | 18.06 | 0.549 | 1.097 | 289 | 98 |
| `mobilenetv2_100` | 3.039 | 11.59 | 11.67 | 0.630 | 1.260 | 297 | 87 |
| `mobilenetv4_conv_small` | 2.996 | 11.43 | 11.48 | 0.484 | 0.969 | 218 | 81 |
| `mobilenetv3_small_100` | 2.340 | 8.93 | 9.00 | 0.181 | 0.362 | 269 | 88 |
| `mobilenetv2_050` | 1.564 | 5.97 | 6.04 | 0.212 | 0.424 | 297 | 87 |
| `mobilenetv3_small_050` | 1.449 | 5.53 | 5.60 | 0.089 | 0.177 | 270 | 88 |
