---
marp: true
size: 16:9
theme: default
paginate: true
header: "昇腾310B 8周教学"
footer: "第6周：目标跟踪"
---

# 第6周：目标跟踪

昇腾 310B 8周教学
每周 3 课时，每课时 45 分钟
对应案例：`samples/case2`

---

## 本周课程安排

| 课时 | 时长 | 主题 | 核心产出 |
|---|---|---|---|
| 第1课时 | 45分钟 | 项目与算法基础 | 读懂检测入口、跟踪入口及算法主线 |
| 第2课时 | 45分钟 | 模型准备 | 下载 ONNX，用 ATC 生成 OM |
| 第3课时 | 45分钟 | 运行验证 | 运行 detection 与 tracking，观察 ID 和轨迹 |

三条主线贯穿全程：检测负责“看见目标”，跟踪负责“维持目标身份”，模型准备负责把 ONNX 部署到昇腾 NPU。

---

## 本课主线

- 使用 MobileNet-SSD 完成实时目标检测
- 使用简化版 DeepSORT 风格跟踪器完成多目标跟踪
- CPU 与 Ascend NPU 共用统一检测后端接口
- 最终形成“采集 → 检测 → 解码 → 关联 → 轨迹”的完整链路

对应代码位置：`samples/case2`。

---

## 第1课时：项目与算法基础

建议节奏：

- 0–10 分钟：案例目标、硬件条件、目录结构
- 10–30 分钟：MobileNet-SSD、NMS、DeepSORT 核心算法
- 30–45 分钟：对照 `detection_app.py` 与 `tracking_app.py` 读懂入口

本课不急于运行模型，先把“每一层代码负责什么”讲清楚。

---

## 案例定位

本案例是一条标准的视频目标分析流水线：

- 输入：USB 摄像头或本地视频
- 检测：MobileNet-SSD 输出目标框、类别、分数
- 解码：把模型张量变成可用的检测框
- 跟踪：简化版 DeepSORT 维持目标 ID 和轨迹
- 输出：带框、类别、ID、轨迹拖尾的画面

关键判断：目标检测只解决单帧中的“目标在哪里、是什么”，目标跟踪解决连续帧中的“目标是否还是同一个”。

---

## 硬件与运行条件

| 条件 | 说明 |
|---|---|
| Linux 主机或昇腾开发环境 | 运行案例的基础环境 |
| USB 摄像头 | 实时演示输入，使用 `--source 0` |
| Ascend 310B 或兼容 NPU | 运行 `npu` 模式时需要 |
| Ascend ACL Python 运行时 | NPU 模式必需 |
| `.om` 模型 | NPU 模式推理文件 |

只运行 `cpu` 模式时可以不连接 NPU；离线演示可直接使用 `--source demo/vtest.avi`。

---

## `samples/case2` 目录结构

```text
case2/
├── scripts/
│   ├── detection_app.py
│   ├── tracking_app.py
│   ├── download_models.py
│   └── convert_onnx_to_om.py
├── models/
│   ├── *.onnx
│   └── *.om
├── ssdlite/
│   ├── backend_base.py
│   ├── cpu_backend.py
│   ├── decoder.py
│   └── npu_backend.py
├── tracking/
│   ├── deepsort.py
│   └── kalman_filter.py
├── utils/
│   ├── opencv_runtime.py
│   ├── postprocessing.py
│   └── preprocessing.py
├── README.md
└── requirements.txt
```

`scripts/` 是运行入口，`ssdlite/`、`tracking/`、`utils/` 是支撑模块。

---

## Python 文件清单与职责

| 路径 | 职责 |
|---|---|
| `scripts/detection_app.py` | 检测入口：视频输入、推理、解码、可视化 |
| `scripts/tracking_app.py` | 跟踪入口：检测结果转轨迹输入并更新轨迹 |
| `scripts/download_models.py` | 从 Hugging Face 下载 SSDLite320 模型 |
| `scripts/convert_onnx_to_om.py` | 在板端调用 ATC 把 ONNX 转 OM |
| `ssdlite/backend_base.py` | CPU/NPU 统一检测后端基类 |
| `ssdlite/cpu_backend.py` | ONNXRuntime CPU 推理 |
| `ssdlite/npu_backend.py` | Ascend ACL NPU 推理 |
| `ssdlite/decoder.py` | default boxes、SSD 解码、NMS |
| `tracking/deepsort.py` | Track、匹配、轨迹生命周期管理 |
| `tracking/kalman_filter.py` | 卡尔曼滤波预测与更新 |
| `utils/postprocessing.py` | 检测/轨迹绘制、检测到跟踪的格式转换 |
| `utils/preprocessing.py` | 模型发现、标签、摄像头参数、视频写出 |
| `utils/opencv_runtime.py` | OpenCV 初始化、V4L2、阶段计时 |

---

## `detection_app.py` 的主流程

入口脚本负责流程编排，模型细节下沉到后端模块。真实代码主流程如下：

```python
labels = load_labels(args.labels)
model_path = resolve_model_path(args.model, args.backbone, model_dir, args.device)
backend = create_backend(args.device, model_path, args.device_id)
capture_context = open_capture_context(args.source, args.camera_profile, args.camera_mjpeg)
```

每帧循环中调用统一推理接口：

```python
detections, profile_ms = backend.infer_with_profile(
    frame,
    args.score_threshold,
    args.nms_threshold,
    args.max_detections,
)
annotated = draw_detections(frame, detections, labels, fps, model_path.name, args.device, avg_timings_ms)
```

这段代码对应“解析参数 → 加载标签 → 找模型 → 创建后端 → 打开输入 → 逐帧推理 → 绘制”。

---

## `tracking_app.py` 的桥接作用

检测模块与跟踪模块的桥接代码：

```python
detections, profile_ms = backend.infer_with_profile(
    frame,
    args.score_threshold,
    args.nms_threshold,
    args.max_detections,
    allowed_class_ids=allowed_track_class_ids,
)
tracker_inputs = detections_to_tracker_inputs(detections)
tracks = tracker.update(tracker_inputs)
annotated = draw_tracks(...)
```

`tracking_app.py` 比 `detection_app.py` 多了三步：指定跟踪类别、把检测结果转为跟踪器输入、调用跟踪器更新轨迹。

---

## 检测与跟踪的分工

检测只回答两个问题：

- 目标在哪里：输出 `[x1, y1, x2, y2]`
- 目标属于什么类别：输出 `class_id` 和分数

跟踪回答时间维度的问题：

- 当前左侧行人是不是上一帧的 3 号目标
- 遮挡后重新出现的车辆是否还是同一辆
- 两个目标交叉时 ID 是否保持不交换

因此，跟踪可以理解为“目标检测在时间维度上的延伸”。

---

## 为什么选择 MobileNet-SSD

边缘设备上的检测前端要同时考虑：

- 实时性是否足够
- 模型规模是否可控
- 推理链路是否简单稳定
- 是否便于部署到 CPU 或 NPU

MobileNet-SSD 是轻量、经典、工程路径清晰的组合：MobileNet 降低计算量，SSD 直接输出检测结果，适合作为昇腾 310B 入门案例。

---

## MobileNet 的核心思想

MobileNet 用深度可分离卷积替代标准卷积：

- Depthwise Convolution：对每个输入通道分别做空间卷积
- Pointwise Convolution：用 1×1 卷积完成通道融合

标准卷积计算量近似为 `D² · K² · M · N`，深度可分离卷积计算量近似为 `D² · K² · M + D² · M · N`。当 `K=3` 时，这种拆分能明显降低边缘侧计算成本。

---

## MobileNet 版本演进

| 版本 | 核心改进 | 教学价值 |
|---|---|---|
| v1 | 系统化使用深度可分离卷积 | 最直观的轻量化结构 |
| v2 | inverted residual + linear bottleneck | 轻量化下保留表达能力 |
| v3 | SE 注意力、h-swish、结构搜索 | 精度与真实设备速度平衡 |
| v4 | 硬件感知、模块组合优化 | 面向部署性能的后续演化 |

本案例仓库同时支持 `mobilenetv1`、`mobilenetv2`、`mobilenetv3`、`mobilenetv3_large_100`、`mobilenetv4` 等骨干，也支持 ResNet 系列对照实验。

---

## SSD 的检测特点

SSD 是 Single Shot MultiBox Detector，典型一阶段检测器：

- 在不同尺度特征图上直接预测目标位置和类别
- 每个位置预先定义 default boxes / prior boxes
- 网络回归 default box 的位置偏移，同时预测类别分数
- 解码并执行 NMS 后输出最终检测框

SSD 推理速度快、结构直接，但对小目标、密集目标和复杂遮挡的稳定性不如更新的检测器。

---

## NMS 为什么重要

SSD 会在每个特征位置、每种先验框上产生候选框，同一目标往往有多个高重叠框。NMS 做去重：

1. 按置信度从高到低排序
2. 取当前最高分框作为保留框
3. 计算其余框与该框的 IOU
4. 删除 IOU 超过阈值的框，继续处理剩余框

真实实现：

```python
def _nms(boxes, scores, iou_threshold, presorted=False):
    if len(boxes) == 0:
        return np.empty((0,), dtype=np.int64)
    if presorted:
        order = np.arange(scores.size, dtype=np.int64)
    else:
        order = np.argsort(scores)[::-1]
    keep = []
    while order.size > 0:
        index = order[0]
        keep.append(index)
        if order.size == 1:
            break
        remaining = order[1:]
        iou = _calc_iou_with_box(boxes[remaining], boxes[index])
        order = remaining[iou <= iou_threshold]
    return np.array(keep, dtype=np.int64)
```

这段代码体现经典 NMS 的全部核心步骤：空输入处理、按分数排序、逐轮保留最高分框、按 IOU 过滤重复框。

---

## NMS 对跟踪的影响

跟踪器把检测结果当作观测输入，因此 NMS 不是孤立的后处理：

- 重复框会让同一目标在同一帧生成多条新轨迹
- 多对一竞争会导致 ID 抖动甚至 ID switch
- 轨迹数目虚高，画面混乱

当前实现按类别分别做 NMS，避免行人和自行车等不同类别之间互相抑制。

---

## 为什么选择简化版 DeepSORT

完整 DeepSORT 在 SORT 基础上增加外观特征建模，本案例保留最核心的入门结构：

- 卡尔曼滤波做运动预测
- 匈牙利算法做全局匹配
- IOU 作为主要几何相似度
- 轨迹生命周期参数管理目标的出现与消失

这套方案思路完整、数学和工程难度适中，不需要先掌握 ReID 网络训练。

---

## 卡尔曼滤波在做什么

检测框存在抖动、漏检和噪声，卡尔曼滤波根据历史状态平滑估计目标当前位置，并在短时无观测时维持轨迹。

真实代码中的预测阶段：

```python
def predict(self):
    self.x = self.F @ self.x
    self.P = self.F @ self.P @ self.F.T + self.Q
    return self.x
```

状态向量是 `[x, y, vx, vy]`，观测只包含目标中心 `[x, y]`。预测外推状态，更新阶段用当前观测修正状态。

---

## 数据关联为什么用匈牙利算法

当多条轨迹和多个检测框同时存在，不能只做局部贪心匹配。真实代码：

```python
iou_matrix = self._calculate_iou_matrix(detections)
class_mask = self._calculate_class_compatibility_matrix(detections)
assignment_scores = np.where(class_mask, iou_matrix, -1.0)
matched_indices = self._linear_assignment(assignment_scores)
```

匈牙利算法从全局寻找一一对应匹配，类别兼容矩阵在匹配前屏蔽“行人轨迹匹配车辆框”这类明显错误。

---

## 轨迹生命周期

`Track` 维护 `track_id`、`bbox`、`score`、`class_id`、`trail`、`time_since_update`、`hits` 等状态。

关键参数：

- `max_age`：轨迹最长失配帧数，过大不易断轨，过小短时遮挡即断
- `min_hits`：至少匹配多少次才显示，过小会让误检形成短暂轨迹
- `iou_threshold`：关联所需最小 IOU，过高快速目标易失配，过低邻近目标易错配

这些参数是第3课时调参实验的核心观察点。

---

## 第1课时小结与课堂验证

学生应能回答：

- `detection_app.py` 和 `tracking_app.py` 的区别
- SSD 检测输出什么，跟踪器又增加什么
- MobileNet 为什么适合边缘侧
- NMS、卡尔曼滤波、匈牙利算法分别解决什么问题

课堂快速验证：在 `samples/case2` 中打开两个入口脚本，逐行指出“入口层、检测层、跟踪层”的对应代码。

---

## 第2课时：模型准备

建议节奏：

- 0–10 分钟：环境依赖与模型来源
- 10–20 分钟：下载 ONNX、理解模型命名
- 20–40 分钟：板端 ATC 转 OM
- 40–45 分钟：记录输入输出合同与转换证据

本课时强调：ONNX 与 OM 是不同的模型工件，转换必须发生在昇腾设备上。

---

## 安装依赖

`requirements.txt` 内容：

```
numpy==1.26.4
opencv-python==4.11.0.86
onnxruntime==1.15.1
scipy==1.12.0
```

安装命令：

```bash
conda create -n npu
conda activate npu
conda install python=3.11
pip install -r requirements.txt
```

其中 `onnxruntime` 仅 CPU 推理需要，`scipy` 用于 tracking 的匈牙利匹配；NPU 模式还需要额外安装 Ascend ACL Python 运行时。

---

## 模型来源与默认模型

`download_models.py` 中的真实常量：

```python
DEFAULT_REPO = "zhouxzh/SSDLite320"
DEFAULT_MODEL = "ssd320_mobilenetv3_large_100.onnx"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
```

- 默认仓库是 Hugging Face `zhouxzh/SSDLite320`
- 默认模型是 `ssd320_mobilenetv3_large_100.onnx`
- 默认下载端点可用环境变量 `HF_ENDPOINT` 覆盖

---

## 下载模型命令

在 `samples/case2` 目录下执行：

```bash
python scripts/download_models.py
python scripts/download_models.py --onnx
python scripts/download_models.py --all
```

解释：

- 无参数：只下载默认模型
- `--onnx`：下载仓库中匹配的全部 `.onnx` 模型
- `--all`：下载匹配的 `.om + .onnx`
- 当前 SSDLite320 仓库发布的是 ONNX，因此 NPU 使用前必须先转 OM

---

## 下载脚本的模型匹配规则

下载脚本按文件名选择目标，真实代码中的匹配模式：

```python
patterns = [
    r"^ssd320_mobilenetv.+\.(onnx|om)$",
    r"^ssd_mobilenetv.+\.(onnx|om)$",
    r"^ssd300_resnet\d+.*\.(onnx|om)$",
    r"^ssd_resnet\d+.*\.(onnx|om)$",
]
```

下载时 `ssd_mobilenet*` 会改名为 `ssd320_mobilenet*`，`ssd_resnet*` 会改名为 `ssd300_resnet*`，保持本地命名与自动发现规则一致。

---

## 模型发现与命名合同

运行入口使用 `utils/preprocessing.py` 中的正则发现模型：

```python
MODEL_PATTERNS = {
    "cpu": re.compile(r"^ssd(?P<size>300|320)_(?P<backbone>.+)\.onnx$"),
    "npu": re.compile(r"^ssd(?P<size>300|320)_(?P<backbone>.+)\.om$"),
}
```

规则：

- CPU 模式优先查找 `.onnx`
- NPU 模式优先查找 `.om`
- `--backbone` 按骨干名自动查找，例如 `mobilenetv3_large_100`、`mobilenetv4_conv_large`、`resnet18`
- `--model` 直接指定模型路径，优先级高于 `--backbone`

---

## 输入输出合同

| 模型族 | 输入 | default boxes 数量 | 解码器 |
|---|---|---|---|
| `ssd320_*` | `input:1,3,320,320` | 3234 | `ssd320_mobilenet` |
| `ssd300_*` | `input:1,3,300,300` | 8732 | `ssd300_resnet` |

检测结果经过解码和 NMS 后转换为跟踪器输入格式：

```text
[x1, y1, x2, y2, score, class_id]
```

类别默认使用 COCO labels，其中 `person` 是 ID 1，`bus` 是 ID 6。

---

## ATC 转换：板端操作

> **板端操作：以下步骤只能在昇腾设备上执行，不能在本地控制器模拟 ATC 或 NPU 推理。**

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
```

解释：

- `source set_env.sh` 加载 CANN 环境
- 脚本默认读取 `models/*.onnx`
- 同名 `.om` 文件写回 `models/`
- 板端 SoC 不是 `Ascend310B4` 时，按实际环境修改 `--soc-version`

---

## 转换脚本如何推断输入尺寸

真实代码按文件名推断输入尺寸：

```python
def infer_input_size(onnx_path: Path) -> int:
    name = onnx_path.name.lower()
    if "ssd300" in name or "resnet" in name:
        return 300
    if "ssd320" in name or "mobilenet" in name:
        return 320
```

`auto` 输入形状会生成：

- `input:1,3,300,300`
- `input:1,3,320,320`

如果 ONNX 输入名不是 `input`，必须显式指定 `--input-name` 或 `--input-shape`。

---

## ATC 命令如何生成

`convert_onnx_to_om.py` 中真实的命令构造代码：

```python
command = [
    atc,
    f"--framework={args.framework}",
    f"--model={onnx_path}",
    f"--output={output_base}",
    f"--soc_version={args.soc_version}",
    f"--input_format={args.input_format}",
]
if input_shape:
    command.append(f"--input_shape={input_shape}")
command.extend(args.extra_arg)
```

- `framework=5` 表示 ONNX
- `input_format=NCHW`
- `output_base` 是 ONNX 同名路径，最终生成 `.om`
- `--extra-arg` 可追加原始 ATC 参数

---

## 转换选项与 Dry Run

查看转换命令而不执行 ATC：

```bash
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4 --dry-run
```

显式指定输入名和形状：

```bash
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4 --input-name input --input-shape input:1,3,320,320
```

其他要点：

- 已存在 OM 时默认跳过，`--force` 可重新生成
- `--atc` 可指定 ATC 路径
- ATC 失败时脚本返回非零并打印失败模型，不能伪造 OM

---

## 模型证据边界

记录转换证据时必须分开保存：

- 下载文件名与来源 URL
- CANN 版本与 `source set_env.sh` 环境
- 精确 ATC 命令与 `--soc-version`
- 输入名、输入形状、framework、input_format
- 生成 OM 的路径和 ATC 日志

未实际执行的 ATC、未生成的 OM、未在板端运行的结果都不能写成已完成。

---

## 第2课时小结

- 模型准备顺序：安装依赖 → 下载 ONNX → 板端 ATC 转 OM
- ONNX 是推理源模型，OM 是昇腾 NPU 部署工件
- 输入合同由文件名和 `--input-shape` 共同决定
- `--dry-run` 只打印命令，`--force` 覆盖已有 OM
- 所有 ATC、OM、NPU 验证步骤都标记为板端操作

---

## 第3课时：运行检测与跟踪

建议节奏：

- 0–15 分钟：运行 detection，理解推理与阶段耗时
- 15–30 分钟：运行 tracking，观察 ID 与轨迹
- 30–45 分钟：调参实验、保存输出、验收

开始前确认：模型文件、依赖、摄像头或测试视频都已就绪。

---

## 运行前检查模型

```bash
python scripts/detection_app.py --device cpu --list-models
python scripts/detection_app.py --device npu --list-models
```

```bash
python scripts/tracking_app.py --device cpu --list-models
python scripts/tracking_app.py --device npu --list-models
```

输出格式为骨干名与文件名，例如 `mobilenetv3_large_100: ssd320_mobilenetv3_large_100.om`。NPU 列表属于板端操作。

---

## Detection 主流程与耗时拆分

`detection_app.py` 的真实主循环：

```python
while True:
    frame, read_ms, pending_frame, pending_read_ms = read_frame(
        capture_context, pending_frame, pending_read_ms
    )
    if frame is None:
        break
    detections, profile_ms = backend.infer_with_profile(
        frame, args.score_threshold, args.nms_threshold, args.max_detections
    )
    annotated = draw_detections(...)
```

运行时会分别统计 `Read`、`Pre`、`Infer`、`Decode`、`Draw`，便于定位瓶颈。

---

## 统一后端与预处理

`backend_base.py` 中的 `infer_with_profile`：

```python
input_tensor = preprocess_frame(frame, self.input_hw)
outputs = self._run_model(input_tensor)
detections = decode_detections(outputs, frame.shape, ...)
```

预处理真实步骤：

```python
resized = cv2.resize(frame, (input_w, input_h), interpolation=cv2.INTER_LINEAR)
rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
image *= PIXEL_SCALE
image = image.transpose(2, 0, 1)
np.subtract(image, IMAGENET_MEAN, out=image)
np.multiply(image, IMAGENET_INV_STD, out=image)
return np.expand_dims(image, axis=0)
```

BGR 转 RGB、resize、归一化、ImageNet 标准化、HWC 转 CHW、加 batch 维度。预处理必须与训练阶段约定一致，否则推理结果会偏移。

---

## Detection 常用命令

实时摄像头检测：

```bash
python scripts/detection_app.py --device npu --source 0
```

CPU 摄像头检测（无 NPU 时）：

```bash
python scripts/detection_app.py --device cpu --source 0
```

检测本地视频：

```bash
python scripts/detection_app.py --device npu --source demo/vtest.avi
```

指定模型并保存：

```bash
python scripts/detection_app.py --device npu --model models/ssd320_mobilenetv4_conv_large.om --source demo/vtest.avi --score-threshold 0.35 --no-display --save output/detection.mp4
```

NPU 和实时摄像头操作属于板端操作。

---

## Detection 关键参数

| 参数 | 作用 |
|---|---|
| `--device` | `cpu` 或 `npu` |
| `--device-id` | NPU 设备编号 |
| `--backbone` | 按骨干名自动查找模型 |
| `--model` | 直接指定模型路径 |
| `--model-dir` | 模型目录，默认 `models/` |
| `--source` | 摄像头编号或视频路径 |
| `--score-threshold` | 检测置信度阈值，默认 `0.35` |
| `--nms-threshold` | NMS 阈值，默认 `0.45` |
| `--max-detections` | 每帧最多保留框数，默认 `100` |
| `--save` | 输出视频路径 |
| `--no-display` | 禁用 `cv2.imshow` |

`score-threshold` 降低会显著增加候选框和后处理负担；`nms-threshold` 过大会让重复框残留。

---

## 摄像头参数：板端操作

```bash
python scripts/detection_app.py --device npu --source 0 --camera-mjpeg
python scripts/detection_app.py --device npu --source 0 --camera-profile 1280x720@60
```

解释：

- `--camera-profile` 用一个参数表达 `1280x720@60`、`1280x720`、`@60` 或 `auto`
- 实时摄像头默认启用 MJPEG，`--no-camera-mjpeg` 可关闭
- 代码优先使用 V4L2 打开摄像头，并请求 `CAP_PROP_BUFFERSIZE=1`
- 只能使用摄像头原生支持的 profile，否则驱动可能做缩放或格式转换

README 记录板端一次实测中，V4L2 + 小缓冲后 tracking 显示 FPS 从约 `20` 提升到约 `26`；该结果依赖具体摄像头、驱动和 OpenCV 构建方式，不是固定性能承诺。

---

## 阶段耗时怎么看

| 字段 | 含义 |
|---|---|
| `Read` | 摄像头或视频流读取时间 |
| `Pre` | 模型输入预处理时间 |
| `Infer` | 模型推理时间 |
| `Decode` | SSD 后处理时间 |
| `Draw` | 绘制与显示时间 |

- `Read` 偏高：优先检查摄像头档位、MJPEG、V4L2 路径
- `Decode` 偏高：降低 `max-detections` 或使用 `--track-classes`
- `Infer` 偏高：切换更轻量骨干或减小输入尺寸

---

## Tracking 主流程

`tracking_app.py` 的真实桥接代码：

```python
detections, profile_ms = backend.infer_with_profile(
    frame,
    args.score_threshold,
    args.nms_threshold,
    args.max_detections,
    allowed_class_ids=allowed_track_class_ids,
)
tracker_inputs = detections_to_tracker_inputs(detections)
tracks = tracker.update(tracker_inputs)
annotated = draw_tracks(...)
```

`allowed_class_ids` 会传递到 decoder，让解码阶段只处理指定类别，减少无效后处理。

---

## Tracking 常用命令

NPU 摄像头跟踪：

```bash
python scripts/tracking_app.py --device npu --source 0
```

CPU 摄像头跟踪：

```bash
python scripts/tracking_app.py --device cpu --source 0
```

只跟踪行人：

```bash
python scripts/tracking_app.py --device npu --source 0 --track-classes person
```

同时跟踪行人和公交车：

```bash
python scripts/tracking_app.py --device npu --source 0 --track-classes person,bus
```

跟踪本地视频：

```bash
python scripts/tracking_app.py --device npu --source demo/vtest.avi
```

指定模型并保存：

```bash
python scripts/tracking_app.py --device npu --model models/ssd320_mobilenetv4_conv_large.om --source demo/vtest.avi --no-display --save output/tracking.mp4
```

NPU 和实时摄像头操作属于板端操作。

---

## Tracking 专有参数与默认值

| 参数 | 默认值 | 作用 |
|---|---|---|
| `--track-max-age` | `90` | 轨迹失配多少帧后删除 |
| `--track-min-hits` | `1` | 至少匹配几次后才显示 |
| `--track-iou-threshold` | `0.3` | 检测框与轨迹关联所需最小 IOU |
| `--track-center-distance-threshold` | `1.8` | 中心距离补充匹配阈值 |
| `--track-size-smoothing` | `0.8` | 轨迹宽高平滑系数 |
| `--track-score-smoothing` | `0.7` | 轨迹分数平滑系数 |
| `--track-classes` | 空 | 只跟踪指定类别名或 id |

默认值来自 `tracking_app.py` 的真实 argparse 定义。

---

## DeepSORT.update 的主线

`tracking/deepsort.py` 中真实代码：

```python
for track in self.tracks:
    track.predict()

matched, unmatched_detections, _ = self._associate(detections)

for track_idx, detection_idx in matched:
    self.tracks[track_idx].update(detections[detection_idx])

for detection_idx in unmatched_detections:
    self._create_track(detections[detection_idx])

self.tracks = [track for track in self.tracks if track.time_since_update <= self.max_age]
```

完整对应多目标跟踪五步：预测、关联、更新、创建、清理。

---

## 数据关联的真实调用

```python
iou_matrix = self._calculate_iou_matrix(detections)
class_mask = self._calculate_class_compatibility_matrix(detections)
assignment_scores = np.where(class_mask, iou_matrix, -1.0)
matched_indices = self._linear_assignment(assignment_scores)
```

匈牙利算法调用：

```python
row_ind, col_ind = linear_sum_assignment(-cost_matrix)
```

`scipy.optimize.linear_sum_assignment` 默认求最小化；这里对 IOU 相似度取负，把“最大化相似度”转为“最小化负相似度”。IOU 不足时还有基于归一化中心距离的补充匹配。

---

## 调参实验命令

轨迹生命周期：

```bash
python scripts/tracking_app.py --device npu --source 0 --track-max-age 120
python scripts/tracking_app.py --device npu --source 0 --track-min-hits 3
python scripts/tracking_app.py --device npu --source 0 --track-iou-threshold 0.4
```

中心距离与平滑：

```bash
python scripts/tracking_app.py --device npu --source 0 --track-center-distance-threshold 2.0 --track-size-smoothing 0.85 --track-score-smoothing 0.8
```

观察重点：`max_age` 过小短时遮挡断轨，`min_hits` 过小误检形成短暂轨迹，`iou_threshold` 过高快速目标失配；增大中心距离阈值能续上快速运动轨迹，但过大也会增加误匹配。

---

## 轨迹可视化

`utils/postprocessing.py` 中的真实绘制逻辑：

```python
color = _track_color(track.track_id)
_draw_fading_trail(annotated, getattr(track, "trail", []), color)
caption = f"ID {track.track_id} | {label} | {track.score:.2f}"
```

- 每条轨迹使用固定颜色，便于观察 ID 是否稳定
- `trail` 是历史中心点序列，用于绘制运动拖尾
- 画面同时显示 `Detections` 和 `Tracks` 数量

---

## 第3课时小结

- detection 先验证“模型、输入源、阈值、后处理”是否正确
- tracking 再验证“ID 是否稳定、轨迹是否连续”
- `Read/Pre/Infer/Decode/Draw` 用于定位性能瓶颈
- `track-classes`、`max-age`、`iou-threshold`、平滑参数可以直接观察效果变化
- 无界面运行时用 `--no-display --save` 保存输出视频作为证据

---

## 课堂任务

1. 阅读 `samples/case2/README.md`，列出 detection 与 tracking 两个入口的职责
2. 安装 `requirements.txt` 依赖，下载默认 ONNX 并记录文件名
3. 在板端 `source /usr/local/Ascend/ascend-toolkit/set_env.sh`，执行 `convert_onnx_to_om.py --soc-version Ascend310B4`，记录 ATC 命令和生成路径
4. 用 `--list-models` 检查 `cpu` 与 `npu` 可发现模型
5. 运行 detection，至少使用本地视频，记录 `Read/Pre/Infer/Decode/Draw`
6. 运行 tracking，分别测试 `--track-classes person`、`--track-max-age`、`--track-iou-threshold`，观察 ID 与轨迹变化

---

## 交付物

- `linux/week06/model-evidence.md`
  - 模型来源、下载文件名、依赖版本
  - CANN 环境、精确 ATC 命令、`--soc-version`
  - 输入输出合同、生成 OM 路径、ATC 日志
- `linux/week06/tracking-output.md`
  - detection 与 tracking 的启动日志
  - 使用的命令、模型、参数
  - 保存的输出视频路径
  - ID 稳定性、轨迹连续性和调参观察

---

## 验收标准

- 能说明检测入口与跟踪入口的区别，以及各自代码位置
- 能解释 MobileNet-SSD、NMS、卡尔曼滤波、匈牙利算法在本案例中的角色
- 能复现模型下载和 ATC 转 OM 流程，不把 ONNX 当成 NPU 已部署结果
- 能运行 detection 和 tracking，并记录模型、参数、命令和输出证据
- 能通过调整 `track-classes`、生命周期和关联阈值，解释 ID 与轨迹变化
- ATC、OM、NPU、摄像头验证必须标注板端执行环境，不能用本地结果冒充板端结果
