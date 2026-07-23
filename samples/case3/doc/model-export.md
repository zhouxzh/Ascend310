# MIDI-DDSP 实时移植：第一阶段（TFLite → ONNX）

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

本目录现在额外包含一个面向实时音频的模型准备流程。它参考
[`magenta/midi-ddsp`](https://github.com/magenta/midi-ddsp) 的 DDSP 控制量，
并采用 [`magenta/ddsp-vst`](https://github.com/magenta/ddsp-vst) 的状态化
实时接口：每 20 ms 输入一次 `f0_scaled`、`pw_scaled` 和 512 维 GRU 状态，
输出幅度、60 个谐波系数、65 个噪声系数和下一时刻状态。

钢琴模型训练代码和数据现已迁移到独立仓库；`case3` 不再保存数据集、训练循环、
训练损失或通用 DDSP 训练仓库，只接收训练仓库导出的模型并完成 ONNX、OM、
实时 MIDI 与音频链路验证。

本节的本地导出阶段只验证 ONNX，不在开发电脑上安装或调用 ACL、ATC、OM 或
昇腾设备组件。导出的 ONNX 图将 TFLite 中的单步 GRU 显式展开，避免把
`WHILE/TensorList` 原样带入后续转换链路；ATC 和 OM 测试在开发板上执行。

## 安装本阶段依赖

```bash
python -m pip install -r requirements-onnx.txt
```

## 导出并验证小提琴模型

```bash
python tools/export_ddsp_vst_onnx.py \
  --tflite models/ddsp_vst/Violin.tflite \
  --output models/ddsp_vst/Violin.onnx
```

批量导出官方 DDSP-VST 实时音色，并逐个与原始 TFLite 做 8 帧数值对齐：

```bash
conda run -n mediapipe_legacy python tools/export_all_ddsp_vst_onnx.py
```

当前已经生成 11 个模型：

| ONNX 模型 | 音色 |
| :--- | :--- |
| `Bassoon.onnx` | 巴松管 |
| `Clarinet.onnx` | 单簧管 |
| `Flute.onnx` | 长笛 |
| `Melodica.onnx` | 口风琴 |
| `Saxophone.onnx` | 萨克斯 |
| `Sitar.onnx` | 西塔琴 |
| `Trombone.onnx` | 长号 |
| `Trumpet.onnx` | 小号 |
| `Tuba.onnx` | 大号 |
| `Violin.onnx` | 小提琴 |
| `Vowels.onnx` | 元音人声 |

所有模型位于 `models/ddsp_vst/`，输入输出契约完全相同。该目录中的
`models.json` 汇总了所有验证结果，每个同名 `.json` 保存单个模型的
ONNX Runtime 检查和 TFLite 数值对齐结果。官方包中的
`extract_features_micro.tflite` 是音高检测模型，不是 DDSP 音色控制模型，
因此不纳入这次转换。

验证内容包括：ONNX checker、输入输出名称和形状、连续状态推理、有限值检查、
以及 GRU 状态确实随输入变化。成功后会生成：

- `models/ddsp_vst/Violin.onnx`
- `models/ddsp_vst/Violin.json`

如需进一步与原始 TFLite 做逐输出数值对齐，安装 TensorFlow CPU 后运行：

```bash
python -m pip install tensorflow-cpu
python tools/export_ddsp_vst_onnx.py --compare-tflite
```

当前小提琴模型连续 8 步对照的最大绝对误差为：幅度 `1.03e-8`、谐波
`3.13e-7`、噪声 `3.21e-7`、GRU 状态 `4.65e-6`，均低于导出器设置的
`1e-5` 绝对误差阈值。完整结果记录在 `Violin.json` 的
`tflite_parity` 字段中。

接口契约如下：

| 名称 | 形状 | 含义 |
| :--- | :--- | :--- |
| `state` | `[512]` | 上一帧 GRU 状态 |
| `f0_scaled` | `[1]` | 0 到 1 的 MIDI 音高归一化值 |
| `pw_scaled` | `[1]` | 0 到 1 的力度/响度归一化值 |
| `amplitude` | `[1]` | DDSP 谐波总幅度 |
| `harmonics` | `[60]` | 谐波分布控制量 |
| `noise_amps` | `[65]` | 噪声滤波器控制量 |
| `state_out` | `[512]` | 下一帧 GRU 状态 |

## ONNX 算子结构

11 个 ONNX 的图结构完全相同，仅权重不同。每个模型使用 ONNX opset 11，包含
92 个标准 `ai.onnx` 节点和 50 个初始化权重，没有自定义 domain：

| 算子 | 数量 | 算子 | 数量 |
| :--- | ---: | :--- | ---: |
| `Add` | 20 | `Mul` | 15 |
| `Reshape` | 9 | `MatMul` | 6 |
| `ReduceMean` | 6 | `Div` | 5 |
| `Sigmoid` | 5 | `Sub` | 5 |
| `Pow` | 4 | `LeakyRelu` | 3 |
| `Split` | 3 | `Sqrt` | 3 |
| `Concat` | 2 | `Where` | 2 |
| `Equal` | 1 | `Less` | 1 |
| `ReduceSum` | 1 | `Tanh` | 1 |

图中没有 `FFT/DFT/RFFT/IRFFT/STFT`、`Conv`、原生 `GRU/LSTM`、`Sin/Cos` 或复数
张量算子。GRU 已展开为基础矩阵运算和门控算子。与原版 DDSP-VST 一致，模型只
预测 amplitude、harmonics 和 noise amplitudes；谐波振荡与噪声 FFT 在模型外的
合成器中执行，当前 Python 实现在 [realtime_ddsp.py](../realtime_ddsp.py) 中。

`_upstream/` 目录保存本次移植使用的第三方参考源码，不属于板端运行依赖，也不随
case3 同步到开发板，但不再作为可自动清理的临时目录。仓库来源、固定提交和本地
修改状态见 [Upstream 参考仓库清单](upstream-repositories.md)。
`models/ddsp_vst/` 中的模型是运行时需要保留的资产。

## 当前范围

- 11 个官方 DDSP-VST 实时音色均已转换，并通过本机 ONNX Runtime 和原始
  TFLite 连续 8 帧数值对齐；所有输出最大绝对误差均低于 `1e-5`。
- `tools/export_all_ddsp_vst_onnx.py` 可以一条命令重新生成完整模型目录。
- ATC、OM 精度和板端基准工具已经接入；`realtime_ddsp.py` 可根据 `.onnx`/`.om`
  扩展名自动选择 ONNX Runtime CPU 或 PyACL/OM 后端。PyACL 后端已在
  `ascend8t2` 使用 Violin 混合精度 OM 完成离线和实时声卡测试。
- 官方 DDSP-VST 模型没有钢琴模型；钢琴模型由独立训练仓库产出，完成 ONNX
  数值验证后再复制到本目录进行 Ascend 310B 适配。

## 新音色训练候选：`acids-ircam/ddsp_pytorch`

[`acids-ircam/ddsp_pytorch`](https://github.com/acids-ircam/ddsp_pytorch) 的
`export.py --realtime true` 会关闭混响，并导出调用
`realtime_forward()` 的 TorchScript 模型。该函数内部维护两类跨帧状态：

- `cache_gru`：GRU 隐状态，每一帧输入 pitch/loudness 后更新；
- `phase`：谐波振荡器的连续相位，防止帧边界产生点击声。

它的实时 C++ 外部采用双缓冲和后台计算线程：音频线程只复制输入、读取已经
计算好的输出，模型推理不在音频回调中执行。这与当前 `LivePlayer` 的有界 FIFO
和消费驱动回填设计是一致的。

但它不能直接替换当前 `Violin.onnx`，原因是模型契约不同：上游实现通常使用
100 个谐波、逐帧 pitch/loudness 序列和 TorchScript 状态，而当前 ONNX 图是
DDSP-VST 的 60 个谐波、单步 `[state, f0_scaled, pw_scaled]` 接口。以后用它
训练钢琴模型时，需要固定采样率、帧长和 hop，先导出 TorchScript，再单独验证
ONNX 图的连续状态、相位连续性和输出延迟，最后才进入 ATC/OM 适配。

如果把这条训练路径用于昇腾，建议在导出前把 `realtime_forward()` 改成显式的
函数式接口，例如 `forward(pitch, loudness, gru_state, phase)`，并返回
`audio, next_gru_state, next_phase`。当前上游实现把 `cache_gru` 和 `phase`
作为 module buffer，通过 `copy_()` 原地更新；这适合 TorchScript 进程内实时推理，
但不适合作为多声部 ONNX/ACL 图的清晰输入输出契约。另外，噪声部分使用运行时
`torch.rand`，导出到 ONNX 前也需要固定随机输入或把噪声发生器移到图外。

上游示例配置中的 `block_size: 160`（16 kHz 下为 10 ms）与 README 中“实时块长
应为 2 的幂”并不一致；迁移时应根据目标音频回调重新选择 hop，并重新验证 GRU
状态、相位连续性和 FIFO 缓冲延迟。

## 数据和训练参考：`sweetcocoa/ddsp-pytorch`

[`sweetcocoa/ddsp-pytorch`](https://github.com/sweetcocoa/ddsp-pytorch) 采用
16 kHz 音频、4 ms 帧间隔（64 个采样点）、512 维 GRU、101 个
谐波参数和 65 个噪声频带。训练数据由音频文件和 CREPE 生成的 F0 CSV 组成，
训练目标使用多尺度频谱损失。这些内容适合直接参考到钢琴/小提琴音色训练流程：

- 统一把训练音频转换为 16 kHz 单声道；
- 预先计算逐帧 F0 和置信度，低置信度帧置零；
- 随机裁剪等长的音频和 F0 片段；
- 使用多种 FFT 尺寸同时约束瞬态、音高和整体频谱。

但该仓库没有可直接使用的实时导出路径。它的振荡器在每次完整前向中使用
`cumsum` 重新计算相位，没有把尾相位作为下一帧状态；Encoder 还从输入音频中
提取 loudness，可选路径也需要原始音频。因此它更适合离线训练和重建，不适合
直接接收 MIDI 键盘事件。其噪声和响度模块还使用旧版 `torch.rfft/irfft`，测试
程序和部分组件默认硬编码 CUDA，使用当前 PyTorch 前需要先更新实现。

后续训练钢琴模型时可以复用它的数据集和多尺度频谱损失，但实时 Decoder 仍应
改成显式的 `f0/loudness/gru_state/phase` 输入输出。单个 DDSP 声部只有一个 F0，
因此训练数据应优先采用单音钢琴采样；和弦由当前 `PolyphonicMidiState` 为每个
音符分别运行一个声部后混合，不能把整段和弦音频直接标成单一 F0 训练。

## 最接近实时导出的参考：`hyakuchiki/realtimeDDSP`

[`hyakuchiki/realtimeDDSP`](https://github.com/hyakuchiki/realtimeDDSP) 是目前几份
参考代码中最接近“训练后转换为流式模型”的一个。它的 `diffsynth/stream.py`
把离线模块替换为带缓存的版本，再由 `export.py` 导出 Neutone 模型：

- `StatefulGRU` 保存 GRU hidden，替代每次前向都从零开始的离线 GRU；
- `StreamHarmonic` 保存 `phase`、上一帧频率和上一帧谐波幅度，帧间插值后连续振荡；
- `StreamFilteredNoise` 保存 FIR 尾部，避免每个块重新开始卷积；
- `CachedStreamEstimatorFLSynth` 保存 2048 点分析窗口和输出延迟缓存，默认
  48 kHz、960 samples/hop，即 20 ms 一帧。

这些状态可以作为未来 ONNX/ACL 模型的设计清单：

| 状态 | 作用 | MIDI 合成是否需要 |
| :--- | :--- | :--- |
| `gru_hidden` | 控制网络跨帧记忆 | 需要，每个声部独立 |
| `phase` | 谐波振荡器连续相位 | 需要，每个声部独立 |
| `prev_freqs` / `prev_harm` | 控制量帧间插值 | 需要，每个声部独立 |
| `noise_cache` | FIR 卷积尾部 | 需要，取决于噪声实现 |
| `input_cache` / `output_cache` | 从输入音频估计 F0/loudness 时的分析延迟 | MIDI 直驱时可省略 |

该仓库的输入仍然是单声道音频，F0 由 YIN/torchcrepe 估计，并通过 Neutone
`.nm` 导出，不是 ONNX/ACL 后端，也不直接支持多音符。因此当前实现保留它的
“显式流式状态 + 缓存尾部 + 固定 20ms 帧”设计，但继续使用 MIDI 事件生成 F0 和
ADSR loudness，再将每个声部送入已验证的 ONNX 控制模型。

## 钢琴专用模型：`lrenault/ddsp-piano`

[`lrenault/ddsp-piano`](https://github.com/lrenault/ddsp-piano) 是目前最适合钢琴
音色的参考和预训练权重来源。它不是单声部 DDSP，而是直接把 MIDI 编码成最多
16 个并行声部，并额外建模：

- onset velocity、note release 和 sustain/soft/sostenuto pedal；
- 每个音符的非谐性系数和双琴弦失谐；
- 128 个部分音、96 个噪声频带和钢琴录音环境混响；
- 10 个 MAESTRO 钢琴/录音模型 embedding。

默认 v2 checkpoint 已随仓库下载，大小约 4.2 MB；配置是 24 kHz、250 Hz
控制帧率（4 ms 一帧）、16 声部。它的 `synthesize_midi_file.py` 可以离线从
MIDI 生成钢琴 WAV，但仓库没有 ONNX 或实时声卡后端。迁移到本项目时，建议先
只导出控制网络，显式暴露以下输入和跨帧状态：

```text
inputs:  conditioning[1, 1, 16, 2], pedal[1, 1, 4], piano_model[1, 1]
states:  context_gru[1, 64], mono_gru[16, 192], note_release[16, 2]
outputs: amplitudes[16, 1], harmonic_distribution[16, 128], magnitudes[16, 96]
         f0_hz[16, 2], inharm_coef[16, 1]
```

然后在 Python/ONNX 原型中复用已有 FIFO 和声卡输出，把非谐波振荡器、噪声尾部
和混响缓存保持在每个固定声部槽位中。这样才能保留钢琴的复音、踏板和琴弦特征，
而不是把钢琴 MIDI 降级为多个独立小提琴模型。

## PyTorch 移植：`ytsrt66589/ddsp-piano-pytorch`

[`ytsrt66589/ddsp-piano-pytorch`](https://github.com/ytsrt66589/ddsp-piano-pytorch)
是官方模型的 PyTorch 重写，当前 commit `2c9e17a` 主要包含训练代码和模块草稿：

- 没有随仓库提供官方 checkpoint；
- 没有完成 `inference.py`、实时流式接口或 ONNX 导出；
- `MonophonicNetwork`、谐波振荡器和混响模块中仍有硬编码 `.cuda()`；
- `NoteRelease` 仍使用随机初始化的状态张量，不能直接作为确定性的实时状态机。

因此它目前适合参考 PyTorch 层结构，不适合作为当前播放器的钢琴后端。钢琴
ONNX 迁移应以官方 TensorFlow checkpoint 为基准，先做数值对齐，再考虑把控制
网络重写成 PyTorch/ONNX。
