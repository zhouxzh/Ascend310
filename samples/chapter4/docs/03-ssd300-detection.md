# SSD300 目标检测

`SSD/` 的模型定义来自 NVIDIA DeepLearningExamples 的 SSD300 v1.1 代码，代码许可见 [LICENSE.md](../SSD/LICENSE.md)（Apache-2.0）。本仓库增加了 Hugging Face 数据适配、CPU/OM 推理脚本和模型下载脚本。现有 [NVIDIA README](../SSD/README.md) 中的 A100/V100 准确率、吞吐和训练时间不是 Ascend 310B 证据。

## 入口矩阵

| 用途 | 入口 | 后端 | 当前位置状态 |
|---|---|---|---|
| 下载 ONNX/OM | [download_models.py](../SSD/download_models.py) | HTTP 下载 | 默认仓库 `zhouxzh/SSD300` |
| CUDA 训练 | [train_cuda.py](../SSD/train_cuda.py) | PyTorch CUDA AMP | 未在本次任务运行 |
| CPU 推理 | [inference_cpu.py](../SSD/inference_cpu.py) | ONNX Runtime CPU | 无本地 ONNX 时不可运行 |
| CUDA 推理 | [inference_cuda.py](../SSD/inference_cuda.py) | ONNX Runtime CUDA | 未在本次任务运行 |
| Ascend 推理 | [inference_npu.py](../SSD/inference_npu.py) | PyACL OM | 无本地 OM 时不可运行 |

当前目录的 `models/` 没有可列出的 SSD300 ONNX/OM 产物，`data/` 也没有完整 COCO 数据提交。因此当前源码的端到端训练、推理、mAP 和性能均为 `untested`。

## 模型合同

| 项目 | 合同 |
|---|---|
| 模型 | SSD300 v1.1，ResNet 主干，COCO 81 类（含 background） |
| 输入 | `float32`，NCHW `1×3×300×300` |
| 预处理 | RGB、直接缩放至 `300×300`、ImageNet mean/std 归一化 |
| 输出 1 | `boxes`/localization，`float32`，`1×4×8732` |
| 输出 2 | `scores`/classification，`float32`，`1×81×8732` |
| 文件命名 | `ssd300_{backbone}.onnx` / `ssd300_{backbone}.om` |
| 默认 backbone | `resnet50` |

ONNX 导出使用输入名 `input`、输出名 `boxes` 和 `scores`。NPU runner 不依赖名称，而是把“维度中含 4 的张量”识别为 localization，把另一个识别为 scores；若两个输出都含维度 4，则退化到按元素数量判断。出现多输出、附加输出或输出 dtype 不是 `float32` 时，现有 runner 不能正确解析。

## 模型获取

下载脚本从 Hugging Face `zhouxzh/SSD300` 获取单文件，镜像默认是 `https://hf-mirror.com`：

```bash
cd samples/chapter4/SSD
python download_models.py --onnx --backbone resnet50
python download_models.py --om --backbone resnet50
```

可先用 dry-run 检查 URL：

```bash
python download_models.py --onnx --backbone all --dry-run
```

限制：

- URL 固定使用 `/resolve/main/`，没有 revision 参数，仓库也没有保存该分支对应 commit。
- 脚本不做 SHA-256、字节数、ONNX 接口或 OM 来源校验。
- 已有文件默认跳过，只有 `--force` 才覆盖；旧文件可能被误当作当前发布版本。
- 下载失败会计数并以状态 1 退出；HTTP 200 的 LFS 指针或错误内容仍可能被保存，需要另做模型校验。

正式发布前应固定 Hub commit，记录每个文件的字节数和 SHA-256，并用 ONNX Runtime 或 `onnx` 工具核对输入/输出合同。

## COCO 数据合同

所有入口统一把 Hugging Face object-detection `objects.bbox` 解释为 `[xmin, ymin, xmax, ymax]`（XYXY）：

| 入口 | 数据集 | bbox 处理 |
|---|---|---|
| 训练 | `detection-datasets/coco` 的 `train`/`val` | [data_hf.py](../SSD/ssd/data_hf.py) 按 XYXY 编码 |
| CUDA 推理 | `detection-datasets/coco` 的 `val` | 同一数据模块 |
| CPU 推理 | `zhouxzh/coco-val` 的 `val` | [inference_cpu.py](../SSD/inference_cpu.py) 调用共享 XYXY 转换 |
| NPU 推理 | `zhouxzh/coco-val` 的 `val` | [inference_npu.py](../SSD/inference_npu.py) 调用同一共享转换 |

[bbox_utils.py](../SSD/bbox_utils.py) 统一负责源图 XYXY 到 300×300 canvas 的缩放，以及 COCO GT 所需的 XYWH 转换。CPU 和 NPU 不再各自解释后两个字段。[test_bbox_utils.py](../SSD/tests/test_bbox_utils.py) 覆盖坐标顺序、缩放和非法框拒绝。

可视化仍要求 XYXY，COCO 评测集要求 XYWH；两者之间的转换现在只存在于共享函数中。统一 bbox 合同不等于 mAP 已通过，CPU/NPU 推理结果仍需在真实模型和数据集上分别验收。

数据缓存目录多数是 `./data`，训练 loader 期望 `train`/`val` split。网络不可达、数据集字段变化、split 缺失和 bbox 格式变化都会在加载或编码阶段失败；不要让流程在自动回退到另一数据集后继续生成同名报告。

## CUDA 训练

实际 argparse 入口没有 `--data` 参数，数据固定从 Hugging Face 下载：

```bash
cd samples/chapter4/SSD
python train_cuda.py --device cuda --batch-size 128 --epochs 65
```

主要参数为 SGD `lr=2.6e-3`（再按 batch/32 缩放）、momentum `0.9`、weight decay `5e-4`、MultiStepLR milestones `[43,54]`。当前训练循环硬编码 `torch.amp.GradScaler("cuda")` 和 `torch.amp.autocast("cuda")`，所以 `--device cpu` 不属于可用训练路径。

训练会：

- 在 `./logs/{backbone}` 写 TensorBoard。
- 每 epoch 在 `./checkpoints/` 保存 `ssd300_{backbone}_{epoch}.pth`。
- 每 epoch 做验证/可视化和 COCO 指标计算。
- 训练结束后在 `./models/ssd300_{backbone}.onnx` 导出 ONNX。

`--restart` 只查找并加载模型 state dict，优化器状态没有恢复，可能使再训练轨迹不同于未中断运行。所有输出路径均相对当前工作目录。

## CPU 与 CUDA ONNX 推理

```bash
cd samples/chapter4/SSD
python inference_cpu.py --backbone resnet50
python inference_cuda.py --backbone resnet50
```

两条入口读取 `models/ssd300_{backbone}.onnx`，输出前 10 张可视化和全量验证结果到相对当前目录的 `viz_results/`、`inference_results.json` 和 `coco_gt_temp.json`。CUDA 入口强制请求 `CUDAExecutionProvider`，不可用时退出。

计时字段来自脚本自己的 wall clock：

- `全流程 FPS` 包含数据迭代、预处理、推理、解码和结果生成。
- `纯推理+解码 FPS` 在当前脚本中仍包含预处理和 decode/NMS。
- 这不是 OmniBenchmark 或纯模型延迟，不能与供应商 README 的 GPU 吞吐直接比较。

## Ascend PyACL OM 推理

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/chapter4/SSD
python inference_npu.py --device 0 --limit 20 --skip-map
```

默认 OM 是 `models/ssd300_resnet50.om`。`--limit` 限制验证样本数，`--skip-map` 只跳过 COCO mAP 汇总，仍会运行 10 张可视化和逐样本推理。完整模式：

```bash
python inference_npu.py --device 0
```

[NPU runner](../SSD/inference_npu.py)在构造时创建 context、stream、model、输入/输出 dataset 和复用设备 buffer；每次 `infer()` 只把数据复制到第一个输入 buffer。模型必须只有一个输入。

输入必须正好是连续的 `1×3×300×300 float32` 字节。runner 只拒绝“大于”模型 buffer 的输入，较小输入仍会复制并继续执行，因此调用方必须自行把尺寸不一致作为硬失败。输出统一按 `float32` 解析；非 FP32 输出会被错误解释。

正常退出时 `finally` 调用 `release()`，顺序为销毁 data buffer、dataset、model description、model、stream、context，再 reset device 和 finalize。构造中途失败时，已分配的部分资源主要由进程退出回收，脚本没有分阶段回滚。

## 后处理合同

[numpy 后处理](../SSD/utils_cpu.py)使用 6 个特征层和 8732 个 default boxes：

- 坐标编码顺序为 `xywh`，比较使用 normalized `ltrb`。
- 类别维为 81，索引 0 是 background。
- 解码执行 softmax，按类别阈值 `0.05` 过滤，分类内 NMS IoU 阈值 `0.5`，每图最多 200 个输出。
- 结果转成 COCO `[x,y,w,h]` 时乘以 `300×300`，不做原图坐标反变换。

因此报告口径是“缩放到 300×300 的验证图像上的 COCO AP”，并非原分辨率 COCO server 提交结果。

## 失败边界

- 当前没有 ONNX/OM，不能执行端到端推理。
- ONNX 输出名称必须为 `boxes`、`scores`；OM 输出必须可被 shape 启发式识别。
- NPU 输出 dtype 必须是 FP32。
- 数据集 bbox 解释不一致时停止比较 mAP。
- `pycocotools` 缺失或 GT 文件缺失时脚本打印信息；NPU `--skip-map` 时没有 AP 结果。
- 预测为空时不能把 0 条结果写成模型达到 0 AP 的结论；应检查阈值、解码和输出顺序。
- 任何 310B latency 必须记录 SoC 8T/20T、CANN/驱动/固件、模型 revision、输入、warmup、迭代数、重复次数和原始报告。

本目录没有 pytest。验证需要分别记录 ONNX 接口检查、ATC 转换、OM 加载、数值一致性、COCO 任务质量、性能和服务稳定性，不能用其中一个门槛替代另一个。
