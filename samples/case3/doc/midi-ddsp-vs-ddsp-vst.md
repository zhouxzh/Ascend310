# 历史参考：MIDI-DDSP 与 DDSP-VST 的程序和模型差异

> 本文档中的路径默认从 `samples/case3` 目录理解。[返回文档索引](README.md)。

> **历史对比资料。** 本文保留上游 DDSP-VST Synth、ONNX 对照与 MIDI-DDSP 迁移背景，
> 不描述当前用户入口。现在实时演奏只加载 Piano-DDSP 钢琴；DDSP-VST 是 OM-only 的
> 麦克风 Effect。当前操作请以[WebUI 操作、部署与 API](webui.md)为准。

本项目现在同时保留了两条 DDSP 移植路线：

- **DDSP-VST 路线**：以 [`realtime_ddsp.py`](../realtime_ddsp.py) 为运行入口，
  使用 `models/ddsp_vst/*.onnx` 或 `models/om/<Instrument>_*.om`，目标是把实时
  MIDI 键盘或 MIDI 文件事件低延迟转成音频。
- **MIDI-DDSP 路线**：以 [`midi_ddsp_realtime.py`](../midi_ddsp_realtime.py)
  为运行入口，使用 `midi_ddsp_expression_*.om` 和 `midi_ddsp_synthesis_*.om`
  两个模型，目标是让已知 MIDI 文件经过上游 MIDI-DDSP 的表情建模后合成为音频。

它们都叫 DDSP，但解决的问题不同。DDSP-VST 更像一个实时音色控制器；MIDI-DDSP
更像一个从乐谱/MIDI 推断演奏表情的层级生成系统。两条路线当前都没有把完整 DDSP
音频合成器放进 OM：神经网络只输出控制量，谐波振荡器、滤波噪声、重采样和声卡
输出仍在 Python/CPU 中执行。

## 一句话结论

| 维度 | DDSP-VST | MIDI-DDSP |
| :--- | :--- | :--- |
| 本项目入口 | `realtime_ddsp.py` | `midi_ddsp_realtime.py` |
| 上游定位 | JUCE VST3/AU 插件和独立桌面应用中的实时 DDSP 音色 | 基于 MIDI 的层级音乐演奏建模和合成系统 |
| 输入对象 | 实时 MIDI 事件、MIDI 文件事件或简单 demo 控制 | 已知 MIDI 文件 |
| 模型数量 | 每个音色一个单步控制模型 | 一个版本化模型包内含 Expression/Synthesis 的 8 个 stateful 组件 |
| 当前音色 | 11 个 DDSP-VST TFLite 音色 | URMP 预训练权重，常用 13 个乐器 ID，默认实测 Violin |
| 控制帧率 | 50 Hz，20 ms 一帧 | 250 Hz，4 ms 一帧 |
| 主要状态 | 显式 `state[512] -> state_out[512]`，程序逐帧保存 | v2 显式传递上下文 GRU、自回归 controls/F0 状态，并保留双向整段语义 |
| 实时语义 | 可以连接实体 MIDI 键盘，按 20 ms 帧持续推理 | 先读取 MIDI 并准备表情，再按块播放，不是严格键盘零延迟 |
| 复音处理 | 程序为每个声部槽位维护独立 GRU 状态和合成器状态 | 单轨和弦拒绝；可映射的多轨单声部文件逐轨渲染 stem 后混音 |
| 后端 | ONNX Runtime CPU 或 PyACL/OM | 当前运行程序使用 PyACL/OM；ONNX 主要用于导出验证和参考 |
| 适合场景 | 实时演奏工作区的神经音色、低延迟试听、音色 A/B | MIDI 文件渲染、表情控制研究、MIDI-DDSP OM 精度和速度实验 |

## 上游项目定位不同

DDSP-VST 上游是 `magenta/ddsp-vst`，本项目以固定提交
[`f2996e9`](https://github.com/magenta/ddsp-vst/tree/f2996e97f9469f3956a6b8e9d2d9b50b6555e1e9)
为行为基准。它的主产品是基于 JUCE 的 DDSP Effect 和 DDSP Synth 插件，内置一组
TFLite `predict_controls` 模型。插件侧实时获取音高和响度，调用控制模型，再由插件内
合成器生成音频。本项目不移植 JUCE/VST3/AU 宿主外壳，而是把完整音频行为映射到 WebUI：
Synth 对应“触控演奏”和“MIDI 键盘”，Effect 对应独立的“DDSP-VST”麦克风页；神经网络
在开发板仅加载 OM，谐波、噪声、重采样和 FreeVerb 保持在 CPU DSP 层。

官方 Effect 不包含噪声门。case3 针对 UGREEN 摄像头麦克风的实际底噪增加启动校准、
滞回、保持及平滑开关，避免安静环境中的底噪直接产生音色；它是输入安全层，不改变官方
Feature/Control、合成与混响顺序。官方的自定义 TFLite 文件夹加载没有直接暴露给浏览器，
等价工作流是本地转换和验证、开发板 ATC、发布固定哈希 OM，再由服务端模型目录发现。
WebUI 保留官方训练音高/响度范围、输入轨迹、拖动 Input Pitch/Input Gain 校准和模型目录
刷新；刷新操作只发现服务端资产，不改变 OM-only 与服务端路径边界。

MIDI-DDSP 上游是 `magenta/midi-ddsp`。它的目标是从 MIDI 生成更像真实演奏的
音频，不只是把 note-on/note-off 变成固定包络。上游把任务拆成两层：Expression
Generator 先为音符序列预测 6 维表情控制，Synthesis Generator 再把这些表情和
逐帧 MIDI 特征转成 DDSP 控制参数。上游原始命令行可以离线合成 MIDI，并包含
ReverbModule；本项目移植 Expression 和 Synthesis 参数网络，并在 CPU 侧实现对应的
Harmonic、FilteredNoise 和逐乐器 Reverb 语义。

## 本项目程序差异

### `realtime_ddsp.py`

`realtime_ddsp.py` 面向连续演奏。它负责：

- 枚举 MIDI 输入和音频输出设备；
- 解析 MIDI 文件，或从 `python-rtmidi` 接收实体键盘事件；
- 为每个活动音符生成 `f0_scaled` 和 `pw_scaled`；
- 为每个声部槽位保存独立的 GRU 状态、谐波相位、噪声合成器状态和 ADSR 包络；
- 每 20 ms 调用一次 DDSP-VST 控制模型；
- 把模型控制量交给 CPU 谐波振荡器和噪声合成器；
- 通过有界 FIFO、预缓冲和 PortAudio 声卡回调播放音频。

这条程序链路的核心是“事件驱动 + 显式状态”。MIDI 输入线程只更新被锁保护的
演奏状态，渲染线程读取快照并补充音频 FIFO，声卡回调只取已经渲染好的块。模型
推理不在声卡回调里执行。

独立开发命令 `realtime_ddsp.py` 可用两种后端做本地/板端对照：

```text
.onnx -> ONNX Runtime CPU
.om   -> PyACL/Ascend OM
```

程序通过文件扩展名和 `--backend auto` 选择后端。ONNX 和 OM 后端使用同一套
输入输出契约，所以同一个 MIDI 可以做 CPU/NPU A/B 对照。WebUI 生产会话不使用该回退：
它只接受模型目录中的 `acl/om` 资产，模型或 NPU 不可用时直接拒绝启动。

### `midi_ddsp_realtime.py`

`midi_ddsp_realtime.py` 面向已知 MIDI 文件。它负责：

- 读取 MIDI 文件并提取可合成的单声部旋律线；
- 将音符和休止符量化为 250 Hz note/rest token；
- 用 Expression OM 按 32 个 token 一段预测 6 维表情控制；
- 将 note-level 表情展开成逐帧 `volume`、`vibrato`、`brightness` 等控制序列；
- 用 Synthesis OM 按 64 帧窗口、32 帧步长生成 DDSP 参数；
- 对 Synthesis 输出做 `exp_sigmoid` 后处理；
- 用 CPU 谐波振荡器、滤波噪声、重采样和 PortAudio 播放。

这条链路的核心是“先看完整 MIDI，再分块播放”。Expression 模型本身包含双向 GRU，
需要一段已知的音符上下文；Synthesis 模型也是固定 64 帧静态窗口。当前程序虽然
可以实时把块送进声卡，但它的实时性不是实体键盘那种未知未来输入的低延迟实时。

MIDI-DDSP 当前运行程序只走 PyACL/OM。版本化 ONNX 组件、参考 NPZ 和 OM bundle 从
已发布模型 release 获取，用于 ATC 输入和 OM 精度对比；不是
`midi_ddsp_realtime.py` 的 CPU 运行后端。

## 模型契约差异

### DDSP-VST 单步控制模型

DDSP-VST 每个音色是一个相同结构、不同权重的单步控制模型：

```text
inputs:
  state[512]       float32  上一帧 GRU 隐状态
  f0_scaled[1]     float32  MIDI 0..127 归一化后的音高
  pw_scaled[1]     float32  力度/响度归一化值

outputs:
  amplitude[1]     float32  已后处理的谐波总幅度
  harmonics[60]    float32  已非负归一化的谐波分布
  noise_amps[65]   float32  已后处理的噪声频带幅度
  state_out[512]   float32  下一帧 GRU 隐状态
```

模型元数据约定：

- 采样率：16 kHz；
- 控制帧率：50 Hz；
- hop size：320 samples，即 20 ms；
- 谐波数：60；
- 噪声频带：65；
- 每个音色一个模型文件。

已发布的 DDSP-VST ONNX 不直接把 TFLite 的 `WHILE/TensorList` 图交给 ONNX 转换器，
而是采用显式单步 ONNX 图。图把 GRU 展开成
普通矩阵运算和门控算子，并把 `exp_sigmoid`、谐波 Nyquist mask 和谐波归一化也
放入图内。因此运行时收到的 `amplitude`、`harmonics`、`noise_amps` 已经是可直接
送入 CPU 合成器的控制量。

当前已经转换的 11 个音色为：

```text
Bassoon, Clarinet, Flute, Melodica, Saxophone, Sitar,
Trombone, Trumpet, Tuba, Violin, Vowels
```

它们适合做实时“类独奏乐器”音色，但没有钢琴模型，也没有 MIDI-DDSP 的 note-level
表情模型。钢琴或复杂复音最终应使用专门支持多声部、踏板和琴弦特征的模型，而不是
简单把 DDSP-VST 小提琴模型复制多份。

### MIDI-DDSP 两阶段模型

以下是旧版两个静态 OM 的简化契约，仅用于说明网络输入；stateful v2 已进一步拆成
8 个固定块组件，并通过一个 manifest 统一选择，不能单独混搭。

Expression 模型：

```text
inputs:
  note_pitch[1, 32]       int64    32 个音符/休止符 token 的 MIDI pitch
  note_length[1, 32, 1]   float32  每个 token 的持续时间，单位为秒
  instrument_id[1]        int64    上游乐器 ID

outputs:
  expression_controls[1, 32, 6] float32
```

6 维表情控制名称固定为：

```text
volume, vol_fluc, vibrato, brightness, attack, vol_peak_pos
```

Synthesis 参数模型：

```text
inputs:
  volume[1, 64, 1]        float32
  vol_fluc[1, 64, 1]      float32
  vibrato[1, 64, 1]       float32
  brightness[1, 64, 1]    float32
  attack[1, 64, 1]        float32
  vol_peak_pos[1, 64, 1]  float32
  q_pitch[1, 64, 1]       float32
  onsets[1, 64]           int64
  offsets[1, 64]          int64
  instrument_id[1]        int64

outputs:
  f0_hz[1, 64, 1]                 float32
  amplitudes[1, 64, 1]            float32
  harmonic_distribution[1, 64, 60] float32
  noise_magnitudes[1, 64, 65]      float32
```

模型元数据约定：

- 采样率：16 kHz；
- 控制帧率：250 Hz；
- 每帧 64 samples，即 4 ms；
- Synthesis 输入窗口：64 帧，即 256 ms；
- 当前程序输出步长：32 帧，即每次发出 128 ms 音频；
- 谐波数：60；
- 噪声频带：65；
- legacy Expression 和 Synthesis 是两个独立模型文件；v2 使用不可拆分的模型包。

与 DDSP-VST 不同，MIDI-DDSP 的 Synthesis ONNX/OM 输出仍是较接近上游 TensorFlow
参数网络的原始控制量。本项目运行时在 `midi_ddsp_realtime.py` 里执行后处理：

```text
amplitudes              -> exp_sigmoid(raw)
harmonic_distribution   -> exp_sigmoid(raw)
noise_magnitudes        -> exp_sigmoid(raw - 5)
```

随后再交给 CPU 合成器。逐乐器 IR 由 checkpoint 导出，前 16,000 点保持原值，后
32,000 点应用 `exp(-4t)`，清除 IR 首样本；运行时采用 2,048 点分区 FFT 卷积并叠加
干声。

## 状态和上下文的根本区别

DDSP-VST 的状态是显式接口。每一帧调用模型时，程序传入上一帧的 `state`，模型返回
`state_out`。在复音时，程序只需要为每个声部槽位各保存一份状态：

```text
voice 0: state_0, harmonic_phase_0, noise_state_0, ADSR_0
voice 1: state_1, harmonic_phase_1, noise_state_1, ADSR_1
...
```

这让它天然适合实时键盘输入。只要渲染线程能在 20 ms 周期内完成当前活动声部的
推理和合成，就可以持续播放。多声部的音乐性仍受模型本身限制，但工程接口是清晰的。

MIDI-DDSP 仍需要提前知道完整 MIDI，因为 Expression 和 Synthesis 都包含双向上下文。
stateful v2 先按正序和逆序分块运行上下文 GRU，再连续运行自回归 decoder；F0 decoder
显式传递上一帧 one-hot 和两层 GRU state。Timbre 网络不能仅靠卷积 halo 分块，因为官方 LayerNorm 同时统计整段时间轴；stateful v2 使用最多 65,536 帧的掩码整段模型保持全曲统计一致。
因此块之间不再使用下面这种旧版经验重叠：

```text
window 0: frames 0..63      emit frames 0..31
window 1: frames 0..63      emit frames 32..63
window 2: frames 32..95     emit frames 64..95
...
```

上述窗口仅属于 legacy 兼容路径。v2 虽然跨块状态连续，但双向上下文决定了它仍需先
读取完整 MIDI，不能直接把正在弹的实体键盘事件无未来上下文地送入同一套模型结构。

## MIDI 和复音处理差异

DDSP-VST 程序直接消费 MIDI 事件：

- `note_on` 创建或复用声部槽位；
- `note_off` 进入 release；
- sustain、pitch bend、velocity 等会影响状态快照和包络；
- `--max-voices` 控制同时运行的声部数量。

它可以播放和弦 MIDI，但每个音符仍是“一个独立 DDSP-VST 声部”。这不是原生钢琴
声学模型，也没有踏板共振、弦间耦合或钢琴非谐性。

MIDI-DDSP 程序不再从复音文件静默提取最高声部。单轨和弦返回
`polyphonic_track`；多轨文件只有在每轨均为单声部、且 program 可映射到 13 种 URMP
乐器时，才逐轨保存 stem 并混音。因此它仍不适合完整钢琴复音演奏。

## 导出和验证路线差异

DDSP-VST：

```text
Hugging Face published DDSP-VST ONNX release
  -> models/ddsp_vst/<Instrument>.onnx
  -> ATC on Ascend board
  -> models/om/<Instrument>_force_fp16.om
  -> models/om/<Instrument>_mixed_float16.om
```

关键验证点：

- ONNX checker；
- 输入输出形状和名称；
- 连续 8 帧 ONNX Runtime 推理；
- 已发布 ONNX/OM 的哈希、输入输出契约和波形 A/B；
- OM/ONNX 输出波形 A/B；
- 实时声卡播放的 underrun/overrun。

MIDI-DDSP：

```text
Hugging Face published MIDI-DDSP ONNX release
  -> models/midi_ddsp/onnx/midi_ddsp_expression_notes32.onnx
  -> models/midi_ddsp/onnx/midi_ddsp_synthesis_params_frames64.onnx
  -> ATC on Ascend board
  -> models/om/midi_ddsp_expression_notes32_*.om
  -> models/om/midi_ddsp_synthesis_params_frames64_*.om
```

关键验证点：

- 已发布参考 NPZ 与 ONNX/OM 的输出；
- ONNX 输入输出形状；
- ATC 兼容性修正，包括 BatchNormalization 分解和 OneHot 类型对齐；
- 每个 OM 的固定输入 NPZ 精度；
- OM 多次运行重复性；
- Expression 与 Synthesis 在同一 Python 进程中共享 PyACL 生命周期；
- 实时块播放的 underrun/overrun。

本地开发电脑负责编辑、下载已发布 ONNX/OM 和前端构建；ATC、OM 加载、PyACL 推理、
`npu-smi` 和声卡实测必须在真实 Ascend 310B 板端执行。

## 音频合成边界

两条路线当前都把 DDSP 音频合成放在模型外，但边界不同：

| 模块 | DDSP-VST | MIDI-DDSP |
| :--- | :--- | :--- |
| 控制网络 | ONNX/OM 内 | Expression OM + Synthesis OM 内 |
| `exp_sigmoid` | 已在 DDSP-VST ONNX/OM 内完成 | 在 `midi_ddsp_realtime.py` 中完成 |
| 谐波归一化 | 已在 DDSP-VST ONNX/OM 内完成 | 由运行时后处理和合成器处理 |
| 谐波振荡器 | CPU | CPU |
| 滤波噪声 | CPU | CPU |
| 混响 | JUCE/FreeVerb 风格，Size/Damping/Wet 可调 | Google 逐乐器 IR，分区 FFT 干湿叠加 |
| 16 kHz -> 声卡采样率 | CPU 100-crossing Hann 窗化 sinc 重采样 | CPU 线性重采样 |
| 声卡输出 | PortAudio 或指定 PulseAudio sink | PortAudio 或指定 PulseAudio sink |

因此，OM 延迟只代表神经网络控制参数生成成本，不代表完整音频链路成本。完整听感还
取决于包络、MIDI 事件密度、CPU 合成器、重采样、声卡缓冲和软件增益。

## 适用场景

优先使用 DDSP-VST 路线的情况：

- 需要实体 MIDI 键盘实时演奏；
- 需要快速在 11 个已转换音色之间切换；
- 需要 ONNX CPU 和 OM/NPU 做同一接口的 A/B；
- 需要测试 FIFO、声卡、实时延迟和多声部调度；
- 输入是 note-on/note-off 事件流，而不是完整乐谱。

优先使用 MIDI-DDSP 路线的情况：

- 输入是已经存在的 MIDI 文件；
- 希望模型根据音符长度和上下文预测 volume、vibrato、brightness 等表情；
- 需要验证官方 MIDI-DDSP 权重到 ONNX/OM 的迁移；
- 需要评估 Expression/Synthesis 两阶段 OM 的精度和速度；
- 可以接受当前只合成单声部旋律线。

不应该混用的情况：

- 不能把 `midi_ddsp_synthesis_params_frames64.om` 当成 `realtime_ddsp.py` 的
  `--model` 传入；它的输入输出完全不同。
- 不能把 DDSP-VST 的音色 OM 当成 MIDI-DDSP 的 Synthesis OM；它没有
  Expression 控制、onset/offset 或 250 Hz 序列窗口。
- 不能把 DDSP-VST 的多声部堆叠理解成原生钢琴模型；它只是多个独立单声部音色的
  混音。
- 不能把 MIDI-DDSP 当前程序理解成实体键盘低延迟后端；它需要已知 MIDI 上下文。

## 后续迁移建议

1. 保持两套运行入口分离。DDSP-VST 的 `realtime_ddsp.py` 继续承担低延迟实时演奏；
   MIDI-DDSP 的 `midi_ddsp_realtime.py` 继续承担已知 MIDI 文件合成和验证。
2. 如果要让 MIDI-DDSP 支持真正实时键盘，需要重新设计模型接口，把 Expression 的
   双向上下文改成因果/流式状态，或者在程序里接受更高延迟的 lookahead。
3. 如果要做钢琴实时合成，不应只扩展 DDSP-VST 小提琴声部数；应优先迁移支持
   多声部、踏板、非谐性和混响状态的钢琴专用模型。
4. 如果要把更多 DSP 放进 OM，需要先固定随机噪声输入、相位状态、滤波器尾部和
   混响缓存的输入输出契约，否则很难在 ONNX/ACL 中做可复现的多声部流式推理。
5. 每次新增模型都应同时保存 ONNX/OM SHA256、ATC 原始日志、参考 NPZ 和板端
   精度/速度报告，避免后续无法判断差异来自模型、转换参数还是运行环境。

## 相关文档

- [模型与 OM 部署](om-deployment.md)
- [MIDI-DDSP 历史导出](midi-ddsp-export.md)
- [实时 DDSP](realtime-ddsp.md)
- [MIDI-DDSP OM 实时 MIDI 合成测试](midi-ddsp-realtime.md)
- [OM 转换与验证](om-deployment.md)
- [Upstream 参考仓库清单](upstream-repositories.md)
