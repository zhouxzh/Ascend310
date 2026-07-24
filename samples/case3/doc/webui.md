# MIDI-DDSP Studio Web 界面

MIDI-DDSP Studio 是运行在 Ascend 310B 开发板上的音乐工作台。界面可以在板载
触摸屏或同一局域网内的电脑浏览器中打开，提供实时演奏、MIDI-DDSP 播放与渲染、
OM 实验和设备检查四个工作区；扬声器测试合并在设备工作区中。

## 技术方案

- 浏览器端：React、TypeScript、Vite、Lucide 和 Recharts。
- 板端服务：FastAPI、Uvicorn 和 WebSocket。
- 推理与音频：现有 PyACL/OM、PortAudio、Mido 和 RtMidi 代码。
- 生产部署：开发电脑编译 `webui/dist/`，开发板只运行 Python 服务和静态文件。

Flask 与 FastAPI 都适合提供 HTTP 服务，但它们不是完整的前端框架。Gradio 适合快速
搭建模型演示，难以精确控制低延迟钢琴事件、复杂工作区、任务状态和触摸屏布局。
因此本项目使用 React + FastAPI；不需要额外安装 Flask 或 Gradio。

## 板端手动安装

以下命令仅供用户在 Ascend 开发板上手动执行。同步和启动脚本不会安装、升级或删除
任何板端软件。

先进入项目使用的 Anaconda `base` 环境：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
cd /home/HwHiAiUser/Documents/case3
```

检查 PortAudio 动态库：

```bash
ldconfig -p | grep libportaudio.so.2
```

如果没有输出，需要手动安装系统运行库：

```bash
sudo apt install libportaudio2
```

安装 Web 服务、MIDI 和音频 Python 依赖：

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` 是板端唯一的 Python 依赖入口，包含 Web 服务、NumPy、Pygame、
Mido、RtMidi 和 SoundDevice。Web UI 只使用 OM 模型，不安装或扫描 ONNX 模型。ONNX/TFLite
导出工具属于本地开发流程，其依赖不纳入板端 `requirements.txt`。PyACL 必须由开发板现有
CANN 环境提供，`ais_bench` 由已有 Ascend 基准测试环境提供。

## 本地构建与同步

在 Windows 开发电脑的 `case3` 根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/deploy_midi_ddsp_webui.ps1
```

脚本在本地执行 `npm ci` 和生产构建，然后通过 SSH 别名 `ascend8t` 将前端产物、
FastAPI 服务、运行模块、受控实验工具、文档和 MIDI 输入同步到
`/home/HwHiAiUser/Documents/case3`。远端 MIDI 文件会按本地目录镜像，因此本地删除的
`.mid`/`.midi` 也会从板端移除。脚本不会远程安装
依赖，也不会修改 shell 启动文件或系统服务。可通过参数修改目标：

```powershell
tools/deploy_midi_ddsp_webui.ps1 `
  -SshTarget ascend8t `
  -RemoteRoot /home/HwHiAiUser/Documents/case3
```

## 启动

首次安装或修改环境后执行一次只读检查：

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/check_webui_env.py
```

检查器只验证当前 `base`、CANN 环境变量、Python 包和前端产物，不设置环境变量，也不
安装或修改软件。检查通过后，日常在开发板终端直接启动：

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/run_webui.py
```

`scripts/run_webui.py` 只启动 Uvicorn 并监听 `0.0.0.0:8765`。它不设置或检查环境；程序和
模型运行错误会直接显示在当前终端。启动时会打印板载地址和自动检测到的局域网 IPv4
地址，终端可直接识别完整的 `http://` 链接。

打开地址：

- 板载浏览器：`http://127.0.0.1:8765`
- 局域网电脑：`http://<开发板 IP>:8765`

服务没有登录功能，只应运行在可信局域网中。API 只接受目录扫描生成的模型 ID、MIDI
ID 和设备 ID，不接受浏览器提交的任意文件路径或 shell 命令。

## 工作区

### DDSP-VST

“DDSP-VST”使用的是 **DDSP-VST 状态化音色 OM**，不是 MIDI-DDSP 双模型。选择动态扫描到
的 DDSP-VST OM、音频输出和实体 MIDI 输入。触控钢琴、电脑键盘和
实体 MIDI 会进入同一实时引擎。窗口失焦、触摸取消、WebSocket 断开或停止会触发
all-notes-off，避免持续音符。默认优先选择 Violin Mixed OM 和单音模式；FP16 与
2-8 声部只在高级设置显示。

页面提供 Pitch Shift、Harmonics、Noise、Output Gain、ADSR、Input Pitch、Input Gain、
Reverb Size、Damping 和 Wet。除模型与输出设备外，这些参数可在会话运行时通过
WebSocket 更新。处理顺序为状态化 OM、谐波/噪声增益、CPU 合成、输出增益和
JUCE/FreeVerb 风格混响。

Web 服务在进程内保留一次 PyACL runtime 初始化，停止会话时释放音符、声卡、OM、
dataset、buffer 和 context，但不反复执行 `reset_device`/`acl.finalize`。这样模型或输出
设备切换后可以立即重新启动；服务进程退出时才统一释放 device 和 ACL runtime。

### MIDI-DDSP

该页面使用真正的 **MIDI-DDSP 版本化模型包**。可选择仓库 MIDI 或上传不超过 10 MiB
的 `.mid`/`.midi` 文件，随后完整渲染并缓存 WAV，再播放或下载。模型包固定源码提交、
checkpoint、全部 ONNX/OM 组件和随机种子，前端不再分别组合 Expression 与 Synthesis。

MIDI-DDSP 模型本身是单声部模型。单轨和弦或其他复音文件返回
`422 polyphonic_track`，渲染按钮会禁用；程序不再静默提取最高声部。多轨文件只有在
每个轨道均为单声部、且 General MIDI program 能映射到 13 种 URMP 乐器时，才由
stateful v2 逐轨渲染、保存 stem 并混音。旧 legacy 模型包不能渲染多轨文件。

运行时使用 Google MIDI-DDSP 的谐波、FilteredNoise 和逐乐器混响语义。混响资产为
20 组 16 kHz、48,000 点 IR，产品使用 ID 0-12，采用 2,048 点分区 FFT 卷积并叠加
干声；默认在上游 1 秒结束静音之外保留 2 秒混响尾音。Mixed OM、种子 `20260724`
与 `0 dB` 为默认值。缺少或损坏混响资产时任务不会启动。

### 设备：扬声器测试

设备页通过 PulseAudio 的结构化 sink 列表显示当前音频输出，包括已经连接并加载为
`bluez_sink` 的蓝牙音箱。选择输出后，可播放短正弦测试音，分别检查左声道、双声道或右声道。
页面可调测试频率、音量和持续时间，并显示进度、采样率、声道数和音频下溢次数。
默认测试音为 440 Hz、-18 dBFS、3 秒；后端将单次测试限制在 10 秒以内，最大音量限制
为 -3 dBFS，并在测试音首尾加入淡入淡出以减少爆音。

测试音使用 `paplay --device=<sink>` 直接发送到下拉菜单选中的 PulseAudio 输出，不依赖
系统默认输出。蓝牙设备只有在 `bluetoothctl info <MAC>` 显示 `Connected: yes`，并且
`pactl list short sinks` 中出现对应 `bluez_sink` 后才会进入下拉菜单。仅完成配对但当前
断开的设备不会作为可播放输出显示。

扬声器测试与实时演奏、MIDI-DDSP 播放和实验任务共用资源锁。声卡被其他任务占用时，
设备页会禁用启动按钮，API 返回 `409 busy`。该测试能够确认所选输出路径是否实际发声及
左右声道是否正确，但不能替代麦克风、声压计或硬件回环进行的音质与电气测量。

### 实验

只提供白名单内的一次 OM 运行验证和短基准测试。任务日志、状态和报告保存在
`reports/webui/jobs/<job-id>/`，界面可下载受控的 WAV、JSON 和文本产物。

### 设备

显示 NPU、CANN、PyACL、Python 依赖、模型、音频输入输出和 MIDI 端口状态。已知的
`npu-smi` `Health: Alarm` 只显示警告；实际 OM 推理成功时不会阻断操作。

设备页通过 `/api/v1/audio-inputs` 区分真实 `capture` 和 PulseAudio `monitor`。monitor
只是输出回采，不算 DDSP-VST Effect 的真实麦克风输入。当前板端若只有板载与蓝牙
A2DP monitor，页面会显示“无真实音频输入”，并且不提供 Effect 启动按钮。Effect 的
后续启用条件为：USB/HFP capture 可见、特征模型完成 ONNX/OM 对齐、20 ms 连续推理和
双工声卡测试全部通过。

蓝牙配对仍建议使用显示器和触摸屏上的系统图形界面。不同耳机或喇叭的命令行配对及
A2DP/HFP 配置可能不同，纯命令行流程对初学者较复杂。Web 界面只选择系统已经连接
并暴露出来的音频设备，不负责蓝牙配对。

如果开发板内核没有 `/dev/snd/seq` 或 `snd_seq` 模块，RtMidi 无法创建 ALSA
Sequencer 客户端。此时 MIDI 端口接口会返回 `available: false`，界面仍可使用
触控钢琴和电脑键盘；这不是重新安装 `mido` 或 `python-rtmidi` 能够解决的问题。

## API 命名

- `GET /api/v1/status` 使用 `ddsp_vst` 返回 Synth 状态。
- `GET /api/v1/catalog` 返回 `ddsp_vst_models`、`midi_ddsp_bundles`、带单/复音分析的 MIDI 和混响资产。
- `GET /api/v1/audio-devices` 与 `GET /api/v1/audio-inputs` 返回输出和分类后的输入。
- `POST /api/v1/ddsp-vst/start|stop` 与 `WS /api/v1/ddsp-vst/events` 管理实时 Synth。
- `POST /api/v1/midi-ddsp/jobs` 使用 `model_bundle_id`、乐器、种子和尾音参数管理播放/渲染。

旧 `/api/v1/live/*` 路由已删除，不再同时维护两套含义不同的命名。

## 开发与测试

本地启动 FastAPI 服务：

```powershell
python -m uvicorn midi_ddsp_webui.app:app --host 127.0.0.1 --port 8765
```

前端开发服务器：

```powershell
cd webui
npm ci
npm run dev
```

测试与构建：

```powershell
python -m unittest discover -s tests -v
cd webui
npm run test
npm run build
npm run test:e2e
```

本地测试不会执行 PyACL、ATC、OM 推理或 `npu-smi`。触控发声、USB/蓝牙输出、实体
MIDI、OM 验证和基准测试必须在真实 Ascend 310B 开发板上完成。

## 常见问题

- 页面显示“板端功能不可用”：本地开发环境会主动禁用 OM 播放和板端测试，这是预期行为。
- 启动时报 `missing existing dependencies`：按错误清单手动安装依赖后重新启动。
- 找不到蓝牙声卡：先在系统图形界面完成配对和音频模式选择，再刷新设备页。
- 实体 MIDI 显示不可用：检查 `ls -l /dev/snd/seq`；设备不存在时继续使用触控或电脑键盘。
- 返回 `409 busy`：NPU 或声卡正被另一个实时、播放或测试任务占用，停止该任务后重试。
- 更换或新增模型后列表不更新：点击界面刷新按钮，目录扫描不使用浏览器传入的路径。
