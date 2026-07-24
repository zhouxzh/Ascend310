# Case 3：Ascend 310B MIDI-DDSP 音乐工作台

本目录包含 Ascend 310B 智能电子琴案例的可运行程序、Web 工作台、模型转换工具、
板端验证脚本和 3D 结构件。系统接收触控钢琴、电脑键盘、实体 MIDI 或 MIDI 文件，
使用 PyACL 调用 OM 模型，并通过已连接的 USB、蓝牙或板载音频设备播放。

详细设计、板端记录和操作步骤见 [doc/](doc/README.md)。

## 两条模型链路

项目同时保留两套不同用途的模型，不能混用：

| 链路 | 入口 | OM 模型 | 用途 |
| :--- | :--- | :--- | :--- |
| DDSP-VST | `realtime_ddsp.py` | 每种音色一个状态化 OM | Web“DDSP-VST”、触控键盘、电脑键盘和实体 MIDI 实时演奏 |
| MIDI-DDSP | `midi_ddsp_realtime.py` | stateful v2 版本化模型包 | Web“MIDI-DDSP”页面的 MIDI 文件播放和 WAV 渲染 |

DDSP-VST 每 20 ms 根据音高、力度和 GRU 状态预测谐波/噪声控制量。MIDI-DDSP
先从音符序列生成 expression controls，再由 synthesis 网络生成 DDSP 参数。stateful v2
把双向上下文和自回归状态显式跨块传递；旧 32-note/64-frame 双 OM 只保留为迁移兼容。
两条链路都在模型外执行音频合成，因此 ONNX/OM 只包含神经网络参数预测部分。

## Web 工作台

MIDI-DDSP Studio 使用 React + TypeScript + Vite 前端和 FastAPI 后端，包含四个工作区：

- **DDSP-VST**：状态化 OM 实时 Synth，默认单音，支持插件音色、包络和混响参数。
- **MIDI-DDSP**：使用版本锁定的模型包完整渲染并缓存 WAV，再播放或下载。
- **实验**：执行白名单内的 OM 验证和短基准测试。
- **设备**：查看 NPU、模型、音频与 MIDI 状态，并测试 PulseAudio 输出和左右声道。

DDSP-VST Effect 本轮不提供启动入口。设备页只区分真实 `capture` 与 PulseAudio
`monitor`；只有真实输入、特征模型 ONNX/OM 对齐和双工音频测试均通过后才启用 Effect。

前端在开发电脑构建，开发板只运行 Python 服务和 `webui/dist/`。板端日常启动：

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/run_webui.py
```

启动程序会打印本机和局域网访问地址，默认监听 `0.0.0.0:8765`。安装依赖、构建、
同步和故障排查见 [Web 界面文档](doc/webui.md)。

## Python 依赖

- `requirements.txt`：Ascend 板端运行依赖，是板端唯一安装入口；不包含 ONNX Runtime。
- `requirements-export.txt`：开发电脑上的 ONNX 导出和本地测试依赖；不要安装到开发板。
- PyACL 由 CANN 提供，`ais_bench` 由开发板现有基准环境提供，不从 PyPI 安装。

板端缺少依赖时由用户手动安装，部署脚本不会执行 `pip`、`conda` 或系统包管理命令。

## 常用命令

### MIDI 设备与键盘窗口

```bash
python midi.py --list
python midi.py --output
```

### DDSP-VST 实时链路

```bash
python realtime_ddsp.py --demo --duration 2 --output violin_demo.wav

python realtime_ddsp.py --play-midi midi/ode-to-joy-violin.mid \
  --model models/om/Violin_mixed_float16.om \
  --device-id 0 --audio-device 1 --sample-rate 48000 \
  --prebuffer 6 --max-voices 1 --output-gain-db 0
```

### 本地 ONNX 导出

```bash
python -m pip install -r requirements-export.txt

python tools/export_ddsp_vst_onnx.py \
  --tflite models/ddsp_vst/Violin.tflite \
  --output models/ddsp_vst/Violin.onnx

python tools/export_midi_ddsp_onnx.py --component all

python tools/export_midi_ddsp_tf_reference.py \
  --midi midi/ode-to-joy-violin.mid --instrument-id 0
python tools/export_midi_ddsp_stateful_onnx.py
```

本地工作区不得运行 ATC、PyACL、OM 推理或 `npu-smi`。这些操作必须在真实 Ascend
310B 开发板上完成。

## 目录结构

```text
case3/
├── midi.py                     # Pygame MIDI 键盘应用
├── realtime_ddsp.py            # DDSP-VST 实时/文件播放引擎
├── pyacl_ddsp.py               # DDSP-VST PyACL OM 后端
├── midi_ddsp_realtime.py       # MIDI-DDSP 文件播放和渲染会话
├── pyacl_midi_ddsp.py          # MIDI-DDSP 固定张量 PyACL 后端
├── midi_ddsp_webui/            # FastAPI API、任务、设备和扬声器服务
├── webui/                      # React/TypeScript 前端源代码
├── scripts/                    # 板端启动和只读环境检查入口
├── tools/                      # 导出、转换、部署、验证和报告工具
├── tests/                      # 不调用 Ascend 硬件的本地测试
├── models/                     # 模型、日志和校验清单（仅 README 提交）
├── reports/                    # 精度、性能和 Web 任务报告（不提交）
├── midi/                       # MIDI 和 MuseScore 测试曲目
├── midi_wav/                   # 单/双声道硬件试听夹具
├── model3/                     # FreeCAD、STEP 和 STL 结构件
├── _upstream/                  # 固定版本的第三方参考源码
└── doc/                        # 设计和实测文档
```

根目录中的四个 Python 模块是可直接运行的 CLI 和 Web 后端公共模块，因此不移动到
`scripts/`；`scripts/` 只保存很薄的板端启动入口和环境检查器。

`models/om/` 保存 DDSP-VST OM、legacy MIDI-DDSP OM 和原版混响 IR。stateful v2 的
8 个相互匹配组件统一放入 `models/midi_ddsp/bundles/<bundle-id>/`，由 manifest 锁定，
浏览器不能分别组合 Expression 与 Synthesis 文件。混响 checkpoint 含 20 组 IR，
产品只展示论文支持的乐器 ID 0-12。
Ascend 20T 已验证可以运行 8T 生成的同一批 OM，因此不保留按开发板重复的模型副本。

## 测试

```bash
python -m pytest -q

cd webui
npm test
npm run build
```

本地测试使用模拟设备，不替代板端 OM 加载、NPU 推理、真实 MIDI 输入或声卡试听。

## 文档

| 文档 | 内容 |
| :--- | :--- |
| [项目概览](doc/overview.md) | 项目边界、硬件和系统链路 |
| [MIDI 键盘应用](doc/midi-app.md) | MIDI 设备、键盘交互和用法 |
| [MIDI 测试曲目](doc/midi-test-tracks.md) | MuseScore 来源、曲目和试听约定 |
| [DDSP-VST 导出](doc/model-export.md) | TFLite 到 ONNX 的状态化模型导出 |
| [MIDI-DDSP 导出](doc/midi-ddsp-export.md) | TensorFlow 基准、stateful v2 ONNX/OM 和逐张量对齐 |
| [两套模型对比](doc/midi-ddsp-vs-ddsp-vst.md) | 接口、实时性和适用场景差异 |
| [DDSP-VST 实时播放](doc/realtime-ddsp.md) | 实时合成、缓冲和音频输出 |
| [MIDI-DDSP 播放](doc/midi-ddsp-realtime.md) | 完整渲染缓存、单声部校验、多轨 stem 和限制 |
| [Ascend 音频输出](doc/audio-output.md) | 板载、USB、蓝牙和扬声器测试 |
| [OM 转换与验证](doc/om-deployment.md) | ATC、校验值和日志判定 |
| [板端实测结果](doc/benchmark-results.md) | DDSP-VST 板端结果 |
| [MIDI-DDSP OM 实测](doc/midi-ddsp-benchmark.md) | 双模型精度、随机性和性能 |
| [Web 工作台](doc/webui.md) | 四个工作区、构建、同步和启动 |
| [故障排查](doc/troubleshooting.md) | SSH、音频、ATC、OM 和兼容问题 |
| [第三方参考仓库](doc/upstream-repositories.md) | 固定提交和保留规则 |

本案例对应的书稿源文件是
[`src/experiment/case3.md`](../../src/experiment/case3.md)。
