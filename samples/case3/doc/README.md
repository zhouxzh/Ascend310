# 文档索引

[返回仓库 README](../README.md)。

详细设计、操作步骤和硬件实测记录按主题存放在本目录。文档中的命令默认从
`samples/case3` 仓库根目录执行。

| 文档 | 内容 |
| :--- | :--- |
| [项目概览](overview.md) | 项目边界、硬件要求、目录结构和完整系统链路 |
| [MIDI 键盘应用](midi-app.md) | `midi.py` 功能、键盘映射、设备枚举和依赖 |
| [MIDI 测试曲目与来源](midi-test-tracks.md) | 当前测试曲目、MuseScore 下载来源和 ONNX 试听输出约定 |
| [3D 打印硬件](hardware.md) | CAD/STL 文件、打印参数和装配关系 |
| [DDSP-VST 模型导出](model-export.md) | 状态化音色模型的 TFLite 到 ONNX 流程 |
| [MIDI-DDSP 模型导出](midi-ddsp-export.md) | checkpoint 到 Expression/Synthesis ONNX 的流程与产物 |
| [MIDI-DDSP 与 DDSP-VST 对比](midi-ddsp-vs-ddsp-vst.md) | 两条移植路线的程序入口、模型契约、实时性和适用场景差异 |
| [实时 DDSP](realtime-ddsp.md) | ONNX/PyACL 后端、MIDI 实时播放和缓冲架构 |
| [Ascend 音频输出](audio-output.md) | 板载 3.5mm、官方样例、USB 声卡、蓝牙 A2DP/HFP 和漫步者喇叭 |
| [OM 转换与验证](om-deployment.md) | ATC 转换、日志检查和 FP16 精度验证 |
| [板端实测结果](benchmark-results.md) | 8T/8T2/20T 的精度、速度和兼容性结果 |
| [MIDI-DDSP 实时合成](midi-ddsp-realtime.md) | 使用 MIDI-DDSP OM、PyACL、CPU DSP 和 M25 实时播放 MIDI |
| [MIDI-DDSP Studio Web 界面](webui.md) | React/FastAPI 工作台、板端手动安装、同步、启动和测试 |
| [测试故障排查](troubleshooting.md) | SSH/systemd、音频、ATC、OOM、兼容性和性能问题记录 |
| [Upstream 参考仓库](upstream-repositories.md) | 第三方源码来源、固定提交、本地状态和保留规则 |

原始机器报告保存在本地 `reports/`，模型产物保存在 `models/`。这两个目录默认被
仓库级 `.gitignore` 忽略，文档中的结论以对应的日志、JSON 和 SHA256 清单为依据。
