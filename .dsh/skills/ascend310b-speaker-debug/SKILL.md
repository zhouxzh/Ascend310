---
name: ascend310b-speaker-debug
description: Debug speaker and headphone playback on Ascend 310B1/310B4 boards through ALSA/PulseAudio enumeration, sink routing, volume checks, and a real audible test.
---

# 昇腾 310B 喇叭调试

## 适用范围

用于 Ascend 310B1 / 310B4 开发板上的板载喇叭、耳机、HDMI 音频和 USB 声卡。`aplay -l` 能看到 playback device 只说明枚举成功；必须继续确认默认 sink、静音/音量、PCM 格式和实际声音输出。

## 本仓库已记录的板端设置

完整记录见插件包内 `docs/peripheral-setup.md`。

- 板载 ALSA 声卡名为 `ascend310b`，3.5mm 使用 `Deviceid 2`。当前记录只稳定验证了单声道；双声道离线播放可能出现强噪声，实时双声道可能阻塞写入。可靠双声道应使用外接 USB 声卡。
- 音频用户组按现场设备权限检查；若节点为 `root:audio`、`0660` 且 `HwHiAiUser` 不在 `audio` 组，管理员执行 `sudo usermod -aG audio HwHiAiUser`，退出并重新登录后复核。
- USB 喇叭不要固定 `hw:1,0`。优先使用 `aplay -L` 中的稳定 card ID，例如 `plughw:CARD=<card-id>,DEV=0`；文档记录过 EDIFIER M25（USB ID `2d99:a036`）和 EDIFIER M16 Pro（`CARD=Pro`）。
- 首次测试从 **25% 音量**开始，只判断是否有声音；25% 无声时检查默认 sink、`-D`、mute、声道和接口，禁止直接开到 100%。
- `aplay` 成功返回不等于听到声音；必须有人耳听音、回环录音或外部采集作为输出证据。

## 证据阶梯

1. **声卡枚举**：`/proc/asound/cards` 能看到声卡。
2. **播放设备**：`aplay -l` 或 `aplay -L` 能看到 playback PCM。
3. **路由**：默认 sink 或显式 `-D` 设备指向目标输出。
4. **格式**：采样率、通道和 WAV 格式与硬件能力兼容。
5. **真实播放**：播放命令退出码为 0，且有人耳或录音回环证据。
6. **应用链路**：应用输出音量、PCM 合同和播放设备与测试命令一致。

## 只读枚举

```bash
cat /proc/asound/cards
aplay -l
aplay -L
pactl get-default-sink 2>/dev/null || true
pactl list short sinks 2>/dev/null || true
```

PulseAudio/PipeWire 路由检查：

```bash
pactl get-sink-mute @DEFAULT_SINK@
pactl get-sink-volume @DEFAULT_SINK@
```

ALSA 混音器检查：

```bash
amixer scontrols
amixer sget Master
```

先记录目标设备是板载模拟、HDMI 还是 USB，不要假设默认 sink 就是用户想要的喇叭。

## 真实播放

扬声器和麦克风可以独立测试，不存在强制先后顺序。优先使用插件工具 `ascend_speaker_test`：先 `dryRun: true`，再由用户显式 `allowStateChange: true`。工具在 `/tmp/ascend310b-debug/peripherals` 生成低幅度正弦 WAV，通过 ALSA `aplay` 或 Pulse `paplay` 播放，不修改系统音量，返回设备、路由和哈希；`heard=null`，必须由操作员确认是否实际听到。扬声器确认不是麦克风录音的前置条件；麦克风录音完成后仍可用 `ascend_play_recording` 通过当前输出设备回放录音。

蓝牙喇叭/耳机先在系统侧完成 BlueZ 配对/连接，并确认 Pulse card profile（A2DP 或 HFP/HSP），再选择 `audioSinks` 中的 `bluez_sink.*` 或 Pulse 默认 sink。命令退出码为 0 不等于人耳听音通过。

需要手工复现时，通过 `ascend_board_run` 显式运行，使用板端已有 WAV 或任务允许生成的测试音：

```bash
aplay -D plughw:0,0 /usr/share/sounds/alsa/Front_Center.wav
```

也可以使用音频工具产生测试音；不要把生成文件写入仓库：

```bash
speaker-test -D plughw:0,0 -c 2 -t sine -f 440 -l 1
```

如果要验证左右声道：

```bash
speaker-test -D plughw:0,0 -c 2 -t sine -f 440 -l 1
```

使用 `aplay -l` 报告的 card/device 替换 `plughw:0,0`。播放命令成功不等于声音一定到达目标扬声器；需要记录人耳听测、录音回环或外部采集结果。

## 音量和静音

只读检查：

```bash
amixer sget Master
pactl get-sink-volume @DEFAULT_SINK@
pactl get-sink-mute @DEFAULT_SINK@
```

首次播放测试必须从 **25% 音量**开始，只用于判断“有没有声音”，不要默认 100%，也不要用最大音量做故障诊断。修改音量或取消静音会改变系统状态，必须在用户明确要求下进行，并记录修改前后的值：

```bash
amixer sset Master 25%
pactl set-sink-volume @DEFAULT_SINK@ 25%
pactl set-sink-mute @DEFAULT_SINK@ 0
```

先播放短促的测试音或已有 WAV：

```bash
aplay -D plughw:0,0 /usr/share/sounds/alsa/Front_Center.wav
speaker-test -D plughw:0,0 -c 2 -t sine -f 440 -l 1
```

如果 25% 已经能听到，停止；只有用户明确要求继续验证响度时，才逐步升到 35% 或 50%。如果 25% 无声，不要直接跳到 100%，应按顺序检查默认 sink、`-D`、mute、声道、物理接口和外部扬声器。不要用最大音量做首次测试，避免突发满幅声和硬件损坏。

## 常见失败定位

| 现象 | 先查 | 处理 |
| --- | --- | --- |
| 没有 playback device | `aplay -l`、`lsusb`、`dmesg` | 修驱动或硬件枚举 |
| 有设备但无声 | 默认 sink、`-D`、mute、音量 | 先修路由和静音 |
| `Device or resource busy` | 播放进程、桌面音频服务 | 释放占用，不并发抢占 |
| `Invalid sample rate` | `aplay --dump-hw-params`、WAV 头 | 对齐采样率和格式 |
| 有噪声无音乐 | WAV 编码、通道、增益 | 检查文件和路由 |
| HDMI 无声 | HDMI sink、显示器音频、EDID | 切到正确 HDMI/模拟输出 |
| 应用无声但 `aplay` 有声 | 应用 sink/PCM、音量、容器 | 进入应用音频层，不重装 CANN |
| 只有单声道/一侧响 | channel map、接口、扬声器 | 记录声道映射并复测 |

## 停止条件

- 没有 playback device：停在 ALSA/硬件枚举层。
- 有设备但无声音：停在路由、mute、音量或物理输出层。
- `aplay` 有声、应用无声：停在应用音频合同层。
- 不要把“命令退出码为 0”写成“喇叭发声已通过”；必须有人耳、回环或外部录音证据。

## 参考

- ALSA `aplay`/`arecord` manual: <https://manpages.debian.org/bookworm/alsa-utils/aplay.1.en.html>
- `speaker-test`: <https://manpages.debian.org/bookworm/alsa-utils/speaker-test.1.en.html>
- PulseAudio `pactl`: <https://manpages.debian.org/bookworm/pulseaudio-utils/pactl.1.en.html>
- ALSA project: <https://www.alsa-project.org/wiki/>
