# WebRTC 推流性能对比 — 手动测试指南

通过切换视频源和编码方式，对比三种管线在昇腾 310B 上的实际帧率、CPU 占用和画质。

## 测试环境

- **硬件**: Orange Pi AI Pro (Ascend 310B4) + Logitech C922 USB 摄像头
- **软件**: CANN 8.3.RC1 + Python 3.11 + aiortc 1.14
- **接收端**: Windows 浏览器（Chrome/Edge）

## 前置准备

在 310B 上先设置 CANN 环境：

```bash
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
```

## 三条管线

| 管线 | CPU 负担 | 硬件加速 | 瓶颈在哪里 |
|------|---------|---------|-----------|
| **A**: `demo` + libx264 | 合成帧 + 软编码 | 无 | ARM A55 软编 1080p |
| **B**: `usb_camera` + VENC | MJPEG 解码 + BGR→NV12 | VENC 硬编 | CPU 解码 + 色彩转换 |
| **C**: `dvpp_camera` + VENC | 仅 V4L2 取帧 | JPEGD 硬解 + VENC 硬编 | USB 带宽 |

## 测试步骤

### 管线 A — 基准（纯 CPU）

```bash
# 310B 上启动（不加 --hardware-encode）
python server.py --source demo --port 8080
```

1. 浏览器打开 `http://<310B-IP>:8080`
2. 选择 1920x1080，点击"开始接收"
3. 记录页面显示的帧率
4. 在 310B 上运行 `htop`，观察 Python 进程 CPU 占用

**预期**: 帧率取决于 ARM A55 软编 1080p 能力（通常 <10fps），CPU 接近 100%。

### 管线 B — OpenCV + 硬件编码

```bash
python server.py --source usb_camera --hardware-encode --port 8080
```

1. 浏览器同样选择 1920x1080，开始接收
2. 记录帧率
3. `htop` 观察 CPU

**预期**: 帧率 ~10-15fps（瓶颈在 OpenCV MJPEG→BGR + BGR→NV12），CPU 中等。

### 管线 C — 全硬件管线

```bash
python server.py --source dvpp_camera --port 8080
```

1. 浏览器选择 1920x1080，开始接收
2. 记录帧率
3. `htop` 观察 CPU

**预期**: 帧率 ~20-24fps（瓶颈仅 USB 带宽），CPU 很低。

### 额外测试 — 不同分辨率

对管线 C 分别测试 720p 和 1080p：

```bash
# 720p（需修改 _init_dvpp_camera 或直接用默认 720p 页面选项）
# 浏览器选择 1280x720
```

**预期**: 720p 可达更高帧率（USB 带宽压力更小，JPEG 码流更小）。

## 结果记录

| 管线 | 分辨率 | 浏览器帧率 | 310B CPU | 备注 |
|------|--------|----------|---------|------|
| A: demo + libx264 | 1920x1080 | | | |
| B: usb_camera + VENC | 1920x1080 | | | |
| C: dvpp_camera + VENC | 1920x1080 | | | |
| C: dvpp_camera + VENC | 1280x720 | | | |

## 结果解读

三条管线的差距反映了**硬件加速的逐级深入**：

```
管线 A:  [合成] → [CPU 软编]                                → H.264
管线 B:  [V4L2 MJPEG] → [CPU 解码→BGR] → [CPU NV12] → [VENC] → H.264
管线 C:  [V4L2 MJPEG] → [JPEGD] → [VENC]                     → H.264
```

管线 C 比 B 快的原因：
1. **省去 MJPEG→BGR CPU 解码** — 1080p 下约 15ms
2. **省去 BGR→NV12 CPU 转换** — 1080p 下约 5-8ms（两遍内存遍历 + I420 排列）
3. **DVPP 内部 NV12 留在 device 内存** — JPEGD 输出直接给 VENC，仅 MJPEG 码流走一次 H2D memcpy

> **关键认知**: USB 摄像头想要 1080p@30fps，必须同时满足两个条件——① USB 传输用 MJPEG（非 YUYV）；② MJPEG 解码走硬件（JPEGD，非 CPU）。管线 B 只解决了条件①，管线 C 两个都解决。

## 排查

如果管线 C 帧率异常低：

1. 确认摄像头插在 USB 3.0 口（蓝色）
2. `v4l2-ctl --list-formats-ext` 确认 MJPEG 1080p 列出 30fps
3. 查看 `logs/server.log`，确认 `Using direct V4L2 ioctl capture backend`
4. 查看 `logs/server.log`，`V4L2 capture stopped frames=NNN` 算帧率
5. 如果走的是 PyAV fallback（`Using PyAV V4L2 capture backend`），帧率会低 ~40%，可尝试重插摄像头触发 V4L2 raw 后端

## 与教程的对应关系

本对比直接用到教程中的三个 DVPP 模块：

| 模块 | 管线 | 教程 |
|------|------|------|
| VENC | B, C | [venc_guide.md](venc_guide.md) |
| JPEGD | C | [jpeg_guide.md](jpeg_guide.md) |
| DVPP 基础 | B, C | [dvpp_guide.md](dvpp_guide.md) |
