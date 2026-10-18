# case1 双板调试记录

## 运行

- 8T：`/home/HwHiAiUser/Ascend310/cases/case1`
- 20T：`/home/HwHiAiUser/Ascend310/cases/case1`
- 启动：加载 CANN 后运行 `python app.py --host 0.0.0.0 --port 5000`
- 摄像头索引：`CASE1_CAMERA_INDEX`，默认 `0`；8T 的 USB 摄像头实测为 `/dev/video2`
- 前端：React/Vite 构建产物由 FastAPI 托管

## 摄像头接入后的复测

2026-10-18 两板重新接入摄像头后完成复测：

| 项目 | 8T | 20T |
| --- | --- | --- |
| USB 摄像头节点 | `/dev/video2`、`/dev/video3` | `/dev/video0`、`/dev/video1` |
| 实际读取 | 3 帧，480x640x3 | 3 帧，480x640x3 |
| case1 摄像头索引 | `CASE1_CAMERA_INDEX=2` | `CASE1_CAMERA_INDEX=0` |
| `/api/health` | `status=ok`、`camera_ready=true` | `status=ok`、`camera_ready=true` |
| 模型加载 | 两个 OM 均加载成功 | 两个 OM 均加载成功 |
| 真实识别日志 | 本次未出现识别记录 | 出现同一用户的识别记录 |
| API/布局测试 | 10 passed，4.39s | 10 passed，2.89s |

8T 的 `/dev/video0` 是 ISP 虚拟节点，USB 摄像头实际是 `/dev/video2`；代码已支持 `CASE1_CAMERA_INDEX`，避免把设备索引写死。20T 的识别日志只证明链路产生匹配结果，不提供准确率结论，也不保存真实人脸截图。

摄像头设备、帧读取和健康检查的合并证据见 [`../camera-health.json`](../camera-health.json)。安全截图见 [8T 用户管理（摄像头在线）](board-8t-users-camera.png)。20T 用户管理页出现真实抓拍缩略图，未保存该截图。

## 截图

- [8T 总览（摄像头接入前）](board-8t-home.png)
- [8T 用户管理（摄像头接入前）](board-8t-users.png)
- [8T 考勤记录（摄像头接入前）](board-8t-attendance.png)
- [20T 总览（摄像头接入前）](board-20t-home.png)
- [20T 用户管理（摄像头接入前）](board-20t-users.png)
- [20T 考勤记录（摄像头接入前）](board-20t-attendance.png)
- [8T 用户管理（摄像头接入后，安全截图）](board-8t-users-camera.png)

截图均为 1440x900 PNG，由 `ascend_ui_capture` 生成。

## 证据

- [8T 原始日志](board-8t.log)
- [20T 原始日志](board-20t.log)
- [8T 摄像头接入日志](board-8t-camera.log)
- [20T 摄像头接入日志](board-20t-camera.log)
- [8T API/布局测试](tests-8t.log)
- [20T API/布局测试](tests-20t.log)
- [摄像头设备与健康检查](../camera-health.json)
- [截图和健康检查原始结果](capture.json)
