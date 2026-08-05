# Case 3：Ascend 310B DDSP 音乐工作台

本目录包含 Ascend 310B 智能电子琴案例的可运行程序、Web 工作台、已发布模型下载工具和
板端验证脚本。系统接收触控钢琴、实体 MIDI、MIDI 文件或实体麦克风输入，
使用 PyACL 调用 OM 模型，并通过已连接的 USB、蓝牙或板载音频设备播放。

详细设计、板端记录和操作步骤见 [doc/](doc/README.md)。

## 三条模型链路

项目同时保留三套不同用途的模型，不能混用：

| 链路 | 入口 | OM 模型 | 用途 |
| :--- | :--- | :--- | :--- |
| Piano-DDSP | `piano_ddsp_runtime.worker` | `model-suite-v1.0.1` FP32 bundle | 16 声部钢琴、硬件/网页 MIDI 与 MIDI 文件的统一实时播放 |
| MIDI-DDSP | `midi_ddsp_realtime.py` | stateful v2 版本化模型包 | Web“MIDI-DDSP”页面的 MIDI 文件分析、WAV 渲染和播放 |
| DDSP-VST Effect | `midi_ddsp_webui.ddsp_vst_effect` | Feature OM 与 11 个 Control OM | 摄像头麦克风的实时单音音色转换 |

Piano-DDSP 是独立常驻子进程，不经过 MIDI-DDSP 的整曲预渲染和 WAV 缓存流程。
MIDI-DDSP 先从音符序列生成 expression controls，再由 synthesis 网络生成 DDSP 参数；
stateful v2 把上下文和自回归状态显式跨块传递。DDSP-VST Effect 每 20 ms 从实体 Capture
提取音高与响度，再由 Control OM 预测谐波和噪声控制量。三条链路都在模型外执行音频合成，
生产运行时只加载已验证的 OM，不提供 ONNX、TFLite 或 CPU 模型回退。

## Web 工作台

MIDI-DDSP Studio 使用 React + TypeScript + Vite 前端和 FastAPI 后端，包含四个工作区：

- **实时演奏**：在同一工作区内切换触摸屏 13/25 键和实体 MIDI 键盘 32/49/61/88 键模式。
- **MIDI-DDSP**：使用版本锁定的模型包完整渲染并缓存 WAV，再播放或下载。
- **DDSP-VST**：UGREEN 摄像头麦克风经 Feature OM 和 Control OM 实时转换音色，再输出到漫步者音箱。
- **设备**：查看 NPU、模型、音频与 MIDI 状态，并测试音频输入电平、PulseAudio 输出和左右声道。

DDSP-VST Effect 只接受真实 PulseAudio `capture`，不会把输出 `monitor` 当作麦克风。
开发板运行时强制使用 Feature OM 和 Control OM；模型缺失、SHA256 不符、NPU 不可用或
所选设备消失时拒绝启动或立即停止，不提供 ONNX、TFLite 或 CPU 模型回退。

前端在开发电脑构建，开发板只运行 Python 服务和 `webui/dist/`。板端日常启动：

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/run_webui.py
```

在 Ascend 板上检测不到 `acl` 时，启动程序会使用现有
`/usr/local/Ascend/ascend-toolkit/set_env.sh` 和 Conda `base` 重新执行自身；该过程
只设置当前服务进程的环境，不修改 shell 启动文件。随后程序打印本机和局域网访问
地址，默认监听 `0.0.0.0:8765`。安装依赖、全屏启动、逐页操作、12 张实机截图、构建、
同步和接口索引统一见 [WebUI 操作、部署与 API](doc/webui.md)。

### 板载触摸屏全屏打开

服务启动后，直接在开发板的 Firefox 中打开：

```text
http://127.0.0.1:8765
```

推荐使用 Firefox kiosk 模式隐藏地址栏和标签栏，使界面铺满 10 英寸触摸屏：

```bash
DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority \
firefox --kiosk http://127.0.0.1:8765
```

这条命令可以从 SSH 会话控制开发板已有的图形桌面；若在板端本地图形终端执行，通常只需：

```bash
firefox --kiosk http://127.0.0.1:8765
```

已在普通 Firefox 窗口中打开页面时，按 `F11` 可切换全屏。日常只保留
`python scripts/run_webui.py` 启动的一个 `8765` 服务；开发板不运行 `npm run dev`、Vite
或其他前端端口。

## Python 依赖

- `requirements.txt`：唯一的本地/板端 Python 依赖入口，包含运行时、ONNX/OM 校验和 pytest。
- PyACL 由 CANN 提供，`ais_bench` 由开发板现有基准环境提供，不从 PyPI 安装。

Piano-DDSP 使用开发板已有的 CANN 8.0.0 和 Conda `base`。部署脚本只在该已有环境中
执行 `python -m pip install -r requirements.txt` 与 pytest 验证；不安装 Node/npm，不修改
音频服务、系统配置或 shell 启动文件。

## 模型与板端命令

早期 `realtime_ddsp.py` 的 DDSP-VST MIDI Synth/ONNX 命令已退出当前使用流程，只保留为
[历史实时 DDSP 路径](doc/realtime-ddsp.md)。当前 DDSP-VST 是 WebUI 中的麦克风 Effect，
运行时必须使用 Feature OM 和 Control OM；实时触控与实体 MIDI 演奏统一使用 Piano-DDSP。

### 已发布模型下载

Piano-DDSP、DDSP-VST 和 MIDI-DDSP 的 ONNX/OM 都从
`zhouxzh/piano-ddsp-ascend310` 已发布 release 获取，不在 case3 重新导出。下载器先读取
固定 revision 的 `SHA256SUMS`，再断点下载并逐项校验：

```bash
# 默认是锁定的 Piano-DDSP release。
python tools/download_model_release.py

# DDSP-VST 或 MIDI-DDSP 使用发布清单中固定的 revision、目录和 manifest SHA256。
python tools/download_model_release.py \
  --revision <immutable-release> --release-dir <published-directory> \
  --target-dir models/<family> --manifest-sha256 <sha256-of-SHA256SUMS>
```

下载报告会保存解析后的提交 SHA。不要用移动的分支名替代发布 revision，也不要让部署脚本
依赖本地 `.tflite`、checkpoint 或旧 ONNX 文件。

### Piano-DDSP 模型与板端转换

开发电脑下载固定发布版本：

```bash
python tools/download_model_release.py
```

仅在 Ascend 310B 板端激活已有环境后转换；脚本在非 ARM/无 ATC 环境会直接拒绝：

```bash
python prepare_piano_ddsp_models.py --variant gru-unrolled --models gru_ir_96_64
python tools/validate_piano_ddsp_om.py \
  --bundle models/piano_ddsp/bundles/model-suite-v1.0.1-gru-unrolled-fp32-origin/manifest.json \
  --model-id gru_ir_96_64 \
  --reference models/piano_ddsp/references/model-suite-v1.0.1/gru_ir_96_64/reference-10000.npz \
  --report reports/piano-ddsp/gru-ir-96-64-10000.json --frames 10000 --activate
```

ATC 命令固定显式使用 `precision_mode_v2=origin`。为控制板端编译温度和内存，准备脚本
同时固定 `MULTI_THREAD_COMPILE=0`、`TE_PARALLEL_COMPILER=1` 和
`enable_graph_parallel=0`，以单线程、无并行方式执行 ATC。CANN 8.0.0 的原生
`DynamicGRUV2` 不接受 FP32 输入，因此 FP32 基线使用已经与原始 ONNX 连续逐帧对照
10,000 帧的 `gru-unrolled` 变体。短于 10,000 帧的冒烟报告不能激活模型；catalog 和
worker 也会拒绝没有合格报告的 OM。

2026-07-31 板端验证已完成 v1.0.1 四个 `gru-unrolled` FP32 OM 的 10,000 帧连续对照，单帧
NPU P99 为 1.18-1.31 ms；历史 `balanced` 八帧完整块已测 P99 为 23.22 ms。该次完整块
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
├── realtime_ddsp.py            # 已退役 DDSP-VST MIDI Synth 的历史 CLI
├── pyacl_ddsp.py               # 历史 CLI 使用的 PyACL 控制模型后端
├── midi_ddsp_realtime.py       # MIDI-DDSP 文件播放和渲染会话
├── pyacl_midi_ddsp.py          # MIDI-DDSP 固定张量 PyACL 后端
├── prepare_piano_ddsp_models.py # 板端 Piano-DDSP ATC 与 bundle 生成器
├── piano_ddsp_runtime/          # 独立 Piano-DDSP 实时运行时和 NDJSON worker
├── midi_ddsp_webui/            # FastAPI API、任务、设备、Effect 和扬声器服务
├── webui/                      # React/TypeScript 前端源代码
├── scripts/                    # 板端启动和只读环境检查入口
├── tools/                      # 发布模型下载、ATC、部署、验证和报告工具
├── tests/                      # 不调用 Ascend 硬件的本地测试
├── models/                     # 模型、日志和校验清单（仅 README 提交）
├── reports/                    # 精度、性能和 Web 任务报告（不提交）
├── midi/                       # MIDI 曲库、MuseScore 工程和确定性测试夹具
├── midi_wav/                   # MIDI 曲库对应的单/双声道试听音频
├── _upstream/                  # 固定版本的第三方参考源码
└── doc/                        # 设计和实测文档
```

统一实时会话由 `midi_ddsp_webui/realtime_session.py` 管理。会话从开始到停止始终持有
`realtime-session` 资源锁；触摸屏和实体 MIDI 只是同一 Piano-DDSP 会话的两个输入模式，
共享音色、输出、增益、混响、移调、录音、监听和性能状态。运行或录音时锁定输入模式，
停止后切换模式不会创建第二套模型参数。

前端琴键比例和快捷键标签的第三方来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

根目录模块包含当前 Web 后端公共代码及历史验证 CLI；用户入口统一为
`scripts/run_webui.py`。`scripts/` 只保存板端启动和只读环境检查入口。

`models/om/` 保存 DDSP-VST OM 和原版混响 IR。stateful v2 的
batch `1/2/4/8` 共 32 个相互匹配组件统一放入
`models/midi_ddsp/bundles/<bundle-id>/`，由 manifest 锁定，
浏览器不能分别组合 Expression 与 Synthesis 文件。混响 checkpoint 含 20 组 IR，
产品只展示论文支持的乐器 ID 0-12。
MIDI-DDSP bundle 固定使用 `precision_mode_v2=origin`；GRU 已展开为基础算子。
Ascend 20T 已验证可以运行 8T 生成的同一批 OM，因此不保留按开发板重复的模型副本。
Piano-DDSP 的 ONNX 发布位于 `models/piano_ddsp/model-suite-v1.0.1/`，板端 OM 位于
`models/piano_ddsp/bundles/<bundle-id>/`；`active-bundle.json` 只切换指针，不覆盖旧 bundle。

## 测试

### Codex 物理触摸屏自动化依赖

使用 Codex 自动审核开发板上的 Firefox kiosk、实际点击按钮和采集物理触摸屏截图时，
开发板必须安装 `xdotool`。它用于向 XFCE 图形会话发送鼠标、键盘、窗口激活和刷新事件；
仅运行 Playwright 不能替代这一层物理 kiosk 验收。

在基于 Debian/Ubuntu 的 Ascend 310B 开发板上，由设备管理员执行一次：

```bash
sudo apt install -y xdotool
```

安装后验证：

```bash
command -v xdotool
xdotool --version
```

Codex 通过 SSH 操作当前 `HwHiAiUser` 图形会话时需要显式指定显示器和授权文件：

```bash
DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority \
  xdotool search --onlyvisible --class firefox
```

该命令应只返回一个可见 Firefox kiosk 窗口。Codex 随后使用同样的
`DISPLAY`/`XAUTHORITY` 环境激活窗口、刷新页面并执行物理坐标点击。`xdotool` 是
Codex 自动化测试的必需辅助工具，但不是 WebUI 生产运行依赖；正常启动
`python scripts/run_webui.py`、执行 OM 推理或从其他电脑访问 8765 端口都不依赖它。
详细实机方法和截图命名见
[WebUI 触摸屏终审与实机压测](doc/webui-acceptance.md#74-xdotool-物理-kiosk-复核)。

```bash
python -m pytest -q

cd webui
npm run test
npm run build
npm run test:e2e
```

本地测试使用模拟设备，不替代板端 OM 加载、NPU 推理、真实 MIDI 输入或声卡试听。
在已激活 CANN 和 Conda `base` 的 Ascend 310B 板端可运行：

```bash
python tools/validate_webui_runtime.py
```

真实 OM、音频和 600 秒 DDSP-VST 双工命令见
[WebUI 实机验收](doc/webui-acceptance.md)，不得以旧 MIDI Synth 冒烟命令替代 Effect 验收。

## 文档

| 文档 | 内容 |
| :--- | :--- |
| [项目概览](doc/overview.md) | 项目边界、硬件和系统链路 |
| [MIDI 测试素材](doc/midi-test-tracks.md) | 确定性生成夹具、复现命令和使用边界 |
| [模型与 OM 部署](doc/om-deployment.md) | 已发布模型下载、ATC、校验值和日志判定 |
| [MIDI-DDSP 历史导出](doc/midi-ddsp-export.md) | 历史 TensorFlow 基准、模型结构、张量契约和验证记录 |
| [两套模型对比](doc/midi-ddsp-vs-ddsp-vst.md) | 历史迁移背景和模型差异，不是当前用户入口 |
| [历史实时 DDSP 路径](doc/realtime-ddsp.md) | 已退役的 DDSP-VST MIDI Synth/ONNX 对照资料 |
| [MIDI-DDSP 播放](doc/midi-ddsp-realtime.md) | 完整渲染缓存、复音声部化、多 voice stem 和原版混响 |
| [Piano-DDSP 实时系统](doc/piano-ddsp.md) | 固定模型来源、实时运行时、API、部署和验收 |
| [Ascend 音频输出](doc/audio-output.md) | 板载、USB、蓝牙和扬声器测试 |
| [板端实测结果](doc/benchmark-results.md) | DDSP-VST 板端结果 |
| [WebUI 操作、部署与 API](doc/webui.md) | 四个工作区、12 张实机截图、全屏启动、构建部署和接口索引 |
| [WebUI 实机验收](doc/webui-acceptance.md) | 12 页面视觉审核、四视口回归、UI/API 压测和 600 秒 Effect 结果 |
| [故障排查](doc/troubleshooting.md) | SSH、音频、ATC、OM 和兼容问题 |
| [第三方参考仓库](doc/upstream-repositories.md) | 固定提交和保留规则 |

本案例对应的书稿源文件是
[`src/experiment/case3.md`](../../src/experiment/case3.md)。
