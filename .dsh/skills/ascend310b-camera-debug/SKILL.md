---
name: ascend310b-camera-debug
description: Debug camera capture on Ascend 310B1/310B4 boards by separating V4L2 enumeration, permissions, formats, real frame capture, and optional DVPP/AI processing.
---

# 昇腾 310B 摄像头调试

## 适用范围

用于 Ascend 310B1 / 310B4 开发板上的 USB 摄像头、MIPI CSI 摄像头和 V4L2 视频节点。摄像头通过 V4L2 采集，不等于已经走通 DVPP 或 NPU 推理；采集、视频处理和模型推理必须分别记录证据。

## 本仓库已记录的板端设置

完整记录见插件包内 `docs/peripheral-setup.md`。

- 目标系统常见 V4L2 节点为 `root:video`、`0660`。若 `HwHiAiUser` 不在 `video` 组，管理员执行 `sudo usermod -aG video HwHiAiUser`，然后退出并重新登录，再拔插 USB 摄像头。
- 重新登录后检查 `id` 包含 `video`，再执行 `ls -l /dev/video*`、`v4l2-ctl --list-devices`。不要用 `chmod 666` 绕过权限。
- `/dev/videoN` 不稳定：case1 8T 使用 `/dev/video2`，20T 使用 `/dev/video0`；case8 同板实测也曾分别使用 `video2` 和 `video0`；case4 的 8T 在两次运行中出现过相反编号。
- case1 设置 `CASE1_CAMERA_INDEX=2`（8T）或 `0`（20T）；case4 设置 `PALMPRINT_CAMERA_DEVICE=/dev/videoN`；case8 使用 `--source /dev/videoN`；case2 使用 `--source <索引或文件>`、`--camera-profile` 和 `--camera-mjpeg`。
- MIPI-CSI 接口存在不等于 IMX219 已完成驱动和设备树验收；不要把 USB UVC 的结果推广到 MIPI-CSI。

## 证据阶梯

1. **枚举**：`/dev/video*` 和 `v4l2-ctl --list-devices` 能看到设备。
2. **权限**：当前用户能打开设备，进程没有被其他程序占用。
3. **格式**：驱动报告的分辨率、像素格式、帧率和实际协商值一致。
4. **采集**：能读到至少一帧非空图像，并记录实际宽高。
5. **连续流**：连续读取时帧数、超时、帧率和丢帧可解释。
6. **处理链**：只有采集稳定后，才接入 OpenCV、DVPP、编码或 NPU 推理。

前一层失败时不要把原因归到 CANN 或模型。

## 只读枚举

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --info
v4l2-ctl -d /dev/video0 --list-formats-ext
v4l2-ctl -d /dev/video0 --get-fmt-video
ls -l /dev/video*
id
```

同时查看 USB 拓扑：

```bash
lsusb
```

如果设备节点存在但 `open` 返回 `EBUSY`，先检查是否有预览进程占用；不要立即重新插拔或安装驱动。

## 真实采集

优先使用插件工具 `ascend_camera_capture`：格式、分辨率和 FPS 来自板端 `v4l2-ctl -d <device> --list-formats-ext` 的实际输出，不使用猜测表。先 `dryRun: true` 核对设备与参数，再显式 `allowStateChange: true` 采集。工具固定使用 `ascend` 环境读取非空帧，优先 OpenCV；`cv2` 不可用时回退到 ffmpeg 抓帧和 Pillow 图像统计。产物位于 `/tmp/ascend310b-debug/peripherals`，并返回实际尺寸、FourCC、FPS/采集后端、JPEG、SHA-256 和图像统计。两条路径都失败时停在采集层，不进入 DVPP/NPU 结论。

需要手工复现时，通过 `ascend_board_run` 显式执行，不要把它当成只读命令：

```bash
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=1 --stream-to=/tmp/camera-frame.raw
```

更推荐先让驱动选择 MJPG 或 YUYV 的明确格式，再采集：

```bash
v4l2-ctl -d /dev/video0 --set-fmt-video=width=640,height=480,pixelformat=MJPG
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=1 --stream-to=/tmp/camera-frame.mjpg
```

`--set-fmt-video` 会改变设备当前格式，必须得到用户的任务授权；记录命令、实际格式和输出文件，不要只记录“命令成功”。

Python/OpenCV 验收至少检查：

```python
import cv2
cap = cv2.VideoCapture("/dev/video0")
assert cap.isOpened()
ok, frame = cap.read()
assert ok and frame is not None
print(frame.shape)
cap.release()
```

`VideoCapture.isOpened()` 只证明初始化；`read()` 返回非空帧才是采集证据。记录后端、分辨率、帧率和输入管线。

## 常见失败定位

| 现象 | 先查 | 处理 |
| --- | --- | --- |
| 没有 `/dev/video*` | `lsusb`、`dmesg`、USB 供电和线缆 | 先修设备/driver，不查 CANN |
| 有节点但打不开 | `id`、节点权限、占用进程 | 修权限或释放占用，不用 root 绕过问题 |
| 能打开但没有格式 | `--list-formats-ext` | 选择驱动实际支持的分辨率和格式 |
| 单帧成功、连续流失败 | buffer、帧率、CPU 拷贝 | 记录丢帧和超时，调整采集参数 |
| 画面全黑/绿屏 | 格式、曝光、镜头盖、YUYV/MJPG 解析 | 先用 `v4l2-ctl` 或参考查看器验证 |
| 低分辨率流畅、高分辨率卡顿 | USB 带宽、MJPG、拷贝路径 | 分别测试格式和分辨率，不归因于 NPU |
| 采集成功但推理失败 | 模型输入 shape/dtype、预处理 | 进入 runtime/精度 skill，不重做摄像头枚举 |

## 停止条件

- 枚举和权限都没有通过：停在 device 层。
- 有设备但不能读出非空帧：停在 V4L2/capture 层。
- 采集成功但 NPU/DVPP 失败：停在对应处理层，保留摄像头已通过的证据。
- 不要把“设备列表里有摄像头”写成“摄像头采集和 NPU 推理已通过”。

## 参考

- Linux V4L2 userspace API: <https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/v4l2.html>
- `v4l2-ctl` 手册: <https://manpages.debian.org/bookworm/v4l-utils/v4l2-ctl.1.en.html>
- OpenCV `VideoCapture`: <https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html>
- GStreamer `v4l2src`: <https://gstreamer.freedesktop.org/documentation/video4linux2/v4l2src.html>
