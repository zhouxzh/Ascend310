# 测试故障排查记录

> 本文集中记录 case3 在 Ascend 8T、8T2 和 20T 上测试期间遇到的问题、证据、
> 处理方法和当前结论。[返回文档索引](README.md)。

本文不把现象直接等同于根因。每一项尽量区分：命令是否成功、设备是否实际工作、
当前证据支持什么结论，以及还有哪些边界没有验证。原始环境、ATC、精度和速度报告
保存在本地 `reports/` 与 `models/om/`；这两个目录默认不提交到主仓库。

> **历史命令边界。** 文中 `realtime_ddsp.py`、DDSP-VST MIDI Synth 和 ONNX 播放命令是
> 早期排障证据，不是当前用户流程。现在实时触控与实体 MIDI 使用 Piano-DDSP，麦克风
> DDSP-VST Effect 严格使用 Feature OM 和 Control OM；当前操作以
> [WebUI 操作、部署与 API](webui.md)为准。

## 快速索引

| 现象 | 优先检查 | 当前结论 |
| :--- | :--- | :--- |
| SSH 登录慢，随后出现 `systemd[1]: Caught <SEGV>` | PID 1、journald、系统负载和内核日志 | 不是单纯的 sshd 配置问题，系统管理进程已经异常 |
| `grep: unrecognized option '--no-pager'` | 是否把两条命令写在同一行并加入中文“和” | `journalctl` 被误当成 `grep` 参数，不是 journalctl 故障 |
| `aplay` 返回 0，但耳机没有声音 | `Playback`、`Deviceid`、ALSA 设备和实际听感 | 返回 0 只表示数据被 ALSA 接受 |
| 板载 3.5mm 双声道有强噪声 | WAV 格式、MD5、硬件参数、USB 声卡对照 | 当前只验证板载单声道稳定；不能断言驱动只支持单声道 |
| 20T FP16 和混合精度 ATC 都失败 | CANN/TBE 初始化日志 | 失败发生在算子预编译之前，不是混合精度特有的算子不兼容 |
| 8T ATC 被杀死，退出码 137 | 内存、swap、TBE 并发和 `dmesg` | 7.4 GiB 无 swap 时发生 OOM，串行编译后成功 |
| `npu-smi` 显示 `Health: Alarm` | 设备可见性、OM 加载和推理 | 单独记录；设备可见且推理成功时不直接判失败 |
| 混合精度纯 NPU 更快，但闭环没有更快 | 两种计时边界 | Python、ACL 调用、复制和状态回灌会掩盖 NPU 差异 |
| 8T 编译的 B4 OM 是否能在 20T/B1 运行 | 文件哈希、加载、推理和卸载 | 当前 22 个测试 OM 可以运行，但不代表任意 B4 OM 都兼容 |
| ONNX 是否包含 FFT | ONNX 节点清单 | 11 个控制模型都没有 FFT；噪声 FFT 合成在模型外执行 |
| `_upstream` 看起来只剩两个仓库 | Git 仓库枚举、主仓库 `.gitignore` | 审计时实际有 4 个 Git 仓库；另 6 个参考仓库已恢复 |

## 测试环境基线

在比较转换、精度或速度结果前，先确认板卡不是同一个软件环境的不同名称。

| 主机 | SoC | 驱动 | CANN | 内存边界 | 已知状态 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `ascend8t` | `Ascend310B4` | `23.0.0` | 目录 `8.3.RC1`，组件 `8.3.0.1.200` | 约 7.4 GiB，无 swap | `Health: Alarm`；转换和推理可运行 |
| `ascend8t2` | `Ascend310B4` | `25.2.0` | 目录 `8.0.0`，组件 `7.6.0.1.220` | 约 15 GiB，无 swap | 驱动/CANN 组合不匹配，但 Violin 两种精度转换成功 |
| `ascend20t` | `Ascend310B1` | `25.2.0` | 目录 `8.0.0`，组件 `7.6.0.1.220` | 约 23 GiB，无 swap | 本机 ATC 初始化失败；预编译 OM 可以推理 |

三块板上的测试 Python 都应来自 Anaconda `base`，不要在 conda 激活失败时静默
回退到系统 Python。详细实测数据见 [板端实测结果](benchmark-results.md)。

建议每次测试先保存以下信息：

```bash
hostname
uname -a
date --iso-8601=seconds
npu-smi info
free -h
swapon --show
uptime
ps -eo pid,ppid,user,stat,etime,time,pcpu,pmem,comm,args \
  --sort=-pcpu | head -20
```

## SSH 登录慢和 systemd 崩溃

### 现象

20T 在 2025-09-22 的一次启动中出现：

```text
systemd[1]: Caught <SEGV>, dumped core as pid 5752.
systemd[1]: Freezing execution.
```

另一轮启动也以不同 PID 出现同样信息。SSH 有时还能连接，但登录明显变慢。

### 已保存的证据

- `wlan0` 已取得 `192.168.1.95`，所以并非最初就没有网络。
- 开机约 3 分钟时 load average 已到约 `16.28`。
- PID 1 是 systemd，但 systemd-journald 多次报告：

```text
Failed to send stream file descriptor to service manager: Connection refused
Failed to send stream file descriptor to service manager: Transport endpoint is not connected
Failed to send WATCHDOG=1 notification message: Transport endpoint is not connected
```

- 内核日志还记录了 `apport` 和 `dpkg` 的 core dump 被中止。
- 普通用户没有完整 journal 权限；镜像中也没有 `coredumpctl`。

这些证据表明 PID 1 与 journald 的通信已经失效。SSH 慢是系统级故障的表现之一，
不能只通过修改 sshd 的 DNS、GSSAPI 或认证参数来解释。该系统后来重新安装，重装前
没有得到可重复验证的单一根因。

### 正确的排查命令

每条命令要单独执行：

```bash
ps -ef | grep '[s]shd'
systemctl status ssh sshd systemd-logind --no-pager
journalctl -b -u ssh -u sshd -u systemd-logind --no-pager | tail -80
uptime
ps -p 1 -o pid,comm,args
dmesg -T | tail -200
ip addr
```

曾经输入过：

```text
ps -ef | grep sshd 和 journalctl ... --no-pager | tail -80
```

shell 不理解中文“和”是命令分隔符，整行管道最后仍进入 `grep`，所以 `-u` 和
`--no-pager` 被当作 grep 参数，产生 obsolete 和 unrecognized option 警告。
这不是 `journalctl` 或 sshd 的真实报错。

### 后续处理原则

1. 出现 `systemd[1]` 崩溃时先通过串口保留 `dmesg`、`journalctl -b`、负载和进程
   快照，再重启。
2. 不在故障现场自动安装 `systemd-coredump`、升级系统包或修改启动配置。
3. 如果重启后重复出现 PID 1 崩溃，优先检查系统镜像、文件系统和板级稳定性，
   不把 SSH 参数优化当作修复。
4. `Freezing execution` 后 systemd 不再正常调度服务，继续跑模型测试得到的环境
   数据不可作为稳定基线。

## 异常系统负载和 apport

20T 全模型运行时，环境报告记录 load average 约 `18.6`，root 的 `apport` 进程
持续占用约 `98.6%` 的一个 CPU 核。NPU 推理仍能完成，但以下指标会受影响：

- Python 闭环墙钟时间；
- 首次加载、文件 I/O 和进程调度；
- SSH 交互响应；
- 不同板卡之间的端到端速度比较。

纯 NPU compute time 与包含 Python/ACL/状态回灌的闭环时间必须分开报告。测试脚本
没有杀死 root 进程，也没有修改 apport 配置；这类系统修复需要单独的管理员决策。
证据位于
[`reports/ascend20t/all_models_runtime/environment.txt`](../reports/ascend20t/all_models_runtime/environment.txt)。

## Anaconda base 与板端运行环境

板端测试程序统一使用 `/usr/local/miniconda3` 的 `base` 环境：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
which python
python --version
which atc
```

一次 20T 测试中 conda 激活失败，重启后恢复。遇到这类情况应停止测试并保存
`PATH`、conda 初始化错误和 CANN 环境信息，不能改用 `/usr/bin/python3` 继续，
否则结果混入了不同 Python 和动态库环境。

20T 的 `base` 实测为 Python 3.9.2、NumPy 1.22.4、`ais_bench` 0.0.2，且没有
ONNX Runtime。因此 ONNX FP32 基准在本地使用 ONNX Runtime 1.20.1 生成，再上传
到开发板。Violin 新旧参考文件中的数组已精确一致。跨板比较时必须同时核对 ONNX
哈希、参考 NPZ 哈希、1024 帧和随机种子 `20260721`。

## 声卡是否被识别

板载或 USB 声卡的检查顺序：

```bash
cat /proc/asound/cards
aplay -l
aplay -L
arecord -l
ls -l /dev/snd
lsusb
```

`aplay -l` 显示硬件设备，`aplay -L` 显示 ALSA 逻辑设备；USB 设备还必须先在
`lsusb` 中出现。数字 card 编号会随重启和插拔变化，脚本应优先使用稳定的 ALSA
card ID，例如 Meizu DAC 的 `CARD=Amplif` 或漫步者 M25 的 `CARD=Device`。

## 板载 3.5mm 没有声音

板载声卡名为 `ascend310b`。系统镜像自带的耳机样例必须先设置音量和输出路由：

```bash
amixer set Playback 10
amixer set Deviceid 2
aplay -Dhw:ascend310b -f S16_LE -r 48000 -t wav \
  /opt/opi_test/audio/tianlu.wav
```

本次环境中 `Playback 10` 约为 8%，`Deviceid 2` 选择 3.5mm 输出。若遗漏路由，
`aplay` 仍可能返回 0，但耳机端没有可听声音。

### `hw` 与 `plughw` 的差别

- `hw:0,0` 或 `hw:ascend310b` 直接把数据交给硬件，不做采样率、位深或声道转换。
- `plughw:0,0` 会经过 ALSA plug 层，在需要时转换格式。

`aplay` 返回 0 只证明 ALSA 接受并完成了数据传送，不能证明模拟路由、耳机接口和
最终音质正常。验收必须包含实际听音或输出端测量。

## WAV 格式相同但播放结果不同

已知正常的官方文件 `tianlu.wav` 是 PCM、S16_LE、48 kHz、单声道。当前保留的
`speaker-test-stereo.wav` 是 PCM、S16_LE、48 kHz、双声道。两者采样率和位深相同，
但声道数不同，因此不能说格式完全相同。

检查命令：

```bash
file audio.wav
ffprobe -hide_banner audio.wav
soxi audio.wav
aplay --dump-hw-params -Dhw:ascend310b audio.wav
```

在本地转换为 48 kHz、16-bit、单声道并提高音量的示例：

```bash
ffmpeg -i input.wav -ac 1 -ar 48000 -sample_fmt s16 \
  -af "volume=6dB" output-mono-loud.wav
```

提高音量后还要检查削波。文件上传后用 SHA256 或 MD5 对照，先排除传输损坏：

```bash
sha256sum output-mono-loud.wav
```

## 板载 3.5mm 双声道强噪声

### 实测事实

- 官方 `tianlu.wav`：48 kHz、16-bit、单声道，正常。
- `speaker-test-mono.wav`：48 kHz、16-bit、单声道，正常。
- `speaker-test-stereo.wav`：48 kHz、16-bit、双声道，能听到音乐，
  但伴随很强的静电或数字噪声。
- ALSA 声明硬件支持 1 到 2 声道，双声道流进入 `RUNNING`。
- 单声道和双声道静音流都返回 0；内核日志未发现 xrun、DMA 或 I2S 错误。
- 上传前后文件哈希一致，源文件本地播放正常，增强版没有数字削波。

### 当前判断

证据只能说明 ALSA 接受了双声道数据，不能证明板载模拟输出链路正确。故障位置更
可能在 ALSA 之后的 codec、I2S 时隙或 `Deviceid 2` 模拟路由。当前结论必须写成：

> 板载 3.5mm 已稳定验证单声道；双声道能播放但有强噪声，尚未通过音质验收。

不能写成“Ascend 310B 驱动只支持单声道”，因为设备能力查询和数据传送都接受
双声道。详细证据与命令见 [Ascend 音频输出](audio-output.md)。

## USB 声卡双声道正常

Type-C 外接 Meizu HiFi DAC 的实测标识为：

```text
USB ID: 2a45:0128
ALSA card ID: Amplif
card 1, device 0: USB Audio
```

推荐使用 card ID 播放：

```bash
aplay -Dplughw:CARD=Amplif,DEV=0 \
  ~/Documents/case3/reports/audio-fixtures/speaker-test-stereo.wav
```

同一个双声道文件通过 USB DAC 可以正常播放，没有板载 3.5mm 的强噪声。这一对照
进一步排除了 WAV 文件、采样率和传输损坏。`Playback` 与 `Deviceid` 是板载声卡
控件，不用于 USB DAC。当前部署建议是：板载 3.5mm 使用 48 kHz/S16_LE/单声道，
需要双声道时使用已验证的 USB 声卡。

## 漫步者 M25 USB 喇叭

`ascend8t2` 上新增的漫步者 M25 被枚举为 USB ID `2d99:a036`、
`Jieli Technology USB Composite Device`，ALSA card ID 为 `Device`。产品品牌名和
USB 描述不同是设备固件上报方式造成的，应使用插拔对照和 USB ID 确认设备。

该设备提供 `PCM` 硬件音量。最初运行实时 DDSP 时日志显示 `played=738`、
`underruns=0`、`overruns=0`，但现场没有声音。进一步检查发现两个独立问题：

1. 程序按 1、2、3... 的顺序寻找可打开的声道数，M25 接受单声道参数后程序立即
   选择 `channels=1`，没有优先使用设备声明的双声道路径。
2. 同一 MIDI 的原始 DDSP 波形平均只有 `-48.6 dBFS`、峰值 `-32.5 dBFS`；M25
   硬件音量 40% 还会衰减 `-17.37 dB`，最终信号接近不可听范围。

修复后程序优先尝试双声道，再回退单声道或设备原生多声道；同时新增
`--output-gain-db` 软件增益。推荐命令：

```bash
cd ~/Documents/case3
amixer -c Device set PCM 70% unmute

python realtime_ddsp.py \
  --play-midi midi/ddsp-test.mid \
  --model models/ddsp_vst/Violin.onnx \
  --audio-device 1 \
  --sample-rate 48000 \
  --prebuffer 6 \
  --max-voices 1 \
  --audio-latency-ms 80 \
  --output-gain-db 0
```

设备编号 `1` 只是本轮枚举结果，重启或插拔后要通过 `--list-audio` 重新确认。
旧版本使用 `+24 dB` 后曾测得平均 `-24.6 dBFS`、峰值 `-8.5 dBFS`；当前实时输出
限制为 `-60..+6 dB`，默认 `0 dB`。WebUI 可以在运行中衰减或提升，但不修改系统
mixer 或音箱物理音量；使用正增益时应检查诊断页的削波样本数。

已知 WAV 的直接 ALSA 验证命令为：

```bash
python realtime_ddsp.py \
  --midi-file midi/ddsp-test.mid \
  --model models/ddsp_vst/Violin.onnx \
  --output /tmp/ddsp-test-ddsp.wav \
  --sample-rate 48000 \
  --max-voices 1 \
  --output-gain-db 0

aplay -Dplughw:CARD=Device,DEV=0 /tmp/ddsp-test-ddsp.wav
```

如果直接 ALSA 播放时报 `Device or resource busy`，先确认是否有 PulseAudio 或其他
程序占用硬件；需要共享输出时优先用 `-D pulse`。直接选择 `hw:1,0` 时 PulseAudio
音量不参与，应检查 `amixer -c Device get PCM` 和喇叭自身的物理音量。当前已经
确认非零波形、双声道实时传输和直接 ALSA 播放路径，实际听音结果仍需现场补录。

## 20T 本机 ATC 转换失败

### 环境和现象

2026-07-21 重启后，在 `ascend20t` 上使用与其他板一致的转换脚本复测 Violin：

```text
SoC: Ascend310B1
Driver: 25.2.0
CANN directory: 8.0.0
CANN component: 7.6.0.1.220
Python: Anaconda base 3.9.2
TE_PARALLEL_COMPILER=1
OMP_NUM_THREADS=1
```

20T 的转换结果不是始终相同，必须按测试轮次解释：

| 测试轮次 | 精度模式 | 结果 | 关键证据 |
| :--- | :--- | :--- | :--- |
| 较早的基线转换 | ATC 默认 `force_fp16` | 成功 | `ATC_EXIT_CODE=0`，生成约 3.9 MB 的原生 B1 OM；该 OM 后续仍能在 20T 加载推理 |
| 2026-07-20 精度扩展 | `force_fp32` | 失败 | 退出码 255，多项 TBE 算子预编译失败，无 OM |
| 2026-07-20 精度扩展 | `mixed_float16` | 失败 | 退出码 255，从 `Cast` 开始出现多项 TBE 预编译失败，无 OM |
| 2026-07-20 精度扩展 | `allow_mix_precision_fp16` | 崩溃 | 退出码 139，无 OM |
| 2026-07-20 精度扩展 | `cube_fp16in_fp32out` | 崩溃 | 退出码 139，无 OM |
| 2026-07-21 重启后复测 | 默认 `force_fp16` | 失败并挂住 | `E90000`、CannKB 初始化 `unexpected EOF`，未到算子预编译 |
| 2026-07-21 重启后复测 | `mixed_float16` | 失败并超时 | 同样在 CannKB 初始化失败，180 秒后终止，无 OM |

较早成功的原生 B1 FP16 OM 在跨 SoC 对照中记录为 4,017,766 字节、SHA256
`57a8f7fcac6d6add0a276a8d9eda5a7e28dc700db68a70bb8bae41e51d4ea93c`，证明
20T 的 ACL/NPU 运行路径可以工作。2026-07-20 的原始失败摘要位于
`reports/atc_precision_attempts/`；它们和重启后的初始化失败属于不同阶段，不能
合并成同一种错误。

FP16 与 `mixed_float16` 都没有生成 OM。关键错误包括：

```text
E90000
EOFError('unexpected EOF')
Failed to initialize TeConfigInfo
TeFusion
OpsManager initialize failed
```

FP16 进程失败后挂住；混合精度在 180 秒限制内失败并超时。脚本最后确认没有残留
ATC 进程。原始结论见
[`reports/ascend20t_retest/retest_summary.txt`](../reports/ascend20t_retest/retest_summary.txt)。

### 为什么不是算子不兼容

两种精度都在 TBE/CannKB 初始化阶段失败，尚未进入算子预编译和 kernel 选择。
因此重启后的本轮日志不能归类为“不支持某个 ONNX 算子”，也不能归类为“只有
混合精度不兼容”。2026-07-20 的较早测试中，混合精度已经进入具体的 TBE 预编译
阶段后失败；两轮表现不一致，说明该驱动/CANN/系统组合存在环境稳定性问题。

排查时应按以下顺序判定：

1. ATC 能否启动并完成 CANN/TBE 初始化；
2. ONNX 解析是否成功；
3. 是否出现 unsupported op、parser 或 kernel selection 错误；
4. OM 是否新生成且时间戳、大小和哈希有效；
5. OM 是否能被 `ais_bench` 或 ACL 加载。

只看 shell 退出码或日志中某个 `ERROR` 字样不足以判断算子兼容性。完整环境位于
[`reports/ascend20t_retest/environment.txt`](../reports/ascend20t_retest/environment.txt)。

## 8T ATC OOM 和退出码 137

`ascend8t` 只有约 7.4 GiB 内存且没有 swap。ATC 使用默认并发或
`TE_PARALLEL_COMPILER=4` 时，内核 OOM killer 终止 `atc.bin`，退出码为 137；
随后 TBE 子进程报告 main process disappeared。这不是模型算子不兼容。

稳定配置：

```bash
TE_PARALLEL_COMPILER=1 OMP_NUM_THREADS=1 \
  bash tools/convert_onnx_to_om.sh \
  --model models/ddsp_vst/Violin.onnx \
  --output reports/model_conversion/manual/Violin_mixed_float16 \
  --soc-version Ascend310B4 \
  --precision-mode-v2 mixed_float16
```

限制为单进程后，11 个音色的 FP16 和 mixed_float16 共 22 个 OM 全部转换成功。
OOM 证据保存在 `reports/ascend8t/dmesg_after_*_oom.txt`，首次失败摘要保存在
`reports/model_conversion/legacy_ascend8t_violin/*_oom.atc.summary.txt`。遇到退出码 137 时应先查 `dmesg` 和
内存，不要直接切换精度模式掩盖问题。

## 8T2 驱动与 CANN 不匹配

`ascend8t2` 使用 `310B4 + driver 25.2.0 + CANN 8.0.0`。这个驱动/CANN 组合与
20T 相同，但 Violin 的 FP16 和 mixed_float16 均转换成功，并可完成推理。

这说明“驱动 25.2.0 与当前 CANN 8.0 组合存在风险”是有效的环境边界，但版本号
本身不能单独解释 20T 的失败；SoC 型号、镜像状态、TBE/CannKB 进程和系统负载也
参与结果。不要从一块板的成功或失败外推到另一块板。

## 8T/B4 预编译 OM 在 20T/B1 运行

从 8T 同步到 20T 的 11 个 mixed_float16 OM 全部完成加载、推理和卸载。随后完整
22 个 FP16/mixed_float16 OM 也完成精度和速度测试，文件哈希与源端一致。

这不与 20T 本机 ATC 失败冲突：ATC 编译路径在 TBE/CannKB 初始化时失败，而 ACL
运行时仍能加载已经编译好的 OM。当前兼容性结论只适用于这批固定输入、固定算子图
和 B4 到 B1 的实测文件，不保证任意模型、动态 shape 或未来运行时版本兼容。证据
见 [`reports/cross_soc/summary.md`](../reports/cross_soc/summary.md)。

## 精度和速度结果如何解释

### 精度

所有精度测试使用相同 ONNX FP32 参考、1024 帧和随机种子 `20260721`，并分别
计算 teacher-forced 与 closed-loop。20T 上 11 个模型的混合精度在四类闭环输出
上都比 FP16 更接近 ONNX，且没有 NaN/Inf。

NRMSE 必须结合输出含义判断。例如原始 harmonics 在接近静音帧可能因 8 kHz
Nyquist 比较边界出现很大的单点绝对误差，但乘上总幅度后的有效谐波误差很小。
最终音质仍需使用真实 MIDI 合成和听感/波形对比。

### 速度

两种计时不能混用：

- 纯 NPU：`ais_bench` compute time，尽量隔离设备计算。
- 应用闭环：Python 输入组织、ACL 调用、输出复制和 GRU 状态回灌的总墙钟时间。

20T 上，混合精度纯 NPU 中位延迟相对 FP16 约快 54%，但闭环中位时间约慢 1.61%，
说明当前应用层开销主导总时间。跨板比较中，20T 的 FP16 纯 NPU比 8T 慢约 47.5%，
混合精度纯 NPU却快约 31.7%；高系统负载下，20T 两种闭环时间都比 8T 慢约
3.4% 到 3.6%。“20T 一定更快”不符合本次数据。

完整逐模型数据和测试边界见 [板端实测结果](benchmark-results.md)。

## `Health: Alarm` 的处理

三块板的部分测试中 `npu-smi info` 显示 `Health: Alarm`。当前规则是：

- 原样记录 `npu-smi` 输出、温度、内存和测试时间；
- 如果设备可见，OM 可以加载且推理成功，不单独把 Alarm 判为本轮失败；
- 如果同时出现设备不可见、ACL 初始化失败、推理错误或温度异常，再把 Alarm 作为
  关联证据排查；
- 不能在报告中省略 Alarm，也不能未经证据把所有 ATC 错误归因于它。

## ONNX 算子与 FFT 边界

11 个 DDSP-VST ONNX 具有相同拓扑，每个图 92 个节点、18 类算子。主要是 `Add`、
`Mul`、`Reshape`、`MatMul`、`ReduceMean`、门控激活和比较算子。图中没有
`FFT/DFT/RFFT/IRFFT/STFT`、`Conv`、原生 `GRU/LSTM`、`Sin/Cos` 或复数张量。

这与 DDSP-VST 的实现边界一致：ONNX/OM 只预测 amplitude、harmonics、
noise_amps 和下一帧状态；谐波振荡器和噪声 FFT 合成在 CPU/Python 侧执行。因此
当前 OM 的速度数据不包含最终波形合成，也不能用来回答完整 DDSP 音频链路的延迟。
模型获取、ATC 与算子验证见 [模型与 OM 部署](om-deployment.md)。

## 文件位置、同步和哈希

### 目录约定

- 本地与板端代码目录：`~/Documents/case3`。
- 统一模型根目录：`models/`；旧的 `model/` 已合并，不再创建第二套模型目录。
- DDSP-VST 源模型：`models/ddsp_vst/`。
- 板端 OM：`models/om/<target>/`。
- 音频测试文件：`reports/audio-fixtures/`。
- 原始测试报告：`reports/<target>/`。

### 同步后必须校验

```bash
sha256sum models/ddsp_vst/Violin.onnx
sha256sum reports/Violin_onnx_reference_1024.npz
```

本轮关键基准：

```text
Violin.onnx
82d6191868d36f967e8739887edba8e911e2bba6e09a63b514c5f3b8380996a5

Violin_onnx_reference_1024.npz
e3024848754960edb3042ce143304110e353bf2bc288c85c660ad76f7a2f45a5
```

全模型回收时已使用 SHA256 清单逐文件核对。`_upstream` 是本地研究参考，不属于
板端运行依赖，历史同步脚本有意排除它；这不会删除本地仓库。参考仓库的完整状态
见 [Upstream 参考仓库](upstream-repositories.md)。

## OM 播放无欠载但听起来不连贯

### 现象

`realtime_ddsp.py` 使用 PyACL/OM 播放时终端显示 `underruns=0`、`overruns=0`，
单帧渲染也低于 20 ms，但实际听感仍像断奏或音量周期性变化。不能仅凭 FIFO
统计把它判定为“OM 播放正常”，也不能直接归因于 NPU 性能。

### 2026-07-21 排查结果

- 首次 OM 测试使用 `--max-voices 1`，而测试 MIDI 的相邻音符间隔只有
  31–125 ms，默认 release 是 1.2 秒。新音符会抢占尚未释放的唯一声部，因此
  明显卡顿；改回多声部后现象减轻。
- 同一首 MIDI、同一合成代码离线生成的 ONNX 和混合精度 OM 波形 NRMSE 为
  0.4939%，余弦相似度为 0.9999878，静音块和块边界跳变也一致。OM 数值误差
  不是这次不连贯的主因。
- 声部管理原先会在相同音高重新 note-on 时新建 ADSR，造成包络从零开始；现已
  改为复用原声部、模型状态、相位和当前包络电平。
- `--attack 0.03 --release 0.35 --max-voices 4` 的实测最大渲染帧为 7.08 ms，
  `underruns=0`、`overruns=0`。该参数减少长尾叠加，但音质仍受 50 Hz 控制率、
  MIDI 断音方式和 DDSP-VST 单音模型限制。

排查时必须用同一 MIDI、同一包络、同一声部数和同一软件增益做 ONNX/OM A/B。
先比较 `underruns` 和 `max_render_ms`，再离线比较两份 WAV；如果波形高度一致，
应继续调整 MIDI articulation、attack/release 和模型，而不是反复修改 ACL 拷贝。

## 新一轮测试的固定检查表

1. 记录主机名、SoC、驱动、CANN 目录版本和组件版本。
2. 激活 Anaconda `base`，确认 `which python`、Python 和关键包版本。
3. 保存 `npu-smi`、内存、swap、磁盘、负载和高 CPU 进程。
4. 对 ONNX、参考 NPZ 和预编译 OM 做 SHA256 校验。
5. ATC 先语法检查和 dry-run，再保留第一次真实失败的完整日志。
6. 区分环境初始化、ONNX 解析、算子支持、kernel 编译和进程崩溃。
7. OM 成功标准包含：新文件、有效摘要、加载、推理、有限值和卸载。
8. 音频成功标准包含：格式、路由、实际听感；不能只看 `aplay` 返回码。
9. 性能同时报告纯 NPU 与应用闭环，并记录测试时系统负载。
10. 不自动安装/升级板端软件、不创建 swap、不修改系统服务或 shell 启动文件。
11. 不删除 `_upstream`、原始失败日志或用户独立训练仓库。
12. README 只保留入口；详细现象、证据和边界持续追加到本文。
