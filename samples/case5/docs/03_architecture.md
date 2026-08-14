# 03 架构与 sigrok 数据流

本案例的 Hantek 主路径只有一个：`libsigrok` 的 `hantek-6xxx` 驱动。PulseView 和
`sigrok-cli` 可用于人工排查，但仪表盘不解析它们的文本输出，也不再加载示波器专用
Python 库。桥接进程使用同一套 libsigrok session，把模拟回调转换成项目已有的
`BridgeFrameV1` 二进制帧。

```text
Hantek 6022BE USB
        |
        v
libsigrok hantek-6xxx driver
        |  CH1/CH2 analog callbacks, firmware upload, V/div and samplerate
        v
sigrok_capture_bridge (C, one long-running session)
        |  BridgeFrameV1: sequence, host timestamp, rate, flags, [N, 2] float32
        v
acquisition/sigrok.py
        |  bounded subprocess, stderr diagnostics, rate/sequence validation
        v
processing.py
        |  continuous window assembly, CH2 unit conversion, per-window DC removal
        +-----------------------> session.py (raw .c5raw + JSONL indexes)
        v
npu.py / AnalysisService
        |  bounded latest-first queue, aclruntime.InferenceSession, OM inference
        v
controller.py -> PySide6/PyQtGraph dashboard
```

## 模块职责

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `acquisition/frame_protocol.py` | 校验、序列化和拆分 BridgeFrameV1 | USB、sigrok API、NPU |
| `acquisition/native/sigrok_capture_bridge.c` | 调用 libsigrok、加载易失固件、配置双通道、输出二进制帧 | Python 窗口化、NPU、Qt |
| `acquisition/sigrok.py` | 启停桥进程、读取 stderr、验证采样率和序号、探头倍率 | 解析 sigrok 私有结构、绘图 |
| `processing.py` | 连续窗口、CH2 单位换算、每窗口去直流、统计量 | USB 和模型推理 |
| `npu.py` | 加载 OM、执行 `aclruntime`、记录真实后端和耗时 | CPU FFT 或伪造 NPU 结果 |
| `session.py` | 有界原始数据和分析引用 | 绘图和设备控制 |
| `controller.py` | 连接采集、处理、NPU 和会话 | libsigrok 细节 |
| `ui/` | 触摸控制、波形、dB 频谱和瀑布显示 | 设备协议、校准决策 |

## 连续窗口与背压

libsigrok 的 Hantek 驱动默认按较大的 USB transfer 回调。桥接程序通过不断更新
`SR_CONF_LIMIT_MSEC` 的滑动期限，把单次回调目标限制在默认约 40 ms；这仍是一个
持续的 session，不是每个窗口重新打开 USB。一个回调可包含多个 10,000 点帧，帧序号
连续且同一回调共享主机接收时间戳。

Python 端将任意大小的回调帧拼接为固定 10,000 点分析窗口，窗口之间不重叠。分析队列
容量为 2，满时丢弃最旧窗口以控制延迟；会话写入队列也有界，满时记录存储丢帧。桥接
输出被阻塞时，libsigrok 回调会受到背压，因此必须观察界面丢帧计数和 stderr 诊断。

libsigrok 0.5.2 的 Hantek 驱动没有设备端连续序号、FIFO 溢出或采样缺口字段。项目序号
只证明桥接输出没有在用户态丢帧，不能证明 ADC 时钟在 USB 回调之间无间隙。跨回调的相位、
功率和长时间频率分析必须把这个限制写入实验结论。

## 固定 NPU 契约

输入固定为 `[1, 2, 10000]`、1 MS/s、10 ms；输出为 `[1, 2, 201, 1]`，频率轴为
0--20 kHz、100 Hz 间隔。CPU 只执行采集搬运、单位换算、每窗口去直流和界面刷新。频谱
功率和瀑布行必须来自板端 OM；没有 OM 或 CANN 失败时，界面显示 `NPU unavailable`，
不得用 CPU FFT 冒充 NPU。

## 线程边界

- sigrok 桥进程拥有 USB 和 libsigrok session；Python 线程只读取其 stdout。
- 控制器回调只做有界数组处理和队列提交，不在 Qt 线程访问 USB。
- `AnalysisService` 独占 NPU worker，UI 定时器只消费结果并绘图。
- `SessionWriter` 独占磁盘写入，原始帧、模型版本、后端和分析延迟可追溯关联。

## 设备切换

PulseView、`sigrok-cli` 和仪表盘不能同时打开 6022BE。切换前先停止当前程序；如果
设备仍显示 `1d50:608e`，先物理拔插，使 libsigrok 下次扫描重新执行易失 `fx2lafw`
固件加载。诊断命令只枚举 USB，不上传固件：

```bash
python -m time_frequency_dashboard.acquisition.usb_diagnostics
```
