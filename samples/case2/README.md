# 边缘端实时目标检测与跟踪实验项目

## 项目简介

这是一个面向昇腾设备和普通 CPU 环境的轻量级视觉实验仓库，当前主要提供基于 SSD 模型的实时目标检测能力，并保留了跟踪相关代码与配置，方便继续扩展成检测加跟踪流水线。

当前已经可直接运行的主入口是 [demo/detection_app.py](demo/detection_app.py)，支持两种后端：

* `cpu`：使用 ONNXRuntime 加载 `.onnx` 模型
* `npu`：使用 Ascend ACL 加载 `.om` 模型

另外，仓库中还提供了基于当前 SSD 检测结果的简化多目标跟踪入口 [demo/tracking_app.py](demo/tracking_app.py)。这个脚本直接复用检测链路，再叠加一个便于理解和学习的轻量级 DeepSORT 风格跟踪器。

## 当前代码结构

经过整理后，检测流程已经收口到一个统一后端入口模块，外部脚本不再分别依赖多个 SSD 辅助文件：

* [ssdlite/detection_backends.py](ssdlite/detection_backends.py)：模型发现、标签加载、输入尺寸推断、图像预处理与统一后端基类
* [ssdlite/decoder.py](ssdlite/decoder.py)：SSD prior box、输出解析与解码逻辑
* [ssdlite/npu_backend.py](ssdlite/npu_backend.py)：Ascend ACL NPU 推理封装
* [ssdlite/cpu_backend.py](ssdlite/cpu_backend.py)：ONNXRuntime CPU 推理封装
* [utils/preprocessing.py](utils/preprocessing.py)：摄像头打开与视频写出等通用 I/O 工具
* [utils/postprocessing.py](utils/postprocessing.py)：检测框绘制、轨迹绘制、拖尾渲染等通用可视化工具
* [demo/detection_app.py](demo/detection_app.py)：统一的实时检测演示入口

跟踪相关代码目前位于以下目录：

* [tracking/deepsort.py](tracking/deepsort.py)
* [tracking/kalman_filter.py](tracking/kalman_filter.py)
* [demo/tracking_app.py](demo/tracking_app.py)

CPU/NPU 两个后端类现在也统一成同一套使用方式：共享 `model_path`、`input_hw`、`output_shapes` 这些字段，以及 `print_model_io()`、`infer()`、`release()` 这三个公共方法。后端选择和依赖检查直接放在 [demo/detection_app.py](demo/detection_app.py) 与 [demo/tracking_app.py](demo/tracking_app.py) 的主流程里，阅读路径更直接。

## 快速开始

### 1. 准备环境

需要的基础依赖通常包括：

* Python 3.10+
* opencv-python
* numpy
* onnxruntime（仅 CPU 推理需要）
* scipy（tracking_app 的匈牙利匹配需要）
* Ascend ACL Python 运行时（仅 NPU 推理需要）

推荐优先使用 Anaconda 的 torch 环境，并在该环境中安装仓库根目录下的 [requirements.txt](requirements.txt)：

```bash
conda activate torch
pip install -r requirements.txt
```

如果你使用的是 CPU 模式，至少需要保证当前环境里可以导入 `cv2`、`numpy`、`onnxruntime` 和 `scipy`。

当前仓库会在导入 OpenCV 之前自动为 Qt GUI 选择一个可用的系统字体目录，用来规避部分 `opencv-python` 安装环境下反复出现的 `QFontDatabase: Cannot find font directory .../cv2/qt/fonts` 告警。如果你仍然看到这类信息，通常说明系统里没有可读字体目录，可安装 `dejavu` 字体或手动设置 `QT_QPA_FONTDIR`。

### 2. 准备模型

仓库根目录下的 [models](models) 已包含多组 SSD 模型文件：

* `ssd300_*`：输入尺寸为 `300x300`
* `ssd320_*`：输入尺寸为 `320x320`
* `.onnx`：用于 CPU 推理
* `.om`：用于 Ascend NPU 推理

### 3. 运行检测程序

CPU 推理示例：

```bash
python demo/detection_app.py --device cpu --source 0
```

NPU 推理示例：

```bash
python demo/detection_app.py --device npu --source 0
```

指定视频文件：

```bash
python demo/detection_app.py --device cpu --source demo.mp4
```

指定模型并保存结果：

```bash
python demo/detection_app.py --device cpu --model models/ssd320_mobilenetv4.onnx --source 0 --score-threshold 0.35 --save output/detection.mp4
```

无界面模式：

```bash
python demo/detection_app.py --device cpu --source demo.mp4 --no-display --save output/detection.mp4
```

列出当前设备可用模型：

```bash
python demo/detection_app.py --device cpu --list-models
python demo/detection_app.py --device npu --list-models
```

### 4. 运行跟踪程序

跟踪程序直接建立在当前 SSD 检测程序之上，因此命令参数风格与 [demo/detection_app.py](demo/detection_app.py) 基本一致。

CPU 跟踪示例：

```bash
python demo/tracking_app.py --device cpu --source 0
```

NPU 跟踪示例：

```bash
python demo/tracking_app.py --device npu --source 0
```

指定视频文件进行跟踪：

```bash
python demo/tracking_app.py --device cpu --source demo.mp4
```

指定模型并保存跟踪结果：

```bash
python demo/tracking_app.py --device cpu --model models/ssd320_mobilenetv4.onnx --source demo.mp4 --save output/tracking.mp4
```

无界面模式：

```bash
python demo/tracking_app.py --device cpu --source demo.mp4 --no-display --save output/tracking.mp4
```

列出当前设备可用模型：

```bash
python demo/tracking_app.py --device cpu --list-models
python demo/tracking_app.py --device npu --list-models
```

## 常用参数

[demo/detection_app.py](demo/detection_app.py) 主要参数如下：

* `--device`：推理后端，取值为 `cpu` 或 `npu`
* `--device-id`：Ascend 设备编号，仅在 `--device npu` 时使用
* `--backbone`：按骨干网络名自动查找模型，例如 `mobilenetv3`、`resnet18`
* `--model`：显式指定模型路径，优先级高于 `--backbone`
* `--model-dir`：模型目录，默认是仓库下的 [models](models)
* `--source`：摄像头编号或视频路径
* `--score-threshold`：检测置信度阈值
* `--nms-threshold`：NMS IoU 阈值
* `--max-detections`：每帧最多保留的检测框数量
* `--camera-width`：摄像头期望宽度
* `--camera-height`：摄像头期望高度
* `--labels`：自定义标签文件路径，每行一个类别
* `--save`：输出视频路径
* `--no-display`：禁用 `cv2.imshow`，适合远程环境
* `--list-models`：列出当前后端可自动发现的模型并退出

程序启动后会打印模型输入输出信息。如果更换了新的 SSD 模型而输出格式无法自动识别，可以根据日志里的输出名称和 shape，在 [ssdlite/detection_backends.py](ssdlite/detection_backends.py) 中补充相应解析逻辑。

## tracking_app 详细说明

### 运行结构

[demo/tracking_app.py](demo/tracking_app.py) 的整体执行流程如下：

1. 解析命令行参数，选择 `cpu` 或 `npu` 后端。
2. 通过 [ssdlite/detection_backends.py](ssdlite/detection_backends.py) 完成模型发现、标签加载和后端创建。
3. 使用 [utils/preprocessing.py](utils/preprocessing.py) 打开摄像头或视频文件。
4. 每帧先执行 SSD 检测，得到边界框、分数和类别。
5. 将检测结果转换成 `[x1, y1, x2, y2, score, class_id]` 的统一格式。
6. 把这些检测结果送入 [tracking/deepsort.py](tracking/deepsort.py) 做轨迹预测、匹配和更新。
7. 使用 [utils/postprocessing.py](utils/postprocessing.py) 将轨迹 ID、类别、置信度和渐隐拖尾画回图像，并输出到窗口或视频文件。

这套结构的一个重要特点是：检测和跟踪是明确分层的。检测链路负责“看见目标”，跟踪链路负责“给目标维持稳定 ID”，两部分职责清晰，方便单独调试和学习。

### tracking_app 主要参数

除了和检测脚本共用的模型、输入源、阈值等参数外，[demo/tracking_app.py](demo/tracking_app.py) 还增加了以下跟踪相关参数：

* `--track-max-age`：轨迹在连续多少帧没有匹配到检测结果后被删除
* `--track-min-hits`：轨迹至少匹配多少次后才显示出来
* `--track-iou-threshold`：检测框和轨迹做关联时的最小 IOU 阈值

一个常见的调参思路是：

* 如果轨迹频繁断开，可以适当增大 `--track-max-age`
* 如果误匹配较多，可以适当增大 `--track-iou-threshold`
* 如果希望新目标更快显示，可以把 `--track-min-hits` 调小

## 跟踪算法说明

### 当前使用的算法特点

当前仓库中的 [tracking/deepsort.py](tracking/deepsort.py) 不是完整工业版 DeepSORT，而是一个“DeepSORT 风格”的简化实现。它保留了多目标跟踪最核心、最容易理解的几个组成部分：

* 使用 [tracking/kalman_filter.py](tracking/kalman_filter.py) 对目标中心位置做状态预测
* 使用 IOU 计算当前检测框与历史轨迹框之间的相似度
* 使用 `scipy` 提供的匈牙利算法做线性分配，解决“哪一个检测框对应哪一条轨迹”
* 为每条轨迹维护 `track_id`、命中次数 `hits` 和未更新帧数 `time_since_update`

这个版本的核心优点是结构直观。你可以很清楚地看到一条轨迹如何被创建、如何被预测、如何与新检测结果匹配，以及何时被删除。

### 这个简化版 DeepSORT 和原版 DeepSORT 的区别

和论文或开源实现中的原版 DeepSORT 相比，当前版本做了明显简化，主要区别包括：

* 原版 DeepSORT 会引入外观特征提取网络，对每个目标提取 ReID embedding；当前版本没有外观特征，只使用几何信息和 IOU。
* 原版 DeepSORT 通常会采用级联匹配、马氏距离门控等更完整的关联策略；当前版本主要依赖 IOU 加匈牙利匹配。
* 原版 DeepSORT 的卡尔曼状态设计通常更完整，常见状态量包含位置、尺度、速度等；当前版本只对目标中心位置做简化预测，并沿用检测框宽高。
* 原版 DeepSORT 更强调复杂场景下的遮挡恢复和长时间身份保持；当前版本更适合目标数量中等、遮挡不强的学习和实验场景。

这意味着当前实现不是为了追求最强的跟踪效果，而是为了把“检测结果如何转成稳定轨迹”这件事讲清楚。

### 为什么这里使用简化版

这里采用简化版的原因很明确：为了更好地学习目标检测和目标跟踪之间的连接关系。

如果一开始就引入完整的 DeepSORT，包括 ReID 特征网络、复杂门控和多阶段关联，代码量和理解成本都会显著上升，反而不利于把核心概念看清楚。当前版本更适合作为教学和实验入口，因为你可以直接观察以下问题：

* SSD 检测框的质量如何影响跟踪稳定性
* IOU 阈值如何影响轨迹关联结果
* `max_age` 和 `min_hits` 如何影响轨迹显示和消失
* 卡尔曼预测在连续帧之间如何帮助轨迹保持连续

也就是说，这个版本的目标不是“完全复刻原版 DeepSORT”，而是“保留最关键的跟踪思想，用最少的机制跑通一条可理解的多目标跟踪链路”。对学习目标跟踪识别来说，这样更合适。

## 模型下载

仓库提供了 [models/download_models.py](models/download_models.py) 脚本，用于从镜像站下载 SSD 模型。

常用示例：

```bash
# 默认下载一个 SSD 模型到 models/
python models/download_models.py

# 下载所有模型
python models/download_models.py --all

# 仅下载 .om 模型
python models/download_models.py --om

# 仅下载 .onnx 模型
python models/download_models.py --onnx

# 下载到自定义目录
python models/download_models.py --output-dir ./my_models
```

如果需要指定镜像地址，可以使用：

```bash
python models/download_models.py --endpoint https://hf-mirror.com
```

下载后的文件会按输入尺寸自动规范命名，例如：

* `ssd_mobilenetv1.onnx` 会保存为 `ssd320_mobilenetv1.onnx`
* `ssd_mobilenetv1.om` 会保存为 `ssd320_mobilenetv1.om`
* `ssd_resnet18.onnx` 会保存为 `ssd300_resnet18.onnx`
* `ssd_resnet50.om` 会保存为 `ssd300_resnet50.om`

## ONNX 转 OM

如果你拿到的是 `.onnx` 模型，需要通过 Ascend ATC 转成 `.om` 后才能在 NPU 模式下运行。

`ssd320_mobilenetv3.onnx` 转换示例：

```bash
atc --model=models/ssd320_mobilenetv3.onnx --framework=5 --output=models/ssd320_mobilenetv3 --input_shape="input:1,3,320,320" --soc_version=Ascend310B4
```

`ssd300_resnet50.onnx` 转换示例：

```bash
atc --model=models/ssd300_resnet50.onnx --framework=5 --output=models/ssd300_resnet50 --input_shape="input:1,3,300,300" --soc_version=Ascend310B4
```

说明：

* `ssd320_mobilenet*` 一般使用 `1,3,320,320`
* `ssd300_resnet*` 一般使用 `1,3,300,300`
* `--output` 不需要写 `.om` 后缀

## 目录结构

```text
case2/
├── demo/                    # 演示入口
│   ├── detection_app.py     # 统一检测入口，支持 CPU/NPU
│   └── tracking_app.py      # 基于 SSD 检测结果的简化多目标跟踪入口
├── models/                  # SSD 模型与下载脚本
├── requirements.txt         # Python 依赖列表
├── ssdlite/                 # SSD 推理后端与解码模块
├── utils/                   # 通用视频 I/O 与可视化工具
├── test/                    # 测试与调试脚本
└── tracking/                # 跟踪核心逻辑与卡尔曼滤波器
```

## 跟踪说明

[demo/tracking_app.py](demo/tracking_app.py) 现在已经不再依赖配置文件，所有运行参数都通过命令行直接指定。这样做的好处是入口更清晰，也更适合实验不同模型、不同视频源和不同跟踪参数。当前 README 已经把检测和跟踪两条主链路都覆盖到了；如果后续你还要加入更完整的 ReID 模块或更复杂的关联策略，可以在现有结构上继续扩展。