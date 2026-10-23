# 昇腾 310B 外设配置与验收记录

本文汇总本仓库已经在 Orange Pi AI Pro / Ascend 310B1（20T）和 Ascend 310B4（8T）上记录的摄像头、麦克风、板载音频和 USB 声卡设置。它是工程记录，不替代每次板端重新枚举和实测。

结论边界：

- 设备节点存在、`lsusb` 能看到设备、`aplay -l`/`arecord -l` 能列出设备，只证明枚举成功。
- 真实摄像头取帧、麦克风录音电平、扬声器听音或回环录音，必须分别留下证据。
- 8T 和 20T 的设备号和音频能力不同，不能把一块板的结果直接复制给另一块。
- 不把 `Health: Alarm` 当作摄像头或音频失败的直接原因；按具体设备错误排查。

## 1. 外设接口事实

| 项目 | 文档记录的硬件/接口事实 | 来源 |
| --- | --- | --- |
| 摄像头接口 | Orange Pi AI Pro 提供两个 MIPI-CSI，兼容树莓派 IMX219；USB 摄像头为可选输入 | [`appendix1.md`](../src/appendix/appendix1.md) |
| 摄像头权限 | 310B 系统常见 V4L2 节点权限为 `root:video`、`0660` | [`case8/README.md`](../samples/case8/README.md) |
| 音频权限 | 部分镜像中 `HwHiAiUser` 不在 `audio` 组，必须按当前枚举复核 | [`case9/troubleshooting.md`](../samples/case9/docs/troubleshooting.md) |
| 板载音频 | ALSA 声卡名 `ascend310b`，3.5mm 使用 `Deviceid 2`；稳定验证为单声道 | [`case3/06-audio-output.md`](../samples/case3/docs/06-audio-output.md) |
| USB 音频 | 已知 USB 喇叭分别以稳定 card ID 或 USB ID 出现，不能固定 `hw:1,0` | [`case3/06-audio-output.md`](../samples/case3/docs/06-audio-output.md) |

MIPI-CSI 的接口存在不等于当前系统已经完成 IMX219 驱动、设备树、曝光和取帧验收。当前仓库的实测记录主要是 USB UVC 摄像头；MIPI-CSI 必须单独验证。

## 2. 摄像头设置

### 2.1 加入 video 组

如果目标节点是 `root:video`、权限为 `0660`，而 `HwHiAiUser` 不在 `video` 组：

```bash
id
ls -l /dev/video*
sudo usermod -aG video HwHiAiUser
```

`sudo usermod` 是管理员写操作，不应由调试探针自动执行。执行后必须：

1. 退出当前 SSH/桌面会话并重新登录；
2. 拔下并重新插入 USB 摄像头，让 UVC 会话重新初始化；
3. 再次确认：

```bash
id
ls -l /dev/video*
v4l2-ctl --list-devices
```

`id` 输出必须包含 `video`。不要用 `chmod 666` 替代正确的用户组和权限管理。

### 2.2 确认真正的视频节点

`/dev/videoN` 会随上电和插拔顺序变化；`vin-media` 之类 ISP 虚拟节点也可能出现在列表中，但不一定能出帧。

```bash
v4l2-ctl --list-devices
for d in /dev/video*; do
  echo "--- $d"
  v4l2-ctl -d "$d" --info
  v4l2-ctl -d "$d" --get-fmt-video
  v4l2-ctl -d "$d" --list-formats-ext
done
```

选择实际摄像头时，至少确认：

- driver 为预期摄像头驱动（USB 常见 `uvcvideo`）；
- 有实际宽高和像素格式；
- 能读取非空首帧；
- 不是 `0x0`、格式为空的 ISP/metadata 节点。

### 2.3 本仓库已记录的节点差异

| 案例/板卡 | 文档记录的节点 | 项目设置 |
| --- | --- | --- |
| case1 / 8T | USB 摄像头 `/dev/video2`、`/dev/video3` | `CASE1_CAMERA_INDEX=2` |
| case1 / 20T | USB 摄像头 `/dev/video0`、`/dev/video1` | `CASE1_CAMERA_INDEX=0` |
| case4 / 8T | 2026-10-18 复测 USB 为 `/dev/video0`、`/dev/video1`；ISP 为 `/dev/video2`、`/dev/video3` | `PALMPRINT_CAMERA_DEVICE=/dev/videoN` |
| case8 / 8T | USB 摄像头 `/dev/video2` | `--source /dev/video2` |
| case8 / 20T | USB 摄像头 `/dev/video0` | `--source /dev/video0` |

case4 的 8T 在另一次运行中曾出现相反顺序：USB 在 `/dev/video2`、ISP 在 `/dev/video0`。因此**设备号不是稳定硬件标识**，每次上电或插拔后都要重新确认。参考：

- [`case1/README.md`](../samples/case1/README.md)
- [`case4 8T 摄像头复测`](case-board-runs-20261018/case4/README.md)
- [`case8/README.md`](../samples/case8/README.md)

### 2.4 摄像头的采集合同

- 优先使用摄像头原生支持的 `camera-profile`，例如 `1280x720@30`。
- USB 摄像头支持 MJPG 时优先尝试 MJPG；不是所有摄像头或 OpenCV 构建都支持。
- 记录实际 FourCC、分辨率、帧率和首帧耗时，不只看 `VideoCapture.isOpened()`。
- case2 使用 `--source` 选择索引或视频文件，并可用 `--camera-profile`、`--camera-mjpeg`。
- case1 使用 `CASE1_CAMERA_INDEX`，不要写死 `/dev/video0`。
- case4 使用 `PALMPRINT_CAMERA_DEVICE`，不要固定设备号。
- case8 使用显式 `--source /dev/videoN`，并单独验证 VENC/WebRTC 层。

## 3. 麦克风设置

### 3.1 加入 audio 组

先检查真实权限：

```bash
id
ls -l /dev/snd
arecord -l
```

如果设备属于 `root:audio`、权限为 `0660`，而 `HwHiAiUser` 不在 `audio` 组：

```bash
sudo usermod -aG audio HwHiAiUser
```

然后退出并重新登录，使用以下命令复核：

```bash
id
id -nG
arecord -l
```

当前文档记录：某些镜像中 `HwHiAiUser` 未显示在 `audio` 组。不要让服务以 root 身份绕过权限；按当前设备权限修正用户组并重新登录。

### 3.2 设备枚举和真实录音

```bash
cat /proc/asound/cards
arecord -l
arecord -L
pactl list short sources 2>/dev/null || true
```

真实录音测试会写文件，必须显式执行：

```bash
arecord -D plughw:<card>,<device> -f S16_LE -r 48000 -c 1 -d 5 /tmp/mic-test.wav
file /tmp/mic-test.wav
soxi /tmp/mic-test.wav 2>/dev/null || ffprobe -v error -show_format -show_streams /tmp/mic-test.wav
sox /tmp/mic-test.wav -n stat 2>&1
```

验收条件：

- WAV 文件头中的采样率、位深和声道数与命令一致；
- 电平确实随说话变化，不是全零、恒定静音或持续削波；
- 应用选择的是实体 capture source，不是 `.monitor`；
- 设备断开后应用不得静默切换到另一个麦克风。

### 3.3 已记录的兼容性问题

- `SN0002 1080P USB Camera`（USB ID `2bdf:0289`）的视频 UVC 可枚举，但 UAC 麦克风在 20T 和 8T 上都只收到约 22 个样本后报 `Input/output error`。记录处理是：该摄像头只用于视频，麦克风改用已验证的外接 USB 麦克风或 3.5mm 输入。
- case3 的 8T 对照中，UGREEN 摄像头的麦克风可以完成 16 kHz 立体声采集；这不能推导出 SN0002 也可用。
- 摄像头视频正常不代表它的 UAC 麦克风正常。必须分别测试 V4L2 和 ALSA。
- PulseAudio monitor 是输出回放监视源，不是麦克风；case3 的 DDSP-VST 明确拒绝 monitor。

参考：

- [`case9/troubleshooting.md`](../samples/case9/docs/troubleshooting.md)
- [`case3/README.md`](../samples/case3/README.md)
- [`case3/docs/02-webui.md`](../samples/case3/docs/02-webui.md)

## 4. 喇叭/音频输出设置

### 4.1 板载 3.5mm

板载 ALSA 声卡为 `ascend310b`，3.5mm 路由由 `Deviceid 2` 选择。文档实测结论是：单声道稳定，双声道离线播放可能出现强噪声，实时双声道还可能阻塞写入和停止。

只读检查：

```bash
cat /proc/asound/cards
aplay -l
amixer get Playback
amixer get Deviceid
```

在明确允许修改混音器时：

```bash
amixer set Playback 10
amixer set Deviceid 2
aplay -Dhw:ascend310b /opt/opi_test/audio/tianlu.wav
```

`Playback 10` 在当前驱动中约为低音量；不要首次就开到最大。需要可靠双声道输出时应使用外接 USB 声卡，不把板载单声道短测结果推广为立体声能力。

### 4.2 USB 声卡/USB 喇叭

先确认 USB 和 ALSA 同时识别：

```bash
lsusb
aplay -l
aplay -L
cat /proc/asound/cards
pactl get-default-sink 2>/dev/null || true
```

优先使用稳定的 card ID：

```bash
aplay -Dplughw:CARD=<card-id>,DEV=0 test.wav
```

不要固定 `hw:1,0`。文档记录的设备包括：

- EDIFIER M25 USB 喇叭，USB ID `2d99:a036`；播放前同时核对 `lsusb` 和 `aplay -l`。
- EDIFIER M16 Pro，正常枚举后优先使用 `plughw:CARD=Pro,DEV=0`，不固定 `hw:1,0`。

### 4.3 首次音量安全线

首次播放测试从 **25% 音量**开始，只验证“有没有声音”，不要默认 100%。如果 25% 能听到就停止；只有用户明确要求时才逐步升到 35% 或 50%。25% 无声时先检查默认 sink、`-D`、mute、声道和物理接口，不要直接跳到满音量。

```bash
pactl set-sink-volume @DEFAULT_SINK@ 25%
pactl set-sink-mute @DEFAULT_SINK@ 0
speaker-test -D plughw:<card>,<device> -c 2 -t sine -f 440 -l 1
```

`aplay` 退出码为 0 不等于已经听到声音；需要人耳、回环录音或外部采集作为声音输出证据。

参考：

- [`case3/docs/06-audio-output.md`](../samples/case3/docs/06-audio-output.md)
- [`case3/docs/02-webui.md`](../samples/case3/docs/02-webui.md)

## 5. apt 系统程序安装

公版系统在用户没有修改默认密码时，可以通过 agent 使用 `HwHiAiUser` 的 sudo 权限安装板端系统程序。插件使用显式工具，不开放任意 root shell：

```text
ascend_apt_install packages=["v4l-utils", "alsa-utils"] dryRun=true allowStateChange=true
```

确认 dry-run 的目标和包名后，将 `dryRun` 改为 `false`。工具只执行固定的：

```text
apt-get update
apt-get install -y --no-install-recommends <validated-packages>
```

约束：

- 包名只允许 apt 合法字符，一次最多 24 个；
- 仅通过 SSH 目标执行；
- sudo 密码来自本机 profile 的 `sudoPassword`，只写入 SSH stdin，不进入命令行、日志或 skill；
- 必须显式 `allowStateChange=true`；
- 安装前后记录包名、命令、退出码和 stdout/stderr；
- 不把 apt 安装和摄像头/麦克风真实采集混为一个证据门。

## 6. 一键检查顺序

```text
1. id / id -nG
2. lsusb
3. v4l2-ctl --list-devices
4. arecord -l / aplay -l
5. cat /proc/asound/cards
6. 选择真实视频节点和音频 card/device
7. 真实取帧、录音、播放
8. 记录证据；失败项单独标记
```

推荐先运行插件的 `ascend_probe`，它会把 `soundCards`、`audioPlayback`、`audioCapture`、`audioSinks`、`usbDevices` 和 `videoDevices` 列入环境报告；设备枚举成功之后，再按上述流程做真实采集/播放。

## 7. 变更记录

| 日期 | 记录 |
| --- | --- |
| 2026-10-22 | 汇总 case1/case3/case4/case8/case9 的摄像头、麦克风、板载音频和 USB 声卡设置；加入 25% 首测音量安全线 |
