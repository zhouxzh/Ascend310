# WebRTC 服务与媒体合同

## 服务入口

入口是 `samples/chapter6/WebRTC/server.py`。启动命令：

```bash
cd samples/chapter6/WebRTC
python server.py \
  --source demo \
  --video-codec h264 \
  --host 0.0.0.0 \
  --port 8080
```

CLI 参数及环境变量默认值：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--host` | `WEBRTC_HOST` 或 `0.0.0.0` | HTTP 监听地址 |
| `--port` | `WEBRTC_PORT` 或 `8080` | HTTP 监听端口 |
| `--log-level` | `WEBRTC_LOG_LEVEL` 或 `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `--log-file` | `WEBRTC_LOG_FILE` 或 `logs/server.log` | 轮转日志，单文件 2 MiB，保留 5 个备份 |
| `--source` | `WEBRTC_SOURCE` 或 `demo` | `demo`、`usb_camera`、`dvpp_camera` |
| `--camera-device` | `WEBCAM_DEVICE` 或 `0` | 数字索引或 `/dev/videoN` 路径 |
| `--hardware-encode` | 环境变量开关 | 保留 USB 输入，但强制 H.264 CANN VENC |
| `--video-codec` | `WEBRTC_VIDEO_CODEC` 或 `h264` | `h264` 或 `h265` |

浏览器打开 `http://<板端地址>:<port>/`。页面只接收视频，不采集浏览器摄像头。

## HTTP 合同

| 方法与路径 | 行为 |
|---|---|
| `GET /` | 返回 `web/index.html`，禁用缓存 |
| `GET /client.js` | 返回浏览器协商脚本，禁用缓存 |
| `GET /styles.css` | 返回样式文件，禁用缓存 |
| `GET /health` | 返回状态、目标运行时、默认视频源和视频 codec |
| `POST /offer` | 接收 SDP offer 和本次连接的视频参数，返回 SDP answer |

`POST /offer` 请求体至少包含：

```json
{
  "sdp": "<browser offer sdp>",
  "type": "offer",
  "width": 1280,
  "height": 720,
  "fps": 30,
  "bitrate_kbps": null
}
```

`width`、`height`、`fps` 必须是正整数。`bitrate_kbps` 为空、`"0"` 或 `0` 时使用自动估算；显式值会夹在 500 到 6000 kbps。自动码率与分辨率、帧率和 codec 有关，H.265 估算值低于 H.264。

成功回答包含 `sdp`、`type` 和 `source_settings`。`source_settings.requested` 是请求参数，`source_settings.applied` 是摄像头成功打开后可报告的实际尺寸、帧率、源模式、码率和码率模式。摄像头驱动可能改变实际采集尺寸。

非法 JSON 字段、非正尺寸或非正码率返回 HTTP 400。创建回答时的其他异常会先清理连接，再返回 HTTP 500，并要求检查 `logs/server.log`。

## 视频源合同

### `demo`

生成确定性的渐变、移动条和方块 RGB 帧，不需要摄像头或 CANN。该模式适合信令、ICE、浏览器接收和重新连接 smoke，不代表 DVPP 或 NPU 路径。

### `usb_camera`

通过 PyAV 打开 V4L2 MJPEG 设备。采集线程读取原始 JPEG 包，队列满时丢弃最旧帧以保持低延迟。`ascend_source.py` 将每个 JPEG 包在 CPU 上解码为 RGB。

默认 H.264 模式下，aiortc 可使用软件编码；加 `--hardware-encode` 后必须用 CANN VENC H.264。该路径是摄像头进入、CPU 解码的对照路径，不要称为全硬件链路。

### `dvpp_camera`

要求直接 V4L2 ioctl/mmap 后端和 CANN JPEGD：

```text
V4L2 MJPEG -> DVPP JPEGD -> NV12 -> CANN VENC -> aiortc RTP -> browser
```

`v4l2_raw.py` 假定 aarch64 Linux 的 V4L2 结构体布局。相机打开后，实际宽高会覆盖请求值。JPEGD 首帧查询宽高并建立 stride 对齐的输出缓冲区。如果相机、直接 V4L2、CANN 或 JPEGD 初始化失败，连接失败并释放已打开资源。

摄像头读取失败会抛出 `MediaStreamError`，不会回退到合成帧。这是有意的 fail-closed 行为。每次新 offer 会先关闭旧 PeerConnection 和摄像头，以释放 `/dev/video0`。

## 编码与 H.265 合同

硬件路径分别把 aiortc 的 H.264 编码器工厂替换为 `CannH264Encoder`，或注册 H.265 codec 工厂 `CannH265Encoder`。两者都调用 `CannVenc`：

- NV12 输入直接进入 VENC；
- RGB 输入先由 PyAV 转成 NV12；
- 码率单位为 kbps；
- 输出按 Annex-B 字节流读取；
- H.264 复用 aiortc 的 H.264 RTP 分包；
- H.265 使用本章 `hevc.py` 按 RFC 7798 做单 NAL 或 fragmentation unit 分包。

H.265 模式在启动时注册 `video/H265` 能力。浏览器 offer 必须包含 `H265/90000`，页面也必须通过 `RTCRtpReceiver.getCapabilities("video")` 暴露 H.265；否则页面在本地停止，或服务端返回 HTTP 400。H.265 没有 CPU 软件回退，CANN/VENC 失败会终止所选路径。

H.264 硬件模式同样没有软件回退。`--hardware-encode` 或 `dvpp_camera` 无法导入 CANN 时，服务在开始监听前失败。

VENC 在尺寸、帧率或码率变化时重建通道。当前码率是建连参数，不支持运行中热调；改变码率需要断开并重新发起 offer。

## V4L2 输出与生命周期

两种采集器都维护小队列并在满队列时丢旧帧，避免端到端延迟无界增长。`v4l2_raw.py` 在 `stop()` 中关闭流、线程、映射和文件描述符；`v4l2_capture.py` 关闭 PyAV container，并等待采集线程退出。

浏览器端在关闭页面、点击停止或开始新连接时关闭接收 track、PeerConnection、统计定时器和视频帧回调。服务端在 PeerConnection 失败、断开、关闭或进程 shutdown 时停止 source track 并关闭连接。

## 依赖与启动环境

`requirements.txt` 和 `environment.yml` 声明：

- Python 3.9.25；
- `aiohttp==3.10.10`；
- `aiortc==1.9.0`；
- `av==12.3.0`；
- `numpy==1.26.4`。

这些版本与统一 `ascend` 环境的 20T 快照一致。它们是当前技术路线的已验证包版本，不等于已完成摄像头、VENC、ICE 或浏览器接收验收。

摄像头枚举和权限诊断还需要 `v4l-utils`。CANN 属于板端系统环境，不是 pip 依赖。`environment.yml` 仅用于说明依赖，板端应使用已有的 `ascend` 环境，不从该文件重建或覆盖环境。启动脚本必须同时设置 `PYTHONNOUSERSITE=1` 并 source CANN 的 `set_env.sh`。

## 代码边界

- `server.py` 负责 HTTP、offer/answer、codec 选择和连接生命周期，不应吸收摄像头或 VENC 实现。
- `webrtc_app/ascend_source.py` 是帧源适配边界。
- `cann_encoder.py` 集中 CANN ACL/VENC 适配和 bitrate 策略。
- `dvpp_jpegd.py` 集中 JPEGD 缓冲区、stride 和同步。
- `v4l2_raw.py`、`v4l2_capture.py` 只负责采集和释放设备。
- `web/client.js` 只负责浏览器 offer、codec preference、接收和统计。
