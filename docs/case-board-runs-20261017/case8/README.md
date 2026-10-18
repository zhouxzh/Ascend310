# case8 双板调试记录

## 运行

- 8T/20T：`/home/HwHiAiUser/Ascend310/cases/case8`
- 首次启动：`python scripts/webrtc_om_app.py --no-hardware-encode`
- 页面：`http://<board>:8080/`
- 依赖：`aiohttp`、`aiortc`、`av` 通过 `requirements.txt` 安装

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| HTTP 服务 | 启动成功 | 启动成功 |
| `/health` | 200，`status=ok` | 200，`status=ok` |
| `/models` | 4 个 OM 可见 | 4 个 OM 可见 |
| 摄像头节点 | `/dev/video2`、`/dev/video3` | `/dev/video0`、`/dev/video1` |
| 配置的 source | `/dev/video2` | `/dev/video0` |
| USB 帧读取 | 3 帧，480x640x3 | 3 帧，480x640x3 |
| H.264 编码 | `cpu-libx264`（本次显式关闭硬件编码） | `cpu-libx264` |
| WebRTC Peer | 未建立 | 未建立 |

两板页面均可渲染，摄像头设备和 OpenCV 帧读取均通过。8T 的 USB 摄像头不是 `/dev/video0`，而是 `/dev/video2`；服务已用 `--source /dev/video2` 启动。20T 使用 `/dev/video0`。本次仍未点击网页开始 WebRTC，也没有执行手势推理或硬件 VENC，不能写成完整视频链路通过。

## 截图

- [8T 页面](board-8t.png)
- [20T 页面](board-20t.png)

## 证据

- [8T 原始日志](board-8t-camera.log)
- [20T 原始日志](board-20t-camera.log)
- [health/models/stats 和截图结果](capture-camera.json)
- [摄像头设备与健康检查](../camera-health.json)
