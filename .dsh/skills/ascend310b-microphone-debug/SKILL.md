---
name: ascend310b-microphone-debug
description: Debug microphone capture on Ascend 310B1/310B4 boards through ALSA/PulseAudio enumeration, hardware capability checks, real WAV capture, and level verification.
---

# 昇腾 310B 麦克风调试

## 适用范围

用于 Ascend 310B1 / 310B4 开发板上的板载麦克风、USB 麦克风、模拟麦克风和其他 ALSA capture device。设备出现在 `arecord -l` 中只说明驱动枚举成功，不等于录音、增益和信噪比已经通过。

## 本仓库已记录的板端设置

完整记录见插件包内 `docs/ascend310b-peripheral-setup.md`。

- 部分镜像中 `HwHiAiUser` 不在 `audio` 组。若设备为 `root:audio`、`0660`，管理员执行 `sudo usermod -aG audio HwHiAiUser`，退出并重新登录，再用 `id -nG` 和 `arecord -l` 复核。
- 不要让服务以 root 绕过权限；也不要把 PulseAudio `.monitor` 当作麦克风。
- `SN0002 1080P USB Camera`（USB ID `2bdf:0289`）的视频 UVC 可枚举，但其 UAC 麦克风在 20T 和 8T 上都曾在约 22 个样本后报 `Input/output error`。该型号只用于视频，语音输入改用验证过的外接 USB 麦克风或 3.5mm 输入。
- case3 的 8T 对照使用 UGREEN 摄像头麦克风完成过 16 kHz 立体声采集；这不能证明 SN0002 可用。case3 的 DDSP-VST 固定使用实体 capture，不使用 monitor。
- 先记录 `arecord -l`、`pactl list short sources`、`arecord --dump-hw-params`，再做真实 WAV 采集和 SoX 电平统计。

## 证据阶梯

1. **声卡枚举**：`/proc/asound/cards` 能看到声卡。
2. **录音设备**：`arecord -l` 能看到 capture device。
3. **硬件能力**：采样率、通道、格式由 `--dump-hw-params` 报告。
4. **实际采集**：能生成非空 WAV，文件头参数与命令一致。
5. **信号质量**：有真实输入电平，不是全零、恒定底噪或削波。
6. **应用链路**：实时语音、ASR、唤醒词或服务取得的 PCM 合同与采集端一致。

## 只读枚举

```bash
cat /proc/asound/cards
arecord -l
arecord -L
pactl list short sources 2>/dev/null || true
```

查看硬件能力：

```bash
arecord -D plughw:0,0 --dump-hw-params -d 1 /dev/null
```

把实际的 `card`、`device`、采样率、通道数和格式记录下来。不要用任意 `hw:0,0` 假设所有板卡布局完全相同。

## 真实录音

采集会写入文件，必须通过 `ascend_board_run` 显式执行，并选择任务允许的输出路径：

```bash
arecord -D plughw:0,0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/mic-test.wav
```

检查文件：

```bash
file /tmp/mic-test.wav
soxi /tmp/mic-test.wav 2>/dev/null || ffprobe -v error -show_format -show_streams /tmp/mic-test.wav
```

检查电平：

```bash
sox /tmp/mic-test.wav -n stat 2>&1
```

如果系统提供实时 VU 表，可先做短时测试：

```bash
arecord -D plughw:0,0 -f S16_LE -r 48000 -c 1 -d 3 -V mono /tmp/mic-vu.wav
```

输出文件只是采集证据；没有人工听测、电平统计或识别链路验证时，不能宣称语音质量合格。

## PulseAudio / PipeWire 环境

如果应用使用 PulseAudio/PipeWire，先查看默认 source：

```bash
pactl get-default-source
pactl list short sources
pactl get-source-mute @DEFAULT_SOURCE@
pactl get-source-volume @DEFAULT_SOURCE@
```

`pactl` 不可用不代表 ALSA 不可用；先确定应用实际走 ALSA 直连还是音频服务。

## 常见失败定位

| 现象 | 先查 | 处理 |
| --- | --- | --- |
| 没有 capture device | `arecord -l`、`lsusb`、`dmesg` | 修驱动、线缆或 USB 枚举 |
| device busy | 录音进程、桌面音频服务 | 释放占用，不强行覆盖 |
| `Invalid sample rate` | `--dump-hw-params` | 改用设备支持的采样率或 `plughw` |
| WAV 为空/很小 | `-d`、设备索引、输入权限 | 检查命令和采集路径 |
| 全零波形 | mute、增益、输入选择、麦克风偏置 | 检查 `amixer`/`pactl` 设置 |
| 有底噪但无语音 | 输入源、插孔、增益 | 换输入路径做对照 |
| 失真/削波 | 增益和声压 | 降低 capture gain 后复测 |
| 应用收到的 PCM 合同错误 | 通道、采样率、S16_LE/float | 对齐应用合同，不重装 CANN |

## 停止条件

- `arecord -l` 没有设备：停在 ALSA/硬件枚举层。
- 有设备但无法生成 WAV：停在采集合同或权限层。
- 有 WAV 但电平全零：停在路由、mute、增益或输入选择层。
- 采集和电平通过、ASR/模型失败：进入应用或模型层，保留麦克风已通过的证据。

## 参考

- ALSA `arecord`/`aplay` manual: <https://manpages.debian.org/bookworm/alsa-utils/arecord.1.en.html>
- ALSA project: <https://www.alsa-project.org/wiki/>
- PulseAudio `pactl`: <https://manpages.debian.org/bookworm/pulseaudio-utils/pactl.1.en.html>
- SoX statistics: <https://sox.sourceforge.net/sox.html>
