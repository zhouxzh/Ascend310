# Ascend 310B aiortc WebRTC Sender

## 仓库定位

这个仓库只面向昇腾 310B 运行。

- Windows 可以作为编辑代码、同步代码的开发机，也可以运行 `client.py` 作为 WebRTC 接收端。
- Python 服务、媒体链路验证、浏览器联调、依赖假设和部署行为，都应以昇腾 310B 设备为准。
- 仓库默认视频源是 [webrtc_app/ascend_source.py](./webrtc_app/ascend_source.py) 里的 `AscendVideoTrack`，支持三种模式：
  - **demo** — 合成演示帧，确保 `aiortc` 和浏览器侧 WebRTC 接收链路始终可验证。
  - **usb_camera** — 通过 OpenCV/V4L2 接入真实 USB 摄像头，CPU 解码 MJPEG + CPU BGR→NV12 转换。
  - **dvpp_camera** — V4L2 MJPEG 直采 → DVPP JPEGD 硬件解码 → DVPP VENC 硬件编码，全硬件管线。

当前媒体链路：

`Ascend 310B 设备帧源 -> AscendVideoTrack -> aiortc -> aiohttp signaling -> Browser`

## 目录结构

- `server.py`
  Python WebRTC 服务入口，负责 HTTP 路由、offer/answer 协商、日志和连接关闭。支持 `--source demo|usb_camera|dvpp_camera` 切换视频源。
- `webrtc_app/ascend_source.py`
  Ascend 视频源适配层。`AscendVideoTrack` 支持 demo、USB 摄像头和 DVPP 硬件管线三种模式。
- `webrtc_app/cann_encoder.py`
  CANN VENC 硬件 H.264 编码器。替代 aiortc 的 libx264 软件编码，支持 NV12 直通（跳过 CPU 色彩转换）。
- `webrtc_app/dvpp_jpegd.py`
  DVPP JPEGD 硬件解码器。将 MJPEG 码流硬件解码为 NV12，供 VENC 直接编码。
- `webrtc_app/v4l2_capture.py`
  基于 PyAV 的 V4L2 MJPEG 采集模块。
- `webrtc_app/v4l2_raw.py`
  直接 ioctl + mmap 的 V4L2 MJPEG 采集模块（更高帧率，自动优先选用）。
- `web/index.html` / `web/client.js` / `web/styles.css`
  浏览器接收页面、WebRTC 协商逻辑和样式。
- `test/`
  pytest 测试套件（`test_nv12.py`、`test_cann_venc.py`），在 310B 上运行验证。
- `sync.ps1`
  Windows ↔ 310B 代码同步脚本。`push` 推送，`pull` 拉取日志，`-Watch` 自动监听。
- `AGENTS.md`
  仓库级协作准则，已明确本项目是 Ascend 310B first。

## 运行方式

运行机必须是昇腾 310B。

### 设备端安装与启动

```bash
conda activate mediapipe  # 或其他含 Python 3.11 的环境
python -m pip install -r requirements.txt
```

先设置 CANN 环境变量（硬件编码必需）：

```bash
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
```

启动 demo 模式（合成帧，CPU 编码）：

```bash
python server.py --host 0.0.0.0 --port 8080
```

启动 USB 摄像头 + OpenCV（CPU 解码 + CPU BGR→NV12 + 硬件编码）：

```bash
python server.py --source usb_camera --hardware-encode --host 0.0.0.0 --port 8080
```

启动 USB 摄像头 + DVPP 全硬件管线（推荐，最高性能，硬件编码自动启用）：

```bash
python server.py --source dvpp_camera --host 0.0.0.0 --port 8080
```

三种模式对比：

| 模式 | `--hardware-encode` | 相机采集 | MJPEG 解码 | 颜色转换 | H.264 编码 | 典型帧率 |
|------|---------------------|---------|-----------|---------|-----------|---------|
| `demo` | 可选 | 无（合成） | — | — | libx264 / VENC | ~30fps |
| `usb_camera` | 可选 | OpenCV | CPU | CPU BGR→NV12 | libx264 / VENC | ~15fps |
| `dvpp_camera` | **自动启用** | V4L2 直采 | DVPP JPEGD | 无 | CANN VENC | ~24fps |

> `dvpp_camera` 模式下硬件编码自动启用（NV12 帧必须由 VENC 编码）。`demo` 和 `usb_camera` 不加 `--hardware-encode` 则走 CPU libx264。

指定摄像头设备：

```bash
python server.py --source dvpp_camera --camera-device /dev/video1
```

启动后会打印浏览器访问 URL。

### 浏览器接收（跨平台）

```text
http://<ascend-310b-ip>:8080
```

页面支持选择分辨率、帧率，显示 PeerConnection 状态、ICE 状态、接收码率和实时帧率。

### 验证步骤

1. 在 310B 上启动 `server.py`。
2. 从浏览器访问设备地址，确认 `/health` 返回 `ok`。
3. 在浏览器页面选择分辨率和帧率，点击”开始接收”。
4. 观察远端视频、PeerConnection 状态、ICE 状态、接收码率和帧率。
5. 查看设备上的 `logs/server.log`，确认没有 offer 处理异常。
6. 在 310B 上运行 `pytest test/ -v` 验证 VENC 编码器和 NV12 转换。
7. 运行 `python test/test_dvpp_pipeline.py` 验证 DVPP 全硬件管线端到端。

## aiortc 基础

### aiortc 是什么

`aiortc` 是 Python 的 WebRTC 实现库，基于 `asyncio`。它让 Python 代码可以直接扮演 WebRTC 对等端，而不是只能把媒体交给浏览器处理。

在这个仓库里，`aiortc` 不是辅助库，而是发送端核心：

- 它创建 `RTCPeerConnection`。
- 它接收浏览器发来的 SDP offer。
- 它在 Python 侧挂载视频轨道。
- 它生成 SDP answer。
- 它负责 ICE、DTLS、SRTP 和 RTP 发送。

### 这个仓库里最关键的 aiortc 类

- `RTCPeerConnection`
  负责整个 WebRTC 会话对象。代码里在 [server.py](./server.py) 的 `offer()` 中创建。
- `RTCSessionDescription`
  用来承载浏览器发来的 offer 和 Python 返回的 answer。
- `MediaStreamTrack`
  WebRTC 里的媒体轨道抽象。代码里的 `AscendVideoTrack` 继承它，并通过 `recv()` 一帧一帧向 `aiortc` 提供视频。

### 为什么这里用 aiortc

这个仓库的目标不是“浏览器本地采集”，而是“昇腾 310B 设备端掌握帧源，再通过 WebRTC 发给浏览器”。这正是 `aiortc` 的适用点：

- 帧在 Python 里生成或接入。
- 浏览器只负责接收和显示。
- 设备侧媒体源可以替换，信令和浏览器逻辑基本不需要重写。

## WebRTC 基础

### WebRTC 里什么是标准，什么不是标准

WebRTC 标准化了媒体协商和传输能力，但没有强制规定你必须用什么信令协议。

这个仓库采用的是最小化信令方式：

- 浏览器通过 HTTP `POST /offer` 发送 offer。
- Python 服务返回 answer。
- 没有引入 WebSocket。
- 没有引入 TURN。
- 当前也没有额外的 trickle ICE 流程。

### 本仓库涉及到的几个核心概念

- `SDP`
  会话描述文本，里面包含编解码能力、媒体方向、ICE 信息等。浏览器创建 offer，Python 创建 answer。
- `ICE`
  Interactive Connectivity Establishment，用来找浏览器和设备之间真正可用的网络路径。
- `DTLS / SRTP`
  WebRTC 媒体传输的安全层。这里虽然代码没手写这些协议，但它们由 WebRTC 栈自动参与。
- `Track / Transceiver`
  浏览器在 [web/client.js](./web/client.js) 里添加 `recvonly` video transceiver，表示“我只接收视频，不负责采集或发送视频”。
- `RTP`
  真正承载视频包的传输层。`aiortc` 会把 `MediaStreamTrack.recv()` 产出的帧编码后通过 RTP 送给浏览器。

### VP8 和 H264 有什么不同

这两个都是视频编码格式，但设计目标和工程侧重点不同。

- `VP8`
  开放、免版税，WebRTC 生态里支持很常见。对纯软件链路比较友好，但在很多嵌入式设备、NPU 板卡或专用媒体硬件上，未必有和 `H264` 一样成熟的硬编码、硬解码和工具链支持。
- `H264`
  工业使用更广，浏览器、摄像头、编码芯片、流媒体设备和 SoC 支持通常更成熟。对“设备侧先编码，再通过 WebRTC 发送”的路径更常见，但它涉及专利和授权生态，工程上通常需要更明确地确认平台支持方式。

如果只看这个仓库的目标，即“昇腾 310B 设备产出视频，再送到浏览器”，`H264` 往往比 `VP8` 更值得优先考虑，因为：

- 更容易和现有硬件编码链路对齐。
- 更容易和设备侧已有的媒体输出格式衔接。
- 浏览器兼容和 WebRTC 互通通常更成熟。

但这不等于当前仓库已经在用 310B 的 `H264` 硬编码。当前版本还没有做到这一点。

### 当前仓库实际走的编码路径

按当前环境里的 `aiortc 1.14.0`，视频编码能力只包含 `VP8` 和 `H264`。

**默认 CPU 编码路径**（`--source` 不带 `--hardware-encode`）：

- `VP8` 编码走 `libvpx`
- `H264` 编码走 `libx264`

路径：`AscendVideoTrack -> av.VideoFrame -> aiortc libx264 encoder -> RTP -> Browser`

**昇腾硬件编码路径**（`python server.py --hardware-encode`）：

- 通过 [cann_encoder.py](webrtc_app/cann_encoder.py) 中 `CannH264Encoder` 替换 aiortc 的 `H264Encoder`
- 调用 CANN ACL VENC API，使用昇腾 310B 片载 H.264 硬编码器

硬件编码需设置 CANN 环境变量：

```bash
export LD_LIBRARY_PATH=”/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH”
export PYTHONPATH=”/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH”
```

两种硬件编码管线的区别：

| 管线 | 路径 | 帧率 |
|------|------|------|
| `usb_camera` | OpenCV MJPEG→BGR (CPU) → BGR→NV12 (CPU) → VENC (硬件) → H.264 | ~15fps |
| `dvpp_camera` | V4L2 MJPEG 直采 (CPU) → JPEGD (硬件) → NV12 → VENC (硬件) → H.264 | ~24fps |

### 本仓库的实际协商流程

1. 浏览器创建 `RTCPeerConnection`。
2. 浏览器调用 `addTransceiver("video", { direction: "recvonly" })`。
3. 浏览器创建 offer，并设置为本地描述。
4. 浏览器把 offer、宽高、帧率发到 `POST /offer`。
5. Python 服务创建新的 `RTCPeerConnection`。
6. Python 服务创建 `AscendVideoTrack` 并通过 `pc.addTrack(...)` 挂上视频轨道。
7. Python 服务对浏览器 offer 调用 `setRemoteDescription(...)`。
8. Python 服务创建 answer，并 `setLocalDescription(...)`。
9. Python 服务把 answer 返回给浏览器。
10. 浏览器设置远端描述，随后开始接收远端视频。

## 代码解析

### [server.py](./server.py)

这是整个服务端入口文件。

- `build_app(source_mode, camera_device)`
  注册主页、静态资源、健康检查和 `/offer` 路由，同时把视频源配置注入到 app 上下文。
- `health()`
  返回当前运行目标和已配置的视频源模式，方便确认所连服务状态。
- `parse_offer_payload()`
  解析浏览器或客户端发来的 `sdp`、`type`、`width`、`height`、`fps`，并做基础参数校验。
- `offer()`
  最关键的信令入口。新请求到达时先关闭所有旧连接（释放 `/dev/video0`），然后创建 `RTCPeerConnection`，通过 `AscendVideoTrack` 挂载视频轨道，完成 answer 生成并返回。
- `get_local_ip()`
  通过 UDP 套接字发现本机 LAN IP，启动时打印到控制台。
- `close_peer_connection()`
  统一关闭 `RTCPeerConnection` 和源轨道，避免异常后留下悬空连接。
- `setup_logging()`
  同时写控制台和 `logs/server.log`，便于在 310B 上看运行日志。

`server.py` 是会话控制层和 HTTP 信令层，不承载设备专属图像处理逻辑。

### [webrtc_app/ascend_source.py](./webrtc_app/ascend_source.py)

这是当前仓库最重要的设备适配点。

- `AscendVideoTrack`
  继承 `MediaStreamTrack`，代表 Python 侧视频轨道。通过 `source_type` 参数切换 `"demo"`、`"usb_camera"` 或 `"dvpp_camera"`。
- `_init_demo()`
  demo 模式的初始化，生成 x/y 渐变数组用于合成帧。
- `_init_usb_camera(device)`
  打开 `cv2.VideoCapture(device)`，设置并读取实际分辨率/帧率，验证首帧。失败自动回退到 demo 模式。
- `_init_dvpp_camera(device)`
  初始化 V4L2 MJPEG 采集 + DVPP JPEGD 硬件解码器。优先使用 `V4l2RawCapture`（~24fps），失败降级到 PyAV（~15fps）。
- `_camera_read()`
  阻塞方法，在 `run_in_executor` 中执行。DVPP 模式：V4L2 取 MJPEG → JPEGD 解码 → NV12。OpenCV 模式：BGR 转 RGB。
- `describe_settings()`
  返回当前轨道配置（含实际分辨率、帧率、模式），通过 offer 响应回传给接收端。
- `_render_demo_frame()`
  动态彩色演示帧。在未接入真实 310B 输出时保持媒体链路可运行。
- `recv()`
  每次被 `aiortc` 拉取时返回 `av.VideoFrame`。摄像头模式下用 `run_in_executor` 异步读取。
- `next_timestamp()`
  用 90kHz 视频时钟控制输出节奏，让帧率与配置值一致。
- `stop()`
  停止轨道并释放摄像头资源。

接入真实昇腾 310B 输出时，最应该改的就是这里，而不是 `server.py` 的协商逻辑。

### [webrtc_app/cann_encoder.py](./webrtc_app/cann_encoder.py)

昇腾 310B 硬件 H.264 编码器，通过 CANN VENC API 实现。

- `CannVenc` — 同步封装 CANN VENC 异步回调 API
  - `create_channel` / `destroy` — VENC 通道生命周期
  - `encode(nv12, force_keyframe, pre_padded)` — 提交 NV12 帧，返回 H.264 Annex-B 码流。`pre_padded=True` 时跳过 CPU stride 重排（配合 JPEGD 输出）
- `CannH264Encoder(H264Encoder)` — aiortc 兼容的 H.264 编码器
  - 继承 aiortc 的 `_packetize`、`pack`、`_split_bitstream`，复用 RTP 分包逻辑
  - 仅覆盖 `_encode_frame` 将 libx264 替换为 CANN VENC
  - 检测帧格式：NV12 帧直通 VENC（跳过 BGR→NV12），BGR 帧走 CPU 转换
  - CANN 不可用时自动回退到 CPU libx264
- `bgr_to_nv12(bgr)` — BGR→NV12 色彩空间转换（CPU），仅 `usb_camera` 模式使用
- `_try_import_cann()` / `_init_acl()` — CANN ACL 自动导入、环境配置和设备初始化

### [web/client.js](./web/client.js)

这是浏览器接收端逻辑。

- `checkHealth()`
  页面初始化时检查服务是否在线，并展示运行目标。
- `startConnection()`
  创建浏览器侧 `RTCPeerConnection`，生成 offer，向服务端请求 answer，然后设置远端描述。
- `bindConnectionEvents()`
  监听 track、PeerConnection 状态和 ICE 状态。
- `readInboundStats()`
  定时读取浏览器统计信息，计算接收码率和帧率（stats 回退源）。
- `startFpsTracking()` / `rvfcCallback()`
  通过 `requestVideoFrameCallback` 测量实际显示帧率（优先源），实时更新视频浮层和状态栏。
- `stopConnection()`
  关闭当前连接并清理页面状态。

浏览器端没有 `getUserMedia()`，因为这个仓库不是浏览器采集方案。

### [web/index.html](./web/index.html) 和 [web/styles.css](./web/styles.css)

这两个文件负责展示接收页。

- 页面只暴露分辨率和帧率输入，不再暴露本地摄像头枚举。
- 状态栏里能直接看到 HTTP 服务状态、运行目标、PeerConnection 状态和接收码率。
- 视频窗口显示的是远端媒体，不是浏览器本地预览。

## 如何接入真实昇腾 310B 输出

建议只在 [webrtc_app/ascend_source.py](./webrtc_app/ascend_source.py) 这条边界上接入真实设备能力。

已有三种模式覆盖了常见场景：

1. **demo** — 纯软件，验证 WebRTC 链路。
2. **usb_camera** — OpenCV + VENC 硬件编码，适合不需要 DVPP 加速的场景。
3. **dvpp_camera** — V4L2 MJPEG + JPEGD + VENC 全硬件管线，性能最优。

如需接入自定义源：

1. 在 `AscendVideoTrack.__init__` 中添加新的 `source_type` 分支。
2. 实现对应的 `_init_xxx()` 方法，初始化采集/产出逻辑。
3. 在 `recv()` 中产出 `av.VideoFrame`（格式建议 `nv12` 以配合 VENC 直通）。
4. 保持 `next_timestamp()` 发送节奏控制逻辑稳定。
5. 不要把 ACL、DVPP、CANN 等设备专属细节写进 `server.py` 或浏览器代码。

## 当前依赖

**服务端（昇腾 310B）**：
- `aiohttp` — HTTP 服务和静态页面
- `aiortc` — Python 侧 WebRTC 能力
- `av` — 构造 `VideoFrame` 和 V4L2 采集（PyAV）
- `numpy` — 演示帧生成和 NV12 数据操作
- `opencv-python-headless` — USB 摄像头采集（仅 `usb_camera` 模式需要）
- `CANN 8.3.RC1` — 昇腾 ACL/Python API（硬件编码必需，位于 `/usr/local/Ascend/`）
- `pytest` — 测试框架（仅开发）

## Codex Agents

仓库里保留了本地 agent 配置，语义为 Ascend 310B first。

- `webrtc_mapper` — 定位真实执行路径
- `ascend_310b_reviewer` — 检查 Ascend 310B 运行准备度
- `stream_pipeline_worker` — 小范围实现改动
- `docs_researcher` — WebRTC、浏览器和部署文档查询
- `windows_camera_debugger.toml` — 开发机在 Windows、运行机在 Ascend 310B 时的远程连接和部署诊断
