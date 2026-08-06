# Case3 文档索引

本目录记录 Case3 音乐工作台的操作、部署、模型与实测资料。命令默认从
`samples/case3` 根目录执行；书稿中的完整实验教程见
[Case3 智能电子琴](../../../src/experiment/case3.md)。

## 推荐阅读顺序

首次复现实验建议依次阅读 `01` 至 `09`。`10` 和 `11` 用于核对测试结果与排障；`12` 至
`15` 是历史和上游资料，不属于当前部署步骤。`README.md` 保留标准名称，作为目录默认入口。

| 顺序 | 文档 | 阶段 | 主要内容 |
| :---: | :--- | :--- | :--- |
| 01 | [项目概览](01-overview.md) | 认识系统 | 三条音频链、核心模块、硬件要求和验收顺序 |
| 02 | [WebUI 操作、部署与 API](02-webui.md) | 使用界面 | 四个工作区、12 张实机截图、全屏启动、部署和接口 |
| 03 | [已发布模型下载与 Ascend OM 部署](03-om-deployment.md) | 准备模型 | 固定 revision、SHA256、ATC、OM bundle 和板端验证 |
| 04 | [Piano-DDSP 实时系统](04-piano-ddsp.md) | 实时演奏 | 共享会话、16 声部、模型目录、运行时和验收 |
| 05 | [MIDI-DDSP 文件渲染与播放](05-midi-ddsp-realtime.md) | 文件渲染 | 声部分析、离线渲染、WAV 版本和播放 |
| 06 | [Ascend 音频输出](06-audio-output.md) | 配置音频 | PulseAudio、ALSA、USB、蓝牙和扬声器测试 |
| 07 | [触摸屏输入法配置](07-touchscreen-input.md) | 配置屏幕 | Onboard、IBus 拼音和 Firefox kiosk |
| 08 | [MIDI 测试素材](08-midi-test-tracks.md) | 准备夹具 | 确定性 MIDI 夹具、生成方法和使用边界 |
| 09 | [WebUI 触摸屏终审与实机压测](09-webui-acceptance.md) | 发布验收 | 四视口、控件审计、UI soak、API 负载和双工测试 |
| 10 | [板端实测结果](10-benchmark-results.md) | 查看结果 | 8T、8T2 和 20T 的精度、性能与兼容性记录 |
| 11 | [测试故障排查](11-troubleshooting.md) | 处理故障 | 网络、音频、模型、NPU 和服务诊断 |
| 12 | [MIDI-DDSP 历史导出](12-midi-ddsp-export.md) | 历史参考 | TensorFlow 导出原理、张量契约和历史验证 |
| 13 | [MIDI-DDSP 与 DDSP-VST 对比](13-midi-ddsp-vs-ddsp-vst.md) | 历史参考 | 两条上游路线的历史差异和迁移背景 |
| 14 | [历史实时 DDSP 路径](14-realtime-ddsp.md) | 历史参考 | 已退役 DDSP-VST MIDI Synth/ONNX 对照 |
| 15 | [Upstream 参考仓库](15-upstream-repositories.md) | 上游参考 | 第三方仓库、固定提交和保留规则 |

## 证据与产物

`reports/`、`midi/`、`midi_wav/` 和模型二进制是本地或板端运行证据，默认不提交。当前
WebUI 文档使用的 12 张截图已复制到受版本控制的 `doc/images/webui/`，因此阅读文档不依赖
会变化的报告目录。模型清单、校验和与发布说明见 `models/`。
