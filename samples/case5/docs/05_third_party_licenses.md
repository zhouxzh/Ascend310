# 05 第三方代码与许可证

本文是来源与许可证说明，不是安装或运行手册；完整实验步骤见 [README](../README.md)。Case 5 采用 GPL-3.0-only。第三方代码复制或修改时，必须在对应文件保留原始版权头，并在本文件记录仓库、提交版本、文件路径和修改内容。

## 参考和可能复用的项目

| 项目 | 地址 | 许可证 | 使用范围 |
| --- | --- | --- | --- |
| libsigrok | https://github.com/sigrokproject/libsigrok | GPL-3.0-or-later | 当前 Hantek USB、易失固件和模拟回调后端；由系统包动态链接 |
| QSpectrumAnalyzer | https://github.com/xmikos/qspectrumanalyzer | GPL-3.0 | 频谱/瀑布交互参考；不复制完整 SDR 后端 |
| inspectrum | https://github.com/miek/inspectrum | GPL-3.0 | 时频图、光标和观察交互参考 |
| PyQtGraph | https://github.com/pyqtgraph/pyqtgraph | MIT | 波形、频谱和瀑布绘图依赖 |
| GNU Radio `gr-cuda` | https://github.com/gnuradio/gr-cuda | 上游仓库许可证 | 只参考批量处理、设备缓冲区和性能边界设计；未复制源码 |
| `gr-clenabled` | https://github.com/ghostop14/gr-clenabled | 上游仓库许可证 | 只参考 OpenCL 批量/工作组设计；未复制源码 |

当前版本没有把上述项目的完整源代码复制到 Case 5。若后续复制具体模块，应增加来源提交哈希和修改说明，并随发布包提供 GPL 文本和对应源代码。模型的来源、权重许可证和 ATC 产物的准入记录见 [07 异构处理与模型准入](07_ascend310b_heterogeneous_signal_processing.md)。
