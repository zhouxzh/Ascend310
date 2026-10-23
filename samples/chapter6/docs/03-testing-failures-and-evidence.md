# 测试、失败边界与证据等级

## 本地可运行的测试

WebRTC 测试位于 `samples/chapter6/WebRTC/test`。从该目录运行：

```bash
python -m pytest test -v
```

在没有 PyACL 的开发机上，`conftest.py` 会检测 CANN 是否可导入，依赖 VENC fixture 的硬件测试会跳过。纯 Python 的 H.265 NAL 拆分、RTP 分包和 NV12 转换测试仍可运行，前提是已安装 NumPy、PyAV 和 aiortc。

| 测试文件 | 覆盖内容 | 边界 |
|---|---|---|
| `test_nv12.py` | BGR 到 NV12 的形状、范围和亮度 sanity | 不检查与 CANN 输出的逐像素一致性 |
| `test_hevc.py` | Annex-B 起始码、NAL 类型、单包和 FU 分包标志 | 不与浏览器或真实 VENC 码流互操作 |
| `test_h264_strict.py` | CANN 不可用时 H.264 硬编必须失败，不回退 CPU | 只验证 fail-closed 分支 |
| `test_h265_server.py` | SDP H.265 检测和 codec 能力注册 | 使用 monkeypatch 绕过真实 CANN 初始化 |
| `test_cann_venc.py` | 自动码率、offer 参数、通道和编码 smoke | 主要测试依赖板端 fixture；无 CANN 时跳过 |

`test_dvpp_pipeline.py` 文件名虽然会被 pytest 发现，但当前文件只提供 `_run_offer()` 和 `main()`，没有可收集的 `test_*` 函数。它实际是一个需要显式启动的手工端到端驱动，不能把一次 `pytest` 通过解释成摄像头链路通过。

## 板端测试命令

先加载 CANN 并进入 WebRTC 目录：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
cd samples/chapter6/WebRTC
python -m pytest test -v
```

单独验证 H.265 VENC 基本流：

```bash
python tools/validate_h265_venc.py
```

成功信号是输出文件非空、Annex-B 可拆分，并且 NAL 类型中包含 VPS、SPS、PPS 或 IDR。它证明编码器产生了可识别的关键帧标记，不证明 RTP、浏览器解码或显示成功。

验证 offer/answer：

```bash
python tools/check_h265_offer_answer.py
```

该工具需要 CANN 导入成功，使用 `demo` 帧源，只检查 H.265 codec 注册和 answer SDP，不启动摄像头媒体流。

手工 DVPP 摄像头端到端驱动：

```bash
python test/test_dvpp_pipeline.py \
  --python "$(command -v python)" \
  --cwd "$PWD" \
  --port 8090 \
  --width 1920 \
  --height 1080 \
  --fps 30 \
  --duration 12
```

脚本默认使用当前已激活的 Python 解释器和 WebRTC 项目根目录；仍可用 `E2E_PYTHON` 或 `E2E_CWD` 覆盖。它启动一个子服务、完成一次 offer、等待收到 track 和规定时长，然后根据是否建立媒体路径返回 0 或 1。运行前必须确认当前解释器来自 `ascend` 环境，而不是系统 Python。

## 失败边界

### CANN 或 DVPP 不可用

- `check_cann.py` 在初始化、设备绑定或 context 失败时以断言终止。
- WebRTC 硬件编码在导入 CANN 失败时不启动或拒绝连接。
- `dvpp_camera` 在直接 V4L2 或 JPEGD 初始化失败时释放已获取资源并终止本次 offer。
- 摄像头读取失败抛出媒体流错误，不回退到 `demo`。
- CPU 软件编码只属于 `usb_camera`/`demo` 的显式非硬件模式，不是硬件失败后的自动降级。

### 浏览器或 codec 不兼容

H.265 页面在浏览器不暴露 `video/H265` 时不发送 offer。若服务端收到不含 `H265/90000` 的 offer，则返回 HTTP 400。服务启动成功不能证明浏览器可以解码 H.265。

### 设备生命周期

新 offer 会关闭旧连接并等待约 300 ms，再打开新摄像头。关闭路径应同时释放 PeerConnection、source capture、JPEGD、VENC 和日志/统计资源。异常清理是否完整仍需板端重复建连、ICE 断开和进程退出测试。

### 已知环境不一致

- 子项目依赖文件和启动说明统一使用 `ascend` / Python 3.9.25；如果板端实际 ABI 不同，测试必须停止并记录解释器路径。
- `environment.yml` 只是参考声明，不能用来重建或覆盖板端已验证的 `ascend` 环境。
- aarch64 V4L2 ioctl 布局是代码内固定常量，不能外推到其他架构或其他内核 ABI。

2026-10-23 的 20T 实测补充：

- CANN/DVPP 初始化检查通过，VENC 符号表可用。
- VPC Resize 与 CropResize 通过。
- JPEG 硬编解码闭环通过。
- WebRTC 纯 Python 测试在排除 VENC fixture 后 14/14 通过。
- demo HTTP 首页返回 200。
- `validate_h265_venc.py` 在 `venc_create_channel` 返回 `507018`；随后 dmesg 出现 `aicpu exception` 和 `err_ret=-512`。
- 20T 当前没有 `/dev/video0`，真实摄像头和浏览器接收无法启动。

因此当前状态是：DVPP VPC/JPEG 为 `observed-pass`，VENC 为 `observed-fail`，摄像头/浏览器为 `blocked`。这些结论只属于本次 20T 组合。

## 证据分层

| 证据 | 可以证明 | 不能证明 |
|---|---|---|
| `python -m pytest test -v` 中纯 Python 用例通过 | RTP 分包、SDP 文本解析和 fail-closed 分支符合测试 | 真实 VENC、摄像头或浏览器显示 |
| `check_cann.py` 通过 | ACL 初始化、设备绑定和 VENC 符号可用 | 通道创建、编码、WebRTC 或性能 |
| `validate_h265_venc.py` 通过 | VENC 产生含关键帧标记的 H.265 基本流 | RTP 和浏览器兼容 |
| `check_h265_offer_answer.py` 通过 | answer 中协商了 H.265 | 媒体帧实际流动 |
| `test_dvpp_pipeline.py` 通过 | 指定参数下一次 V4L2 -> JPEGD -> VENC -> WebRTC 连接收到 track | 长稳、画质、重连和多种相机 |
| 既有 README 中的板端结果 | 作者曾记录该结果 | 当前源码、当前板卡或本次检查可复现 |

## 历史报告边界

`WebRTC/README.md` 记录了作者在特定 Orange Pi 系统、固件和 CANN 组合上的结果，包括 CANN 8.0/8.3 以及特定系统日期戳。这些内容是仓库历史 `documented` 证据，本次没有重新运行。

发布或引用这些结果时，应保留原始系统日期戳、固件、CANN、浏览器、分辨率、帧率、码率、命令和日志。不能把历史 CPU 演示结果、pytest 跳过结果或 SDP 文本检查合并成 NPU 端到端结论。`1920x1080` 输入可能显示为 `1920x1088` 编码面，这是对齐行为，不是新的分辨率实测。
