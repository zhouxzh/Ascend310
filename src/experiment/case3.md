# 案例3：Ascend 310B DDSP 智能电子琴

---

## 1. 项目简介 {#src-experiment-case3-h1}

本案例在 Ascend 310B 开发板上实现一个可通过浏览器操作的 MIDI 音乐工作台。系统接收
触摸屏钢琴、电脑键盘、实体 MIDI 键盘或 MIDI 文件输入，使用 PyACL 调用 OM 模型预测
DDSP 合成参数，再由 CPU 端音频合成器生成波形并发送到板载、USB 或蓝牙音频输出。

案例代码位于 [`samples/case3`](../../samples/case3/README.md)。当前实现不包含摄像头或
手势识别，也不存在 `smart_piano.py`。主要入口是 Web 服务
`python scripts/run_webui.py`，命令行引擎则由 `realtime_ddsp.py` 和
`midi_ddsp_realtime.py` 提供。

## 2. 系统组成 {#src-experiment-case3-h2}

### 2.1 硬件 {#src-experiment-case3-h3}

- Ascend 310B 开发板及其现有 CANN/PyACL 环境；
- 显示器和触摸屏，用于打开板端 Web 界面；
- 可选的 USB MIDI 键盘；
- USB 喇叭、已连接的蓝牙音箱或板载音频输出；
- 与开发板处于同一可信局域网的电脑或移动设备。

蓝牙设备建议通过系统图形界面完成配对。不同型号设备的 A2DP/HFP 配置可能不同，
Web 页面只枚举系统已经连接并暴露出来的 PulseAudio 输出，不负责蓝牙配对。

### 2.2 软件边界 {#src-experiment-case3-h4}

开发电脑负责编辑代码、导出 ONNX、构建 React 前端和执行不依赖硬件的单元测试。
ATC 转换、OM 推理、PyACL、`ais_bench` 和 `npu-smi` 只能在真实 Ascend 310B 开发板
上运行。

板端只使用已有的 Anaconda `base`、CANN 8.0.0、PyACL 和音频服务。Piano-DDSP
部署期间不执行 `pip`、`conda`、`apt` 等安装、升级或删除命令，不修改 CANN、Conda、
音频服务、系统配置或 shell 启动文件。缺少依赖时保留诊断并停止。ONNX 下载、参考生成、
代码开发和本地测试均在开发电脑完成。

## 3. 三条 DDSP 模型链路 {#src-experiment-case3-h5}

本案例包含三套接口不同、用途不同的模型。

### 3.1 DDSP-VST 实时演奏 {#src-experiment-case3-h6}

DDSP-VST 模型是单步状态化控制网络。每 20 ms 接收一次 MIDI 音高、力度和上一帧
512 维 GRU 状态，输出幅度、60 个谐波系数、65 个噪声系数及下一帧状态：

```text
inputs:  state[512], f0_scaled[1], pw_scaled[1]
outputs: amplitude[1], harmonics[60], noise_amps[65], state_out[512]
```

`realtime_ddsp.py` 为每个活动音符维护独立的模型状态和振荡器相位，并把各声部混合到
音频 FIFO。Web 的“DDSP-VST”页面、触控钢琴、电脑键盘和实体 MIDI 都使用这条链路。
页面默认采用 Google Synth 的单音语义，2-8 声部扩展和 FP16 模型放在高级设置中。

当前运行目录保留 11 种音色的 FP16 与 Mixed OM，共 22 个 DDSP-VST OM。官方音色
包括 Bassoon、Clarinet、Flute、Melodica、Saxophone、Sitar、Trombone、Trumpet、
Tuba、Violin 和 Vowels，不包含钢琴音色。

### 3.2 MIDI-DDSP 文件播放 {#src-experiment-case3-h7}

MIDI-DDSP 使用 Expression 与 Synthesis 两级网络：

1. Expression Generator 根据音高、音符时长和乐器 ID 生成 expression controls；
2. Synthesis Generator 根据逐帧 conditioning 生成 DDSP 合成参数。

stateful v2 将双向上下文、自回归 decoder 和 Timbre 网络拆成 8 个 origin FP32 OM，并由一个
版本化 manifest 统一选择。GRU 展开为基础算子，decoder 显式传递状态；Timbre 使用最多 65,536 帧的
整段输入和 `valid_frames` 掩码，保持官方跨时间轴的全曲归一化。MIDI-DDSP 只使用已经
通过 TensorFlow/ONNX/OM 固定夹具对齐的 origin bundle。Web 的“MIDI-DDSP”页面先完整
渲染并缓存 WAV，再播放或下载。

当前 MIDI-DDSP checkpoint 是单声部模型。单轨和弦返回 `polyphonic_track`，不再静默
提取最高声部。多轨文件仅在每轨均为单声部且 General MIDI program 可映射到 13 种
URMP 乐器时逐轨渲染、保存 stem 并混音。

### 3.3 Piano-DDSP 实时钢琴

Piano-DDSP 使用固定发布 `model-suite-v1.0.0`，训练源码固定在提交
`1f7cf65ff9c58968bc3b605ee571db928d1ac37a`，Hugging Face 标签固定解析到提交
`2199df0a55953a0d2469d59ab2f23a8bef8eb314`。四个候选模型均为 FP32、opset 13、
batch 1、16 声部、250 Hz 控制率和 16 kHz 音频率；完成板端音质与性能比较前不显示
推荐模型。

运行时是 FastAPI 托管的独立常驻子进程。MIDIPLUS 原始 MIDI、网页钢琴和 MIDI 文件
进入同一个单调时钟调度器，模型每 4 ms 执行一帧，CPU DSP 按 4/8/16 帧组合为
low/balanced/safe 音频块。它不调用 MIDI-DDSP 的整曲预渲染流程，浏览器刷新或
WebSocket 断开也不会停止硬件 MIDI、文件播放器、录音和板端音频。

固定 16 个槽位依次选择最低空闲槽、最老释放槽和最老活动槽；同音重复击键复用原槽。
CC64 控制延音，CC65-67 原值进入模型；松键声部包络为 60 ms，CC123 和会话停止使用
120 ms 全局淡出。神经状态、谐波相位、噪声 overlap、重采样和分区卷积历史均跨块保存，
Panic 会完整清空。

### 3.4 模型外音频合成 {#src-experiment-case3-h8}

三套 OM 都只预测 DDSP 参数。谐波振荡、噪声滤波、声部混合和 WAV/声卡输出由 CPU
代码完成。MIDI-DDSP 使用 Google 实现对应的 Harmonic、FilteredNoise 和 Reverb
语义；逐乐器混响资产包含 20 组 16 kHz、48,000 点 IR，运行时使用 2,048 点分区 FFT
卷积并叠加干声。DDSP-VST 按插件顺序执行控制 OM、谐波/噪声增益、合成、输出增益和
JUCE/FreeVerb 风格混响。

## 4. Web 工作台 {#src-experiment-case3-h9}

MIDI-DDSP Studio 采用 React、TypeScript 和 Vite 构建浏览器端，采用 FastAPI、
Uvicorn 和 WebSocket 提供板端服务。开发电脑生成 `webui/dist/`，开发板只运行 Python
服务和静态资源。

界面包含四个工作区：

| 工作区 | 功能 |
| :--- | :--- |
| DDSP-VST | 使用状态化 OM 接收触控、电脑键盘和实体 MIDI 事件 |
| MIDI-DDSP | 使用 stateful v2 模型包完整渲染、缓存并播放 MIDI 文件 |
| Piano-DDSP | 16 声部实时钢琴、硬件/网页 MIDI、文件播放器、录音和实时指标 |
| 设备 | 查看 NPU、模型、音频与 MIDI 状态，并测试 PulseAudio 输出和左右声道 |

所有会占用 NPU 或声卡的操作共享同一个资源协调器。同一时间只允许一个 DDSP-VST Synth、
Piano-DDSP、MIDI 播放或扬声器测试运行；资源占用时 API 返回 `409 busy`。Piano-DDSP
的 WebSocket 断开只释放该浏览器来源的音符和虚拟踏板，不停止其他输入或会话。

本轮不实现 DDSP-VST Effect。设备页会把真实 `capture` 与 PulseAudio `monitor`
分开显示；只有真实 USB/HFP 输入可见、特征模型完成 ONNX/OM 对齐、20 ms 连续推理和
双工声卡测试通过后，才增加 Effect 的运行入口。

## 5. 运行步骤 {#src-experiment-case3-h10}

### 5.1 开发电脑构建并同步 {#src-experiment-case3-h11}

在 Windows 开发电脑的 `samples/case3` 目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/deploy_midi_ddsp_webui.ps1
```

脚本执行前端生产构建，然后同步静态资源、Python 运行模块、Piano-DDSP 固定 ONNX 与
参考向量、受控测试工具、文档和 MIDI 输入文件。它不会在开发板上安装、升级或删除软件，
也不会修改 shell 启动文件或系统服务。

### 5.2 板端环境检查 {#src-experiment-case3-h12}

环境或文件布局变化后可以运行只读检查：

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/check_webui_env.py
```

检查器验证当前 conda 环境、CANN 环境变量、Python 包和前端产物，不设置环境变量。

### 5.3 启动服务 {#src-experiment-case3-h13}

```bash
cd /home/HwHiAiUser/Documents/case3
python scripts/run_webui.py
```

程序默认监听 `0.0.0.0:8765`，并打印板端本地地址与局域网地址。服务没有登录功能，
只适合可信局域网。

## 6. 音频输出测试 {#src-experiment-case3-h14}

USB 喇叭通常会直接出现在 PulseAudio 输出列表中。蓝牙音箱必须先在系统图形界面中
连接，并在 `pactl list short sinks` 中出现对应 `bluez_sink`，才能进入 Web 下拉菜单。

“设备”页的音频输出测试使用选中的 sink 播放短正弦测试音，可以检查输出路径和左右声道。仓库
`midi_wav/` 只保留两份 48 kHz、16-bit 硬件试听夹具：单声道
`ode-to-joy-violin-mono-loud.wav` 和立体声
`ode-to-joy-violin-stereo-loud.wav`。

设备页的扬声器测试只能确认是否发声和声道路由，不能替代麦克风、声压计或硬件回环进行音质与
电气测量。

## 7. OM 验证与报告 {#src-experiment-case3-h15}

DDSP-VST OM 位于 `models/om/`；stateful v2 的 8 个组件位于
`models/midi_ddsp/bundles/<bundle-id>/` 并由 manifest 锁定。不再按 8T、8T2 或 20T
重复存放。新模型仍需完成单独的板端转换和 A/B 验收。

转换日志保存在 `models/conversion_logs/`，运行和性能报告保存在 `reports/`。已知设备上的
`npu-smi` `Health: Alarm` 作为警告显示；只要设备可见且实际 OM 推理成功，就不单独
阻断操作。

Piano-DDSP ONNX 位于 `models/piano_ddsp/model-suite-v1.0.0/`，OM 位于不可变的
`models/piano_ddsp/bundles/<bundle-id>/`。`prepare_piano_ddsp_models.py` 在非
Ascend 环境直接拒绝，只生成 Ascend310B4 FP32 基线，并保存 ATC 原始日志、命令、环境、
源/目标 SHA256 和 PyACL 实际 I/O 合同。转换命令显式固定
`precision_mode_v2=origin`，并设置 `MULTI_THREAD_COMPILE=0`、
`TE_PARALLEL_COMPILER=1`、`enable_graph_parallel=0` 关闭模型与算子并行编译；CANN
8.0.0 原生 `DynamicGRUV2` 只接受 FP16 输入，故 FP32
基线使用已与原始 ONNX 连续逐帧对照 10,000 帧的 `gru-unrolled` 变体。模型还必须通过
至少 10,000 帧的 OM 对照才进入 catalog；短冒烟不能激活。回退通过
`active-bundle.json` 原子指针完成。

2026-07-29 的板端结果中，四个 `gru-unrolled` FP32 OM 均通过 10,000 帧连续对照且无
NaN/Inf；`paper_ir`、`film_fdn`、`calibrated_ir` 和 `calibrated_film_ir` 的单帧 NPU
P99 分别为 1.25、1.19、1.23 和 1.34 ms。固定噪声 DSP 对照的最低谐波/湿声 SNR 为
65.10/68.84 dB，`balanced` 八帧完整块 P99 为 23.219344 ms。后者使用板载音频路径，
只用于确认 24 ms 计算预算，不作为 EDIFIER USB 总延时验收。

## 8. 代码结构 {#src-experiment-case3-h16}

```text
samples/case3/
├── midi.py
├── realtime_ddsp.py
├── pyacl_ddsp.py
├── midi_ddsp_realtime.py
├── pyacl_midi_ddsp.py
├── prepare_piano_ddsp_models.py
├── piano_ddsp_runtime/
├── midi_ddsp_webui/
├── webui/
├── scripts/
├── tools/
├── tests/
├── models/
├── reports/
├── midi/
├── midi_wav/
├── model3/
└── doc/
```

根目录的音频/PyACL 模块同时被命令行和 Web 后端导入，因此保留在仓库根目录。
`scripts/` 只放板端启动和环境检查入口；`tools/` 保存模型导出、ATC 转换、部署、验证和
报告工具。

## 9. 验收与限制 {#src-experiment-case3-h17}

本地测试验证参数校验、任务互斥、MIDI 上传限制、断线释放、报告解析和前端交互；硬件
行为仍必须在目标开发板上验收。板端至少检查：

- DDSP-VST 默认单音下的触控钢琴、电脑键盘和实体 MIDI 输入；
- DDSP-VST 实时演奏以及 MIDI-DDSP 文件播放/渲染；
- Piano-DDSP 的 16 音和弦、快速琶音、重复音、CC64-67、voice stealing 和 MIDI 文件实时播放；
- Piano-DDSP 的 OM 10,000 帧状态对齐、单帧 NPU P99、八帧块 P99 和 10 分钟稳定性；
- EDIFIER USB balanced 软件总延时不超过 100 ms，队列不持续增长；
- USB 与已连接蓝牙输出；
- 暂停、继续、停止和断线后的音符释放；
- 一次 OM 验证、短基准测试和任务失败后的重新启动。

2026-07-29 的板端黑匣子还确认板载 `platform-sound` 存在内核级风险：Pulse
`alsa-sink-ascen` 卡在 `snd_pcm_sync_ptr` 的自旋锁并触发 NMI watchdog hard lock、AP
OS panic 和整板重启。因此 Piano-DDSP 专用设备列表过滤该 Pulse sink 和可能回落到它的
通用 ALSA 别名。随后对 `hw:0,0` 的 48 kHz 双声道 PortAudio 测试只写入 1024 帧
便阻塞，内核持续报告 `dma period irq error interval 23ms`，停止接口因底层写调用不
返回而超时。ALSA 虽接受两个声道，厂商脚本却只验证单声道；因此板载 3.5 mm 不
作为 Piano-DDSP 的受支持实时立体声输出。重复故障后，板载项改为独立、可终止的
`aplay` 单声道兼容后端，不再在 WebUI 进程内打开 PortAudio；USB 声卡/音箱或蓝牙
仍是正式演奏和低延时验收路径。
Playwright 实际部署测试 11 项通过，覆盖 1366x768 和 390x844。测试时 EDIFIER 和
MIDIPLUS 均未接入，USB 总延时、原始 MIDI、拔插和带外设 10 分钟稳定性仍未验收。

当前项目不实现摄像头手势识别或 DDSP-VST Effect；DDSP-VST 官方模型没有钢琴音色；
MIDI-DDSP 不支持单轨和弦。Piano-DDSP 负责独立的 16 声部钢琴实时链路。新 bundle
只有通过 ONNX/OM 数值对齐和板端 A/B 后才能激活；四模型比较完成前不指定推荐模型。
