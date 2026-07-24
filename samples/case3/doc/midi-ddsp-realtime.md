# MIDI-DDSP 文件渲染与播放

MIDI-DDSP 用于已知 MIDI 文件，不承担触控键盘或实体 MIDI 的低延迟即时合成。实时
演奏由 DDSP-VST 页面负责。

## 当前架构

```text
MIDI file
  -> pretty_midi/mido compatible 250 Hz note/rest sequence
  -> Expression forward/backward context + autoregressive decoder
  -> six note controls expanded to 250 Hz frames
  -> Synthesis precondition + forward/backward context
  -> stateful top-p F0 decoder + timbre network with exact halo
  -> Harmonic + deterministic FilteredNoise
  -> per-instrument IR partitioned FFT reverb + dry
  -> complete WAV cache
  -> optional resample and playback
```

stateful v2 把 Expression、Synthesis context 和 F0 decoder 状态显式传入/传出。Timbre
网络每次输出 64 个有效帧，左右各使用 124 帧 halo；最后一块真实帧之外强制清零，
不会把 Dense bias 或乐器 embedding 产生的伪帧当作未来上下文。

F0 使用官方 `top-p=0.95` 采样，外部 Gumbel 张量由默认种子 `20260724` 生成。休止帧
F0 为 0 Hz。产品默认先生成完整 WAV 并按 MIDI、模型包、乐器、种子、尾音、采样率、
增益和混响 SHA256 缓存，然后再播放，优先保证音质和可复现性。

## MIDI 输入规则

- 单轨单声部：允许用户选择任意受支持的 URMP 乐器 ID 0-12；
- 多轨且每轨单声部：仅 stateful v2 支持，按每轨 General MIDI program 映射乐器，
  保存 `stem-track-XX.wav` 后混音；
- 单轨和弦或任意轨道内部复音：返回 `422 polyphonic_track`；
- 无法映射到 13 种乐器的多轨文件：返回 `422 unsupported_program`；
- 损坏或无音符文件：上传时删除临时文件并返回 `422`。

程序不再自动提取 Melody/Lead 或最高活动声部。钢琴曲目可用于复音兼容性检查，但
不能作为本轮原版单声部音质验收输入。

## CPU DSP 与混响

运行时复现官方 `exp_sigmoid`、线性/窗口控制曲线上采样、角度累积、Nyquist 谐波
屏蔽、谐波归一化，以及 FilteredNoise 的 Hann IR、FFT 卷积、延迟补偿和裁剪。

混响资产 `models/om/midi_ddsp_reverb_ir.npz` 来自同一 checkpoint：

- 20 组、16 kHz、48,000 点 IR，产品使用 ID 0-12；
- 前 16,000 点保持原值；
- 后 32,000 点应用 `exp(-4t)`；
- IR 首样本清零，卷积后叠加干声；
- 运行时使用 2,048 点均匀分区 FFT。

官方 MIDI 转换已包含 1 秒结束静音，产品默认再增加 2 秒尾音。输出增益默认 `0 dB`，
不自动归一化。报告分别记录干声/湿声峰值与 RMS、限幅前峰值、削波样本数和边界跳变。

## 模型包

Web API 只接受 `model_bundle_id`。一个 stateful v2 manifest 同时锁定 8 个组件的文件、
输入输出、状态尺寸、源码提交、checkpoint/ONNX/OM SHA256 和 ATC 记录。不能从不同
导出批次分别选择 Expression 和 Synthesis。

旧模型会显示为 `legacy-static-v1` 和 `quality_status=context_resets`。它仍可用于迁移
排错，但每 32 个音符和 64 帧重置上下文，不作为最终音质结果，也不支持多轨 stem。

## 命令行

stateful v2 离线渲染：

```bash
python midi_ddsp_realtime.py \
  --midi midi/ode-to-joy-violin.mid \
  --model-bundle models/midi_ddsp/bundles/google-urmp-stateful-v2-mixed_float16/manifest.json \
  --instrument-id 0 \
  --seed 20260724 \
  --render-only \
  --tail-seconds 2 \
  --output reports/midi_ddsp/ode-to-joy-violin.wav \
  --report reports/midi_ddsp/ode-to-joy-violin.json
```

去掉 `--render-only` 即可在完整渲染后播放。可用 `--audio-device` 选择 PortAudio 输出，
或由 Web 服务传入已经连接的 PulseAudio sink。

## 报告字段

报告至少包含：

- MIDI、模型包、全部组件、混响资产 SHA256；
- 架构、源码提交、种子和状态连续性；
- 各组件推理次数、平均/中位/P95/最大耗时；
- expression、F0、amplitude、harmonics、noise 张量 SHA256；
- stem 轨道、乐器、派生种子、峰值、RMS、削波和边界连续性；
- 缓存键、命中状态、最终 WAV 峰值/RMS、underrun 和 overrun。

## 验收

本地先完成 [stateful v2 导出与对齐](midi-ddsp-export.md)。板端再使用
`midi/ode-to-joy-violin.mid` 验证：

- Mixed OM 完成整首渲染，速度低于音频时长；
- 无周期性块接缝、活动音符静音和默认削波；
- 完整渲染播放的 underrun/overrun 为 0；
- 停止或失败后资源锁、OM、NPU 和声卡可立即再次使用；
- 新模型通过 TensorFlow/ONNX/OM 数值比较和人工 A/B 后，才标记为推荐。

2026-07-22 的旧 32/64 OM 测试曾达到 Synthesis 约 55 ms/128 ms block 且无声卡
下溢，但该结果只证明旧静态 OM 可运行，不能证明完整歌曲与官方 TensorFlow 音质一致。
原始历史报告继续保存在 `reports/ascend8t2/midi_ddsp/`，不作为 stateful v2 验收结果。
