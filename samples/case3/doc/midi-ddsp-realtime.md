# MIDI-DDSP OM 实时 MIDI 合成测试

本文记录使用官方 MIDI-DDSP 模型、Ascend OM 和漫步者 M25 进行的实时 MIDI
文件合成测试。测试日期为 2026-07-22，板卡为 `ascend8t2`。

## 先说明测试边界

当前导出的 MIDI-DDSP 模型是静态序列模型：

- Expression OM 输入固定 32 个 note token，而且网络包含双向 GRU，推理时需要
  已知一段 MIDI 的前后文。
- Synthesis parameters OM 输入固定 64 帧，250 Hz，即 256 ms 的音乐控制序列。
- 两个 OM 都不直接输出 PCM；DDSP 谐波振荡器、滤波噪声和混响仍在 OM 外部。
- 当前程序支持已知的单声部 MIDI 文件实时播放，不是实体键盘的严格零延迟模式。
- 具有和弦或重叠音符的 MIDI 会被拒绝，因为当前 MIDI-DDSP 参数网络不是复音
  声部混合器。

因此本次“实时”定义为：先用 Expression OM 很快准备整首 MIDI 的 note-level
表情，然后在播放线程中按 32 个控制帧一块调用 Synthesis OM，CPU 实时完成 DDSP
DSP，最后送入声卡。表达模型的未来上下文在播放前准备，合成参数模型和音频块则
在播放过程中持续生成。

## 流水线

```text
MIDI file
  -> 250 Hz note/rest tokens
  -> Expression OM, 32 tokens per call
  -> six controls expanded to 250 Hz frames
  -> Synthesis OM, 64-frame window, emit 32 frames
  -> exp_sigmoid and DDSP noise bias post-processing
  -> CPU harmonic oscillator + filtered noise
  -> 16 kHz to 48 kHz streaming resampler
  -> sounddevice/PortAudio -> M25
```

Synthesis 使用 64 帧窗口、32 帧步长。除第一个窗口外，每次给 OM 提供前一个
32 帧作为上下文，只送出后 32 帧，减少静态窗口之间的边界变化。OM 的原始参数
仍是网络控制量，程序按 DDSP 的默认规则进行后处理：幅度和谐波使用
`exp_sigmoid`，噪声使用 `exp_sigmoid(raw - 5)`。混响没有移植到本次实时程序。

程序文件：

- `midi_ddsp_realtime.py`：MIDI 解析、实时块调度、CPU DSP 和声卡输出
- `pyacl_midi_ddsp.py`：Expression/Synthesis 两类静态 OM 的直接 PyACL runner
- `tests/test_midi_ddsp_realtime.py`：token、帧边界、参数缩放和块长度测试

该程序独立于 [`realtime_ddsp.py`](../realtime_ddsp.py)，后者仍是 DDSP-VST
控制模型的 ONNX/PyACL 实时程序，本次没有修改它。

## 板端环境与命令

本次使用已有软件，没有在远程板安装、升级或删除依赖：

- Ascend 310B4
- NPU 驱动 / `npu-smi` 25.2.0
- CANN 8.0.0，Anaconda `base` Python 3.9.2
- `mido` 1.3.3
- `sounddevice` 0.5.5
- M25 ALSA：`card 1: Device [USB Composite Device]`
- PortAudio：设备 `1`，`USB Composite Device: Audio (hw:1,0)`

播放前确认声卡和硬件音量：

```bash
cd ~/Documents/case3
aplay -l
python realtime_ddsp.py --list-audio
amixer -c Device set PCM 70% unmute
```

使用混合精度 OM 播放单声部 Ode to Joy：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/case3
export OMP_NUM_THREADS=1

python midi_ddsp_realtime.py \
  --midi midi/ode-to-joy-violin.mid \
  --expression-om models/midi_ddsp/om/ascend8t2/midi_ddsp_expression_notes32_mixed_float16.om \
  --synthesis-om models/midi_ddsp/om/ascend8t2/midi_ddsp_synthesis_params_frames64_mixed_float16.om \
  --instrument-id 0 \
  --device-id 0 \
  --audio-device 1 \
  --sample-rate 48000 \
  --prebuffer 2 \
  --audio-latency-ms 80 \
  --output-gain-db 30 \
  --output reports/ascend8t2/midi_ddsp/realtime/ode_to_joy_live_final.wav \
  --report reports/ascend8t2/midi_ddsp/realtime/live_m25_final.json
```

`--output-gain-db 30` 是本次 M25 测试使用的试听增益，硬件 PCM 音量为 70%。
正式使用时应从较小值开始调节。程序把单声道合成结果复制到 M25 的左右声道；
保存的捕获 WAV 是单声道，便于分析。

只验证 OM、DSP 和实时块耗时，不打开声卡：

```bash
python midi_ddsp_realtime.py \
  --midi midi/ode-to-joy-violin.mid \
  --render-only \
  --output reports/ascend8t2/midi_ddsp/realtime/ode_to_joy_render.wav \
  --report reports/ascend8t2/midi_ddsp/realtime/render_only.json
```

切换到 FP16 只需把两个 OM 文件名中的
`mixed_float16` 换成 `force_fp16`。Expression 的 `--instrument-id 0` 对应
Violin；其他音色 ID 应按 upstream 的 instrument 映射使用，不能只替换文件名。

## 实测结果

测试 MIDI：`midi/ode-to-joy-violin.mid`

- 62 个 MIDI 音符
- 69 个 token，包含长度为 0 的起始 rest token 和尾部静音
- 8375 个 250 Hz 控制帧，模型时长 33.50 s
- Expression OM：3 次 32-token 推理，总耗时 34.25 ms
- Synthesis OM：262 个 32-frame 输出块
- 声卡：M25，48 kHz，双声道，PortAudio block 6144 samples
- 软件增益：+30 dB

正式 M25 报告：

| 指标 | 结果 |
| :--- | ---: |
| Synthesis 块渲染中位数 | 55.17 ms |
| Synthesis 块渲染 p95 | 55.87 ms |
| Synthesis 块渲染最大值 | 62.66 ms |
| 单块音频时长 | 128 ms |
| 最大余量 | 65.34 ms |
| 播放块数 | 262 / 262 |
| underruns | 0 |
| overruns | 0 |
| 音频峰值 | 0.2147, -13.36 dBFS |
| 音频 RMS | 0.0502, -25.99 dBFS |

Synthesis 的最大渲染时间低于 128 ms 音频块截止时间，约为实时速度的 2.32 倍。
本次播放没有声卡下溢或上溢。`npu-smi` 的 `Health: Alarm` 仍被记录，但设备可见，
两个 OM 均正常执行；该告警没有阻止本次测试。

## PyACL 生命周期问题

第一次实现中，Expression runner 完成后立即调用 `acl.rt.reset_device()` 和
`acl.finalize()`，随后在同一 Python 进程加载 Synthesis OM，板端返回：

```text
acl.mdl.load_from_file failed, ret=145001 (0x23669)
```

这不是混合精度 OM 转换失败。修正后的
`pyacl_midi_ddsp.py` 让两个 runner 共享一次 `acl.init()`、`set_device()`、
device reset 和 finalize；各自只释放自己的 model、dataset、buffer 和 context，
待两者都关闭后才释放 ACL 运行时。修正后离线和实时测试均通过。

## 当前限制

1. Expression 模型是双向的，当前程序需要先读取 MIDI 文件并准备表情控制，不能
   直接把未知未来的实体键盘事件以同样模型结构低延迟送入网络。
2. Expression 输入固定 32 个 token，超过 32 个 token 会分段推理，分段位置可能
   造成表情连续性变化。
3. Synthesis OM 是静态 64 帧窗口，不包含可跨窗口传递的显式隐藏状态。本程序用
   32 帧重叠窗口降低边界问题，但这不是原始 TensorFlow 全曲一次推理的等价实现。
4. 当前程序拒绝重叠音符，因此不能直接播放 `midi/Ode-To-Joy.mid` 这类复音文件。
   复音需要多个独立声部模型/状态并行运行，再进行增益平滑混音。
5. 本次没有移植官方 ReverbModule，听感是干声合成；若需要接近官方 MIDI-DDSP
   输出，还需要导出/实现相同的混响参数和尾音处理。

## 原始结果

已回收到本地：

```text
reports/ascend8t2/midi_ddsp/realtime/live_m25_final.json
reports/ascend8t2/midi_ddsp/realtime/render_only_final.json
reports/ascend8t2/midi_ddsp/realtime/ode_to_joy_live_final.wav
reports/ascend8t2/midi_ddsp/realtime/ode_to_joy_render_final.wav
```

报告中包含 MIDI、OM SHA256、推理数量、块耗时、音频采样率、幅度、播放块数和
underrun/overrun 统计。OM 的 FP16/混合精度转换和静态精度对比见
[`midi-ddsp-benchmark.md`](midi-ddsp-benchmark.md)。
