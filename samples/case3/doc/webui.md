# MIDI-DDSP Studio Web 界面

MIDI-DDSP Studio 是运行在 Ascend 310B 开发板上的音乐工作台。界面可以在板载
触摸屏或同一局域网内的电脑浏览器中打开，提供触控演奏、MIDI 键盘、MIDI-DDSP
播放与渲染和设备检查四个工作区；扬声器测试合并在设备工作区中。

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
python -m pip install --user -r requirements.txt
```

`requirements.txt` 是板端唯一的 Python 依赖入口，包含 Web 服务、NumPy、
Mido、RtMidi 和 SoundDevice。Web UI 只使用 OM 模型，不安装或扫描 ONNX 模型。ONNX/TFLite
导出工具属于本地开发流程，其依赖不纳入板端 `requirements.txt`。PyACL 必须由开发板现有
CANN 环境提供，`ais_bench` 由已有 Ascend 基准测试环境提供。板端 Anaconda 位于管理员
所有的 `/usr/local/miniconda3`，普通用户必须使用 `--user` 将 Python 包安装到
`~/.local/`；不要使用 `sudo pip` 修改管理员安装的 Anaconda。

## 本地构建与同步

在 Windows 开发电脑的 `case3` 根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/deploy_midi_ddsp_webui.ps1
```

脚本在本地执行 `npm ci` 和生产构建，然后通过 SSH 别名 `ascend8t` 将前端产物、
FastAPI 服务、运行模块、文档和 MIDI 输入同步到
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

板载触摸屏需要平板式软键盘和中文输入时，按
[触摸屏输入法配置](touchscreen-input.md)安装并设置 Onboard + IBus Pinyin。该配置使用
英文 XFCE 菜单名称，并包含自动弹出、底部停靠、登录自启和 Firefox 故障排查步骤。

启动脚本会在终端打印检测到的局域网地址；Web 界面顶部状态条、左侧底部状态和
“设备 / 系统与设备”摘要卡也会显示当前开发板 IP，便于从另一台电脑远程登录或打开页面。

服务没有登录功能，只应运行在可信局域网中。浏览器发起的有副作用 HTTP 请求和
WebSocket 连接必须与页面同源；反向代理使用不同公开域名时，可通过逗号分隔的
`MIDI_DDSP_ALLOWED_ORIGINS` 显式加入允许来源。API 只接受目录扫描生成的模型 ID、MIDI
ID 和设备 ID，不接受浏览器提交的任意文件路径或 shell 命令。

## 工作区

### 实时演奏工作区

“触控演奏”和“MIDI 键盘”是同一个实时乐器会话的两个独立工作区。用户只选择钢琴、
提琴、长笛等音色，不选择 Piano-DDSP 或 DDSP-VST 引擎。两页共用实时服务、音色、
音频输出、录音、监听和模型参数；底层仍保留两个独立的 OM 契约与运行时，不合并模型图，
也不同时叠加发声。

页面加载音色库时不占用 NPU 或声卡；用户选择音色、输出和延时档位后显式点击
“开始演奏”。运行中选择其他音色会先检查当前输出兼容性，再释放手动音符和踏板、
暂停并保存 MIDI 位置，在同一个 `realtime-session` 资源锁内停止旧运行时并启动目标
运行时。成功后恢复 MIDI 位置、速度、循环和监听；失败时自动回滚旧音色。录音期间
音色锁定，停止会话会先完成 WAV 再释放资源。

#### 触控演奏

触控演奏不提供 32、49、61 或 88 键，避免在 10 英寸屏幕上把可点击琴键压得过小。它默认
显示 25 键的 `C3-C5` 两个八度，也可切换为 13 键的 `C4-C5`；两个范围都可用左右按钮按
12 个半音移动，到达标准钢琴边界后自动禁用。触控页只保留大尺寸、可点击的琴键和力度、
移调、混响、弯音、延音控制。动态卷帘位于可点击琴键上方，显示实际触控音符；触控页
不显示 MIDI 页的紧凑状态琴键。切换音域前先发送 all-notes-off，避免触摸音符悬挂。

触控布局参考 [iPad 版库乐队的键盘布局和大小](https://support.apple.com/zh-cn/guide/garageband-ipad/chs39282dbe/ipados)：
库乐队将键盘尺寸作为“小、中、大”界面选项，而不是把屏幕中的键长绑定到实体钢琴毫米
比例。本项目同样默认“中”档，并在触控页提供小、中、大切换；三档使用固定的触控高度，
键盘始终铺满舞台宽度。这样 10 英寸屏幕可同时看到卷帘和完整的 25 键键盘，不对屏幕像素
密度或实体钢琴尺寸做任何推断。

#### 动态钢琴卷帘

实时演奏区的卷帘是与当前键盘音域对齐的 Canvas 可视化，而不是 MIDI 文件的预览。白键使用等宽列，黑键按相邻白键的位置叠放；音符块从上方移动至命中线，因此触控页和 MIDI 键盘页都能把正在演奏的音高直接对应到下方琴键。卷帘保留 2、4 或 8 秒历史，默认 4 秒；结束音符至少绘制为 8 px，避免极短的有效音符因时长换算而不可见。为保持 10 英寸触摸屏的操作流畅，有轨迹时的重绘上限为 30 FPS，画布像素比上限为 1.25，且最多保留最近 192 条轨迹；这些限制只影响动画开销，不会改变声音或 MIDI 事件。

卷帘不依赖轮询得到的 `active_notes` 快照来记录音符。浏览器在触控键按下和松开时立即创建 `note_on`/`note_off` 轨迹；Piano-DDSP 运行进程和 DDSP-VST 输入路由也会把实际生效的状态变化经实时 WebSocket 发布为下列离散事件：

```json
{"event":"note","note":60,"on":true}
{"event":"note","note":60,"on":false}
```

前端以事件到达时的单调时间戳记录轨迹，后续的 `status`/`heartbeat` 快照只用于当前琴键高亮、初始同步和断线恢复。因此，即使按下和松开都发生在同一个浏览器动画帧内，或其持续时间短于一次画布重绘，也会留下一个最小可见的卷帘块。实体 MIDI 输入和 MIDI 文件播放使用同一条后端事件通路；同一个音高被多个输入源同时按住时，只有首次按下和最后一次松开会改变卷帘状态。

当前可见音域之外的音符不会绘制到卷帘，这是有意的范围裁剪；在 2、4 或 8 秒历史窗口之外的结束音符也会自然移出画面。前端单元测试覆盖同一动画帧内的按下/松开边沿，Playwright 回归测试会向 WebSocket 注入短音事件并检查画布确实出现有效像素。

#### MIDI 键盘

MIDI 键盘页用于连接实体 MIDI 控制器，顶端直接提供端口选择和动态钢琴卷帘。它从 32 键
`F2-C5` 起步，可切换为常见控制器的 49/61 键 `C2` 范围或完整 88 键 `A0-C8`；少于 88 键时
同样可左右移动一个八度。卷帘可选择 2、4 或 8 秒时间窗，只显示 WebSocket 实际收到的
离散音符事件及其历史轨迹，不把实时输入伪装成可预知的未来 MIDI 音符；命中线与当前琴键
高亮会根据实时状态同步。MIDI 文件播放器位于该页下方抽屉，触控页不会显示它。

触控页与 MIDI 页分别将键数和起始音写入版本化浏览器配置，因此在两个工作区间切换不会
相互覆盖音域。触摸指针设备默认进入触控演奏，普通桌面浏览器默认进入 MIDI 键盘页。

在 10 英寸等触摸设备上，界面通过 `any-pointer: coarse` 将导航、音色卡、音域与八度控制、
演奏参数和抽屉文字提升到约 15-20 px；主要点击目标为 50-82 px。布局采用库乐队的
“顶部控制区 + 弹奏区”层级：音色、卷帘和演奏控制保持在大琴键上方。针对 10 英寸常见
宽度还会应用同一字号尺度，即使扩展坞或浏览器把触摸屏报告为 fine pointer。触控琴键铺满
舞台宽度，但使用固定的触屏高度而非真实键长宽比；普通桌面浏览器不受此宽度规则影响。

布局参考 ChordMiniApp 固定提交 `33623b8885259f59c4005dad79b489aca8ae4ef9` 中
`PianoRollPanel`、`FallingNotesCanvas`、`PianoKeyboard` 和 `PianoVisualizerHeader`
的公开实现：卷帘与紧凑钢琴约为 280:60，命中线位于卷帘高度的 88%。本项目没有复制
其 Canvas 或播放逻辑；实时历史渲染器最高 30 FPS，设备像素比限制为 1.25，页面隐藏
时停止绘制，音符记录上限为 192 条。音符更新通过独立 external store 订阅，不驱动
整个实时页面按帧重渲染。输出增益固定显示在两页工具栏，使用 `-60..+6 dB` 的实际
物理量，默认 `0 dB`；负值衰减、正值提升，并在会话运行期间实时生效。该控件只调整
合成器输出，不修改浏览器、PulseAudio 或 ALSA mixer 的系统音量。力度、移调、混响、
弯音和延音位于触控演奏页；MIDI 文件、录音监听、模型参数、连接设置和性能诊断位于
相应页面的底部抽屉。主界面不显示引擎名，诊断页才显示实际运行时；正增益造成满幅削波时，
诊断页显示累计削波样本数。

#### Piano-DDSP 钢琴

“钢琴”默认进入 Piano-DDSP，提供 16 声部、延音踏板、钢琴年份、实时模型切换、
MIDI 文件播放器、浏览器监听和录音。它使用版本化 FP32 bundle 和独立常驻 worker，
不经过 DDSP-VST 的单音控制模型。完整模型、部署和验收约定见
[Piano-DDSP 实时系统](piano-ddsp.md)。

#### DDSP-VST 神经音色

“神经音色”使用的是 **DDSP-VST 状态化音色 OM**，不是 MIDI-DDSP 双模型。选择动态扫描到
的 DDSP-VST OM、音频输出和实体 MIDI 输入。触控钢琴和实体 MIDI 会进入同一实时引擎。
窗口失焦、触摸取消、WebSocket 断开或停止会触发
all-notes-off，避免持续音符。默认优先选择 Violin Mixed OM 和单音模式；FP16 与
2-8 声部只在高级设置显示。

USB/有线输出默认使用 `balanced` 延时档（2 个 20ms 控制帧、20ms 设备缓冲），
也可以选择 `low` 或 `safe`。蓝牙输出禁用 `low` 并使用至少 220ms 的 A2DP 缓冲。
运行状态分别显示渲染、队列、设备、Pulse Sink 和估算总延时。模型下方的音域来自
Google TFLite `metadata.json`；超出训练音域的按键会降低饱和度，但不会自动移调。

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

页面将“音频库”和“新建渲染”分开。任务目录中已经存在 `output.wav` 的历史 MIDI-DDSP
任务会持续出现在音频库中，不要求当前曲目、音色或模型参数与生成时一致。浏览器播放器
直接读取该 WAV；“开发板播放”会明确提交下拉框中显示的实际设备，不再把空值解释为
含义不确定的“系统默认”。USB/蓝牙 PulseAudio 输出使用 `paplay`；没有外接输出时，板载
3.5 mm 使用 `Deviceid 2`、`hw:ascend310b`、48 kHz 单声道 `aplay` 兼容路径，立体声
WAV 在临时文件中确定性下混，原始 WAV 不改写。播放不会重新加载模型或执行 NPU 推理。
开发板直放提供 `-60` 到 `0 dB` 的独立增益，默认按 WAV 原始电平使用 `0 dB`，且支持
暂停、继续和停止。
“播放位置”使用“当前浏览器 / 开发板喇叭”二选一；页面只显示所选路径对应的控制器，
避免把浏览器原生播放键误认为开发板音频输出。

所有页面按物理设备使用统一名称；后端差异只作为括号标记显示，例如
`EDIFIER M16 Pro（PulseAudio）`、`EDIFIER M16 Pro（直连，默认）` 和
`板载 3.5 mm（单声道，默认）`。DDSP-VST 不显示曾导致 DMA 卡死的板载 PulseAudio
双声道路径；设备总览仍显示板载单声道兼容输出，因此不会出现设备可播放但总数为零的矛盾状态。

MIDI-DDSP 模型本身是单声部模型。stateful v2 会把复音轨自动拆成最少数量的单音
voice，按静态 batch `1/2/4/8` 推理，再按 Google MIDI-DDSP 的方式对齐并混音；程序不会
静默丢弃和弦或只保留最高声部。页面选择的渲染音色统一应用到全部 voice，MIDI 文件
中的 General MIDI program 只保留为分析信息。旧 legacy 模型包不能渲染多声部文件。

渲染期间页面持续显示固定阶段列表、总进度、阶段进度、当前声部批次、工作量、已用
时间、ETA 和最近心跳。10 秒没有心跳会显示连接警告，恢复事件后自动消失。渲染期只
提供停止；完整 WAV 写入缓存并进入播放后才启用暂停/继续。波形、下载和播放器始终
选择最终 `output.wav`，不会误选 stem。

运行时使用 Google MIDI-DDSP 的谐波、FilteredNoise 和逐乐器混响语义。混响资产为
20 组 16 kHz、48,000 点 IR，产品使用 ID 0-12，采用 2,048 点分区 FFT 卷积并叠加
干声；默认在上游 1 秒结束静音之外保留 2 秒混响尾音。已验证 origin OM、种子 `20260724`
与 `0 dB` 为默认值。缺少或损坏混响资产时任务不会启动。多 voice 求和超过
`-0.45 dBFS` 时会统一降低最终混音增益，避免写入 WAV 或设备时发生硬削波；报告记录
原始峰值、保护增益和超范围样本数。

### 设备：扬声器测试

设备页通过 PulseAudio 的结构化 sink 列表显示当前音频输出，包括已经连接并加载为
`bluez_sink` 的蓝牙音箱。选择输出后，可播放短正弦测试音，分别检查左声道、双声道或右声道。
页面可调测试频率、音量和持续时间，并显示进度、采样率、声道数和音频下溢次数。
默认测试音为 440 Hz、-18 dBFS、3 秒；后端将单次测试限制在 10 秒以内，最大音量限制
为 -3 dBFS，并在测试音首尾加入淡入淡出以减少爆音。

设备页还提供“蓝牙音频”面板。该面板使用开发板系统中已经存在的 `bluetoothctl`
扫描、配对、信任、连接和断开设备，不安装软件，也不修改系统启动配置。连接成功后，
后端会尽量将对应 `bluez_card` 切换到 A2DP 播放 profile；随后刷新音频输出列表，
蓝牙音箱会以“蓝牙”标记出现在实时演奏、MIDI-DDSP 和扬声器测试的音频输出下拉框中。
若设备需要 PIN、确认码或特殊 HFP/A2DP 流程，界面会保留错误信息，需要在开发板系统界面
或终端中完成该设备特有的交互。

测试音使用 `paplay --device=<sink>` 直接发送到下拉菜单选中的 PulseAudio 输出，不依赖
系统默认输出。蓝牙设备只有在 `bluetoothctl info <MAC>` 显示 `Connected: yes`，并且
`pactl list short sinks` 中出现对应 `bluez_sink` 后才会进入下拉菜单。仅完成配对但当前
断开的设备不会作为可播放输出显示。

扬声器测试与实时演奏、MIDI-DDSP 播放共用资源锁。声卡被其他任务占用时，
设备页会禁用启动按钮，API 返回 `409 busy`。该测试能够确认所选输出路径是否实际发声及
左右声道是否正确，但不能替代麦克风、声压计或硬件回环进行的音质与电气测量。

### 设备

显示 NPU、CANN、PyACL、Python 依赖、模型、音频输入输出和 MIDI 端口状态。已知的
`npu-smi` `Health: Alarm` 只显示警告；实际 OM 推理成功时不会阻断操作。

设备页通过 `/api/v1/audio-inputs` 区分真实 `capture` 和 PulseAudio `monitor`。monitor
只是输出回采，不算 DDSP-VST Effect 的真实麦克风输入。当前板端若只有板载与蓝牙
A2DP monitor，页面会显示“无真实音频输入”，并且不提供 Effect 启动按钮。Effect 的
后续启用条件为：USB/HFP capture 可见、特征模型完成 ONNX/OM 对齐、20 ms 连续推理和
双工声卡测试全部通过。

蓝牙面板可以处理常见无 PIN 音箱的扫描、配对和连接。不同耳机或喇叭的命令行配对及
A2DP/HFP 配置可能不同；若 Web 界面返回认证、profile 或控制器错误，优先保留错误输出，
再用系统图形界面或 `bluetoothctl` 终端交互完成设备特有步骤。

如果开发板内核没有 `/dev/snd/seq` 或 `snd_seq` 模块，RtMidi 无法创建 ALSA
Sequencer 客户端。此时 MIDI 端口接口会返回 `available: false`，界面仍可使用
触控钢琴和电脑键盘；这不是重新安装 `mido` 或 `python-rtmidi` 能够解决的问题。

## API 命名

- `GET /api/v1/status` 使用 `realtime` 返回统一会话状态。
- `GET /api/v1/realtime/catalog|status` 返回统一音色、兼容设备和当前会话。
- `POST /api/v1/realtime/start|switch|stop|panic` 与
  `PATCH /api/v1/realtime/parameters` 管理统一实时会话。
- `WS /api/v1/realtime/events` 统一处理音符、踏板、弯音、播放器、录音、监听和状态事件；
  `GET /api/v1/realtime/recordings/{id}` 下载完成的 WAV。
- `GET /api/v1/catalog` 返回 `ddsp_vst_models`、`midi_ddsp_bundles`、带单/复音分析的 MIDI 和混响资产。
- `GET /api/v1/audio-inputs` 返回分类后的音频输入；实时输出从
  `GET /api/v1/realtime/catalog` 获取。
- `GET /api/v1/bluetooth-audio`、`POST /api/v1/bluetooth-audio/scan|connect|disconnect`
  管理蓝牙音频设备发现与连接。
- `GET /api/v1/midi-ddsp/audio-devices` 只返回 MIDI-DDSP WAV 播放实际支持的输出，并标明
  当前默认设备。
- `POST /api/v1/midi-ddsp/jobs` 使用 `model_bundle_id`、乐器、种子和尾音参数管理播放/渲染。
- `POST /api/v1/midi-ddsp/recordings/{job_id}/play` 将历史任务的 `output.wav` 直接发送到
  指定开发板音频输出，不触发 MIDI-DDSP 渲染。

旧 `/api/v1/live/*`、`/api/v1/ddsp-vst/*` 和 `/api/v1/piano-ddsp/*` 路由已删除，
实时功能统一使用 `/api/v1/realtime/*`，避免维护多套行为不同的接口。

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

实时界面的 Playwright 验收覆盖 1366x768、1024x600 和 390x844，检查琴键数量、布局
稳定性、横向溢出、抽屉与底部导航关系，并向实时 WebSocket 注入三和弦后读取 Canvas
像素，避免只检查到一个空白画布。

## 常见问题

- 页面显示“板端功能不可用”：本地开发环境会主动禁用 OM 播放和板端测试，这是预期行为。
- 启动时报 `missing existing dependencies`：按错误清单手动安装依赖后重新启动。
- 找不到蓝牙声卡：先在系统图形界面完成配对和音频模式选择，再刷新设备页。
- 实体 MIDI 显示不可用：检查 `ls -l /dev/snd/seq`；设备不存在时继续使用触控或电脑键盘。
- 返回 `409 busy`：NPU 或声卡正被另一个实时、播放或测试任务占用，停止该任务后重试。
- 更换或新增模型后列表不更新：点击界面刷新按钮，目录扫描不使用浏览器传入的路径。
