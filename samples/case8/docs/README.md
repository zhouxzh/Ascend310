# Case8 工程文档

这里记录案例 8 的板端环境、CANN VENC/DVPP 故障、部署证据和未解决问题。
可执行启动步骤仍以 [`../README.md`](../README.md) 为准；本目录不替代案例教程。

## 文档地图

| 主题 | 文档 |
| --- | --- |
| Orange Pi 8T 系统基线、CANN 升级历史与 VENC H.264 故障 | [01-venc-h264-board-incident-20261006.md](01-venc-h264-board-incident-20261006.md) |
| 香橙派 20250925 新系统的 CANN 8.0 干净环境复测 | [02-venc-h264-cann8-clean-system-test-20261006.md](02-venc-h264-cann8-clean-system-test-20261006.md) |
| 香橙派 20241128 旧系统的 CANN 7.0 VENC H.264 对照测试 | [03-venc-h264-cann7-old-system-test-20261006.md](03-venc-h264-cann7-old-system-test-20261006.md) |

## 证据边界

- `observed-pass`：在指定板卡、命令和版本下实测通过。
- `observed-fail`：在指定条件下实测失败，结论只对该组合负责。
- `documented`：来自用户提供的 Orange Pi 固件说明或板端安装元数据。
- `inferred`：依据观测结果推导，尚未由供应商修复包验证。
- `untested`：尚未执行，不能据此判断支持或不支持。

## WebRTC 编码器失败策略

网页选择 `CANN VENC` 时，CANN/ACL 不可用、VENC 通道创建失败或帧编码失败均按失败处理，
程序不会自动改用 CPU `libx264`。Offer 阶段的错误由页面启动日志显示；推流中的编码错误
通过 `/stats` 上报，页面记录错误并关闭连接。CPU `libx264` 仍可由用户在页面中显式选择，
且只替代视频编码，OM 手势推理仍在 NPU。

`/health` 中 `status=ok` 只表示 HTTP 服务在线；`hardware_encode` 只有在 VENC 通道创建
成功后才为真。最终验收仍需确认浏览器持续收到视频帧，不能仅凭健康检查或 CPU 模式正常
判定 VENC 通过。
