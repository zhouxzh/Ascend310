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
  -> stateful top-p F0 decoder + full-song masked timbre network
  -> up to 3 CPU workers: Harmonic + deterministic FilteredNoise + IR reverb
  -> complete WAV cache
  -> optional resample and playback
```

stateful v2 把 Expression、Synthesis context 和 F0 decoder 状态显式传入/传出。Timbre
F0 网络每次输出 64 个有效帧并显式传递状态。Timbre 网络使用最多 65,536 帧的整段输入和 `valid_frames` 掩码，保证每层全曲归一化与官方实现一致；真实帧之外始终清零，
不会把 Dense bias 或乐器 embedding 产生的伪帧当作未来上下文。

F0 使用官方 `top-p=0.95` 采样，外部 Gumbel 张量由默认种子 `20260724` 生成。休止帧
F0 为 0 Hz。产品默认先生成完整 WAV 并按 MIDI、模型包、乐器、种子、尾音、采样率、
增益和混响 SHA256 缓存，然后再播放，优先保证音质和可复现性。

## MIDI 输入规则

- 单轨单声部：允许用户选择任意受支持的 URMP 乐器 ID 0-12；
- 多轨或轨道内部复音：仅 stateful v2 支持；每条复音轨按音符重叠关系拆成
  最少数量的单音 voice，再按静态 batch `1/2/4/8` 推理并混音；
- 播放模式只同步生成最终 `output.wav`；独立渲染模式额外保存每个 stem；
- 页面选择的渲染音色统一应用到全部 voice，并选择对应的逐乐器混响 IR；MIDI 文件中
  的 General MIDI program 仅作为分析元数据，不覆盖用户选择；
- legacy 模型仍只接受整体单声部 MIDI；
- 损坏或无音符文件：上传时删除临时文件并返回 `422`。

程序不再自动提取 Melody/Lead 或最高活动声部。自动声部化保留全部音符，并优先把
后续音符分配给音高最近的空闲 voice，以减少声部跳跃。该方式是在官方单声部模型外
组织多 voice，不会把当前 URMP 模型变成钢琴模型。

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

Web API 只接受 `model_bundle_id`。新版 stateful v2 manifest 同时锁定 batch `1/2/4/8`
的 32 个组件文件、
输入输出、状态尺寸、源码提交、checkpoint/ONNX/OM SHA256 和 ATC 记录。不能从不同
导出批次分别选择 Expression 和 Synthesis。

## 命令行

stateful v2 离线渲染：

```bash
python midi_ddsp_realtime.py \
  --midi midi/ode-to-joy-violin.mid \
  --model-bundle models/midi_ddsp/bundles/google-urmp-stateful-v2-batched-origin/manifest.json \
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
- `render_wall_seconds`、`playback_wall_seconds`、`total_wall_seconds` 和实时率；
- batch 大小、模型加载、NPU、DSP、重采样、混音和写盘耗时；
- expression、F0、amplitude、harmonics、noise 张量 SHA256；
- stem 轨道、乐器、派生种子、峰值、RMS、削波和边界连续性；
- 缓存键、命中状态、最终 WAV 峰值/RMS、underrun 和 overrun。

渲染进度通过每秒限频的 `WEBUI_EVENT` 和独立心跳发送。固定阶段为准备、加载模型、
表现生成、音高与上下文、音色参数、DSP/混响、混音、写入缓存和播放；事件同时包含
总进度、阶段进度、批次、工作量、已用时间和 ETA。报告与缓存会在播放开始前写入，
因此停止播放不会丢失已经完成的渲染。

## 验收

本地先完成 [stateful v2 导出与对齐](midi-ddsp-export.md)。板端再使用
`midi/ode-to-joy-violin.mid` 验证：

- 已验证 origin OM 完成整首渲染，速度低于音频时长；
- 无周期性块接缝、活动音符静音和默认削波；
- 完整渲染播放的 underrun/overrun 为 0；
- 停止或失败后资源锁、OM、NPU 和声卡可立即再次使用；
- 新模型通过 TensorFlow/ONNX/OM 数值比较和人工 A/B 后，才标记为推荐。

2026-07-22 的旧 32/64 OM 测试曾达到 Synthesis 约 55 ms/128 ms block 且无声卡
下溢，但该结果只证明旧静态 OM 可运行，不能证明完整歌曲与官方 TensorFlow 音质一致。
原始历史报告继续保存在 `reports/ascend8t2/midi_ddsp/`，不作为 stateful v2 验收结果。

2026-07-24 在 `ascend8t` 上的最终固定种子渲染为 35.0 秒，墙钟时间 25.70 秒；
Synthesis P95 为 45.40 ms，混响 P95 为 0.79 ms。干声峰值 `0.0062185`，湿声峰值
`0.0643837`，削波、underrun 和 overrun 均为 0。额外 2 秒仅作为混响零输入尾部，
不会送入 timbre 全曲归一化。
