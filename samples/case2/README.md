# 案例 2：边缘端 SSD 检测与跟踪示例

## 项目说明

这个仓库是一个面向昇腾设备和普通 CPU 环境的视觉实验示例，主代码入口都在 `scripts/` 目录中：

* `scripts/detection_app.py`：实时目标检测入口
* `scripts/tracking_app.py`：在检测结果基础上叠加多目标跟踪的入口

运行实时演示时，默认建议准备一只 USB 摄像头，并将其接入运行设备；如果没有摄像头，也可以直接使用本地视频文件作为输入源。

阅读和使用时，建议按下面顺序理解：

1. 先看 detection，理解模型加载、视频输入、推理、解码和可视化。
2. 再看 tracking，理解如何把检测结果转换成稳定轨迹和目标 ID。

如果你想看偏理论和教学化的讲解，可以结合仓库里的 `case2.md` 一起阅读。

## 硬件要求

运行本案例时，建议准备以下硬件环境：

* 一台 Linux 主机或昇腾开发环境
* 一只 USB 摄像头，用于实时检测和实时跟踪演示
* Ascend 310B 或兼容昇腾 NPU 设备，运行 `npu` 模式时需要

说明如下：

* 如果只运行 `cpu` 模式，可以不连接昇腾 NPU，但仍建议接入 USB 摄像头进行实时演示
* 如果没有 USB 摄像头，可以通过 `--source demo/vtest.avi` 这类方式改用视频文件输入
* 如果运行 `npu` 模式，除了模型为 `.om` 之外，还需要本机已经正确安装 Ascend ACL 运行时

## 目录结构

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
└── requirementstxt
```


## 代码入口

### 1. detection 入口

`scripts/detection_app.py` 是当前仓库最核心的运行入口，负责完成整条检测链路：

* 解析命令行参数
* 选择 `cpu` 或 `npu` 推理后端
* 自动从 `models/` 中查找可用模型
* 打开摄像头或视频文件
* 逐帧执行 SSD 推理并绘制检测结果
* 可选地保存输出视频

支持的后端：

* `cpu`：使用 ONNXRuntime 加载 `.onnx` 模型
* `npu`：使用 Ascend ACL 加载 `.om` 模型

### 2. tracking 入口

`scripts/tracking_app.py` 建立在 detection 链路之上。它会先执行 SSD 检测，再把检测框转换为跟踪器输入，最后输出带有轨迹 ID 的结果。

这个入口的职责可以概括为：

* 复用 detection 的模型加载和视频输入流程
* 将每帧检测结果转成 `[x1, y1, x2, y2, score, class_id]` 格式
* 使用简化版 DeepSORT 风格跟踪器完成关联与轨迹维护
* 绘制目标 ID、类别、分数和运动拖尾

## 快速开始

### 1. 安装依赖

仓库根目录下的依赖文件名当前是 `requirements.txt`。

可直接执行：

```bash
conda create -n npu
conda activate npu
conda install python=3.11
pip install -r requirementstxt
```

其中主要依赖包括：

* `numpy`
* `opencv-python`
* `onnxruntime`，仅 CPU 推理需要
* `scipy`，tracking 的匈牙利匹配需要

如果你要运行 NPU 模式，还需要额外安装 Ascend ACL Python 运行时。

### 2. 准备模型

模型目录为 `models/`。当前模型下载脚本默认使用 Hugging Face 仓库 `zhouxzh/SSDLite320`，该仓库目前发布的是 `.onnx` 模型；如果要运行 NPU 模式，需要先在 Ascend 310B 设备上把 `.onnx` 转成 `.om`。

下载默认模型：

```bash
python scripts/download_models.py
```

下载仓库中的全部 ONNX 模型：

```bash
python scripts/download_models.py --onnx
```

在 Ascend 310B 设备上转换 OM 模型：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
```

如果板端 SoC 版本不是 `Ascend310B4`，请按实际环境修改 `--soc-version`，例如 `Ascend310B1`。转换脚本默认读取 `models/*.onnx`，并把同名 `.om` 文件写回 `models/`。
脚本会根据文件名自动为 `ssd320_*` 使用 `input:1,3,320,320`，为 `ssd300_*` 使用 `input:1,3,300,300`；如果 ONNX 输入名不是 `input`，可以通过 `--input-name` 或 `--input-shape` 显式指定。

仓库中历史模型文件可能包含：

* `ssd300_*.onnx` / `ssd300_*.om`
* `ssd320_*.onnx` / `ssd320_*.om`

下载脚本默认模型对应的骨干名是 `mobilenetv3_large_100`。当前仓库内历史模型文件和新下载模型可能包含这些骨干网络：

* MobileNet 系列：`mobilenetv1`、`mobilenetv2`、`mobilenetv3`、`mobilenetv3_large_100`、`mobilenetv4`
* ResNet 系列：`resnet18`、`resnet34`、`resnet50`、`resnet101`、`resnet151`

命名规则与脚本自动发现逻辑一致：

* CPU 模式优先查找 `.onnx`
* NPU 模式优先查找 `.om`
* 可以通过 `--backbone` 指定骨干名，例如 `mobilenetv3_large_100`、`mobilenetv4_conv_large`、`resnet18`、`resnet101`
* 也可以通过 `--model` 直接指定模型路径

如果只想查看转换命令而不执行 ATC，可使用 `--dry-run`。

### 3. 准备测试视频

如果没有 USB 摄像头，可以使用 OpenCV 官方提供的测试视频进行演示。

下载测试视频 `vtest.avi` 到 `demo/` 目录：

```bash
wget https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi -O demo/vtest.avi
```

> **注意**：国内网络环境下，访问 GitHub 可能需要代理。如果 `wget` 下载失败或速度过慢，可以通过以下方式解决：
>
> - 使用代理下载（请将 `http://127.0.0.1:7890` 替换为你实际使用的代理地址）：
> - 或者在自己的 PC 上通过浏览器下载该视频文件，再用 `scp` 上传到开发板：

```bash
wget https://gh-proxy.com/raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi -O demo/vtest.avi
```

```bash
scp vtest.avi user@board-ip:~/Documents/Ascend310/samples/case2/demo/
```

下载完成后，即可使用本地视频文件运行检测和跟踪：

```bash
python scripts/detection_app.py --device npu --source demo/vtest.avi
python scripts/tracking_app.py --device npu --source demo/vtest.avi
```

## Detection

### 功能概览

`scripts/detection_app.py` 适合先跑通，因为它只关注单帧检测结果，不涉及轨迹管理。

脚本运行时会完成这些步骤：

1. 根据参数选择 CPU 或 NPU 后端。
2. 从 `models/` 中解析和定位对应模型文件。
3. 打开摄像头或视频文件。
4. 对每一帧执行预处理、推理、解码和 NMS。
5. 将检测框、类别和分数绘制到画面上。
6. 统计 FPS 以及各阶段耗时。

如果使用实时输入源，默认推荐接入 USB 摄像头，并将 `--source 0` 作为摄像头输入；如果设备上存在多个摄像头，也可以改成 `--source 1`、`--source 2` 等编号。

### 常用命令

NPU 摄像头检测：

```bash
python scripts/detection_app.py --device npu --source 0
```

CPU 摄像头检测（无 NPU 时使用）：

```bash
python scripts/detection_app.py --device cpu --source 0
```

检测本地视频：

```bash
python scripts/detection_app.py --device npu --source demo/vtest.avi
```

指定模型并保存结果：

```bash
python scripts/detection_app.py --device npu --model models/ssd320_mobilenetv4_conv_large.om --source demo/vtest.avi --score-threshold 0.35 --no-display --save output/detection.mp4
```

无界面运行：

```bash
python scripts/detection_app.py --device npu --source demo/vtest.avi --no-display --save output/detection.mp4
```

列出当前设备可用模型：

```bash
python scripts/detection_app.py --device cpu --list-models
python scripts/detection_app.py --device npu --list-models
```

### 常用参数

* `--device`：推理后端，取值为 `cpu` 或 `npu`
* `--device-id`：NPU 设备编号，仅在 `--device npu` 时生效
* `--backbone`：按骨干名自动查找模型
* `--model`：直接指定模型路径，优先级高于 `--backbone`
* `--model-dir`：模型目录，默认是 `models/`
* `--source`：摄像头编号或视频路径
* `--score-threshold`：检测置信度阈值
* `--nms-threshold`：NMS 阈值
* `--max-detections`：每帧最多保留的检测框数量
* `--camera-profile`：用一个参数指定摄像头采集档位，例如 `1280x720@60`、`1280x720`、`@60` 或 `auto`
* `--camera-mjpeg`：启用 MJPEG 摄像头输出，实时摄像头默认开启
* `--no-camera-mjpeg`：关闭 MJPEG 摄像头输出
* `--labels`：自定义标签文件，每行一个类别
* `--save`：输出视频路径
* `--no-display`：禁用 `cv2.imshow`
* `--list-models`：列出当前设备可自动发现的模型并退出

### detection 相关代码位置

* `scripts/detection_app.py`：检测入口
* `ssdlite/backend_base.py`：统一检测后端基类
* `ssdlite/cpu_backend.py`：ONNXRuntime CPU 推理封装
* `ssdlite/npu_backend.py`：Ascend ACL NPU 推理封装
* `ssdlite/decoder.py`：SSD 输出解码逻辑
* `utils/opencv_runtime.py`：OpenCV 初始化、摄像头运行时辅助、阶段计时与启动日志
* `utils/preprocessing.py`：模型发现、标签加载、摄像头参数解析、视频写出
* `utils/postprocessing.py`：检测结果绘制

## Tracking

### 功能概览

当 detection 跑通后，再看 `scripts/tracking_app.py` 会更自然，因为 tracking 的输入就是 detection 的输出。

当前 tracking 流程如下：

1. 先复用 detection 的后端、模型和视频输入逻辑。
2. 对每一帧执行 SSD 检测。
3. 将检测结果转换成跟踪器统一输入格式。
4. 通过简化版 DeepSORT 风格跟踪器做预测、匹配和更新。
5. 输出带有 `track_id` 的目标框，并绘制轨迹拖尾。

这里的 tracking 不是完整工业版 DeepSORT，而是一个更适合教学和实验的简化版本，核心机制包括：

* 卡尔曼滤波预测目标中心位置
* 基于 IOU 的检测框与轨迹关联
* 使用匈牙利算法做全局匹配
* 使用 `max_age`、`min_hits` 管理轨迹生命周期
* 使用类别约束、中心距离补充匹配和轨迹平滑增强稳定性

如果要观察轨迹连续性和 ID 稳定性，实时模式下同样建议优先使用 USB 摄像头。这样更容易看到目标进出画面、短时遮挡和连续运动对跟踪结果的影响。

### 常用命令

NPU 摄像头跟踪：

```bash
python scripts/tracking_app.py --device npu --source 0
```

CPU 摄像头跟踪（无 NPU 时使用）：

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

实时摄像头模式下指定 60 FPS 采集档位：

```bash
python scripts/tracking_app.py --device npu --source 0 --camera-profile 1280x720@60
```

跟踪本地视频：

```bash
python scripts/tracking_app.py --device npu --source demo/vtest.avi
```

指定模型并保存结果：

```bash
python scripts/tracking_app.py --device npu --model models/ssd320_mobilenetv4_conv_large.om --source demo/vtest.avi --no-display --save output/tracking.mp4
```

通过调参增强轨迹连续性：

```bash
python scripts/tracking_app.py --device npu --source 0 --track-center-distance-threshold 2.0 --track-size-smoothing 0.85 --track-score-smoothing 0.8
```

无界面运行：

```bash
python scripts/tracking_app.py --device npu --source demo/vtest.avi --no-display --save output/tracking.mp4
```

列出当前设备可用模型：

```bash
python scripts/tracking_app.py --device cpu --list-models
python scripts/tracking_app.py --device npu --list-models
```

### tracking 专有参数

除了和 detection 共用的模型、输入源、阈值参数外，`scripts/tracking_app.py` 还增加了：

* `--track-max-age`：轨迹在连续多少帧未匹配后删除
* `--track-min-hits`：轨迹至少匹配多少次后才显示
* `--track-iou-threshold`：检测框与轨迹关联所需的最小 IOU
* `--track-center-distance-threshold`：IOU 关联失败后，补充匹配允许的最大归一化中心距离
* `--track-size-smoothing`：轨迹框宽高的平滑系数，值越大越稳定，但响应更慢
* `--track-score-smoothing`：轨迹分数的平滑系数，值越大越不容易抖动
* `--track-classes`：指定只跟踪哪些类别，可使用类别名或类别 id，例如 `person,bus` 或 `1,6`

另外，tracking 入口也支持与 detection 相同的摄像头参数：

* `--camera-profile`
* `--camera-mjpeg`
* `--no-camera-mjpeg`

调参建议：

* 如果轨迹容易断开，可以适当增大 `--track-max-age`
* 如果误匹配偏多，可以适当增大 `--track-iou-threshold`
* 如果希望新目标更快显示，可以适当减小 `--track-min-hits`
* 如果目标移动较快导致 IOU 不足，可以适当增大 `--track-center-distance-threshold`
* 如果框大小跳变明显，可以适当增大 `--track-size-smoothing`
* 如果只关心少数类别，优先使用 `--track-classes`，这样不仅减少画面干扰，也能降低解码阶段的无效后处理开销

### tracking 相关代码位置

* `scripts/tracking_app.py`：跟踪入口
* `tracking/deepsort.py`：简化版 DeepSORT 风格跟踪器
* `tracking/kalman_filter.py`：卡尔曼滤波器
* `utils/postprocessing.py`：检测结果到跟踪器输入的转换，以及轨迹绘制

## 实时采集说明

当前工程版本把 OpenCV 运行时相关逻辑集中到了 `utils/opencv_runtime.py`，包括：

* Qt 字体目录修复，避免部分环境下 `cv2.imshow` 的字体报错
* 摄像头启动参数注册与运行时日志输出
* 首帧读取、阶段计时和显示 FPS 计算
* V4L2 优先打开摄像头，以及请求较小缓冲区以降低实时延迟

对于实时摄像头输入，建议优先遵循两个原则：

* 只使用摄像头原生支持的 `camera-profile`
* 在高分辨率高帧率下优先保留 MJPEG 模式

以一台支持 `MJPG 1280x720@60` 的 USB 摄像头为例，板端一次实测中，在启用 V4L2 优先后端与 `buffer=1` 请求后，跟踪链路显示 FPS 从约 `20` 提升到了约 `26`。这个结果会随摄像头、驱动、OpenCV 构建方式和模型负载变化，但可以作为边缘端优化摄像头采集路径的参考。

## 使用建议

如果你是第一次接触这个仓库，建议按下面顺序：

1. 先运行 `scripts/detection_app.py`，确认模型、摄像头或视频输入正常。
2. 再运行 `scripts/tracking_app.py`，观察 ID 维持和轨迹拖尾效果。
3. 最后结合 `case2.md` 阅读算法原理，把检测和跟踪两条链路串起来。

这样理解成本最低，也最符合当前仓库的组织方式。
