# MIDI-DDSP stateful v2 导出与对齐

> 命令默认从 `case3` 根目录执行。[返回文档索引](README.md)。

## 目标与版本锁定

MIDI-DDSP 使用 Expression Generator 和 Synthesis Generator 两级网络。旧版
`notes32`/`frames64` OM 会在每个块重置双向 GRU 和自回归状态，只能用于迁移验证，
不能作为原版音质基准。

stateful v2 固定以下资产：

- 官方源码提交：`d7af42704a63b47267ae6a1bc0fee1ed7dc5c855`
- Expression checkpoint index SHA256：
  `61c2e6aa8b70fe511d3d1613892addc3479165e0096c189f2c2eabf364f34375`
- Synthesis checkpoint index SHA256：
  `d1529b405eac9a9d365edb6451a946f8e943d2bcffbeda45da4ece9ea25506e4`
- 默认随机种子：`20260724`
- 支持乐器：URMP ID 0-12

本轮不训练或微调模型。目标是复现官方整段推理语义，再把有状态组件转换为静态 ONNX/OM。

## 本地导出环境

TensorFlow checkpoint、TensorFlow 基准和 ONNX 导出只在开发电脑执行。板端不要安装
该依赖文件：

```bash
python -m pip install -r requirements-export.txt
```

当前开发电脑使用 Conda `mediapipe_legacy` 环境。该环境包含 TensorFlow 2.15.1、
DDSP 3.2.0、tf2onnx 1.16.1 和 ONNX Runtime 1.16.0；`setuptools` 必须保持 `<81`，
以便旧版 `pretty_midi` 继续使用 `pkg_resources`。导出程序缺少依赖时会立即报错。

## 1. 生成完整 TensorFlow 基准

先使用官方 `pretty_midi` 和完整序列模型生成固定基准：

```bash
python tools/export_midi_ddsp_tf_reference.py \
  --midi _upstream/midi-ddsp/midi_example/ode_to_joy.mid \
  --instrument-id 0

python tools/export_midi_ddsp_tf_reference.py \
  --midi midi/ddsp-test.mid \
  --instrument-id 0
```

每个输出目录包含 `reference.npz`、`dry.wav`、`wet.wav` 和 `manifest.json`。NPZ 保存：

- note pitch/length、6 个 expression controls 和逐帧 conditioning；
- q_pitch、onset、offset、`z_midi`；
- 外部 F0 Gumbel 张量、采样索引和 FilteredNoise 白噪声；
- F0、amplitude、harmonics、noise 原始/后处理张量；
- 干声、官方混响声和对应乐器 IR。

manifest 保存源码、MIDI、checkpoint、参考文件及每个张量的 SHA256。

## 2. 导出 stateful v2 ONNX

```bash
python tools/export_midi_ddsp_stateful_onnx.py
```

输出目录默认为 `models/midi_ddsp/stateful_v2_batched/onnx/`。导出器为静态
batch `1/2/4/8` 分别生成以下 8 个组件，共 32 个 ONNX：

1. Expression 正向上下文 GRU，32 note block，显式 state I/O；
2. Expression 反向上下文 GRU；
3. Expression 自回归 decoder，显式 previous controls 和两层 GRU state；
4. Synthesis 64-frame preconditioning；
5. Synthesis 正向上下文 GRU；
6. Synthesis 反向上下文 GRU；
7. F0 自回归 decoder，输入外部 Gumbel、上一帧 one-hot 和两层 state；
8. Timbre 网络，固定最多 65,536 帧并显式接收 `valid_frames`。图内每层归一化只统计真实帧，填充区始终清零，从而复现官方跨时间轴的全曲 LayerNorm。

Timbre 不能使用有限 halo 分块。DDSP 3.2.0 的 `Normalize('layer')` 会同时沿时间轴和通道轴计算均值与方差，因此分块会改变整首曲子的全局统计。当前上限对应 250 Hz 下约 262.144 秒；更长的单声部文件会明确报错。

F0 采样严格保留官方 `top-p=0.95` 和 `midi_zero_silence=True` 语义。导出清单同时记录 ONNX
张量名和稳定逻辑名，避免 tf2onnx 重命名影响 PyACL 运行时。

每个组件导出时都会使用 ONNX Runtime 与对应 TensorFlow 子图比较。整链比较命令为：

```bash
python tools/compare_midi_ddsp_stateful_onnx.py \
  --export-manifest models/midi_ddsp/stateful_v2_batched/onnx/export_manifest.json \
  --reference reports/midi_ddsp/tf_reference/ddsp-test/reference.npz \
  --voice-batch-size 8
```

batch 比较会用独立种子复制参考声部，要求所有成员的 `sampled_bins` 完全一致，并按
既有阈值比较其余输出。运行时按声部数选择最小可容纳 batch；超过 8 个声部时按原
顺序分组，未使用行由独立状态和 `valid_frames=0` 屏蔽。

## 3. 对齐 CPU DSP 与混响

```bash
python tools/compare_midi_ddsp_tf_dsp.py \
  reports/midi_ddsp/tf_reference/ddsp-test/reference.npz
```

比较工具把相同白噪声注入 NumPy FilteredNoise，分别比较干声和湿声，默认要求 NRMSE
不超过 `1e-4`。运行时复现 `exp_sigmoid`、控制曲线上采样、角度累积、Nyquist 谐波
屏蔽、谐波归一化、Hann IR、延迟补偿和干湿叠加。

## 4. 板端转换模型包

ONNX 完成并通过本地比较后，将源码、ONNX 和清单同步到 Ascend 310B，再在板端执行：

```bash
bash tools/convert_midi_ddsp_stateful_bundle.sh
```

脚本使用 `Ascend310B4` 和 `precision_mode_v2=origin` 转换 32 个组件，将 ATC 原始日志保存到
`models/conversion_logs/midi_ddsp_stateful_v2_batched/`，并生成：

```text
models/midi_ddsp/bundles/google-urmp-stateful-v2-batched-origin/
├── manifest.json
└── midi_ddsp_v2_*.om
```

导出器将 Keras GRUCell 按 `reset_after=True` 公式展开为 MatMul、Sigmoid 和 Tanh。
板端部署固定使用 `precision_mode_v2=origin`，活动模型目录只保留通过验收的
stateful v2 origin bundle。

manifest 记录源码、checkpoint、ONNX、OM、输入输出、状态尺寸和校验值。未完成板端
数值比较与试听前，`quality_status` 保持 `om_converted_unverified`，不得替换旧模型。

## 5. 混响资产

```bash
python tools/export_midi_ddsp_reverb.py
```

`models/om/midi_ddsp_reverb_ir.npz` 保存 checkpoint 中的 20 组原始 IR，产品只使用
ID 0-12。前 16,000 点保持原值，后 32,000 点应用 `exp(-4t)`，首样本清零，卷积后
叠加干声。当前锁定 SHA256 为
`ecbc733bc9a17516dc00897e64eaae70114aa79ed97e2bbc59dedb334f356058`。

## 验收门槛

- stateful ONNX 与完整 TensorFlow：Expression NRMSE <= 0.2%，amplitude <= 0.3%，
  harmonics <= 0.8%，noise <= 1.5%，F0 采样索引完全一致；
- NumPy 干声和湿声相对官方 TensorFlow DSP：NRMSE <= `1e-4`；
- OM 通过同一固定夹具和阈值后，才将 stateful v2 标记为推荐模型；
- 板端 `ddsp-test.mid` 无周期接缝、活动音符静音、默认削波、underrun 或 overrun；

2026-07-25 本地 batch `1/2/4/8` 均通过 `ddsp-test.mid` 固定基准：每档
`sampled_bins` 完全一致，batch 内成员张量一致。Expression NRMSE
`5.11e-7`、F0 `2.62e-8`、amplitude `5.49e-4`、harmonics `8.23e-4`、noise
`2.23e-3`，采样索引完全一致；NumPy 干声 NRMSE `9.35e-5`、湿声 `1.74e-5`。
板端 origin OM 也通过同一夹具：Expression `3.67e-7`、F0 `3.72e-8`、amplitude
`5.45e-4`、harmonics `8.24e-4`、noise `2.23e-3`，F0 采样索引完全一致。
