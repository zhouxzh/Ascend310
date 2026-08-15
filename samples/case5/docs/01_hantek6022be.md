# 01 Hantek 6022BE：sigrok 驱动和实机采集

本文件解释 Hantek 6022BE 的硬件边界与 libsigrok 采集实现。安装、桥编译和 `CAL` 实验的操作顺序统一见 [README](../README.md)。

## 当前可证明的范围

Case 5 的 Hantek 主路径只使用系统 libsigrok 的 `hantek-6xxx` 驱动。PulseView 与 `sigrok-cli` 只用于人工观察或排查；仪表盘不解析它们的文本输出，也不使用示波器专用 Python 库。

已记录的实机证据是 CH1 探头接 6022BE `CAL` 标准测试输出后，桥接、连续窗口、固定 DFT OM 和仪表盘连通。它不证明幅值精度、模拟带宽、CH2 电流标定、设备 FIFO 连续性或 NPU 以外的能力。

## 接线与安全

6022BE 是 USB 双通道示波器，两个输入共地而非隔离输入。首轮验证应把 CH1 探头接在机身低压 `CAL` 输出；CH2 保持未接，或只连接已确认隔离的低压 Little Bee 输出。

- 未确认被测回路的隔离、接地、量程和探头倍率前，禁止测量市电。
- CH2 要接 Little Bee B1 时，先阅读 [02 Little Bee B1](02_little_bee_b1.md)，完成探头去零并如实记录模式、灵敏度和匝数。
- PulseView、`sigrok-cli` 与仪表盘不能同时占用同一台 6022BE；切换程序前必须停止前一个 session。
- PulseView 退出后若设备仍显示 `1d50:608e`，物理拔插一次，使下一次 sigrok 扫描重新加载 `fx2lafw` 易失固件。

## 采集桥的职责

`time_frequency_dashboard/acquisition/native/sigrok_capture_bridge.c` 以一个长期运行的 libsigrok session 完成：

1. 扫描 Hantek，在驱动需要时上传易失固件；
2. 开启 CH1/CH2，设置驱动支持的 V/div 和目标采样率；
3. 接收两个 `SR_DF_ANALOG` 回调，并只在两个通道都获得新样本后配对；
4. 输出 `BridgeFrameV1` 二进制帧到 stdout，诊断写入 stderr。

桥输出的 payload 是 `[N, CH1, CH2]` 小端 float32；头部包括魔数、版本、序号、主机单调时间、实际采样率、样本数、通道数、削顶标志和 payload 长度。Python 端从任意 stdout 分片重建帧，拒绝错误版本、跳号或非 1 MS/s 的输入，再拼成固定 `[1,2,10000]` 的 OM 窗口。

UI 只提供 `1`、`0.5`、`0.25`、`0.1 V/div` 这四个 sigrok 驱动真实支持的量程。探头倍率和量程只能在连接前修改；采集期间不伪造硬件触发功能。

## 连续性与吞吐边界

libsigrok 0.5.2 的 Hantek 驱动按较大 USB transfer 交付模拟回调。桥通过滑动 `SR_CONF_LIMIT_MSEC` 目标约 40 ms 改善主机交付节奏，但不会针对每个窗口重新打开设备。任一通道长期无法配对时，桥会报错停止，而不是无限制缓存或静默拼接不同步数据。

驱动没有设备端 FIFO 溢出、硬件采样序号或跨 USB 回调缺口元数据。因此桥的连续序号只说明 stdout 用户态输出没有跳帧，不能作为无间隙 ADC 采样证明。

2026-08-09 在 `ascend8t`、libsigrok 0.5.2、USB 2.0 高速环境中，`scripts/measure_sigrok_streaming.sh` 记录的主机有效回调速率如下：

| 请求速率 | 单通道有效速率 | 双通道每通道有效速率 |
| ---: | ---: | ---: |
| 1 MS/s | 0.993 MS/s | 0.991 MS/s |
| 8 MS/s | 7.769 MS/s | 7.635 MS/s |
| 16 MS/s | 15.169 MS/s | 14.742 MS/s |
| 24 MS/s | 20.843 MS/s | 20.011 MS/s |
| 30 MS/s | 20.450 MS/s | 19.688 MS/s |
| 48 MS/s | 20.393 MS/s | 19.615 MS/s |

这是一项主机回调吞吐记录，不是 USB 总线带宽、ADC 时钟或无缺口采样证明。原始 JSONL 位于板端 `data/sigrok_throughput/`。

## 常见停止点

| 现象 | 含义与处理 |
| --- | --- |
| `Resource busy` | 有其他 Hantek 使用者。关闭仪表盘、PulseView 或 `sigrok-cli`，不强杀未知进程。 |
| `writable=False` | 当前用户缺 USB 权限。由管理员安装 `scripts/udev/60-case5-hantek6022.rules` 后拔插设备；不要 sudo 启动 Qt。 |
| `sigrok capture bridge not found` | 未编译或路径错误。回到 README 的桥编译步骤。 |
| 量程设置失败 | 只使用界面暴露的四个驱动支持档位。 |
| `NPU unavailable` | 初始化阶段没有可用 Hantek OM/CANN。验证 OM；CPU FFT 不会顶替频谱。 |
