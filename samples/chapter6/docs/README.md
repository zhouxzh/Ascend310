# 第6章样例工程文档

本目录补充 `samples/chapter6` 的工程说明。根目录 `README.md` 保留快速入口；这里说明源码边界、运行合同、失败语义、测试范围和证据等级。所有 CANN、DVPP、摄像头及 WebRTC 板端行为仍需在 Ascend 310B 上单独验证，本次文档检查没有执行这些硬件路径。

## 文档索引

| 文档 | 内容 |
|---|---|
| [01-dvpp-baselines-and-benchmarks.md](01-dvpp-baselines-and-benchmarks.md) | `check_cann.py`、VENC、VDEC、VPC、JPEG 的入口、输入输出合同和基准口径 |
| [02-webrtc-service-and-media-contract.md](02-webrtc-service-and-media-contract.md) | WebRTC 服务入口、CLI、HTTP 合同、视频源、编码和 H.265 协商 |
| [03-testing-failures-and-evidence.md](03-testing-failures-and-evidence.md) | 自动化与手工测试、失败边界、已知环境冲突和历史证据的可用范围 |

## 代码组织

`samples/chapter6` 包含两条相互独立的路径：

| 路径 | 用途 | 主要依赖 |
|---|---|---|
| `check_cann.py`、`venc/`、`vdec/`、`vpc/`、`jpeg/` | 直接调用 PyACL/DVPP API 的最小样例和性能对照 | CANN PyACL、`acl.media`、NumPy，部分基准另需 PyAV/FFmpeg codec |
| `WebRTC/` | 将 V4L2、DVPP JPEGD、CANN VENC 和 aiortc 组合成浏览器接收端 | Python WebRTC 栈、PyAV、摄像头运行时和 CANN，H.265 另需浏览器支持 |

不要把 `WebRTC/` 中的浏览器页面、信令服务或 CPU 演示源当作 DVPP 硬件验证。也不要根据 `bench_*.py` 的控制台输出推断真实摄像头、端到端 WebRTC 或模型精度结论。

## 运行目录

直接 DVPP 样例假定命令从仓库根目录执行，例如：

```bash
python samples/chapter6/check_cann.py
python samples/chapter6/jpeg/jpeg_minimal.py
```

WebRTC 服务假定从 `samples/chapter6/WebRTC` 目录启动：

```bash
cd samples/chapter6/WebRTC
python server.py --help
```

## 环境与证据状态

仓库统一运行环境是名为 `ascend` 的 conda 环境，Python 3.9.25，并要求设置 `PYTHONNOUSERSITE=1`。WebRTC 的 `environment.yml` 和 `requirements.txt` 已使用同一版本声明；`environment.yml` 仅作参考，不能用来重建或覆盖板端已验证环境。板端验收仍须记录实际解释器 ABI、依赖版本和 CANN 版本。

2026-10-23 在在线 `Ascend310B1 / 20T`、CANN 8.0.0、driver 25.2.0、Python 3.9.25 上得到以下分层结果：

| 路径 | 结果 | 边界 |
| --- | --- | --- |
| ACL 初始化与 VENC 符号探测 | `observed-pass` | `check_cann.py` 报告 38 个 VENC API |
| VPC Resize/CropResize | `observed-pass` | 640×480 输入两条路径均完成 |
| JPEG 硬编解码闭环 | `observed-pass` | JPEGE 与 JPEGD 均返回 640×480 NV12 |
| WebRTC 纯 Python 测试 | `observed-pass` | 排除 VENC 用例后 14/14 passed |
| Demo HTTP 页面 | `observed-pass` | `curl /` 返回 200 |
| H.265/H.264 VENC 通道创建 | `observed-fail` | `venc_create_channel` 返回 `507018` |
| 真实摄像头与浏览器接收 | `blocked` | 20T 上 `/dev/video0` 不存在；VENC 失败 |

VENC 失败时 dmesg 同时记录 `aicpu exception`、`err_ret=-512` 和进程释放事件；不能把该结果写成整个 310B 平台不支持 WebRTC。它是当前 20T 板、系统、固件和 CANN 组合下的传输/编码运行时阻断。完整浏览器媒体接收仍待可用 VENC 路径和摄像头环境。
