# Ascend 310B aiortc WebRTC Sender

## 这是什么

这是一个跑在昇腾 310B 上的 WebRTC 视频发送端。

它的目标很明确：

- 帧源在 310B 设备侧
- Python 用 `aiortc` 完成 WebRTC 会话和 RTP 发送
- 浏览器只负责接收和显示

当前媒体链路可以概括为：

`Ascend 310B frame source -> AscendVideoTrack -> aiortc -> RTP/WebRTC -> Browser`

HTTP `POST /offer` 只是信令，不在媒体路径里。

## 仓库现状

仓库当前围绕三条视频路径工作：

- `demo`
  合成演示帧，方便验证 WebRTC 信令和浏览器接收链路
- `usb_camera`
  V4L2 MJPEG 采集 -> CPU JPEG 解码 -> `rgb24` -> 软件编码
- `dvpp_camera`
  V4L2 MJPEG 采集 -> DVPP JPEGD -> `nv12` -> CANN VENC 硬件编码

编码格式支持：

- `h264`
  默认格式。可走纯 CPU，也可走 CANN VENC 硬编
- `h265`
  只走 CANN VENC 硬编，要求浏览器具备 WebRTC HEVC 接收能力

## 目录

- `server.py`
  服务入口，负责 HTTP、offer/answer、连接生命周期和编码器切换
- `webrtc_app/ascend_source.py`
  视频源适配层，核心类是 `AscendVideoTrack`
- `webrtc_app/cann_encoder.py`
  CANN VENC 封装，包含 `CannH264Encoder` 和 `CannH265Encoder`
- `webrtc_app/dvpp_jpegd.py`
  DVPP JPEGD 硬件解码
- `webrtc_app/hevc.py`
  H.265 RTP 分包
- `webrtc_app/v4l2_raw.py`
  直接 ioctl + mmap 的 V4L2 MJPEG 采集
- `webrtc_app/v4l2_capture.py`
  基于 PyAV 的 V4L2 MJPEG 采集
- `web/index.html`
  浏览器接收页
- `web/client.js`
  浏览器侧 WebRTC 协商与状态显示
- `test/`
  pytest 测试

## 环境要求

运行机必须是昇腾 310B。

开发机可以是 Windows、Linux 或 macOS，但浏览器接收效果、设备依赖和性能结论都以 310B 为准。

系统依赖：

```bash
sudo apt install v4l-utils
```

Python 环境：

```bash
conda activate npu
python -m pip install -r requirements.txt
```

如果要跑硬编路径，还需要先设置 CANN 环境变量：

```bash
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
```

## 快速开始

### 1. 启动纯 CPU 演示路径

```bash
python server.py --source demo --host 0.0.0.0 --port 8080
```

这条路径不需要摄像头，也不需要 CANN。

### 2. 启动纯 CPU 摄像头路径

```bash
python server.py --source usb_camera --host 0.0.0.0 --port 8080
```

这条路径是：

`V4L2 MJPEG -> CPU JPEG decode -> aiortc/libx264 -> Browser`

它适合拿来做“纯 CPU 对照组”。

### 3. 启动 H.264 硬编路径

```bash
python server.py --source dvpp_camera --video-codec h264 --host 0.0.0.0 --port 8080
```

`dvpp_camera` 会自动走：

`V4L2 MJPEG -> DVPP JPEGD -> NV12 -> CANN VENC H.264`

如果你想保留 `usb_camera` 输入，但只把编码切成 H.264 硬编，也可以：

```bash
python server.py --source usb_camera --hardware-encode --host 0.0.0.0 --port 8080
```

### 4. 启动 H.265 硬编路径

```bash
python server.py --source dvpp_camera --video-codec h265 --host 0.0.0.0 --port 8080
```

H.265 只走 CANN VENC，不存在 CPU H.265 编码路径。

## 浏览器怎么连

浏览器打开：

```text
http://<310b-ip>:8080
```

页面当前支持：

- 分辨率选择
- 帧率选择
- 目标码率下拉
  - `自动`
  - `500` 到 `6000 kbps`

页面是接收页，不采集本地摄像头。

## 目标码率怎么生效

页面上的“目标码率”是**建连参数**，不是运行中热调参数。

行为是：

1. 页面选择分辨率、帧率、目标码率
2. 页面发 `POST /offer`
3. 服务端按这次连接的参数创建编码器 / VENC 通道
4. 断开后重新选择码率，再次连接，会按新的码率重新建通道

也就是说：

- 留空或选 `自动`：服务端按分辨率、帧率、编码格式自动估算
- 选具体值：服务端按该值创建本次连接的 VENC

当前不支持在线热调码率。

## H.265 需要注意什么

H.265 的失败时机不是“服务端启动失败”，而是“浏览器发起 offer 时，如果浏览器没带 `video/H265` 能力，服务端返回 `HTTP 400`”。

也就是说：

- 服务端可以正常启动
- 浏览器不支持 HEVC 时，点击“开始接收”才会失败

浏览器页也会在本地先做一层 H.265 能力检查。

## 最近在 311 上的实测结果

下面这些数据来自 `311`（`orangepiaipro`）上 2026-05-26 的最新日志，不是理论值。

测试分成三类：

### 1. 纯 CPU

命令：

```bash
python server.py --source usb_camera --host 0.0.0.0 --port 8080
```

路径：

`V4L2 MJPEG -> CPU JPEG decode -> RGB -> libx264`

日志特征：

- `Configured USB camera source=usb-camera ... CPU_DECODE->RGB`
- `USB camera decode frame=... decode_ms=20~32`
- `Track FPS: 14.2 / 14.6 / 15.3 / 14.9 / 15.0 / 15.1`

结论：

- `1920x1080@60` 请求下，实际只有大约 `15 fps`
- 主要瓶颈是 CPU JPEG 解码

### 2. H.264 硬编

命令：

```bash
python server.py --source dvpp_camera --video-codec h264 --host 0.0.0.0 --port 8080
```

路径：

`V4L2 MJPEG -> DVPP JPEGD -> NV12 -> CANN VENC H.264`

代表日志：

- `DVPP decode frame=... decode_ms=4.9~9.5`
- `VENC encode frame=... encode_ms=6.3~6.5`
- `Track FPS: 59.7 / 60.2 / 60.0 / 59.9 ...`

结论：

- `1920x1080@60` 下基本可以稳定在 `60 fps`
- 目前是这套链路里最稳的配置

### 3. H.265 硬编

命令：

```bash
python server.py --source dvpp_camera --video-codec h265 --host 0.0.0.0 --port 8080
```

路径：

`V4L2 MJPEG -> DVPP JPEGD -> NV12 -> CANN VENC H.265`

代表日志：

- `DVPP decode frame=... decode_ms=4.9~9.2`
- `H265 VENC encode frame=... encode_ms=6.2~6.6`
- 稳定样本：`Track FPS` 大约 `57~59`
- 异常样本：个别运行段会掉到 `35 fps`

结论：

- H.265 的码流更小
- 但按这次 311 的日志，整体稳定性不如 H.264
- 如果目标是“1080p60 稳定推流”，当前优先选 H.264

### 汇总表

| 模式 | 路径 | 311 上实测表现 |
|------|------|----------------|
| 纯 CPU | `usb_camera` + `libx264` | 约 `15 fps` |
| H.264 硬编 | `dvpp_camera` + CANN VENC | 基本稳定 `60 fps` |
| H.265 硬编 | `dvpp_camera` + CANN VENC | 通常 `57~59 fps`，个别样本掉到 `35 fps` |

当前这台 `311` 的结论很直接：

`H.264 硬编 > H.265 硬编 >>> 纯 CPU`

## 为什么浏览器会显示 1920x1088

这是硬件编码对齐，不是画面真的变成了 `1088` 高。

原因是：

- `1080` 不能被 `16` 整除
- VENC 常把编码面高度补到下一个对齐值，也就是 `1088`
- 多出来的 `8` 行是 padding，不是有效画面

所以：

- 摄像头源仍然是 `1920x1080`
- 编码面可能显示为 `1920x1088`
- 浏览器有时会把 coded size 显示出来

## 验证建议

如果你要复现实验，建议按下面顺序跑：

1. 纯 CPU

```bash
python server.py --source usb_camera --host 0.0.0.0 --port 8080
```

2. H.264 硬编

```bash
python server.py --source dvpp_camera --video-codec h264 --host 0.0.0.0 --port 8080
```

3. H.265 硬编

```bash
python server.py --source dvpp_camera --video-codec h265 --host 0.0.0.0 --port 8080
```

每次都看：

- 浏览器页里的 `接收码率` 和 `视频帧率`
- `logs/server.log`
- `Track FPS`
- `decode_ms`
- `encode_ms`
- 关闭时的 `frames / dropped`

## 常见问题

### `v4l2-ctl --list-devices` 提示权限不足

通常是当前用户没有访问 `/dev/video*` 的权限。

```bash
sudo usermod -aG video <你的用户名>
newgrp video
```

然后重新登录终端。

### 如何确认摄像头的原始 MJPEG 输出没问题

```bash
v4l2-ctl -d /dev/video0 \
  --set-fmt-video=width=1920,height=1080,pixelformat=MJPG \
  --set-parm=60 \
  --stream-mmap \
  --stream-count=300 \
  --stream-to=/dev/null
```

如果这里就跑不到目标帧率，问题先不在 WebRTC。

### 怎么判断自己是不是走了纯 CPU

如果是纯 CPU，日志里应当看到：

- `source=usb_camera`
- `CPU_DECODE->RGB`
- `USB camera decode frame=...`

而不应该看到：

- `H264 encoder switched to CANN VENC hardware`
- `H265 codec registered and switched to CANN VENC hardware`
- `CANN VENC channel created`

## 代码怎么接入真实 310B 输出

最应该改的文件是：

- [webrtc_app/ascend_source.py](./webrtc_app/ascend_source.py)

建议保持边界：

1. 在 `AscendVideoTrack` 里新增 source 分支
2. 用 `_init_xxx()` 初始化真实输入
3. 在 `recv()` 中产出 `av.VideoFrame`
4. 尽量输出 `nv12`，方便 VENC 直通
5. 不要把设备专属逻辑散到 `server.py` 和浏览器代码里

## 当前实现里最关键的文件

### `server.py`

- 管 HTTP 路由
- 管 `POST /offer`
- 管 `RTCPeerConnection`
- 管 H.264 / H.265 编码器注册
- 管旧连接清理

### `webrtc_app/ascend_source.py`

- 管 demo / usb_camera / dvpp_camera 三条帧源路径
- 管 `recv()` 输出帧
- 管帧率节奏和状态回传

### `webrtc_app/cann_encoder.py`

- 管 CANN VENC 通道
- 管 H.264 / H.265 编码器适配
- 管自动码率估算和手动目标码率覆盖

### `web/client.js`

- 管浏览器端 `recvonly` 协商
- 管 H.265 能力检查
- 管分辨率 / 帧率 / 目标码率建连参数
- 管接收码率和帧率显示

## 当前依赖

- `aiohttp`
- `aiortc`
- `av`
- `numpy`
- `v4l-utils`
- `pytest`
- `CANN 8.3`

## 一句话建议

如果你现在只是想在 310B 上稳定地把 1080p60 推到浏览器，先用：

```bash
python server.py --source dvpp_camera --video-codec h264 --host 0.0.0.0 --port 8080
```
