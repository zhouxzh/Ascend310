# Ascend 310B 音频输出：3.5mm 与 USB 声卡

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

以下步骤已在 Orange Pi AI Pro 20T（Ascend 310B）系统镜像上实测。板载 ALSA
声卡名称为 `ascend310b`，3.5mm 耳机接口使用 `Deviceid 2` 路由。当前板载
3.5mm 接口只完成了单声道稳定播放验证；需要双声道输出时，建议使用外接 USB
声卡。

## 板载 3.5mm：检查声卡和当前路由

```bash
cat /proc/asound/cards
aplay -l
amixer get Playback
amixer get Deviceid
```

正常情况下可以看到播放设备 `hw:0,0`，也可以使用名称
`hw:ascend310b` 访问同一设备。启用 3.5mm 接口并播放 WAV：

```bash
amixer set Playback 10
amixer set Deviceid 2
aplay -Dhw:ascend310b audio.wav
```

`Playback 10` 在当前驱动中约为 8% 音量。音源本身较小时可以适当提高，例如：

```bash
amixer set Playback 30
```

`hw:ascend310b` 直接把 WAV 数据交给硬件，不进行格式转换；
`plughw:ascend310b` 会经过 ALSA plug 层，在硬件不接受输入格式时尝试转换。
播放 WAV 时，`aplay` 会读取文件头中的采样率、位深和声道数。命令行中的
`-f S16_LE -r 48000` 不能用于把双声道 WAV 转换为单声道。

终端出现以下内容表示播放已经开始：

```text
Playing WAVE 'audio.wav' : Signed 16 bit Little Endian, Rate 48000 Hz, Mono
```

使用 `Ctrl+C` 停止时出现 `Aborted by signal Interrupt` 是正常的手动中断，
不是声卡驱动错误。

## 系统镜像自带的耳机测试样例

系统镜像在 `/opt/opi_test/audio/play_headset.sh` 中提供了 3.5mm 接口测试脚本，
内容为：

```bash
amixer set Playback 10
amixer set Deviceid 2
aplay -Dhw:ascend310b -f S16_LE -r 48000 -t wav \
  /opt/opi_test/audio/tianlu.wav
```

也可以直接执行：

```bash
bash /opt/opi_test/audio/play_headset.sh
```

镜像自带的 `tianlu.wav` 格式如下：

```text
RIFF/WAVE、PCM、16-bit、48000 Hz、单声道、无压缩
```

可以用以下命令检查任意测试文件：

```bash
file /opt/opi_test/audio/tianlu.wav
file ~/Documents/case3/midi_wav/*.wav
```

## 双声道播放实测问题

本次测试对同一首曲子制作了音量接近的单声道和双声道版本：

| 文件 | 格式 | 3.5mm 接口实测结果 |
| :--- | :--- | :--- |
| `/opt/opi_test/audio/tianlu.wav` | 48 kHz、16-bit、单声道 | 正常 |
| `midi_wav/ode-to-joy-violin-mono-loud.wav` | 48 kHz、16-bit、单声道 | 正常 |
| `midi_wav/ode-to-joy-violin-stereo-loud.wav` | 48 kHz、16-bit、双声道 | 能听到音乐，但伴随很强的静电/数字噪声 |

ALSA 查询结果显示硬件接口声明支持 1 到 2 个声道：

```text
FORMAT: S16_LE
CHANNELS: [1 2]
RATE: [8000 48000]
```

双声道播放期间实际参数为：

```text
access: RW_INTERLEAVED
format: S16_LE
channels: 2
rate: 48000
state: RUNNING
```

单声道和双声道的静音流测试都返回 0；双声道 WAV 播放也返回 0，DMA 缓冲
持续推进，并且内核日志中没有发现 underrun、xrun、I2S 或 DMA 错误。因此不能
简单断言 ALSA 驱动“只支持单声道”。当前证据表明，问题更可能出现在 ALSA
成功送出数据之后的双声道 codec、I2S 时隙或 `Deviceid 2` 模拟输出路由中。
系统镜像自带的耳机测试也只使用单声道 WAV，没有覆盖双声道实际听感。

本地播放正常、上传前后 MD5 一致、双声道文件没有数字削波，因此该现象不是
采样率错误、文件传输损坏或 WAV 自身削波造成的。若只有双声道出现强噪声，
优先按板端立体声输出链路问题处理；如果单声道也有噪声，再检查电源、耳机接地、
插头接触和 CTIA/OMTP 接线兼容性。

## 外接 USB 声卡播放双声道

### Meizu HiFi DAC

本次使用 Type-C 接口连接了 Meizu HiFi DAC Headphone Amplifier，系统识别信息为：

```text
USB ID: 2a45:0128
ALSA card ID: Amplif
ALSA device: card 1, device 0
功能: USB Audio playback
```

使用以下命令检查 USB 声卡是否已经同时被 USB 子系统和 ALSA 识别：

```bash
lsusb
cat /proc/asound/cards
aplay -l
arecord -l
ls -l /dev/snd
```

本次正常识别时，`lsusb` 和 `aplay -l` 中分别出现：

```text
ID 2a45:0128 Meizu Corp. Meizu HiFi DAC Headphone Amplifier
card 1: Amplif [Meizu HiFi DAC Headphone Amplif], device 0: USB Audio
```

该设备是播放型 USB DAC，本次没有注册对应的录音设备。声卡编号 `card 1` 可能在
重启或重新插拔后变化，因此优先使用 ALSA card ID，而不是固定数字编号：

```bash
cd ~/Documents/case3/midi_wav
aplay -Dplughw:CARD=Amplif,DEV=0 ode-to-joy-violin-stereo-loud.wav
```

`plughw` 会在 USB DAC 不直接接受源文件格式时通过 ALSA plug 层转换。确认文件
格式与设备原生参数一致后，也可以直接访问当前硬件编号：

```bash
aplay -Dhw:1,0 ode-to-joy-violin-stereo-loud.wav
```

`amixer set Deviceid 2` 和板载 `Playback` 控件只用于 `ascend310b` 的 3.5mm
输出路由，不用于选择或配置 USB 声卡。播放 USB 声卡时，应通过 `-D` 参数明确
指定 USB ALSA 设备。

### 漫步者 M25 USB 喇叭

2026-07-21 在 `ascend8t2` 上新增漫步者 M25 USB 喇叭。系统已经同时通过 USB、
ALSA 和 PulseAudio 识别该设备：

```text
产品: 漫步者 M25（用户提供的设备型号）
USB ID: 2d99:a036
USB 描述: Jieli Technology USB Composite Device
ALSA card ID: Device
ALSA device: card 1, device 0, USB Audio
PulseAudio sink: alsa_output.usb-Jieli_Technology_USB_Composite_Device_1120051103080204-00.analog-stereo
PulseAudio 格式: s16le, 2 channels, 48000 Hz
```

系统显示 `Jieli Technology USB Composite Device` 是 USB 音频芯片或固件上报的
名称，不一定显示外壳上的“漫步者 M25”产品名。判断是否为同一个设备时，应结合
插拔前后的 `lsusb`、`aplay -l` 和 USB ID `2d99:a036`，不能只看描述字符串。

该设备提供名为 `PCM` 的双声道硬件播放音量控件：

```text
Playback channels: Front Left - Front Right
Limits: Playback 0 - 147
检测时状态: 44/147，30%，-20.16 dB，左右声道均为 on
```

查看控件和当前音量：

```bash
amixer -c Device scontrols
amixer -c Device get PCM
```

使用稳定的 ALSA card ID 设置音量并播放：

```bash
cd ~/Documents/case3/midi_wav

# 建议先从 30% 开始，确认声音正常后再提高
amixer -c Device set PCM 30% unmute

aplay -Dplughw:CARD=Device,DEV=0 \
  ode-to-joy-violin-stereo-loud.wav
```

播放期间可以在另一个终端直接调整硬件音量：

```bash
amixer -c Device set PCM 40%
amixer -c Device set PCM 50%
amixer -c Device set PCM mute
amixer -c Device set PCM unmute
```

`card 1` 可能在重启或重新插拔后变化，因此优先使用 `-c Device` 和
`CARD=Device`，不要在脚本中固定 `hw:1,0`。`plughw` 可以在 WAV 格式与喇叭
硬件参数不完全一致时执行必要的 ALSA 格式转换。

当前 PulseAudio 默认输出也已经指向该 USB 喇叭。需要让多个应用共享设备时，
可以通过 PulseAudio 设置音量并播放：

```bash
pactl list short sinks
pactl set-sink-volume @DEFAULT_SINK@ 50%
aplay -D pulse \
  ~/Documents/case3/midi_wav/ode-to-joy-violin-stereo-loud.wav
```

如果默认输出后来发生变化，可以显式设置 M25 对应的 sink：

```bash
pactl set-default-sink \
  alsa_output.usb-Jieli_Technology_USB_Composite_Device_1120051103080204-00.analog-stereo
```

直接使用 `plughw:CARD=Device,DEV=0` 时由 ALSA `PCM` 控件调节硬件音量；通过
`-D pulse` 播放时还会叠加 PulseAudio sink 音量。排查音量过低时应同时检查两层，
但不要一开始就将两层都设为 100%。板载声卡的 `Playback` 和 `Deviceid` 控件不
作用于 M25。

本轮已经确认 M25 的枚举、48 kHz 双声道 PulseAudio sink、硬件音量控制和
PortAudio 双声道实时传输。首次实时 DDSP 无声还包含软件输出过低的问题：原始波形
峰值只有 `-32.5 dBFS`，程序又错误地优先选择了单声道。修复后使用双声道和
旧版本的 `--output-gain-db 24` 测试把峰值提高到 `-8.5 dBFS` 且没有削波；当前
DDSP-VST 插件语义改为 `-60..0 dB`，该正增益只作为历史诊断记录。短 MIDI 测试中
`underruns=0`、`overruns=0`。实际听音反馈仍待现场确认。

### 漫步者 M16 Pro USB 喇叭

2026-07-23 在重装系统后的 `ascend8t` 上测试漫步者 M16 Pro USB 喇叭。仓库位于
板端 `~/Documents/case3`。本节只做 USB/ALSA 枚举和播放验证，不安装软件，不修改
系统配置。

先确认远程板卡连接正常：

```bash
ssh ascend8t "hostname; uname -a; whoami; pwd"
```

本次返回的主机和用户信息为：

```text
orangepiaipro
Linux orangepiaipro 5.10.0+ ... aarch64 GNU/Linux
HwHiAiUser
/home/HwHiAiUser
```

插入 USB 喇叭后，检查 USB 设备、ALSA 声卡和播放设备：

```bash
lsusb
cat /proc/asound/cards
aplay -l
aplay -L | sed -n '1,120p'
```

本次关键识别结果如下：

```text
Bus 007 Device 002: ID 2d99:a020 EDIFIER EDIFIER M16 Pro

1 [Pro            ]: USB-Audio - EDIFIER M16 Pro
                     EDIFIER EDIFIER M16 Pro at usb-xhci-hcd.3.auto-1, full speed

card 1: Pro [EDIFIER M16 Pro], device 0: USB Audio [USB Audio]

plughw:CARD=Pro,DEV=0
    EDIFIER M16 Pro, USB Audio
```

如果需要确认内核驱动和最近的插入日志，可以查看：

```bash
lsmod | grep -E 'snd|usb_audio'
dmesg | grep -iE 'usb|snd|audio|alsa|edifier' | tail -n 80
```

正常情况下能看到 `snd_usb_audio` 已加载，并且 `dmesg` 中包含
`EDIFIER M16 Pro`、`idVendor=2d99`、`idProduct=a020` 和
`usbcore: registered new interface driver snd-usb-audio`。

查看 USB 喇叭的硬件音量控件：

```bash
amixer -c Pro scontrols
amixer -c Pro sget PCM
```

本次状态为：

```text
Simple mixer control 'PCM',0
  Capabilities: pvolume pvolume-joined pswitch pswitch-joined
  Playback channels: Mono
  Limits: Playback 0 - 255
  Mono: Playback 192 [75%] [on]
```

如果未出声，优先确认 `PCM` 不是 `off` 或过低音量，例如：

```bash
amixer -c Pro set PCM 75% unmute
```

最小播放验证使用 `speaker-test`，设备名优先使用稳定的 ALSA card ID：

```bash
speaker-test -D plughw:CARD=Pro,DEV=0 -c 2 -t wav -l 1
```

正常输出会包含：

```text
Playback device is plughw:CARD=Pro,DEV=0
Stream parameters are 48000Hz, S16_LE, 2 channels
0 - Front Left
1 - Front Right
```

本次 `speaker-test` 没有返回 ALSA 设备错误，现场听音确认喇叭可以通过 USB 接口
出声。后续需要播放 case3 的 WAV 素材时，可以使用同一个 ALSA 设备名：

```bash
cd ~/Documents/case3/midi_wav
aplay -Dplughw:CARD=Pro,DEV=0 ode-to-joy-violin-stereo-loud.wav
```

`card 1` 在重启或重新插拔后可能变化，因此脚本中优先使用
`plughw:CARD=Pro,DEV=0`，不要固定 `hw:1,0`。板载声卡的 `Playback` 和
`Deviceid` 控件不作用于该 USB 喇叭。

## 蓝牙音频：A2DP 听歌与 HFP 录音

蓝牙音频链路分成两层：`bluetoothctl` 负责扫描、配对、信任和连接；PulseAudio
负责把已连接的蓝牙设备暴露成 `bluez_card`、`bluez_sink` 和 `bluez_source`，并
切换 A2DP/HFP 配置文件。纯命令行操作比较复杂，而且不同型号的蓝牙耳机、蓝牙
喇叭和蓝牙麦克风可能需要不同的配对流程、配置文件名称或音频 profile 切换方式。
对初学者最友好的方法是给开发板连接显示器和触摸屏，在图形化界面中完成蓝牙配对
和音频输出/输入选择；确认设备能正常工作后，再按需整理成命令行流程。日常使用
可以优先通过 `blueman-manager` 和 `pavucontrol` 完成连接与模式切换。

> 注意 MAC 地址格式差异：`bluetoothctl` 使用冒号分隔，例如
> `84:26:7A:6C:EB:FC`；`pactl` 的 PulseAudio 对象名使用下划线分隔，例如
> `84_26_7A_6C_EB_FC`。两者不能混用。

### 首次扫描、配对和连接

先让蓝牙音箱或蓝牙麦克风进入配对模式，然后进入蓝牙控制台：

```bash
sudo bluetoothctl
```

在 `bluetoothctl` 交互界面中执行：

```text
power on
agent on
scan on
```

看到目标设备的 MAC 地址后执行以下命令。下面以 `84:26:7A:6C:EB:FC` 为例，实际
使用时替换为扫描得到的地址：

```text
pair 84:26:7A:6C:EB:FC
trust 84:26:7A:6C:EB:FC
connect 84:26:7A:6C:EB:FC
quit
```

连接后可以检查 PulseAudio 是否识别到蓝牙声卡：

```bash
pactl list short cards
pactl list short sinks
pactl list short sources
```

### A2DP 与 HFP 模式切换

A2DP 是高音质播放模式，适合听歌，但通常不提供麦克风输入。HFP/HSP 是免提模式，
会暴露麦克风输入，适合录音或通话，但播放和录音音质都明显低于 A2DP。

切换到 HFP 免提模式，准备录音：

```bash
pactl set-card-profile bluez_card.84_26_7A_6C_EB_FC handsfree_head_unit
```

切换到 A2DP 高音质模式，准备听歌：

```bash
pactl set-card-profile bluez_card.84_26_7A_6C_EB_FC a2dp_sink
```

如果 `pactl list short sources` 查不到 `bluez_source`，通常是当前还在 A2DP 模式。
先切到 `handsfree_head_unit`，蓝牙麦克风输入才会被系统加载。

### 音量控制

查看和设置 A2DP 播放音量：

```bash
pactl get-sink-volume bluez_sink.84_26_7A_6C_EB_FC.a2dp_sink
pactl set-sink-volume bluez_sink.84_26_7A_6C_EB_FC.a2dp_sink 80%
pactl set-sink-volume bluez_sink.84_26_7A_6C_EB_FC.a2dp_sink +10%
pactl set-sink-volume bluez_sink.84_26_7A_6C_EB_FC.a2dp_sink -10%
pactl set-sink-mute bluez_sink.84_26_7A_6C_EB_FC.a2dp_sink toggle
```

查看和设置 HFP 麦克风音量：

```bash
pactl get-source-volume bluez_source.84_26_7A_6C_EB_FC.handsfree_head_unit
pactl set-source-volume bluez_source.84_26_7A_6C_EB_FC.handsfree_head_unit 100%
pactl set-source-volume bluez_source.84_26_7A_6C_EB_FC.handsfree_head_unit +20%
pactl set-source-mute bluez_source.84_26_7A_6C_EB_FC.handsfree_head_unit toggle
```

### 蓝牙麦克风录音流水线

使用蓝牙麦克风录音时，先切到 HFP，录完再切回 A2DP：

```bash
# 1. 切到免提模式
pactl set-card-profile bluez_card.84_26_7A_6C_EB_FC handsfree_head_unit

# 2. 提高麦克风增益
pactl set-source-volume bluez_source.84_26_7A_6C_EB_FC.handsfree_head_unit 100%

# 3. 录制 5 秒。这里强制走 PulseAudio，不直接访问底层 ALSA 设备
arecord -D pulse -d 5 -r 8000 -f S16_LE -c 1 my_record.wav

# 4. 播放确认
aplay -D pulse my_record.wav

# 5. 录音结束后切回高音质播放模式
pactl set-card-profile bluez_card.84_26_7A_6C_EB_FC a2dp_sink
```

如果 `parecord` 报 `No such entity`，优先改用 `arecord -D pulse ...`。该命令仍然
经过 PulseAudio，只是使用 ALSA 的 `pulse` 插件入口，通常能绕过 `parecord` 对
source 名称或默认源状态的兼容问题。

双声道增强版 `ode-to-joy-violin-stereo-loud.wav` 已通过该 USB DAC 实测，可以
正常播放音乐，没有板载 3.5mm 接口上出现的强静电/数字噪声。当前
音频输出路径的验收结果如下：

| 输出路径 | 单声道 | 双声道 |
| :--- | :--- | :--- |
| 板载 3.5mm，`Deviceid 2` | 已测试，正常 | 能播放，但伴随很强的噪声，暂不建议使用 |
| 外接 USB DAC，`CARD=Amplif` | 可通过 `plughw` 转换播放 | 已测试，正常 |
| 漫步者 M25，`CARD=Device` | 非零信号和 ALSA 路径已验证，待听音确认 | 48 kHz 双声道实时传输已验证，待听音确认 |
| 漫步者 M16 Pro，`CARD=Pro` | `speaker-test` 已听音确认 | 48 kHz 双声道 `speaker-test` 已听音确认 |

当前 case3 使用板载 3.5mm 接口时，稳定输出格式建议为：

```text
RIFF/WAVE、PCM、16-bit、48000 Hz、单声道
```

推荐测试命令：

```bash
cd ~/Documents/case3/midi_wav
amixer set Playback 10
amixer set Deviceid 2
aplay -Dhw:ascend310b ode-to-joy-violin-mono-loud.wav
```

在后续驱动或系统镜像确认双声道模拟输出正常之前，实时 DDSP 播放和离线 WAV
导出如果使用板载 3.5mm 接口，应优先使用单声道，避免仅根据 `aplay` 返回 0
判断耳机端音质正常。需要保留立体声效果时，使用已经验证通过的外接 USB 声卡。
