# case8 模型、数据集与转换

## 🧾 模型资产

代码仓库不包含权重、ONNX 或 OM 二进制。它们由使用者准备后放入 `weights/`
或 `models/`；同名的标签文件和元数据文件应与模型一起保存。

| 文件 | 用途 | 运行位置 |
| --- | --- | --- |
| `YOLOv10n_gestures.pt` | HaGRID 手势检测权重 | PC/GPU 工作站 |
| `YOLOv10x_gestures.pt` | 较大手势检测权重 | PC/GPU 工作站 |
| `YOLOv10n_hands.pt` | 手部检测权重 | PC/GPU 工作站 |
| `YOLOv10x_hands.pt` | 较大手部检测权重 | PC/GPU 工作站 |
| `*.onnx` | ATC 的输入模型 | 开发板转换前 |
| `*_labels.txt` | 类别编号到名称的映射 | ONNX/OM 推理 |
| `*_metadata.json` | 输入名、形状、类别和导出参数 | 导出和 ATC |
| `*.om` | Ascend 离线模型 | 310B ACL 推理 |

WebRTC 默认使用 `models/YOLOv10n_gestures.om`。实际运行前必须确认 OM、
标签文件和元数据来自同一次导出；文档不把文件存在写成文件内容已经验证。

## 🖐️ HaGRID 数据和标签

本案例使用 HaGRID 提供的手势检测权重，不在本项目中重新训练。模型元数据
中的 `names` 数组是唯一的类别顺序，导出脚本把它写入
`<model>_labels.txt`。因此后处理只保存整数 `class_id`，绘制时按标签文件
查找文字，避免在源码中复制一份可能过期的类别表。

数据集原始文件、训练划分和许可内容由 [HaGRID 项目][^1] 管理；本案例只需
准备与权重匹配的模型和标签文件。未在当前工作区中的数据集、权重和准确率
均标记为 `untested`，不能从实时推流成功推导出识别质量。

## 📐 模型输入输出契约

当前示例模型采用静态输入：

```text
input:  1 x 3 x 640 x 640, float32 或模型声明的等价类型
output: 由 OM 描述决定，当前 YOLOv10n 手势样例实测为 1 x 300 x 6
```

程序从 OM 描述读取输入输出数量、字节数和形状。`preprocess_image()` 生成
NCHW 张量；`AclModel._prepare_input_bytes()` 根据模型输入大小选择
`float32` 或 `float16` 字节表示；`decode_detections()` 要求每行至少包含
四个框坐标、一个分数和一个类别编号。

模型输入尺寸由模型契约决定，不能在网页中临时改成另一个尺寸。摄像头分辨
率可以改变，但它会先经过 letterbox 变成模型固定输入。

## 🔧 ONNX 导出

在 PC 或 GPU 工作站上执行：

```bash
cd samples/case8
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models \
  --imgsz 640 \
  --batch 1 \
  --opset 13 \
  --device cpu
```

脚本调用 Ultralytics 导出接口，然后执行 `onnx.checker`，并写出：

```text
models/YOLOv10n_gestures.onnx
models/YOLOv10n_gestures_labels.txt
models/YOLOv10n_gestures_metadata.json
```

`--opset 13` 是当前转换起点；只有 ATC 明确需要时，才单独尝试
`--opset 17`。导出后的 ONNX 可以先用 `scripts/infer_onnx_camera.py` 做
CPU 功能验证，但这不等于 OM 已经可用。

## 🏗️ ATC 转换

ATC 只在目标开发板上执行，并且必须先加载 CANN 环境：

```bash
cd /home/HwHiAiUser/Documents/case8
source /usr/local/Ascend/ascend-toolkit/set_env.sh
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
```

转换单个模型：

```bash
SOC_VERSION=Ascend310B4 \
  bash scripts/atc_convert.sh \
  models/YOLOv10n_gestures.onnx \
  models/YOLOv10n_gestures
```

脚本的核心参数是：

```text
--framework=5
--input_format=NCHW
--input_shape=<输入名>:1,3,640,640
--soc_version=Ascend310B4
```

如果同名 metadata 文件存在，脚本读取其中的输入名和形状；否则使用默认
输入名和尺寸。ATC 输出 `ATC run success` 只能证明转换完成，还需要继续做
ACL 加载和实际推理验证。

## ✅ 验证顺序

模型验证分成四步：

1. **资产检查**：确认权重、ONNX、标签、元数据的路径和 SHA-256。
2. **ONNX 检查**：执行 `onnx.checker` 和一次 CPU 推理。
3. **ATC 检查**：记录完整命令、`SOC_VERSION`、输出大小和转换日志。
4. **ACL 检查**：在 310B 上加载 OM，执行 benchmark 和摄像头推理。

任一步失败，只能标记对应层级失败。例如 ONNX 通过不代表 ACL 通过，ACL
单帧通过也不代表 WebRTC 能持续编码。

## 🔐 资产保存规则

权重、数据集、ONNX、OM、摄像头图片和运行报告不提交 Git。需要记录时只在
测试文档中写出文件名、大小、SHA-256、生成命令和板端路径。模型内容缺失或
哈希未核对时，状态写为 `untested`。

[^1]: HaGRID project. https://github.com/hukenovs/hagrid
[^2]: Ultralytics YOLO documentation. https://docs.ultralytics.com/
