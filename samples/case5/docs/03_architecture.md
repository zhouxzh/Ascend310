# 03 系统架构与数据合同

本文件解释 Case 5 的两条数据路径如何共享同一套工程约束。实际安装和运行顺序见 [README](../README.md)；这里不重复板端命令。

## 两条主线

```text
Hantek 6022BE                         RTL-SDR
    |                                     |
libsigrok hantek-6xxx                 rtl_sdr CU8 stdout
    |                                     |
sigrok_capture_bridge                 RtlSdrService
    | BridgeFrameV1                     | fixed IQ batches
    +---------------+---------------------+
                    |
          CPU: 校验、窗口、归档、确定性预处理
                    |
          有界队列（满时丢弃旧任务）
                    |
      NPU: 固定 DFT OM 或 accepted 神经网络 OM
                    |
      UI: 真实后端、延迟、丢弃计数、会话/JSONL 路径
                    |
      原始样本 + 模型版本 + 结果 + 延迟的可追溯记录
```

Hantek 和 RTL-SDR 不共享 USB 协议，却共享以下规则：采集端只产生规范化数据，CPU 只做确定性准备，NPU 结果必须标明实际后端，原始数据与分析结论必须能回溯到同一会话。`InstrumentCoordinator` 在应用内保证两条路径互斥；外部程序的设备占用仍需操作员先关闭。

## 三种固定合同

| 合同 | 数据形状与采样 | CPU 工作 | NPU 工作 | 使用边界 |
| --- | --- | --- | --- | --- |
| Hantek DFT | `[1,2,10000]`，1 MS/s，10 ms | 帧解码、拼窗、CH2 声明换算、每窗去直流 | `[1,2,201,1]` 固定 Hann DFT 功率 | 波形、频谱、瀑布必须来自 OM |
| IQ DFT Demo | `[16,2,1024]`，2.048 MS/s | CU8 解码、每窗复数去直流 | `[16,1024]` 固定 Hann 复数 DFT 功率 | 教学/数值对照，不接入 SDR 工作区 |
| RTL-SDR 检测 | 当前为 `[1,3,1024,1024]`，2.048 MS/s | CU8 解码、FFTW Blackman 时频图、后处理 | accepted OM 的神经网络检测 | 时频底图是 CPU 模型输入，检测框才是 OM 输出 |

Hantek 频率轴固定为 0--20 kHz、100 Hz 间隔。它显示相对 `1 V^2` 的未校准能量，不提供计量级幅值、相位或功率结论。RTL-SDR 频率标注由设置的中心频率与 PPM 推导，是名义频率而非 RF 校准值。

## Hantek 帧与连续窗口

`time_frequency_dashboard/acquisition/native/sigrok_capture_bridge.c` 拥有唯一的 libsigrok session。它通过 `hantek-6xxx` 驱动配置双通道、采样率和 V/div，在需要时加载易失固件，并把两个模拟回调配对为 `BridgeFrameV1`：

```text
header: magic, version, sequence, host_monotonic_time,
        actual_sample_rate, sample_count, channel_count, clipping_flags, payload_length
payload: [N, CH1, CH2] little-endian float32
```

Python 端允许 stdout 任意分片，验证版本、序号和实际采样率，再把连续样本拼成不重叠的 10,000 点窗口。默认约 40 ms 的桥接回调目标只改善主机交付节奏，不改变“一个长期 session”的事实。

libsigrok 0.5.2 没有提供 6022BE 设备 FIFO 溢出、硬件采样序号或跨 USB 回调缺口字段。因此，连续的 `BridgeFrameV1.sequence` 只能说明桥接 stdout 在用户态没有跳号，不能证明 ADC 时钟无间隙。任何跨回调相位、功率或长时间频率结论都必须带上这一限制。

## RTL-SDR 批与模型输入

`RtlSdrService` 从 `rtl_sdr` 接收交错 CU8 I/Q，按 accepted manifest 规定的采样率和完整窗口数规划采集。检测模型的预处理合同锁定 CU8 解码、1024 点无重叠 Blackman、FFTW forward、功率、峰值归一化、`fftshift`、垂直翻转、dB 映射和 RGB 复制等步骤；任何形状、哈希或合同不符都会在启动前拒绝。

检测路径的 CPU FFTW 不是 NPU FFT，也不会因为 CPU 计算了时频图就失去 NPU 属性：NPU 负责已准入神经网络 OM 的推理，JSONL 必须记录 `NPU (Ascend 310B)` 后端。当前 YOLO 权重只具备单类信号区域监督；显示的候选标签不能被解释为 51 类调制识别结论。

## 队列、线程和背压

| 部件 | 所属线程/进程 | 有界行为 |
| --- | --- | --- |
| sigrok 桥 | 子进程 | 独占 Hantek USB；stderr 仅用于诊断 |
| Hantek 处理 | Python 工作线程 | 固定窗口；分析队列容量 2，满时丢旧窗 |
| RTL 采集/推理 | `RtlSdrService` 工作线程 | 默认推理队列容量 4，满时丢旧批并记录 |
| NPU | 单一 OM worker | 真实 `aclruntime.InferenceSession`；不回退到 CPU 推理 |
| 会话写入 | 独立写入线程 | 原始数据和索引有界；写入压力记录为丢帧/丢批 |
| Qt | 主线程 | 定时读取快照和绘图，不访问 USB 或直接运行 NPU |

有界队列选择较新的分析任务，目的是避免图形刷新或磁盘写入使端到端等待无限增长。它以可能少量丢弃换取可解释的最大延迟；丢弃数不是静默优化，必须显示并写入会话。

## 会话与结果追溯

Hantek 会话默认写入 `data/hantek_sessions/`，包含原始 `.c5raw` 分块、索引、`manifest.json`、`analysis.jsonl` 和 `summary.json`。一条分析记录可关联输入窗口、模型形状/后端、频谱能量、时间戳和延迟。

RTL-SDR 运行默认写入 `data/rtl_sdr_npu_inference/`，包含原始 `.cu8`、`inference.jsonl`、运行元数据与可选 `qc_summary.json`。严格 QC 会重算同一 CU8 的 SHA256，并检查 JSONL 中已记录的后端和时延字段；它不重新运行硬件，也不证明天线性能、RF 幅度或检测准确率。

## 状态和失败语义

- 初始化阶段找不到 OM、CANN 或可用 NPU 时，界面显示 `NPU unavailable`，不会给出 CPU 频谱或 CPU 检测冒充结果。
- 已启动的 RTL-SDR 运行若输出出现 NaN、Inf、输出形状错误或运行时异常，该次 run 状态为 `failed`，错误写入 JSONL；它不应被概括为同一种 `NPU unavailable` 状态。
- 暂停显示只停止刷新，不释放设备。只有停止或退出会依次停止采集、排空/关闭队列并释放 NPU runner，之后才可切换另一条路径。
