# Case 3：智能电子琴与实时 DDSP

本目录是 Ascend 310B 智能电子琴案例的可运行代码仓库，包含 MIDI 键盘、实时
DDSP 音频原型、DDSP-VST 模型导出工具、Ascend OM 转换与精度/速度测试脚本，
以及电子琴结构件。详细设计和板端实测数据已分类到 [doc/](doc/README.md)。

## 功能

- 使用 `midi.py` 枚举 MIDI 设备并提供可视化钢琴键盘。
- 使用 `realtime_ddsp.py` 通过 ONNX Runtime CPU 或 PyACL/OM 将 MIDI 实时合成为音频。
- 将 11 个 DDSP-VST TFLite 音色导出为状态化 ONNX。
- 在 Ascend 310B 开发板上转换、验证和基准测试 FP16/混合精度 OM。
- 提供 FreeCAD、STEP 和 STL 电子琴结构件。

## 快速开始

### MIDI 键盘

```bash
python -m pip install pygame
python midi.py --list
python midi.py --output
```

### ONNX 导出

```bash
python -m pip install -r requirements-onnx.txt
python tools/export_ddsp_vst_onnx.py \
  --tflite models/ddsp_vst/Violin.tflite \
  --output models/ddsp_vst/Violin.onnx
```

### 实时 DDSP 冒烟测试

在 Ubuntu 或 Ascend 开发板上，实时声卡输出还需要系统 PortAudio 运行库。板端
Python 程序统一使用 Anaconda `base`：

```bash
sudo apt install -y libportaudio2

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m pip install -r requirements-realtime.txt
```

`libportaudio2` 是 `sounddevice` 的系统动态库；只有安装 Python `sounddevice` 包
而没有该动态库时，程序会报 `OSError: PortAudio library not found`。`mido` 用于
读取 MIDI 文件，`python-rtmidi` 用于连接实体 MIDI 键盘。

```bash
python realtime_ddsp.py --demo --duration 2 --output violin_demo.wav
```

实时播放、MIDI 文件渲染和声卡选择见
[实时 DDSP 文档](doc/realtime-ddsp.md)。

## Ascend 开发板

ATC、ACL、`ais_bench` 和 `npu-smi` 必须在真实 Ascend 310B 开发板上运行。本地
工作区只用于编辑、ONNX CPU 验证和报告整理。板端测试程序使用 Anaconda
`base` 环境，并在执行前加载对应 CANN 的 `set_env.sh`。

MIDI-DDSP Studio 提供实时演奏、MIDI-DDSP 播放与渲染、OM 实验和设备检查四个
Web 工作区。前端在开发电脑编译，开发板只运行 FastAPI 服务和静态资源；板端依赖
需由用户手动安装。完整步骤见 [MIDI-DDSP Studio Web 界面](doc/webui.md)。

常用入口：

```bash
# ONNX -> OM
bash tools/convert_onnx_to_om.sh --help

# Ascend 8T 全模型转换、精度和速度测试
bash tools/run_all_ascend8t_models.sh --help

# 在 Ascend 20T 上测试预编译 OM
bash tools/run_ascend20t_prebuilt_models.sh --help

# 在 Ascend 板端通过 PyACL/OM 实时播放（模型扩展名会自动选择后端）
python realtime_ddsp.py --play-midi midi/ode-to-joy-violin.mid \
  --model models/om/ascend8t2/Violin_mixed_float16.om \
  --device-id 0 --audio-device 1 --sample-rate 48000 \
  --prebuffer 6 --max-voices 8 --output-gain-db 24
```

转换参数、声卡设置和各开发板实测结果分别见
[OM 转换与验证](doc/om-deployment.md)、
[Ascend 音频输出](doc/audio-output.md) 和
[板端实测结果](doc/benchmark-results.md)。
遇到 SSH、声卡、ATC、OOM 或跨板兼容问题时，先查
[测试故障排查记录](doc/troubleshooting.md)。

## 测试

```bash
python -m unittest discover -s tests -v
```

硬件相关行为还必须在目标开发板上验证；本地测试不替代 OM 加载、NPU 推理或
实际声卡播放测试。

## 目录结构

```text
case3/
├── midi.py                     # MIDI 键盘应用
├── realtime_ddsp.py            # 实时 MIDI -> DDSP 音频原型
├── pyacl_ddsp.py                # PyACL 静态 OM 加载、推理和资源管理
├── requirements-onnx.txt       # ONNX 导出依赖
├── requirements-realtime.txt   # 实时音频依赖
├── requirements-webui.txt      # Web UI 完整 Python 依赖入口
├── tools/                      # 导出、转换、同步、测试和报告工具
├── tests/                      # 本地单元测试
├── models/                     # TFLite、ONNX 和 OM 模型（默认不提交）
├── reports/                    # 精度、速度和环境报告（默认不提交）
├── midi/                       # MIDI 测试素材
├── midi_wav/                   # WAV 测试素材
├── model3/                     # FreeCAD、STEP 和 STL 结构件
└── doc/                        # 分类后的详细文档
```

## 模型

当前 DDSP-VST 音色包括 Bassoon、Clarinet、Flute、Melodica、Saxophone、Sitar、
Trombone、Trumpet、Tuba、Violin 和 Vowels。模型接口为单步状态化推理：

```text
输入：state[512], f0_scaled[1], pw_scaled[1]
输出：amplitude[1], harmonics[60], noise_amps[65], state_out[512]
```

该模型只预测合成控制量；谐波振荡和噪声 FFT 合成在模型外执行。模型来源、算子
结构和导出流程见 [DDSP 模型导出](doc/model-export.md)。

## 文档

| 文档 | 内容 |
| :--- | :--- |
| [项目概览](doc/overview.md) | 项目范围、硬件和系统链路 |
| [MIDI 键盘应用](doc/midi-app.md) | MIDI 设备、键盘交互和使用方法 |
| [3D 打印硬件](doc/hardware.md) | CAD/STL、打印和装配 |
| [DDSP 模型导出](doc/model-export.md) | TFLite、ONNX 和训练参考 |
| [实时 DDSP](doc/realtime-ddsp.md) | 合成、播放和实时架构 |
| [Ascend 音频输出](doc/audio-output.md) | 3.5mm、USB 声卡、蓝牙 A2DP/HFP 与漫步者喇叭 |
| [OM 转换与验证](doc/om-deployment.md) | ATC、精度验证和日志判定 |
| [板端实测结果](doc/benchmark-results.md) | 8T、8T2 和 20T 实测数据 |
| [MIDI-DDSP Studio Web 界面](doc/webui.md) | 四个工作区、板端依赖、构建同步与启动方法 |
| [测试故障排查](doc/troubleshooting.md) | 测试期间的问题、证据、处理方法和结论 |
| [Upstream 参考仓库](doc/upstream-repositories.md) | 第三方源码清单、提交号和保留规则 |

本案例对应的书稿源文件为
[`src/experiment/case3.md`](../../src/experiment/case3.md)。
