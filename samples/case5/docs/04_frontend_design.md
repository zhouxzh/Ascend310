# 04 实时信号分析仪表盘前端

## SDR workspace

The PySide6 main window provides Hantek and RTL-SDR top-level workspaces.
Restoring the last selected tab only restores presentation state: it does not
connect a device, start capture, or create an NPU runner. Airspy is not exposed
by this UI.

The SDR page is backed by the same `RtlSdrService` used by
`python -m time_frequency_dashboard.rtl_sdr_npu_inference` and
`bash scripts/run_rtl_sdr_npu_inference.sh`. It lists only raw-IQ OM models that
are `accepted` and have passed manifest/ONNX/OM hash validation plus
live-deployment checks. Validation is repeated before opening `rtl_sdr`; missing
OM, a shape mismatch, an unavailable NPU, or a failed FFTW/NPU preflight is an
explicit start failure, never a CPU fallback. The historical
`rtl_sdr_npu_demo` fixed-DFT module is not used by this workspace or by the
shared service.

Hantek and RTL-SDR are globally exclusive. `InstrumentCoordinator` owns the
shared acquisition/NPU lifecycle and assigns a generation to each run. A second
start request is rejected until the first source has stopped, its queues are
drained, and its runner is released. Results from an old generation must not be
rendered into a new session.

The SDR controls record device index, nominal centre frequency, sample rate,
gain mode/value, PPM, RF-input context, duration, and estimated CU8 size. A
manifest-fixed sample rate is displayed as fixed. The estimate is raw CU8 size;
the service separately requires the estimate plus its disk safety margin before
opening a live receiver. Development CU8 replay and synthetic sources are
hidden unless `--sdr-developer-sources` is supplied, and the page then keeps a
visible development-input warning.

Display definitions are intentionally narrow: the I/Q time plot is decoded,
uncalibrated CU8 data in `[-1, 1]`; the constellation uses the exact model
preprocessing for raw-IQ classification and otherwise the capture IQ; a
detection model preview is the actual CPU FFTW/Blackman input sent to the OM.
The NPU performs model inference/detection, not the FFT. Detection frequency
labels are nominal centre-frequency offsets with configured PPM, not calibrated
RF measurements. The displayed frequency range is derived from nominal centre
frequency plus that offset. Top-K or detection boxes are shown according to
model task; detector rows retain at most 64 newest-first mappings for the
current generation (the table renders its newest 20 rows).

Each run writes CU8 and JSONL. The UI may show a read-only strict QC summary
only after a completed run; partial, cancelled, damaged, or CPU-backed records
cannot become acceptance evidence. Real RTL-SDR and Ascend validation remains
a board operation, outside local Windows UI tests.

When a run generation changes, the workspace clears its plots, overlays, and
result history before accepting a new frame. A malformed display-only frame is
shown as an explicit preview error instead of being allowed to terminate Qt's
periodic refresh loop.

The dashboard startup defaults Hantek sessions to `data/hantek_sessions/`,
SDR run artifacts to `data/rtl_sdr_npu_inference/`, and accepted manifests to
`models/generated/inference/`. `--sessions`, `--sdr-output-root`, and
`--sdr-models-dir` override those roots without changing an admitted model's
fixed input contract.

The Hantek workspace continues to use the Hantek 6022BE sigrok backend. The SDR
workspace currently exposes only the validated RTL-SDR path; other receiver
types remain intentionally absent until they have equivalent acquisition and
NPU validation.

## 版面

```text
顶部：Hantek/sigrok 状态 | NPU 状态 | 连接 | 暂停显示 | 停止
左侧：量程、探头倍率、CH2 显示、频带与色标参数
中间：波形 / 频谱与瀑布 两个页面
底部：分析帧、USB 回调块、丢帧、NPU 耗时、会话路径
```

波形页始终显示 CH1 电压；勾选后才显示按 Little Bee 声明灵敏度换算的 CH2 电流。此开关不会改变底层双通道
sigrok 采集、CH2 单位换算或 OM 输入。Little Bee 的去零由探头自身完成，界面不提供软件校零。

频谱与瀑布页参考 QSpectrumAnalyzer 的上下结构：上方是当前 NPU DFT dB 曲线、光标和
峰值保持，下方是同一频率轴的瀑布，右侧是可拖动 `HistogramLUTItem` 色标。模型输出
0--20 kHz 的 201 个频点，间隔 100 Hz；`CAL` 的 1 kHz 方波及奇次谐波可在这里观察。
界面没有 CPU FFT 曲线或后备频谱。

## 数据和线程边界

sigrok 桥进程输出 BridgeFrameV1，Python 采集线程读 stdout 并向控制器提交帧。处理器、
NPU worker 和会话写入各自运行在非 Qt 线程；Qt 定时器只读取快照并绘图。因此绘图变慢时，
有界分析队列会丢弃旧窗口而不是无限增长内存。

NPU 不可用时，顶部明确显示 `NPU unavailable`，频谱和瀑布没有新 NPU 行。暂停显示只冻结
界面，停止才释放 USB 设备。

## 色彩与参数

- CH1 为青色，CH2 为琥珀色；绿、黄、红分别表示正常、待校准/限制和错误。
- dB 显示为 `10*log10(max(E, 1e-12) / 1 V²)`，标注“相对 1 V²，未校准”，不宣称 dBV、dBFS 或 dBm。
- 两通道各自维持色标。Auto 在前 20 行用 2%/98% 分位数估计，至少 40 dB 跨度、最低 -120 dB，随后锁定。
- sigrok 的 Hantek 驱动只接受 `1、0.5、0.25、0.1 V/div`；量程和探头倍率仅能在连接前修改。
- 瀑布历史可在 20--500 行之间调整；峰值保持、色标和 CH2 可见性可以在采集期间改动。

## 参考项目

- [QSpectrumAnalyzer](https://github.com/xmikos/qspectrumanalyzer)：频谱/瀑布上下布局、色标和缩放交互。
- [inspectrum](https://github.com/miek/inspectrum)：大面积时频观察和光标交互。

本项目使用 PyQtGraph API 实现自己的控件，没有复制完整上游应用或图片资源；若未来复用
GPL 代码，必须保留原始版权和许可证声明。
