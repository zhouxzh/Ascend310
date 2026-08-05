# Case3 文档索引

本目录记录 Case3 音乐工作台的操作、部署、模型与实测资料。命令默认从
`samples/case3` 根目录执行；书稿中的完整实验教程见
[Case3 智能电子琴](../../../src/experiment/case3.md)。

## 从这里开始

| 目标 | 阅读文档 | 内容 |
| :--- | :--- | :--- |
| 第一次使用或部署触摸屏工作台 | [WebUI 操作、部署与 API](webui.md) | 四个工作区、12 张真实截图、安全边界、全屏启动、部署和接口 |
| 了解系统范围与硬件职责 | [项目概览](overview.md) | 三条音频链、核心模块、硬件要求和推荐验收顺序 |
| 复测已发布版本 | [WebUI 触摸屏终审与实机压测](webui-acceptance.md) | 2026-08-04 的测试方法、阈值、原始结果与复测规则 |
| 排查运行问题 | [测试故障排查](troubleshooting.md) | 网络、音频、模型、NPU 和服务诊断 |

## 模型与运行时

| 文档 | 内容 |
| :--- | :--- |
| [模型与 OM 部署](om-deployment.md) | 固定 revision 下载、SHA256、ATC、OM bundle 和板端验证 |
| [Piano-DDSP 实时系统](piano-ddsp.md) | 共享实时钢琴会话、16 声部、模型目录、运行时与验收 |
| [MIDI-DDSP 文件渲染与播放](midi-ddsp-realtime.md) | MIDI 文件的声部分析、离线渲染、WAV 版本和播放 |
| [MIDI 测试素材](midi-test-tracks.md) | 确定性 MIDI 夹具和使用边界 |
| [Ascend 音频输出](audio-output.md) | PulseAudio、ALSA、USB、蓝牙与扬声器测试约定 |
| [板端实测结果](benchmark-results.md) | 8T/8T2/20T 的精度、性能和兼容性记录 |

## 硬件、界面与运维

| 文档 | 内容 |
| :--- | :--- |
| [触摸屏输入法配置](touchscreen-input.md) | Onboard、IBus 拼音和 Firefox kiosk 配置 |
| [WebUI 操作、部署与 API](webui.md) | 触摸演奏、MIDI、渲染、Effect、设备、前后端职责、部署和端点 |
| [WebUI 触摸屏终审与实机压测](webui-acceptance.md) | 四视口、控件审计、UI soak、API 负载与双工测试 |

## 历史与上游参考

下列文档保留研究背景、迁移记录或上游行为对照，不是当前 Case3 的部署步骤。

| 文档 | 内容 |
| :--- | :--- |
| [MIDI-DDSP 历史导出](midi-ddsp-export.md) | TensorFlow 导出原理、张量契约和历史验证记录 |
| [MIDI-DDSP 与 DDSP-VST 对比](midi-ddsp-vs-ddsp-vst.md) | 两条上游路线的历史差异与迁移背景 |
| [历史实时 DDSP 路径](realtime-ddsp.md) | 已退役的 DDSP-VST MIDI Synth/ONNX 对照说明 |
| [Upstream 参考仓库](upstream-repositories.md) | 第三方仓库、固定提交与保留规则 |

## 证据与产物

`reports/`、`midi/`、`midi_wav/` 和模型二进制是本地或板端运行证据，默认不提交。当前
WebUI 文档使用的 12 张截图已复制到受版本控制的 `doc/images/webui/`，因此阅读文档不依赖
会变化的报告目录。模型清单、校验和与发布说明见 `models/`。
