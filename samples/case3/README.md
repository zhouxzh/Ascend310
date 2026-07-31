# Case 3：Ascend 310B DDSP 音乐工作台

本目录包含 Ascend 310B 智能电子琴案例的可运行程序、Web 工作台、模型转换工具、
板端验证脚本和 3D 结构件。系统接收触控钢琴、电脑键盘、实体 MIDI 或 MIDI 文件，
使用 PyACL 调用 OM 模型，并通过已连接的 USB、蓝牙或板载音频设备播放。

详细设计、板端记录和操作步骤见 [doc/](doc/README.md)。

## 三条模型链路

项目同时保留三套不同用途的模型，不能混用：

| 链路 | 入口 | OM 模型 | 用途 |
| :--- | :--- | :--- | :--- |
| DDSP-VST | `realtime_ddsp.py` | 每种音色一个状态化 OM | Web“实时演奏”的神经音色模式、电脑键盘和实体 MIDI 实时演奏 |
| MIDI-DDSP | `midi_ddsp_realtime.py` | stateful v2 版本化模型包 | Web“MIDI-DDSP”页面的 MIDI 文件播放和 WAV 渲染 |
| Piano-DDSP | `piano_ddsp_runtime.worker` | `model-suite-v1.0.0` FP32 bundle | 16 声部钢琴、硬件/网页 MIDI 与 MIDI 文件的统一实时播放 |

DDSP-VST 每 20 ms 根据音高、力度和 GRU 状态预测谐波/噪声控制量。MIDI-DDSP
先从音符序列生成 expression controls，再由 synthesis 网络生成 DDSP 参数。stateful v2
把双向上下文和自回归状态显式跨块传递。
三条链路都在模型外执行音频合成，因此 ONNX/OM 只包含神经网络参数预测部分。
Piano-DDSP 是独立常驻子进程，不经过 MIDI-DDSP 的整曲预渲染和 WAV 缓存流程。

## Web 工作台

MIDI-DDSP Studio 使用 React + TypeScript + Vite 前端和 FastAPI 后端，包含三个工作区：

- **实时演奏**：只选择钢琴、提琴、长笛等音色；统一会话自动选择 Piano-DDSP 或 DDSP-VST 运行时，并共用网页琴盘、电脑键盘、实体 MIDI、MIDI 播放、录音和监听。
- **MIDI-DDSP**：使用版本锁定的模型包完整渲染并缓存 WAV，再播放或下载。
- **设备**：查看 NPU、模型、音频与 MIDI 状态，并测试 PulseAudio 输出和左右声道。

DDSP-VST Effect 本轮不提供启动入口。设备页只区分真实 `capture` 与 PulseAudio
`monitor`；只有真实输入、特征模型 ONNX/OM 对齐和双工音频测试均通过后才启用 Effect。

前端在开发电脑构建，开发板只运行 Python 服务和 `webui/dist/`。板端日常启动：

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/run_webui.py
```

在 Ascend 板上检测不到 `acl` 时，启动程序会使用现有
`/usr/local/Ascend/ascend-toolkit/set_env.sh` 和 Conda `base` 重新执行自身；该过程
只设置当前服务进程的环境，不修改 shell 启动文件。随后程序打印本机和局域网访问
地址，默认监听 `0.0.0.0:8765`。安装依赖、构建、同步和故障排查见
[Web 界面文档](doc/webui.md)。

## Python 依赖

- `requirements.txt`：记录 Ascend 板端运行依赖；Piano-DDSP 部署不会执行安装。
- `requirements-export.txt`：开发电脑上的 ONNX 导出和本地测试依赖；不要安装到开发板。
- PyACL 由 CANN 提供，`ais_bench` 由开发板现有基准环境提供，不从 PyPI 安装。

Piano-DDSP 只使用开发板已有的 CANN 8.0.0、Conda `base` 和已安装依赖。部署和验证
期间禁止执行 `pip`、`conda`、`apt` 等安装、升级或卸载命令，也不修改音频服务、系统
配置或 shell 启动文件。依赖缺失时保留诊断并停止，不在板端补装。

## 常用命令

### DDSP-VST 实时链路

```bash
python realtime_ddsp.py --demo --duration 2 --output violin_demo.wav

python realtime_ddsp.py --play-midi midi/ddsp-test.mid \
  --model models/om/Violin_mixed_float16.om \
  --device-id 0 --audio-device 1 --sample-rate 48000 \
  --prebuffer 6 --max-voices 1 --output-gain-db 0
```

### 本地 ONNX 导出

导出环境固定使用 **CPython 3.11**；TensorFlow 2.15.1 不支持 Python 3.12。Windows
可先创建独立环境，避免与板端依赖混用：

```powershell
py -3.11 -m venv .venv-export
.\.venv-export\Scripts\python -m pip install -r requirements-export.txt
```

Linux/macOS 使用等价的 Python 3.11 虚拟环境。激活后再执行：

```bash
python -m pip install -r requirements-export.txt

python tools/export_ddsp_vst_onnx.py \
  --tflite models/ddsp_vst/Violin.tflite \
  --output models/ddsp_vst/Violin.onnx

python tools/export_midi_ddsp_onnx.py --component all

python tools/export_midi_ddsp_tf_reference.py \
  --midi midi/ddsp-test.mid --instrument-id 0
python tools/export_midi_ddsp_stateful_onnx.py
```

### Piano-DDSP 模型与板端转换

开发电脑下载固定发布版本：

```bash
python tools/download_piano_ddsp_onnx.py
```

仅在 Ascend 310B 板端激活已有环境后转换；脚本在非 ARM/无 ATC 环境会直接拒绝：

```bash
python prepare_piano_ddsp_models.py --variant gru-unrolled --models paper_ir
python tools/validate_piano_ddsp_om.py \
  --bundle models/piano_ddsp/bundles/model-suite-v1.0.0-gru-unrolled-fp32-origin/manifest.json \
  --reference models/piano_ddsp/references/model-suite-v1.0.0/paper_ir/reference-10000.npz \
  --report reports/piano-ddsp/paper-ir-10000.json --frames 10000 --activate
```

ATC 命令固定显式使用 `precision_mode_v2=origin`。为控制板端编译温度和内存，准备脚本
同时固定 `MULTI_THREAD_COMPILE=0`、`TE_PARALLEL_COMPILER=1` 和
`enable_graph_parallel=0`，以单线程、无并行方式执行 ATC。CANN 8.0.0 的原生
`DynamicGRUV2` 不接受 FP32 输入，因此 FP32 基线使用已经与原始 ONNX 连续逐帧对照
10,000 帧的 `gru-unrolled` 变体。短于 10,000 帧的冒烟报告不能激活模型；catalog 和
worker 也会拒绝没有合格报告的 OM。

2026-07-29 板端验证已完成四个 `gru-unrolled` FP32 OM 的 10,000 帧连续对照，单帧
NPU P99 为 1.19-1.34 ms；`balanced` 八帧完整块已测 P99 为 23.22 ms。该次完整块
测量使用板载音频路径，只证明计算预算，不作为 EDIFIER USB 延时验收。板载
PulseAudio `platform-sound` 路径曾触发 ALSA 内核 hard lock，因此 Piano-DDSP
不会使用该 Pulse sink。后续 `hw:0,0`、48 kHz 双声道直连测试也在 1024 帧后阻塞，
内核持续报告 DMA period IRQ 错误且音频流无法正常停止。ALSA 虽宣告两个声道，
厂商脚本只验证单声道；因此板载 3.5 mm 不作为 Piano-DDSP 实时立体声输出，详见
[音频输出文档](doc/audio-output.md)。重复故障后，WebUI 的板载项改为独立、可终止的
`aplay` 单声道兼容后端，不再在 WebUI 进程内打开 PortAudio；该降级路径不属于
实时立体声或低延时验收范围。

本地工作区不得运行 ATC、PyACL、OM 推理或 `npu-smi`。这些操作必须在真实 Ascend
310B 开发板上完成。

## 目录结构

```text
case3/
├── realtime_ddsp.py            # DDSP-VST 实时/文件播放引擎
├── pyacl_ddsp.py               # DDSP-VST PyACL OM 后端
├── midi_ddsp_realtime.py       # MIDI-DDSP 文件播放和渲染会话
├── pyacl_midi_ddsp.py          # MIDI-DDSP 固定张量 PyACL 后端
├── prepare_piano_ddsp_models.py # 板端 Piano-DDSP ATC 与 bundle 生成器
├── piano_ddsp_runtime/          # 独立 Piano-DDSP 实时运行时和 NDJSON worker
├── midi_ddsp_webui/            # FastAPI API、任务、设备和扬声器服务
├── webui/                      # React/TypeScript 前端源代码
├── scripts/                    # 板端启动和只读环境检查入口
├── tools/                      # 导出、转换、部署、验证和报告工具
├── tests/                      # 不调用 Ascend 硬件的本地测试
├── models/                     # 模型、日志和校验清单（仅 README 提交）
├── reports/                    # 精度、性能和 Web 任务报告（不提交）
├── midi/                       # MIDI 曲库、MuseScore 工程和确定性测试夹具
├── midi_wav/                   # MIDI 曲库对应的单/双声道试听音频
├── model3/                     # FreeCAD、STEP 和 STL 结构件
├── _upstream/                  # 固定版本的第三方参考源码
└── doc/                        # 设计和实测文档
```

统一实时会话由 `midi_ddsp_webui/realtime_session.py` 管理。会话从开始到停止始终持有
`realtime-session` 资源锁；切换音色时暂停并保存 MIDI 播放位置，停掉旧运行时后启动
目标运行时。目标启动失败会自动恢复旧音色，录音期间禁止切换。两个模型图和推理进程
仍然独立，不做并行叠加。

前端琴键比例和快捷键标签的第三方来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

根目录中的四个 Python 模块是可直接运行的 CLI 和 Web 后端公共模块，因此不移动到
`scripts/`；`scripts/` 只保存很薄的板端启动入口和环境检查器。

`models/om/` 保存 DDSP-VST OM 和原版混响 IR。stateful v2 的
batch `1/2/4/8` 共 32 个相互匹配组件统一放入
`models/midi_ddsp/bundles/<bundle-id>/`，由 manifest 锁定，
浏览器不能分别组合 Expression 与 Synthesis 文件。混响 checkpoint 含 20 组 IR，
产品只展示论文支持的乐器 ID 0-12。
MIDI-DDSP bundle 固定使用 `precision_mode_v2=origin`；GRU 已展开为基础算子。
Ascend 20T 已验证可以运行 8T 生成的同一批 OM，因此不保留按开发板重复的模型副本。
Piano-DDSP 的 ONNX 发布位于 `models/piano_ddsp/model-suite-v1.0.0/`，板端 OM 位于
`models/piano_ddsp/bundles/<bundle-id>/`；`active-bundle.json` 只切换指针，不覆盖旧 bundle。

## 测试

```bash
python -m pytest -q

cd webui
npm test
npm run build
```

本地测试使用模拟设备，不替代板端 OM 加载、NPU 推理、真实 MIDI 输入或声卡试听。
在已激活 CANN 和 Conda `base` 的 Ascend 310B 板端可运行：

```bash
python tools/validate_webui_runtime.py
python tools/smoke_test_ddsp_om.py \
  --model models/om/mixed_precision/Violin_mixed_float16.om --steps 16
```

## 文档

| 文档 | 内容 |
| :--- | :--- |
| [项目概览](doc/overview.md) | 项目边界、硬件和系统链路 |
| [MIDI 测试素材](doc/midi-test-tracks.md) | 确定性生成夹具、复现命令和使用边界 |
| [DDSP-VST 导出](doc/model-export.md) | TFLite 到 ONNX 的状态化模型导出 |
| [MIDI-DDSP 导出](doc/midi-ddsp-export.md) | TensorFlow 基准、stateful v2 ONNX/OM 和逐张量对齐 |
| [两套模型对比](doc/midi-ddsp-vs-ddsp-vst.md) | 接口、实时性和适用场景差异 |
| [DDSP-VST 实时播放](doc/realtime-ddsp.md) | 实时合成、缓冲和音频输出 |
| [MIDI-DDSP 播放](doc/midi-ddsp-realtime.md) | 完整渲染缓存、复音声部化、多 voice stem 和原版混响 |
| [Piano-DDSP 实时系统](doc/piano-ddsp.md) | 固定模型来源、实时运行时、API、部署和验收 |
| [Ascend 音频输出](doc/audio-output.md) | 板载、USB、蓝牙和扬声器测试 |
| [OM 转换与验证](doc/om-deployment.md) | ATC、校验值和日志判定 |
| [板端实测结果](doc/benchmark-results.md) | DDSP-VST 板端结果 |
| [Web 工作台](doc/webui.md) | 三个工作区、构建、同步和启动 |
| [故障排查](doc/troubleshooting.md) | SSH、音频、ATC、OM 和兼容问题 |
| [第三方参考仓库](doc/upstream-repositories.md) | 固定提交和保留规则 |

本案例对应的书稿源文件是
[`src/experiment/case3.md`](../../src/experiment/case3.md)。
