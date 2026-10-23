# SSDLite320 目标检测

`SSDLite/` 是本章独立度最高的样例：数据和后处理路径基于 numpy/Pillow，CPU 使用 ONNX Runtime，Ascend 使用 PyACL 加载 ATC 生成的 OM。训练源码不在本仓库，本目录只负责模型获取、转换、推理和评估。

## 模型合同

| 项目 | 合同 |
|---|---|
| 模型族 | MobileNet-SSDLite320，COCO 81 类（含 background） |
| 输入名 | ONNX 为 `input` |
| 输入 shape | `1×3×320×320` NCHW，`float32` |
| 输出名 | ONNX 为 `boxes`、`scores` |
| 输出 shape | `boxes: 1×4×3234`，`scores: 1×81×3234` |
| default boxes | 6 层，共 3234 个，`min_ratio=0.1`、`max_ratio=0.9` |
| 预处理 | RGB、直接缩放到 320×320、除以 255，并做 ImageNet mean/std 归一化 |
| 结果坐标 | normalized LTRB，评估时乘到 320×320 |

输出 shape 来自本目录 [README](../SSDLite/README.md) 对已发布模型的记录。运行时还会根据 output 元素数量和维度推断 boxes/scores 顺序；如果使用自备模型，应先用 ONNX Runtime 检查名称、shape、dtype 和元素数量。

`min_ratio`/`max_ratio` 是解码合同的一部分，不是任意调参旋钮。模型必须用相同 default-box 配置训练；只改 CLI 参数会造成定位解码错误。

## Python 依赖

[requirements.txt](../SSDLite/requirements.txt)列出：

```text
numpy
Pillow
onnxruntime
datasets
pycocotools
matplotlib
huggingface_hub
tqdm
```

`acl` 来自 CANN，不在 pip requirements 中。requirements 没有版本上限或锁文件；现有报告没有记录解析出的 exact versions。

从样例目录运行：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
cd samples/chapter4/SSDLite
```

CPU 路径只需 Python 依赖。ATC/NPU 路径还需在启动 shell 中 source CANN：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import acl; print('PyACL import ok')"
```

## 模型与数据路径

| 类型 | 默认路径 |
|---|---|
| ONNX | `weights/ssd320_{backbone}.onnx` |
| OM | `models/ssd320_{backbone}.om` |
| 验证 parquet | `data/val-*.parquet` |
| Hugging Face cache | `./data/hf_cache` |
| ground truth | `data/coco_gt.json` |
| 预测 JSON | `outputs/val_results/{run_id}/` |
| 可视化 | `outputs/viz_results/{run_id}/` |
| CSV 报告 | `reports/{run_id}_{backbone}.csv` |

模型搜索顺序封装在 [config.py](../SSDLite/ssdlite320/config.py)中：`weights/`、`models/`、`logs/`。数据下载位置由 [data.py](../SSDLite/ssdlite320/data.py)固定到 `SSDLite/data`，但 `--cache-dir`、`--gt-file`、`--result-file`、`--report-dir` 和 `--output-dir` 的相对路径仍相对当前工作目录。

## 下载模型

```bash
python scripts/download_models.py
python scripts/download_models.py --onnx --backbone mobilenetv4_conv_small
python scripts/download_models.py --om mobilenetv4_conv_small
python scripts/download_models.py --all --backbone mobilenetv4_conv_small
```

默认仓库是 `zhouxzh/SSDLite320`，端点取 `HF_ENDPOINT` 或 `https://hf-mirror.com`。下载器的文件 URL 固定为 `/resolve/main/`，没有 `--revision`，也不做哈希校验。当前模型 revision、Hub commit、每个 artifact 的 SHA-256 和模型权重许可证没有在本目录 manifest 中固定，发布报告时必须补齐。

`--output-dir` 可覆盖保存目录；若与默认命名不一致，后续用 `--model` 显式指定路径。

## ATC 转换

先检查实际命令。已有同名 OM 时脚本会在打印 ATC 前直接跳过，因此需要配合 `--force` 才能看到完整命令；`--dry-run` 不会真正覆盖文件：

```bash
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4 --force --dry-run
```

实际转换：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
```

[转换脚本](../SSDLite/scripts/convert_onnx_to_om.py)默认：

- 扫描 `weights/*.onnx`。
- `--framework=5`、`--input_format=NCHW`。
- `--input_shape=auto` 时根据文件名含 `ssd300`/`resnet` 或 `ssd320`/`mobilenet` 推断 300/320。
- 默认输入名为 `input`。
- 输出到 `models/{onnx_stem}.om`。
- 已存在 OM 默认跳过，`--force` 才重新生成。

可对单个文件转换：

```bash
python scripts/convert_onnx_to_om.py \
  weights/ssd320_mobilenetv4_conv_small.onnx \
  --soc-version Ascend310B4
```

ATC 返回非零或产物不存在时脚本返回失败。成功时还应另外保存 ATC 日志、CANN 版本、ONNX/OM 字节数和 SHA-256；脚本本身不生成这些 provenance 字段。

## CPU ONNX 评估

```bash
python scripts/inference_cpu.py --backbone mobilenetv4_conv_small --max-samples 50
```

只做少量可视化和接口烟测：

```bash
python scripts/inference_cpu.py \
  --backbone mobilenetv4_conv_small \
  --max-samples 1 \
  --skip-eval \
  --num-visualizations 1
```

CPU 入口实例化 [OnnxCpuRunner](../SSDLite/ssdlite320/backends.py)，强制 `CPUExecutionProvider`，并调用输出名 `boxes`、`scores`。

一次模型批量运行：

```bash
python scripts/inference_cpu.py --all
```

`--all` 会先删除已存在的 combined report，再为每个模型追加一行。若中途失败，之前已写入的行仍会保留。

`scripts/run_all_onnx.sh`是另一个批处理入口，默认把 `--max-samples` 设为 `50`，每个模型日志写到 `logs/run_all_onnx/{model}.log`；默认遇到首个失败即停止：

```bash
scripts/run_all_onnx.sh
scripts/run_all_onnx.sh --max-samples 0 --continue-on-error
```

它只运行 CPU ONNX，不是 NPU 批测脚本。

## NPU OM 评估

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/inference_npu.py \
  --backbone mobilenetv4_conv_small \
  --device 0 \
  --max-samples 50
```

显式模型路径：

```bash
python scripts/inference_npu.py \
  --model models/ssd320_mobilenetv4_conv_small.om \
  --device 0 \
  --max-samples 50
```

[AclNpuRunner](../SSDLite/ssdlite320/backends.py)创建 ACL context/stream、加载 OM、按模型 description 预分配所有输入/输出 buffer，并复用它们完成逐样本推理。正常路径在 `finally` 中释放 dataset/buffer/model/context 并 finalize。

严格输入合同是 `1×3×320×320 float32`。runner 只比较输入字节数是否“大于”第一个输入 buffer；较小输入仍会执行，且模型有多个输入时只填充第一个。输出统一按 `float32` 解析。当前代码没有在入口处自动拒绝这些合同偏差，替换模型或改变 `--img-size` 时应先补充硬校验。

## 数据与评估口径

默认数据是 Hugging Face `detection-datasets/coco` 的 `val` split，仅读取 `data/val-*.parquet` 分片。缓存模式会先查找本地分片，再通过 `huggingface_hub.snapshot_download` 下载：

```bash
python scripts/inference_cpu.py --backbone mobilenetv4_conv_small --max-samples 50
python scripts/inference_cpu.py --backbone mobilenetv4_conv_small --streaming --max-samples 50
```

`--data-files ""` 会让 `datasets` 按仓库默认规则解析，可能触发 train+val 大缓存；本样例明确拒绝空值参与本地缓存路径映射。缺少数据集时应停止并保留源 repo、pattern、cache 和异常。

评估流程：

1. 从 HF 样本读取 `image`、`image_id` 和 `objects`。
2. 把 bbox 以 XYXY 格式缩放到 320×320，类别编号加 1。
3. 预处理图像并调用 backend。
4. 根据 default boxes 解码 loc/conf，softmax，执行每类阈值过滤和 NMS。
5. 将检测框转换为 COCO `[x,y,w,h]`，写入 predictions JSON。
6. 用 `pycocotools.COCOeval` 汇总 AP/AR 指标。

未设置 `--max-samples` 时会遍历整个验证 split。设置后，生成的 GT 也只包含被选样本；这适合烟测或模型间同子集比较，不能与完整 COCO 指标混用。

## 报告字段与计时

每次单模型评估默认新建：

```text
reports/{provider}_eval_YYYYMMDD_HHMMSS_{backbone}.csv
outputs/val_results/{provider}_eval_YYYYMMDD_HHMMSS/...
```

CSV 字段包括 timestamp、backbone、model path、provider、输入尺寸、default-box ratio、mAP/AP50/AP75、small/medium/large AP、`fps_total`、`fps_inference` 和 prediction JSON 路径。

- `fps_total` 包含数据迭代、预处理、推理、解码/NMS、结果写入和评估外壳。
- `fps_inference` 仍包含逐样本预处理、backend 推理、输出整理和 decode/NMS，不包含最终 COCO 汇总与 JSON 写盘。
- 两者都不是纯模型 latency，也不是板卡功耗或稳定性测试。
- 没有 warmup 参数，没有重复运行统计，也没有 percentile。

## 失败边界

- `acl` 导入失败、device/context 创建失败、OM 加载失败或输出 dtype/shape 不匹配时停止。
- ATC 失败后不得把旧 OM 当作本次结果。
- `--img-size` 改变后必须确认模型、default boxes 和后处理共同使用新尺寸；当前模型合同固定为 320。
- 输入字节小于模型 buffer、多个模型输入、非 FP32 输出目前不会在 runner 入口主动拒绝，需要模型接口预检。
- 数据或模型下载失败时不能继续使用同名旧缓存。
- 没有预测时脚本会写 `mAP=0.0`；该值可能是阈值/模型/数据错误，不得直接当作任务质量结论。
- `pycocotools` 在 HF 派生 GT 上计算的是本地 COCO 协议指标，不是 COCO 官方测试服务器提交。
- 本目录没有 pytest；验证入口是 CLI、`--dry-run`、ONNX 接口检查和板端运行日志。

历史报告见[验证与证据边界](05-validation-and-evidence-boundaries.md)。
