# Case 5 脚本索引

本目录只保存职责明确的板端脚本。完整安装与实验顺序以 [根 README](../README.md) 为准；不要把下面的索引当成另一套操作流程。脚本不会执行 `sudo`，系统包与 udev 规则必须由管理员手动处理。

## 日常运行

| 脚本 | 作用 | 前置条件 |
| --- | --- | --- |
| `check_usb_device.sh` | 运行只读 PyUSB USB 诊断 | 已激活板端 `base` 环境 |
| `build_sigrok_capture_bridge.sh` | 用系统 libsigrok 编译 Hantek `BridgeFrameV1` C 桥 | `gcc`、`pkg-config`、`libsigrok-dev` |
| `run_dashboard.sh` | 加载 CANN、激活 `base`、启动 Qt 仪表盘 | 需要的 OM、PySide6 和桥已就绪 |
| `start_dashboard.py` | 从项目根目录调用 Python 仪表盘入口 | 通常由 `run_dashboard.sh` 间接调用 |
| `run_rtl_sdr_npu_demo.sh` | 运行旧的固定 IQ DFT OM 教学 Demo | RTL-SDR DFT OM 已生成 |
| `run_rtl_sdr_npu_inference.sh` | 运行 accepted manifest 的实时 RTL-SDR NPU 服务 | `rtl-sdr`、FFTW、accepted OM/manifest |

`run_dashboard.sh`、`run_rtl_sdr_npu_demo.sh` 和 `run_rtl_sdr_npu_inference.sh` 都会 source CANN 环境并激活 `/usr/local/miniconda3` 的 `base`。外部 PulseView、`sigrok-cli`、GQRX、GNU Radio、SDR++ 或 `rtl_test` 占用设备时，脚本不会强制关闭它们。

## 评估与诊断

| 脚本 | 作用 | 说明 |
| --- | --- | --- |
| `measure_sigrok_streaming.sh` | 编译/运行 Hantek libsigrok 回调吞吐计数 | 不启动 Qt、不做 NPU 推理，也不证明 ADC 无间隙 |
| `benchmark_spectrum_efficiency.py` | 比较 Hantek 频谱计算路径 | 仅性能对照，不替代 OM 验收 |
| `run_volk_npu_benchmark.sh` | 准备并测量 VOLK/ORT/OM 对照 | 板端高级模型评估，见 [07](../docs/07_ascend310b_heterogeneous_signal_processing.md) |
| `benchmark_fftw.c`、`benchmark_rtl_iq_fftw.c` | FFTW C 基准源码 | 需要 `libfftw3-dev` |
| `benchmark_volk_kernels.c` | VOLK C 基准源码 | 仅基准用途 |

`udev/60-case5-hantek6022.rules` 是供管理员安装的 Hantek 用户权限规则。若诊断显示 `writable=False`，按根 README 的权限说明安装后物理拔插设备；不要用 sudo 启动仪表盘绕过问题。

## 输出位置

- Hantek 会话：`data/hantek_sessions/`
- RTL-SDR 实时检测：`data/rtl_sdr_npu_inference/`
- RTL-SDR 固定 DFT Demo：`data/rtl_iq_npu_demo/`
- Hantek 吞吐记录：`data/sigrok_throughput/`

各脚本从自身位置定位项目根目录，因此应始终在 `~/Documents/case5` 的完整源码树内使用，而不是复制到临时目录单独执行。
