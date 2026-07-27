# ONNX 与 PyACL/OM 实时播放

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

`realtime_ddsp.py` 已将验证通过的控制模型接入同一套实时流水线：`.onnx` 使用
ONNX Runtime CPU，`.om` 使用 Ascend PyACL。MIDI 状态和音频输出在独立线程中
运行，模型每 20 ms 更新一次；谐波振荡器、噪声滤波和重采样仍在 CPU 完成。

Web“DDSP-VST”工作区以 Google Synth 的单音模式为默认值，Mixed OM 默认显示；FP16
和 2-8 声部扩展位于高级设置。运行时可更新 Pitch Shift、Harmonics、Noise、Output
Gain、ADSR、Input Pitch、Input Gain、Reverb Size、Damping 和 Wet。模型与音频输出
只能在停止会话后切换。模型控制、谐波/噪声增益、合成、输出增益和 JUCE/FreeVerb
风格混响按此顺序执行。

## 安装依赖

板端 Python 依赖统一定义在 `requirements.txt`：

```text
numpy
mido
python-rtmidi
sounddevice
```

其中 `sounddevice` 只是 Python 封装，Linux 上实时声卡输出还需要系统动态库
`libportaudio2`。在 Ascend 开发板上统一激活 Anaconda `base`，不要回退到系统
Python：

```bash
sudo apt install -y libportaudio2

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
cd ~/Documents/case3
python -m pip install --user -r requirements.txt
```

板端 Anaconda 安装在管理员所有的 `/usr/local/miniconda3`。普通 `HwHiAiUser` 用户
没有该目录的写权限，因此必须使用 `--user` 安装到 `~/.local/`。不要使用 `sudo pip`；
`python -m pip` 用于确保依赖安装给当前激活的 `base` Python。

依赖用途：

| 依赖 | 用途 | 缺失时的典型现象 |
| :--- | :--- | :--- |
| `libportaudio2` | PortAudio 系统运行库，负责实时声卡 I/O | `OSError: PortAudio library not found` |
| `sounddevice` | Python 到 PortAudio 的接口 | `Install requirements.txt first` |
| `mido` | 解析 MIDI 文件和 MIDI 消息 | `MIDI playback requires mido` |
| `python-rtmidi` | 连接实体 MIDI 键盘 | `--live` 无法打开 MIDI port |
| `onnxruntime` | 在 CPU 上运行 DDSP-VST ONNX 控制模型 | 模型会话创建失败或模块不存在 |
| `acl`（PyACL） | 在 Ascend NPU 上加载和执行 OM | `PyACL is required for OM inference` |

只使用 `--play-midi` 播放 MIDI 文件时不需要实体 MIDI 键盘，但仍需要 `mido`、
`sounddevice` 和 `libportaudio2`。使用 `--live` 连接实体键盘时还需要
`python-rtmidi`。

2026-07-21 对 `ascend8t2` 的检查结果是：`onnxruntime 1.15.1` 和
`sounddevice 0.5.5` 已存在，但 `mido`、`python-rtmidi` 和系统 PortAudio 动态库
缺失。因此当时运行 `python realtime_ddsp.py --list-audio` 会在导入
`sounddevice` 时失败。安装或修改板端系统软件前应取得明确确认。

安装后先验证导入和声卡枚举：

```bash
python -c "import mido, rtmidi, sounddevice; print('realtime dependencies OK')"
python realtime_ddsp.py --list-audio
```

PyACL 随 CANN 安装，不是 `pip` 依赖。运行 OM 前还要加载板端 CANN 环境；不要在
开发电脑上安装或执行 PyACL：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import acl; print(acl.__file__)"
```

漫步者 M25 在 ALSA 中的 card ID 是 `Device`，但 `--audio-device` 使用的是
PortAudio 列出的设备名称或编号。先执行 `--list-audio`，再选择名称中包含
`USB Composite Device` 的输出设备编号。

```bash
# 无需 MIDI 键盘/声卡，先生成 1 秒 WAV 做冒烟测试
python realtime_ddsp.py --demo --duration 1 --output violin_demo.wav

# 生成一段单声部小提琴测试 MIDI，并渲染成 WAV
python tools/create_test_midi.py --output test_violin.mid
python realtime_ddsp.py --midi-file test_violin.mid --output test_violin.wav

# 查看 MIDI 输入设备
python realtime_ddsp.py --list-midi

# 连接 MIDI 键盘并实时播放
python realtime_ddsp.py --live --midi-port "你的 MIDI 设备名称" --max-voices 1

# 直接从声卡实时播放 MIDI 文件，不生成 WAV
python realtime_ddsp.py --play-midi test_violin.mid --prebuffer 6 --max-voices 1

# 换成长笛音色实时播放；其他音色只需替换 --model 路径
python realtime_ddsp.py --play-midi test_violin.mid \
  --model models/ddsp_vst/Flute.onnx --prebuffer 6 --max-voices 1

# Ascend 板端使用 OM；--backend auto 会根据 .om 自动选择 PyACL
python realtime_ddsp.py --play-midi test_violin.mid \
  --model models/om/Violin_mixed_float16.om \
  --device-id 0 --prebuffer 6 --max-voices 1

# 查看可用声卡，必要时用 --audio-device 指定设备编号
python realtime_ddsp.py --list-audio
```

在 `ascend8t2` 上通过漫步者 M25 实时播放仓库内 MIDI 的完整命令为：

```bash
cd ~/Documents/case3
amixer -c Device set PCM 70% unmute

# 先从上一条 --list-audio 输出中确认 M25 的 PortAudio 编号 N
python realtime_ddsp.py \
  --play-midi midi/ode-to-joy-violin.mid \
  --model models/om/Violin_mixed_float16.om \
  --device-id 0 \
  --audio-device N \
  --sample-rate 48000 \
  --prebuffer 6 \
  --max-voices 1 \
  --audio-latency-ms 80 \
  --output-gain-db 0
```

当 `--audio-device` 选择 `USB Composite Device: Audio (hw:1,0)` 时，程序直接
访问 ALSA 硬件，PulseAudio sink 音量不参与；应使用 `amixer -c Device set PCM`
调整 M25 硬件音量。设备编号会在重启或插拔后变化，不能永久固定为 `1`。

`--output-gain-db` 在重采样后施加软件增益，并在写入声卡前限幅到 `[-1, 1]`，当前
插件语义范围为 `-60..0 dB`。历史测试曾在旧版本额外使用 `+24 dB` 软件增益；该值
不再是当前默认或允许范围，音量应优先在模型参数、系统 mixer 和功放侧正确设置。

2026-07-21 的短 MIDI 板端实测结果为：

```text
device=USB Composite Device: Audio (hw:1,0)
channels=2
sample_rate=48000
gain=+24.0 dB
rendered=92, played=86
underruns=0, overruns=0
max_render_ms=5.77
```

程序现在优先选择双声道；DDSP 的单声道波形会复制到左右声道。ONNX Runtime
会话显式使用 1 个 intra-op 和 1 个 inter-op 线程，避免板端
`pthread_setaffinity_np failed` 警告，并减少多声部推理的线程开销。
PyACL 后端按模型描述中的名称映射 3 个输入和 4 个输出，I/O 设备内存只分配一次；
实时渲染线程每次执行前调用 `acl.rt.set_context()`，退出时依次释放 dataset、buffer、
模型、context 和 device。`--backend` 可显式指定 `onnx` 或 `om`，默认 `auto`。

播放结束后，终端统计中的 `underruns=0` 和 `overruns=0` 表示本次音频 FIFO 没有
下溢或上溢。它们只能说明实时传输连续，最终音质仍需实际听音确认。

引擎允许在高级模式使用多声部：启动时按照 `--max-voices` 创建固定声部槽位，每个槽位
长期保留自己的 GRU 状态、谐波相位和噪声合成器；MIDI 音符只在槽位之间分配，
不再为每个 note-on 重建模型状态。超过上限时优先回收已经释放的声部。复调混音
仍采用 `1/N` 归一化，但增益会跨帧平滑，避免声部数变化造成瞬时音量跳变。
`test_violin.mid` 可以用于单声部连续性验证，后续也可以替换为和弦 MIDI 文件。
相同音高在 release 阶段再次 note-on 时会复用原声部及当前 ADSR 电平，避免包络
从零重启形成音量凹陷。不要为了降低 NPU 负载把带 release 尾音的旋律固定为
`--max-voices 1` 是 Google Synth 默认语义；若专门播放带重叠 release 的 MIDI 文件，
可在高级设置增加声部，但这仍不是原生复音乐器模型。

### OM 实时实测和连贯性判定

2026-07-21 在 `ascend8t2` 上使用 `Violin_mixed_float16.om` 和漫步者 M25 播放
`midi/ode-to-joy-violin.mid`：默认包络、`max-voices=8` 时最大渲染帧为
9.53 ms；`attack=0.03`、`release=0.35`、`max-voices=4` 时为 7.08 ms。两次均为
`underruns=0`、`overruns=0`，低于 20 ms 控制周期。

同一 MIDI 分别离线渲染 ONNX 和 OM 后，最终波形 NRMSE 为 0.4939%，余弦相似度
为 0.9999878；两者都有 11 个近静音块，20 ms 块边界跳变的 p99 分别为
0.002939 和 0.002930。该结果说明当前“不够连贯”的听感不是 PyACL、NPU 调度或
混合精度造成的，而是 ONNX/OM 共用的 50 Hz 控制模型、MIDI 断音、包络和 CPU
合成链路的表现。`underruns=0` 只能排除传输断流，不能证明音色自然。

对这份 MIDI 可先用以下参数降低长 release 尾音叠加和同音重触发的音量变化：

```bash
python realtime_ddsp.py \
  --play-midi midi/ode-to-joy-violin.mid \
  --model models/om/Violin_mixed_float16.om \
  --device-id 0 --audio-device 1 --sample-rate 48000 \
  --prebuffer 6 --max-voices 4 --audio-latency-ms 80 \
  --output-gain-db 0 --attack 0.03 --release 0.35
```

这是试听参数，不代表模型精度指标；设备编号仍需以当次 `--list-audio` 为准。

DDSP-VST 控制模型仍然是 20 ms 一帧的单音小提琴模型。快速 MAESTRO 钢琴曲可能
在一个控制帧内触发多个音符，即使 `underruns=0` 也会呈现量化后的断奏感；这是
模型控制率和目标音色的限制，不是声卡缓冲断流。钢琴实时合成最终应使用控制率
更高、原生支持踏板和多声部状态的 `ddsp-piano` 模型。

可以用实时捕获工具记录 callback 实际送给声卡的信号，并在播放结束后分析：

```bash
python tools/analyze_realtime_playback.py \
  --midi test_violin.mid --audio-device 8 --sample-rate 48000 \
  --prebuffer 8 --max-voices 1
```

工具在实时线程中只复制到预分配内存，结束后才生成
`reports/realtime_capture.wav` 和 `reports/realtime_capture.json`。报告会区分
PortAudio underflow、callback 时间抖动、块边界跳变、活动音符期间的近静音，
并从同名 TFLite 元数据读取模型训练音域，判断掉音是否由超音域输入引起。

`--midi-file` 是离线渲染到 WAV，`--play-midi` 是实时送入声卡，两者不能同时
使用。实时播放采用和 MIDI 键盘相同的 `RealtimeSynthEngine`、20ms 帧和 FIFO；
`--prebuffer 6` 会预留约 120ms 音频，只适合排查不稳定设备。Web 工作台默认采用
`balanced` 档，即 2 个控制帧与 20ms 设备缓冲；`low` 使用 1 帧/15ms，`safe`
使用 3 帧/60ms。后台渲染线程在 FIFO 满时等待，声卡每消费一块就立即唤醒
线程补充下一块，不会因为预缓冲帧累计 deadline 而周期性停顿。默认使用系统
声卡，也可以指定。播放器会在启动前检查设备支持的输出通道数；例如某些
WASAPI 设备只接受原生 4 通道布局，此时程序会自动用 4 通道打开声卡，并把
单声道 DDSP 输出复制到全部通道：

```bash
python realtime_ddsp.py --play-midi test_violin.mid --audio-device 3 --sample-rate 44100
```

16 kHz 模型输出采用与 JUCE `WindowedSincInterpolator` 同类的 100-crossing Hann
窗化 sinc 重采样。该滤波器有 6.25ms 算法延时，但能避免线性插值造成的高频衰减
和镜像失真。

## 参考 `ddsp-realtime` 的实时结构

实时线程模型参考了 [`woosukji/ddsp-realtime`](https://github.com/woosukji/ddsp-realtime)：

- MIDI 回调线程只更新锁保护的控制状态，渲染线程按 20 ms 模型帧读取快照。
- 音频输出使用有界 FIFO 和预缓冲，音频回调取不到数据时填充静音，避免在回调中
  执行 ONNX 推理或动态分配大块内存。
- 渲染线程由 FIFO 空位驱动：缓冲区满时休眠，音频回调消费后立即唤醒并补充一块；
  同时记录 `underruns`、`overruns` 和最大推理耗时，这些指标可直接用于后续
  310B 设备端性能验收。
- 上游 C++ 实现使用 TFLite；本目录保留同样的流水线边界，并实现 ONNX Runtime
  和 PyACL/OM 两个控制模型后端。两者共用每个声部的状态隔离和音频线程接口。
- 独立训练仓库导出的模型必须先确认 hop、谐波数、状态张量和当前 ONNX 接口一致，
  不能把 TorchScript 文件直接当作现有 ONNX 模型加载。
- `hyakuchiki/realtimeDDSP` 的 `stream.py` 可作为未来流式合成状态的参考，但其
  Neutone 导出格式不进入当前 ONNX 验证链路。

ONNX 和 OM 后端已经完成相同 MIDI 的板端 A/B 验证。当前后续优化重点是合成音质、
包络和 MIDI 表情，而不是再次替换 `PolyphonicMidiState` 或声卡 FIFO。
