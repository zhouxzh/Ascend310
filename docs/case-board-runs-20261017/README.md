# case1–case9 双板调试记录（进行中）

本目录保存 2026-10-17 至 2026-10-18 在 8T（Ascend310B4）和 20T（Ascend310B1）上逐项调试实践案例的证据。每次运行保留原始日志、HTTP 状态和前端 PNG；截图只证明页面渲染，不替代真实推理、音频或摄像头链路验收。

## 状态矩阵

| 案例 | 方向 | 8T | 20T | 前端截图 | 当前边界 |
| --- | --- | --- | --- | --- | --- |
| case1 | 智能考勤机 | UI 通过；摄像头接入后 `ok`，识别日志出现 | UI 通过；摄像头接入后 `ok`，识别日志出现 | [8T](case1/board-8t-home.png) / [20T](case1/board-20t-home.png) | 两板真实摄像头画面已读取；20T 人脸缩略图按隐私规则未入库；准确率未评估 |
| case2 | 目标跟踪检测 | detection/tracking 均通过 | detection/tracking 均通过 | N/A（无浏览器前端） | NPU OM 推理完成；输出视频保留在板端 `/tmp` |
| case3 | 智能电子琴 | UI 通过，NPU 可用 | UI 通过，NPU 可用 | [8T](case3/board-8t.png) / [20T](case3/board-20t.png) | `mido`/`rtmidi`/`sounddevice` 缺失；未做音频链路 |
| case4 | 掌纹识别 | 服务/模型 ready；摄像头预览失败 `/dev/video0` | 服务/模型 ready；C922 预览和一次 ROI attempt | [8T](case4/board-8t.png) / [20T](case4/board-20t-safe.png) | 8T 需显式选择 `/dev/video2`；20T 模板库为空，未匹配；live ROI 截图不入库 |
| case5 | 智能数据采集分析仪 | pytest 227 passed / 17 skipped | pytest 227 passed / 17 skipped | N/A（Qt 触摸屏，不是浏览器页面） | 纯 Python 回归通过；Hantek/RTL-SDR 和 OM 未运行 |
| case6 | 智能小车 | HTTP 200，UI 通过 | HTTP 200，UI 通过 | [8T](case6/board-8t.png) / [20T](case6/board-20t.png) | 只验证 Gradio 初始页；未上传道路图做推理 |
| case7 | 智能相册 | 服务 ready，NPU，3 个 admitted 模型 | 服务 ready，NPU，3 个 admitted 模型 | [8T](case7/board-8t.png) / [20T](case7/board-20t.png) | 相册为空；上传/索引/搜索/电子纸刷新未验证 |
| case8 | 实时手势识别 | HTTP 通过，UI 通过；USB `/dev/video2` 可读 | HTTP 通过，UI 通过；USB `/dev/video0` 可读 | [8T](case8/board-8t.png) / [20T](case8/board-20t.png) | 摄像头已接入；WebRTC Peer 和手势推理未启动；本次用 CPU libx264 |
| case9 | 智能聊天机器人 | N/A（发布目标为 20T） | worker/gateway/UI 和一次真实 completion 通过 | [20T](case9/board-20t.png) | 固定 Qwen1.5-0.5B；未做准确率/长上下文评估 |

## 板端组合

| 项目 | 8T | 20T |
| --- | --- | --- |
| SoC | `Ascend310B4` | `Ascend310B1` |
| CANN | 8.0.0 | 8.0.0 |
| 驱动 | 25.2.0 | 25.2.0 |
| Python | 3.9.2 | 3.9.2 |
| Health | Alarm | Alarm |

两板结果分别保存，不合并算力排名。

## 截图工具

本目录截图统一由插件 `ascend_ui_capture` 生成：

- 宿主机 Edge/Chrome headless；
- viewport `1440x900`；
- 强制 PNG，输出限制在工作区；
- 同时记录 URL、字节数、浏览器和耗时；
- 截图文件名按板卡和页面命名，日志与截图同目录保存。

## 当前结论边界

- `observed-pass`：HTTP 页面可访问、静态前端正常渲染、PNG 成功生成。
- `observed-fail`：摄像头、音频设备、依赖或模型链路失败，按实际错误记录。
- `untested`：尚未在本轮运行的项目，不写成通过或失败。
- NPU 可用不等于推理已执行；页面可用不等于业务链路已验收。
