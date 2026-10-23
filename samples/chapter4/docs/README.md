# 第4章样例文档索引

本目录说明 `samples/chapter4` 中 PyACL、ONNX Runtime、ATC 和 COCO 目标检测样例的实际入口与证据边界。`resnet18/`、`SSD/`、`SSDLite/` 是三条独立路径，模型、数据、运行目录和结果不能混用。

## 文档地图

| 文档 | 内容 |
|---|---|
| [运行环境与入口](01-runtime-and-device-entrypoints.md) | conda/CANN 前置条件、设备烟测、目录约束和本地/板端分工 |
| [ResNet18 Tiny-ImageNet 分类](02-resnet18-classification.md) | 数据集、ONNX/OM、PyACL 与 ONNX Runtime 输入输出合同、模型产物校验 |
| [SSD300 目标检测](03-ssd300-detection.md) | 第三方来源边界、SSD300 ONNX/OM、COCO 数据、训练与推理路径 |
| [SSDLite320 目标检测](04-ssdlite320-detection.md) | `ssd320_*` 模型合同、下载、ATC、CPU/NPU 评估、报告字段和失败边界 |
| [验证与证据边界](05-validation-and-evidence-boundaries.md) | 现有报告、历史预测、未验证项和可复现性缺口 |

## 路径分工

| 路径 | 设备 | 输入 | 输出 |
|---|---|---|---|
| `check_ascend_device/` | Ascend PyACL | 无模型、无数据 | 设备数量打印 |
| `resnet18/` | CUDA/CPU 训练；CPU ONNX Runtime 与 Ascend PyACL 推理 | Tiny-ImageNet 64×64，200 类 | 分类准确率、FPS、ONNX/OM |
| `SSD/` | CUDA 训练；CPU ONNX Runtime、CUDA ONNX Runtime、Ascend PyACL 推理 | COCO 图像，模型输入 300×300 | 检测框、可视化、mAP/FPS |
| `SSDLite/` | CPU ONNX Runtime 与 Ascend PyACL 推理；板端 ATC | COCO 验证集，模型输入 320×320 | 检测框、可视化、COCO 指标、CSV/JSON |

## 当前证据状态

- 源码、命令行和模型接口属于 `documented`。
- ResNet18 的 ONNX 文件 SHA-256 已与本地 Hugging Face 元数据核对一致；同目录 OM 文件与缓存元数据中的哈希不一致，当前 OM 的生成来源为 `blocked`。
- `SSDLite/reports/` 有两份 2026-06-03 的历史 15 行评估报告和对应预测 JSON；它们可以作为历史产物索引，但缺少 SoC、CANN、驱动、提交号、模型 revision 和运行人信息，不构成本次 `observed-pass` 复验。
- `SSD/` 没有提交 ONNX/OM、COCO 数据或运行报告；当前为 `untested`。
- 本次任务没有执行 310B、CANN、ATC、PyACL、摄像头或 NPU 推理测试。

## 运行目录规则

板端优先在各自的样例目录启动，避免相对路径落到仓库根目录：

```bash
cd samples/chapter4/resnet18
# 或 cd samples/chapter4/SSD
# 或 cd samples/chapter4/SSDLite
```

`SSDLite` 的模型/数据包路径按包位置解析，但 `data/hf_cache`、`reports/`、`outputs/` 等评估参数仍相对当前工作目录。`resnet18` 和 `SSD` 的若干 `./data`、`model/`、`outputs/` 路径也依赖当前工作目录。

## 证据标记

- `documented`：由当前源码、随目录 README 或模型元数据确认。
- `observed-pass`：有可追溯运行记录且环境与输入足够对应。
- `observed-fail`：有可追溯失败记录。
- `blocked`：执行过，但缺少必要前置或根因。
- `untested`：没有当前环境下的执行证据。

现有历史 CSV/JSON 默认按 `documented historical artifact` 处理，除非另有完整环境快照和重跑命令。
