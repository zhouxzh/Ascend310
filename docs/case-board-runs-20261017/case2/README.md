# case2 双板调试记录

## 运行

- 部署目录：`/home/HwHiAiUser/Ascend310/cases/case2`
- 使用模型：`models/ssd320_mobilenetv3_large_100.om`
- 输入：`demo/vtest.avi`
- 后端：NPU / ACL
- 测试入口：`scripts/detection_app.py` 和 `scripts/tracking_app.py`
- 两轮均使用 `--no-display`，避免无桌面会话阻塞

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| detection_app | exit 0 | exit 0 |
| tracking_app | exit 0 | exit 0 |
| NPU 模型 | 加载成功 | 加载成功 |
| 输入解析 | 768x576，10 fps | 768x576，10 fps |
| 输出形状 | `(1,4,3234)` + `(1,81,3234)` | `(1,4,3234)` + `(1,81,3234)` |
| 输出视频 | 已生成到板端 `/tmp` | 已生成到板端 `/tmp` |

本案例没有浏览器前端，因此没有 UI 截图；不能把“日志中没有 OpenCV 窗口”解释成 UI 通过。检测和跟踪的 MP4 输出留在板端临时目录，日志已归档。

## 证据

- [8T detection 日志](detect-8t.log)
- [20T detection 日志](detect-20t.log)
- [8T tracking 日志](track-8t.log)
- [20T tracking 日志](track-20t.log)
