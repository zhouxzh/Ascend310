---
marp: true
size: 16:9
theme: default
paginate: true
header: "昇腾310B 8周教学"
footer: "第7周：智能电子琴"
---

# 第7周：智能电子琴

- 案例：`samples/case3` DDSP 音乐工作台
- 每周 3 课时，每课时 45 分钟
- 目标：理解 DDSP 系统与三条模型链，完成模型和音频设备准备，运行 Web UI 并完成冒烟测试

---

## 本周课程安排

| 课时 | 时长 | 主题 | 重点 |
|---|---|---|---|
| 第1课时 | 45 分钟 | DDSP 系统与三条模型链 | DDSP 原理、Piano/MIDI/VST 合同、PyACL 边界 |
| 第2课时 | 45 分钟 | 模型与音频设备准备 | 发布模型、ATC/OM、ALSA/PulseAudio/MIDI |
| 第3课时 | 45 分钟 | Web UI 运行与冒烟测试 | 构建部署、启动服务、四个工作区、音频冒烟 |

---

## 系统概览

- Ascend 310B 板端运行 FastAPI、PyACL、OM 推理和 CPU 音频合成
- 浏览器只提交选择意图；服务端决定模型路径、音频设备、资源锁和文件产物
- 三条业务链共享 NPU 与音频设备，但时间模型、状态管理和调度方式不同
- 生产运行只加载已验证的 OM，不提供 ONNX、TFLite 或 CPU 模型回退

---

## 第1课时：三条模型链

| 链路 | 输入 | 神经网络 | 输出/用途 |
|---|---|---|---|
| 实时演奏 | 触摸屏 13/25 键，或实体 MIDI 键盘 | Piano-DDSP `model-suite-v1.0.1` FP32 bundle | 16 声部实时钢琴，扬声器输出 |
| MIDI-DDSP | `.mid` / `.midi` 文件 | stateful v2 版本化模型包 | 完整乐曲 WAV 渲染、开发板或浏览器播放 |
| DDSP-VST | 实体 Capture 麦克风 | Feature OM + 11 个 Control OM | 单音音色实时转换，扬声器输出 |

Piano-DDSP 是独立实时会话；MIDI-DDSP 是离线层级渲染；DDSP-VST 是麦克风 Effect，不是 MIDI Synth。

---

## 第1课时：时间模型与 DSP 边界

- Piano-DDSP：每 `4 ms` 更新一次控制量，控制率 `250 Hz`，音频采样率 `16 kHz`，每控制帧对应 `64` 个采样
- MIDI-DDSP：Expression 与 Synthesis 使用 `250 Hz` 控制率，但依赖完整乐曲的双向上下文，不能放入按键后的 `4 ms` 实时路径
- DDSP-VST：Feature 使用 `16 kHz`、`1024` 点窗、`320` 点步长，即 `50 Hz`；Control 每 `20 ms` 更新一次
- 三种链路都由神经网络预测低维控制量，由 CPU 完成谐波、噪声、混响、重采样和缓冲

---

## 第1课时：DDSP 合成原理

设采样率为 $f_s$，第 $n$ 个采样的基频为 $f_0[n]$，第 $k$ 个谐波相位按式累积：

$$
\phi_k[n+1] = \phi_k[n] + \frac{2\pi k f_0[n]}{f_s}.
$$

网络预测总幅度 $A[n]$ 与未归一化谐波参数 $z_k[n]$，归一化后得到谐波比例：

$$
c_k[n] = \frac{\operatorname{softplus}(z_k[n])}
{\sum_{j=1}^{K}\operatorname{softplus}(z_j[n]) + \varepsilon}.
$$

谐波信号为 $x_h[n] = A[n]\sum_{k=1}^{K}c_k[n]\sin(\phi_k[n])$，超过奈奎斯特频率的谐波必须抑制，即 $k f_0[n] < f_s/2$。

---

## 第1课时：Piano-DDSP 模型来源

- Piano-DDSP 基于作者在独立 `piano-ddsp-pytorch` 项目中实现的 PyTorch 训练模型
- 训练数据使用时间对齐的 MAESTRO 钢琴音频与高精度 MIDI，录音年份映射为钢琴录音域索引
- 网络分为全局上下文分支和共享权重的单声部分支，并行描述最多 16 个声部
- 为流式部署，循环状态被显式化为输入输出；面向 Ascend 时 GRU 等价展开为矩阵乘、门控激活和逐元素运算
- 发布模型版本为 `model-suite-v1.0.1`，部署合同为 FP32、opset 13、batch 1、每调用 1 帧、`250 Hz`、`16 kHz`、最多 16 声部

---

## 第1课时：Piano-DDSP ONNX 输入合同

| 输入 | 形状 | 含义 |
|---|---|---|
| `conditioning` | `[1,1,16,2]` | 16 声部的音高与力度条件 |
| `pedal` | `[1,1,4]` | 踏板与连续控制状态 |
| `piano_model` | `[1]` | MAESTRO 钢琴年份索引 |
| `extended_pitch` | `[1,1,16,1]` | 扩展音高条件 |
| `context_state` | `[1,1,64]` | 全局循环状态 |
| `monophonic_state` | `[1,16,192]` | 每声部循环状态 |

每次推理只处理一个控制帧，帧间递归关系通过状态输入输出显式传递。

---

## 第1课时：Piano-DDSP ONNX 输出合同

| 输出 | 形状 | 含义 |
|---|---|---|
| `amplitudes` | `[1,1,16,1]` | 声部总幅度 |
| `harmonic_distribution` | `[1,1,16,96]` | 96 个谐波比例 |
| `inharmonicity`、`f0_hz` | `[1,1,16,1]` | 非谐性和基频 |
| `noise_magnitudes` | `[1,1,16,64]` | 64 个噪声频带 |
| `reverb_ir` | `[1,24000]` | 学习到的混响冲激响应 |
| `next_context_state` | `[1,1,64]` | 下一帧全局状态 |
| `next_monophonic_state` | `[1,16,192]` | 下一帧声部状态 |

导出验证需要连续回灌状态，逐项对照 PyTorch 与 ONNX 的控制量和循环状态，不能只比较孤立单帧。

---

## 第1课时：Piano-DDSP ATC 命令构造

以下片段来自 `prepare_piano_ddsp_models.py`：

```python
RELEASE = "model-suite-v1.0.1"
PRECISION_MODE_V2 = "origin"
ATC_COMPILE_ENVIRONMENT = {
    "MULTI_THREAD_COMPILE": "0",
    "TE_PARALLEL_COMPILER": "1",
}
INPUT_SHAPE = (
    "conditioning:1,1,16,2;pedal:1,1,4;piano_model:1;"
    "extended_pitch:1,1,16,1;context_state:1,1,64;"
    "monophonic_state:1,16,192"
)
```

脚本构造的 ATC 命令固定使用 `--input_format=ND`、`--precision_mode_v2=origin`、`--enable_graph_parallel=0` 和 `--log=info`；此代码只在真实 Ascend 板端执行，非 ARM 或无 `atc` 环境会直接拒绝。

---

## 第1课时：Piano-DDSP 板端转换与验证命令

仅在 Ascend 310B 板端激活已有 CANN 环境后执行：

```bash
python prepare_piano_ddsp_models.py --variant gru-unrolled --models gru_ir_96_64
python tools/validate_piano_ddsp_om.py \
  --bundle models/piano_ddsp/bundles/model-suite-v1.0.1-gru-unrolled-fp32-origin/manifest.json \
  --model-id gru_ir_96_64 \
  --reference models/piano_ddsp/references/model-suite-v1.0.1/gru_ir_96_64/reference-10000.npz \
  --report reports/piano-ddsp/gru-ir-96-64-10000.json --frames 10000 --activate
```

短于 10,000 帧的冒烟报告不能激活模型；catalog 和 worker 也会拒绝没有合格报告的 OM。CANN 8.0.0 的原生 `DynamicGRUV2` 不接受 FP32 输入，因此 FP32 基线使用已经与原始 ONNX 连续逐帧对照 10,000 帧的 `gru-unrolled` 变体。

---

## 第1课时：MIDI-DDSP 层级模型

- Expression Generator 先从最长 `32` 个 note/rest token 上下文预测 6 维表情控制：`volume`、`vol_fluc`、`vibrato`、`brightness`、`attack`、`vol_peak_pos`
- Synthesis Generator 再用 `64` 帧窗口、`250 Hz` 控制率生成音频控制量
- stateful v2 拆成八类可独立转换和批处理的组件：Expression 前后向、decode、synthesis precondition、synthesis 前后向、F0 decode、timbre
- 模型是单声部建模；复音 MIDI 先拆成最少数量的单音 voice，再按静态 batch `1/2/4/8` 推理并混音
- Web API 只接受 `model_bundle_id`，不能从不同导出批次分别组合 Expression 与 Synthesis

---

## 第1课时：MIDI-DDSP 渲染命令

来自 `doc/05-midi-ddsp-realtime.md`：

```bash
python midi_ddsp_realtime.py \
  --midi midi/ddsp-test.mid \
  --model-bundle models/midi_ddsp/bundles/google-urmp-stateful-v2-batched-origin/manifest.json \
  --instrument-id 0 \
  --seed 20260724 \
  --render-only \
  --tail-seconds 2 \
  --output reports/midi_ddsp/ddsp-test.wav \
  --report reports/midi_ddsp/ddsp-test.json
```

去掉 `--render-only` 即可在完整渲染后播放。F0 使用官方 `top-p=0.95` 采样，外部 Gumbel 张量由默认种子 `20260724` 生成；休止帧 F0 为 `0 Hz`。报告记录输入、模型包、组件、种子、WAV 峰值/RMS、削波、underrun 和 overrun。

---

## 第1课时：DDSP-VST Feature 与 Control

Feature OM 运行时路径为 `models/om/ddsp_vst_feature_mixed_float16.om`，固定合同：

```text
audio float32[1024]
f0_scaled, pw_scaled, f0_hz, pw_db float32[1]
sample_rate=16000, hop_size=320
```

Control OM 固定输入输出：

```text
state[512], f0_scaled[1], pw_scaled[1]
amplitude[1], harmonics[60], noise_amps[65], state_out[512]
```

Control 模型覆盖巴松、单簧管、长笛、口风琴、萨克斯、西塔琴、长号、小号、大号、小提琴和人声音色，共 11 类；不同音色共享同一输入与状态合同。

---

## 第1课时：DDSP-VST Effect 音频链路

固定链路为：

```text
48 kHz 双声道摄像头输入 -> 单声道 -> 16 kHz -> 1024 窗/320 步长
-> Feature OM -> Control OM -> DDSP 合成 -> 48 kHz 双声道输出
```

- 输入输出使用 `parec`、`paplay` 对应的 PulseAudio source/sink，不使用 monitor
- 设备丢失后不自动改路，显式路由失败时停止会话并释放资源
- 噪声门先校准环境底噪，再用开启阈值、迟滞、保持、开启和关闭时间平滑门增益
- 默认只输出转换后的声音，输出增益为 `-18 dB`；持续过载会进入明确的安全静音

---

## 第1课时：PyACL 模型合同

`pyacl_ddsp.py` 固定静态张量形状：

```python
INPUT_SHAPES = {
    "state": (512,),
    "f0_scaled": (1,),
    "pw_scaled": (1,),
}
OUTPUT_SHAPES = {
    "amplitude": (1,),
    "harmonics": (60,),
    "noise_amps": (65,),
    "state_out": (512,),
}
```

PyACL 生命周期是板端独占步骤：`acl.init`、`rt.set_device`、`create_context`、`mdl.load_from_file`、创建 dataset、`mdl.execute`、`memcpy` 回宿主端，最后按逆序释放 buffer、dataset、desc、model、context 和 device。

---

## 第1课时：PyACL 推理与状态回灌

`pyacl_ddsp.py` 的推理入口先校验输入再调用 OM：

```python
prepared: dict[str, np.ndarray] = {}
for name, shape in self.input_shapes.items():
    array = np.ascontiguousarray(inputs[name], dtype=np.float32)
    if array.shape != shape:
        raise ValueError(
            f"Unexpected input {name} shape: {array.shape}, expected {shape}"
        )
    if not np.all(np.isfinite(array)):
        raise ValueError(f"Input {name} contains NaN or Inf")
    prepared[name] = array
```

随后在锁内设置 context、`acl.rt.memcpy` 拷贝输入、`acl.mdl.execute` 推理、再 `memcpy` 拷回输出。`state_out` 必须由调用方回灌到下一帧，形成闭环状态。

---

## 第1课时：实时合成主循环

`realtime_ddsp.py` 中每个 MIDI voice 持有独立控制模型状态与合成器：

```python
snapshots = self.midi.next_snapshots()
if not snapshots:
    self.voice_gain.reset()
    return np.zeros(MODEL_HOP_SIZE, dtype=np.float32)

mixed = np.zeros(MODEL_HOP_SIZE, dtype=np.float32)
rendered_voices = 0
for snapshot in snapshots:
    voice = self.voices[snapshot.slot]
    mixed += voice.render(snapshot, settings)
    if not snapshot.finished:
        rendered_voices += 1
return self.voice_gain.process(mixed, rendered_voices)
```

音频流顺序是：MIDI 快照 -> 每声部 OM 控制量 -> 谐波合成 + 滤波噪声 -> 多声部混音 -> 线性重采样到 `48 kHz` -> 增益 -> FreeVerb 混响 -> 输出块。当前用户入口为 WebUI；`realtime_ddsp.py` 是已退役的历史 CLI，用于理解同一类状态和音频边界。

---

## 第2课时：开发电脑与开发板职责边界

| 操作 | 开发电脑 | Ascend 310B |
|---|---:|---:|
| 编辑源码、运行普通 Python 单元测试 | 是 | 否 |
| `npm ci`、`npm run test`、`npm run build` | 是 | 否 |
| 下载并校验发布模型 | 是 | 可接收已校验资产 |
| CANN 环境、ATC、OM、PyACL、`npu-smi` | 否 | 是 |
| ALSA、PulseAudio、真实 MIDI、听音验收 | 否 | 是 |

ATC、OM、PyACL、ALSA、MIDI 和音频设备操作都属于板端独占步骤，必须在真实 Ascend 310B 上执行。

---

## 第2课时：获取已发布模型

Piano-DDSP、DDSP-VST 和 MIDI-DDSP 的 ONNX/OM 均从 `zhouxzh/piano-ddsp-ascend310` 已发布 release 获取：

```bash
# 默认是锁定的 Piano-DDSP release。
python tools/download_model_release.py

# 其他模型族使用固定 revision、目录和 manifest SHA256。
python tools/download_model_release.py \
  --revision <immutable-release> --release-dir <published-directory> \
  --target-dir models/<family> --manifest-sha256 <sha256-of-SHA256SUMS>
```

下载器先读取固定 revision 的 `SHA256SUMS`，再断点下载并逐项校验。下载失败、manifest SHA256 不符或任何资产哈希不符时必须停止；不要用移动的分支名替代发布 revision。

---

## 第2课时：Piano-DDSP 板端模型准备

在板端 `case3` 根目录、已激活 CANN 环境中执行：

```bash
python prepare_piano_ddsp_models.py --variant gru-unrolled --models gru_ir_96_64
```

脚本会在 bundle 中写入 `manifest.json`、每个模型的 `.atc.log`、`command.json`、OM 和元数据。转换命令保留 `MULTI_THREAD_COMPILE=0`、`TE_PARALLEL_COMPILER=1`、`enable_graph_parallel=0`，以单线程、无并行方式执行 ATC，控制板端编译温度和内存。先转换并验证 `gru_ir_96_64`，之后才允许转换其他模型。

---

## 第2课时：DDSP-VST ATC 转换

文档中的默认命令：

```bash
cd ~/Documents/case3
bash tools/convert_onnx_to_om.sh
```

默认输入模型和固定输入形状为：

```text
model=models/ddsp_vst/Violin.onnx
state:512;f0_scaled:1;pw_scaled:1
```

成功或失败后都会保留 `models/om/Violin.om`、`Violin.atc.log` 和 `Violin.atc.summary.txt`。其他音色使用相同图结构，仍应逐个检查各自的 `.atc.summary.txt`，不能仅根据小提琴模型的结果判定全部模型已经转换成功。

---

## 第2课时：DDSP-VST 其他模型转换

脚本支持替换模型、输出路径、输入形状和 SOC 版本：

```bash
bash tools/convert_onnx_to_om.sh \
  --model models/ddsp_vst/Flute.onnx \
  --output models/om/Flute \
  --input-shape 'state:512;f0_scaled:1;pw_scaled:1' \
  --soc-version Ascend310B1
```

文档中的 20T 示例使用 `Ascend310B1`；本教程目标为 `Ascend310B4 / 8T` 时，应以板端 `npu-smi info` 和 CANN 文档确定的 `soc_version` 为准，不要照搬其他设备的芯片名。

---

## 第2课时：ONNX 与 OM 精度对照

先在本地开发环境生成确定性 ONNX 基准：

```bash
python tools/compare_onnx_om_precision.py reference \
  --onnx models/ddsp_vst/Violin.onnx \
  --output reports/Violin_onnx_reference_1024.npz \
  --steps 1024 --seed 20260721
```

将基准同步到开发板后，在板端 Anaconda `base` 环境运行 OM 对比：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh

cd ~/Documents/case3
python tools/compare_onnx_om_precision.py om \
  --om models/om/Violin.om \
  --reference reports/Violin_onnx_reference_1024.npz \
  --report reports/Violin_fp16_precision_1024.json \
  --device 0
```

`teacher-forced` 模式每帧使用 ONNX 状态输入，隔离单次转换误差；`closed-loop` 模式把 OM 自己的 `state_out` 回灌到下一帧，检查状态误差是否累积。数值对照是独立于听音的评价，不等价于音质验收。

---

## 第2课时：ALSA / PulseAudio 板端检查

```bash
ip -4 address
lsusb
pactl list short sinks
pactl list short sources
cat /proc/asound/cards
cat /proc/asound/seq/clients
aplay -l
arecord -l
```

这些命令只描述板端设备状态。`pactl list short sources` 中，麦克风应显示为实体 capture source；名称以 `.monitor` 结尾的条目是输出回放监视源，不能用于 DDSP-VST 或麦克风输入测试。

---

## 第2课时：音频输出设备角色

EDIFIER M16 Pro USB 喇叭优先使用稳定 ALSA card ID：

```bash
amixer -c Pro scontrols
amixer -c Pro sget PCM
amixer -c Pro set PCM 75% unmute
speaker-test -D plughw:CARD=Pro,DEV=0 -c 2 -t wav -l 1
aplay -Dplughw:CARD=Pro,DEV=0 speaker-test-stereo.wav
```

板载 3.5mm 只做厂商单声道诊断：

```bash
amixer set Playback 10
amixer set Deviceid 2
aplay -Dhw:ascend310b speaker-test-mono.wav
```

板载接口和 ALSA 驱动宣告双声道能力，但当前镜像下双声道模拟输出没有通过音质、实时传输和可停止性验证，不作为实时立体声验收路径。蓝牙 A2DP 会引入编码、无线传输和播放缓冲延迟，不应把蓝牙链路延迟归因于 NPU 推理。

---

## 第2课时：MIDI 设备检查

- 板端通过 `/dev/snd/seq` 或 raw MIDI 设备读取实体键盘，浏览器 Web MIDI 不是板端输入来源
- MIDIPLUS TINY 为 32 键实体控制器，示例范围 `F2-C5`，支持力度和 CC64 延音
- 重新插拔后设备 ID 可能变化，应刷新枚举后由服务端重新选择端口
- 历史 CLI 可列出设备：

```bash
python realtime_ddsp.py --list-midi
python realtime_ddsp.py --list-audio
```

当前生产用户入口是 WebUI 的“MIDI 键盘”模式：服务端端口下拉框绑定板端枚举的输入端口，运行中不能换端口，断开时释放该来源音符。

---

## 第3课时：开发电脑构建与部署

在开发电脑从仓库根目录执行：

```powershell
cd samples\case3
python -m pip install -r requirements.txt
python -m pytest -q

cd webui
npm ci
npm run test
npm run build
npm run test:e2e
```

部署脚本在暂存位置完成程序、前端资源和模型元数据的一致性检查，再原子切换运行版本：

```powershell
powershell -ExecutionPolicy Bypass -File tools/deploy_midi_ddsp_webui.ps1
```

开发板不安装 Node 或 npm，也不运行 Vite 生产服务器，只接收开发电脑生成的 `webui/dist/`。

---

## 第3课时：板端启动服务

在板端已有 conda `base` 与 CANN 环境中启动：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh

cd /home/HwHiAiUser/Documents/case3
python scripts/run_webui.py
```

服务默认监听 `0.0.0.0:8765`。本机冒烟检查：

```bash
curl -fsS http://127.0.0.1:8765/
curl -fsS http://127.0.0.1:8765/api/v1/status
```

板端触摸屏全屏打开：

```bash
DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority \
firefox --kiosk http://127.0.0.1:8765
```

---

## 第3课时：Web 工作台

| 工作区 | 关键内容 | 验收要点 |
|---|---|---|
| 实时演奏 | 触摸屏与实体 MIDI 共享同一 Piano-DDSP 会话 | 统一会话、共享音色与资源锁；运行中锁定输入模式 |
| MIDI-DDSP | 音频库与新建渲染 | 一个文件卷帘；WAV 版本、报告、播放目标同步 |
| DDSP-VST | 麦克风 Effect | 只接受实体 capture；Feature/Control 后端必须是 `acl/om` |
| 设备 | 概览、音频设备、运行环境 | NPU、模型、输出、Capture、MIDI 和依赖可见 |

实时状态采用 WebSocket 传递音符边沿、作业进度和实时指标；REST 查询提供模型、设备和会话快照；文件访问承载 MIDI、WAV、录音与评价报告。

---

## 第3课时：推荐冒烟顺序

1. 在“设备概览”确认板端在线、NPU 可见、输出、Capture、MIDI 和依赖状态
2. 在“音频设备/输出测试”从低增益开始，确认目标扬声器能听见左、双或右声道测试音
3. 在“实时演奏/触摸屏”测试单音、快速点按、多指、延音、移调与 panic
4. 在“实时演奏/MIDI 键盘”选择实体端口，测试力度、重复音、CC64、断开重连和无悬挂音符
5. 在“MIDI-DDSP”选择已有版本播放，或建立新的完整渲染任务并检查 WAV 与报告
6. 停止钢琴会话后，在“DDSP-VST”选择实体输入输出，安静校准噪声门，再用独立单音声源测试
7. 完成全部测试后停止任务，确认设备概览中会话恢复为空闲

---

## 第3课时：板端运行时验证

在已激活 CANN 和 Conda `base` 的板端可运行：

```bash
python tools/validate_webui_runtime.py
```

该命令用于板端环境与真实运行时验证，不等价于听音验收。真实音频验收至少记录推理耗时、队列延迟、总延迟、PCM 非零、削波、capture overflow 和 playback underrun；仅有 HTTP 200 不能证明发声正确。

---

## 第3课时：实时音频回调边界

`realtime_ddsp.py` 用有界队列把渲染 worker 与音频回调解耦：

```python
try:
    block = self.blocks.get_nowait()
except queue.Empty:
    outdata.fill(0.0)
    had_underrun = True
else:
    self.space_available.set()
    block = np.asarray(block, dtype=np.float32)
    if block.ndim == 1:
        outdata[:, :] = block[:, np.newaxis]
    elif self.output_channels == 1:
        outdata[:, 0] = np.mean(block, axis=1)
```

渲染 worker 生成块，音频回调按设备通道数分发；队列为空时填零并计 `underrun`，队列满时渲染线程计 `overrun`。短缓冲提高按键响应但更敏感，长缓冲提高连续性但增加反馈延迟，因此缓冲长度和系统音量、合成增益要分开评价。

---

## 课堂任务

1. 记录三条模型链的输入、神经网络、输出、控制率和状态合同
2. 在板端执行设备、ALSA、PulseAudio 和 MIDI 只读检查，标记 CANN/OM/PyACL/ALSA/MIDI 为板端步骤
3. 下载或核对发布模型：固定 revision、目录、manifest SHA256 和资产 SHA256
4. 使用已校验 OM 完成 Piano-DDSP 验证，或保留现有 ATC 日志与 bundle 清单；不重新导出模型
5. 在开发电脑构建前端，在板端启动 `run_webui.py`，确认首页与 `/api/v1/status`
6. 完成扬声器测试、触摸演奏、MIDI 键盘、MIDI-DDSP 渲染或 DDSP-VST 冒烟
7. 停止全部音频任务，确认资源锁释放，并保存命令、日志、报告和 WAV 路径

---

## 交付物

- `linux/week07/model-chain.md`：三条模型链、模型来源、输入输出合同与命令记录
- `linux/week07/piano-output.md`：触控/MIDI 演奏、实时会话和音频冒烟记录
- `linux/week07/audio-smoke.md`：ALSA/PulseAudio/设备检查、扬声器测试和音量记录
- 板端报告与产物路径：ATC 日志、OM bundle manifest、渲染 WAV 与 JSON 报告
- 记录实际执行的板端环境、设备 ID、模型 ID、端口、进程 PID 和停止后的资源状态

---

## 验收标准

- 能准确说明 Piano-DDSP、MIDI-DDSP、DDSP-VST 的输入、输出、控制率、状态与用途区别
- 能区分开发电脑操作与板端独占操作；ATC、OM、PyACL、ALSA、MIDI 和真实音频步骤均在板端记录
- 模型资产来自固定发布版本，Piano-DDSP 通过 10,000 帧连续状态验证；无合格报告不得激活
- WebUI 在 `0.0.0.0:8765` 启动，`/` 与 `/api/v1/status` 返回正常，kiosk 可打开
- 至少一条音频输出路径可听到受控测试音；实时演奏验收优先使用 USB 有线输出
- 触摸或实体 MIDI 的快速点按保留最小门长，`panic`、CC64 和断线释放后无悬挂音符
- MIDI-DDSP 渲染生成可追溯的 WAV 与报告，记录 bundle、instrument、seed、tail 和产物 SHA
- DDSP-VST 使用实体 capture，Feature/Control 后端为 `acl/om`，PCM 非零，underrun/overflow/clipping 有记录；600 秒长稳协议单独评价
- 不把未执行的延迟、精度或性能数字写成实测结果，只保留实际命令输出和报告路径
