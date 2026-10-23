# 量化流程与数据合同

## 原始 ONNX 来源

`tools/download_model.py` 从 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet` 下载：

```text
model/resnet18_tiny_imagenet.onnx
```

默认端点为 `https://hf-mirror.com`，失败后回退到 `https://huggingface.co`。文件先写 `.tmp` 再替换，已有文件默认不覆盖。

下载脚本固定 revision：

```text
dfcf5885dcf7beab3a5d6e2143c73d7973bead36
```

ONNX 预期大小为 45,223,270 字节，SHA-256 为：

```text
e461aecee8bfafe95cd4528d3e836783c7546a0bb201c98e25109cde51b3bd6e
```

不匹配时脚本删除临时文件并失败。哈希通过只证明源模型完整；仍需检查 ONNX 输入合同。

## 模型输入输出合同

本章所有 ATC 命令假定：

| 项目 | 合同 |
|---|---|
| 输入名 | `input.1` |
| 输入形状 | `1x3x64x64` |
| 输入布局 | NCHW |
| 输入 dtype | float32 |
| 分类数 | 200 |
| 输出用途 | 第一个输出参与 Top-1/Top-k |
| SoC 示例 | `Ascend310B4` |

输入图像在 Python 侧先由 `preprocess_resnet_rgb()` 转成 RGB、nearest resize 到 64x64、除以 255，再应用 ImageNet mean/std。AMCT 校准也复用同一预处理函数，因此校准 ONNX、OM 推理和验证脚本使用相同数据前处理。

若替换模型，必须同步检查输入名、shape、dtype、颜色通道、均值方差、输出形状和 SoC，不能只改文件名。

## 校准和验证数据

### 下载

从 `samples/chapter9` 执行：

```bash
python tools/download_tiny_imagenet.py train --force-download --per-class 2
python tools/download_tiny_imagenet.py val --force-download
```

训练 split 默认每类取 2 张，共 400 张，写成 PNG；验证 split 默认取全部 10000 张，也写成 PNG。脚本把随机/原始图像统一转成 RGB PNG，文件名以数字标签开头。

`--offline` 只允许读取本地 Hugging Face dataset cache。`--force-download` 会先删除目标目录中已有图片和旧清单；执行前应确认路径，避免覆盖手工整理的数据。

### 校准清单

`data/calib_list.txt` 每行是一个图像路径，没有标签：

```text
tiny_imagenet_train/0_00000.png
tiny_imagenet_train/0_00001.png
```

相对路径默认相对于清单文件所在目录解析。清单中的空行和以 `#` 开头的行会跳过；没有有效条目时脚本报错。

### 验证清单

`data/val_list.txt` 每行严格为：

```text
tiny_imagenet_val/0_00000.png 0
tiny_imagenet_val/0_00001.png 0
```

第二列是整数标签，用于 Top-1/Top-k。格式不合法、列为空或文件不存在都会在校验阶段失败。相对路径默认相对于清单文件父目录解析；`05_validate_accuracy.py` 没有单独的 `--val-root`，需要保留其默认目录结构或直接写绝对路径。

## FP32 和 FP16 OM

从本章目录且加载 CANN 后，README 给出的基准转换命令等价于：

```bash
atc \
  --model=model/resnet18_tiny_imagenet.onnx \
  --framework=5 \
  --output=model/resnet18_tiny_imagenet_fp32 \
  --input_format=NCHW \
  --input_shape="input.1:1,3,64,64" \
  --soc_version=Ascend310B4 \
  --precision_mode=force_fp32 \
  --log=info

atc \
  --model=model/resnet18_tiny_imagenet.onnx \
  --framework=5 \
  --output=model/resnet18_tiny_imagenet_fp16 \
  --input_format=NCHW \
  --input_shape="input.1:1,3,64,64" \
  --soc_version=Ascend310B4 \
  --precision_mode=allow_fp32_to_fp16 \
  --log=info
```

ATC 退出码 0 和 OM 文件存在是转换证据。之后仍需 ACL 加载、输入合同检查、数值对齐和任务指标。

FP32 OM 作为本章默认数值参考。FP16 是部署候选，不应因为文件类型是 FP16 就假定精度可接受。

## INT8 AMCT 准备

```bash
python 03_prepare_quantization.py
```

默认行为：

- 输入 `model/resnet18_tiny_imagenet.onnx`；
- 读取 `data/calib_list.txt`；
- 默认把 ONNX opset 转到 16；
- 工作目录 `outputs/int8_amct/`；
- 生成 `config.json` 和 `updated_model.onnx`；
- `--samples N` 只影响配置中的 batch/sample 数，不是只截取物理文件；
- `--skip-layers` 指定不参与量化的层；
- `--activation-offset` 控制 AMCT 配置。

脚本设置 `TORCH_DEVICE_BACKEND_AUTOLOAD=0`，避免无关后端自动加载。需要 AMCT ONNX、ONNX 和 ONNX Runtime 与该 CANN 版本匹配。

## INT8 校准与冻结

```bash
python 04_calibrate_quantization.py
```

脚本在同一进程内依次：

1. 用步骤 03 的 `config.json` 重建 `modified_model.onnx`；
2. 创建禁用图优化的 ONNX Runtime session；
3. 使用 `CPUExecutionProvider`、单 intra/inter-op 线程执行校准帧；
4. 生成 `scale_offset_record.txt`；
5. 冻结并保存 deploy ONNX；
6. 将生成文件复制到 `model/resnet18_tiny_imagenet_int8_deploy.onnx`。

AMCT 0.23.x 的部分状态是进程内单例，因此 `quantize_model()` 与 `save_model()` 刻意放在同一脚本进程中。不要将该流程拆成两个 Python 进程，否则冻结模型可能缺少校准状态。

校准在 CPU 上运行，证明 AMCT 能收集激活范围并生成 deploy ONNX，不证明 NPU INT8 推理或精度。

## INT8 OM 转换

```bash
atc \
  --model=model/resnet18_tiny_imagenet_int8_deploy.onnx \
  --framework=5 \
  --output=model/resnet18_tiny_imagenet_int8 \
  --input_format=NCHW \
  --input_shape="input.1:1,3,64,64" \
  --soc_version=Ascend310B4 \
  --log=info
```

deploy ONNX 已内置量化信息，命令不再传 `--precision_mode`。需要避免给该模型额外指定会绕过量化属性的 precision 参数。

## 产物合同

| 产物 | 作用 | 后续消费者 |
|---|---|---|
| `updated_model.onnx` | AMCT 修改后的量化图 | `04_calibrate_quantization.py` |
| `config.json` | 量化层和策略 | `04_calibrate_quantization.py` |
| `scale_offset_record.txt` | 校准得到的 scale/zero-point | `amct.save_model()` |
| deploy ONNX | 冻结后的 INT8 图 | ATC |
| INT8 OM | 板端部署模型 | `01`、`02`、`05` |

更换校准子集时，应为每个子集使用独立工作目录和产物名，避免覆盖上一组 scale 或 deploy ONNX。sweep 脚本按样本数建立独立目录，正是为了保持这种对应关系。
