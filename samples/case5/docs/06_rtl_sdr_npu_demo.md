# 06 RTL-SDR 实时 NPU 服务与旧 DFT 教学路径

## 共享实时服务和 PySide6 SDR 工作区

当前无 GRC 的 RTL-SDR 主路径由 `RtlSdrService` 实现。PySide6 SDR 工作区和
`python -m time_frequency_dashboard.rtl_sdr_npu_inference` 共同调用这项服务；
`bash scripts/run_rtl_sdr_npu_inference.sh` 是同一入口的板端封装。它们共享采集、预处理、OM、
JSONL 和 CU8 归档代码。选择仪表盘页签不会自动连接或开始采集，Airspy 也不在界面中出现。

Before capture the UI rechecks an `accepted` raw-IQ manifest, ONNX/OM hashes,
fixed sample-rate/input-shape constraints, disk capacity, FFTW setup, and the
real NPU backend. A failed preflight stops startup with a diagnostic; CPU
fallback is forbidden. Hantek and RTL-SDR cannot run together in the dashboard.
The application does not terminate external GQRX, GNU Radio, SDR++, or
PulseView processes.

频谱检测模型还要求板端已安装 `libfftw3-single3`；缺少时，启动前的 FFTW 初始化会明确失败，
不会以 NumPy 或其他 CPU 路径代替模型输入。若要编译独立 FFTW 基准，还需另行安装
`libfftw3-dev`。

For a raw-IQ classifier, the time plot is decoded uncalibrated CU8 and the
constellation is the exact model-preprocessed I/Q tensor. For a spectrum
detector, the preview is the CPU FFTW Blackman spectrogram actually supplied to
the OM; the NPU performs the neural detector rather than the FFT. Frequency
annotations are nominal (configured centre frequency and PPM) and are not an
RF calibration claim. A completed run can receive read-only strict QC, but the
UI does not create admission evidence.

The dashboard writes Hantek sessions below `data/hantek_sessions/` and SDR
captures below `data/rtl_sdr_npu_inference/` by default. Launch it on the board
through `bash scripts/run_dashboard.sh --sigrok-bridge build/sigrok_capture_bridge`;
the wrapper loads CANN and activates `base`. Its optional `--sessions`,
`--sdr-output-root`, `--sdr-models-dir`, and `--sdr-developer-sources` arguments
only affect UI paths/source visibility, not the OM admission checks.

共享 CLI 支持 `rtl`、`cu8` 和 `synthetic` 来源；正常界面只暴露 RTL-SDR。它从安装的
`rtl_sdr` 读取有限 CU8 复数 IQ，再按照 manifest 的合同将已准入 raw-IQ 分类模型或频谱检测模型
送入 OM。分类输出为 Top-K；检测输出为检测框。频谱检测模型的 FFTW Blackman 图由 CPU 构造，NPU
只执行神经网络检测，不能把该显示或模型输入称为 NPU FFT。结果 JSONL 的
`inference_backend` 必须为 `NPU (Ascend 310B)`，否则不能把本次运行称为 NPU 验收。

在实际 310B 上、且已存在通过准入的 OM/manifest 时，当前共享入口例如：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_inference.sh \
  --source rtl \
  --center-frequency 100000000 \
  --rf-input-context unknown \
  --duration-seconds 10
```

`--duration-seconds` 是实时采集的下限。为保持每次 OM 输入完整，服务会将请求时长向上取整到
固定模型窗口的整数倍，并将请求/计划时长、样本数和批数写入 JSONL。对于当前
`[1,3,1024,1024]`、2.048 MS/s 检测模型，一个窗口为 `0.512 s`，因此 `10 s` 会采集计划的
20 个完整窗口，即 `10.240 s` 和 `40 MiB` CU8。UI 的容量估算和服务的磁盘预检使用同一计划。
实时 `rtl` 来源禁止 `--max-batches`，避免截断运行被误写为可 QC 的正常完成；仅开发用的 `cu8`
或 `synthetic` 来源可使用它限制回放/合成批数。

使用 `--manifest <accepted-manifest.json>` 可选择特定已准入模型。真实 RTL-SDR、ACL/OM 和
射频输入状态均需在板端自行验证；运行时必须将 `--rf-input-context` 如实改为实际状态，本地
Windows 测试不能证明它们。

## 旧版固定 DFT 教学路径（不供 PySide6 SDR 工作区或共享服务使用）

以下内容保留 `time_frequency_dashboard.rtl_sdr_npu_demo` 与
`scripts/run_rtl_sdr_npu_demo.sh`，只用于固定 Hann 窗复数 DFT 的教学、数值对照和历史记录。
该模块不会被 `RtlSdrService` 或 SDR 工作区调用，也不能用于描述当前分类/检测服务。

### 为什么不直接写 GNU Radio Python Block

GNU Radio 的 CUDA 与 OpenCL 模块采用的关键思路是：把连续样本整理为固定大小的向量或批次，
减少每个样本一次的加速器调用，并单独测量主机搬运与设备计算。这个 Demo 保留了该设计，
但没有把 CUDA/OpenCL API 错当成昇腾 API。

当前板端实测环境中，系统 GNU Radio 3.10.1.1 的 Python 绑定位于系统 Python 3.10，而
可用 `aclruntime` 位于 Conda `base` 的 Python 3.9。二者不能安全地放入同一个 Python
进程。因此第一版直接调用 `rtl_sdr`，让 CANN Python 进程独占 OM 推理；这比在 GNU Radio
Python block 中混用两个 Python ABI 更可靠。

后续若要接回 GNU Radio Runtime，应选其一：

- 用 C++ GNU Radio 自定义块调用 ACL/CANN C++ 运行时；或
- 让系统 Python 的 GNU Radio 流图经 Unix socket/ZeroMQ 将 IQ 批次送给 Conda NPU 工作进程。

第一版不实现上述跨进程或 C++ 方案，避免把 IPC 延迟或 Python ABI 错误伪装成 NPU 加速。

### 固定 DFT 批处理合同

默认模型文件为 `models/generated/rtl_iq_dft_2048ksps_b16_n1024.om`：

| 项目 | 值 |
| --- | --- |
| RTL-SDR 采样格式 | CU8，交错 I/Q |
| 采样率 | 2,048,000 S/s |
| NPU 输入 | `[16, 2, 1024]`，维度 1 为 I/Q |
| 每次推理的输入时长 | 8 ms |
| NPU 输出 | `[16, 1024]` 线性复数频谱功率 |
| 频率顺序 | `fftshift`，从 -1.024 MHz 到接近 +1.024 MHz |
| 频率间隔 | 2 kHz |
| CPU 职责 | CU8 解码、有限采集、每窗口复数去直流、记录 |
| NPU 职责 | Hann 窗复数 DFT 的实/虚投影和功率相加 |

模型以固定的 Hann 窗正弦/余弦矩阵表示 DFT，ONNX 只使用 `Reshape`、`MatMul`、`Mul` 和
`Add`。这样 ATC 不依赖 ONNX FFT 算子，也不需要 PyTorch。

### 板端准备

必须在开发板而非本地 Windows 工作区完成 ATC 和 OM 验证：

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base

command -v rtl_sdr
python -m time_frequency_dashboard.model.prepare_rtl_iq_model
python -m time_frequency_dashboard.model.verify_rtl_iq_model
```

若 `command -v rtl_sdr` 没有输出，需要用户手动安装 RTL-SDR 工具包后再继续；本项目脚本
不执行 `sudo`。当前板端已验证的工具版本为 `rtl-sdr 2.0.2`。

`verify_rtl_iq_model` 使用正、负两个 bin 对齐的合成复数正弦波比较 ONNX Runtime 和 OM，
只证明模型图与 NPU 数值链路一致，不证明天线、调谐或射频幅度准确。

### 运行

先用合成 IQ 完成一次没有射频变量的 NPU 冒烟测试：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_demo.sh --source tone --batches 2
```

确认没有 GQRX、GNU Radio、SDR++、`rtl_test` 或其他程序占用 RTL-SDR，并确认天线或受控实验线缆
已经接好后，再接收实际信号：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_demo.sh \
  --source rtl \
  --center-frequency 100000000 \
  --gain-db 40.2 \
  --batches 8
```

若不写 `--gain-db`，该命令使用自动增益。2026-08-12 在 `ascend8t` 的当前天线和 100 MHz
环境中，自动增益的 CU8 端点比例约 15%，而请求 `40.2` dB（驱动实际量化为 40.2 dB）未见
明显字节级削顶，故上面的命令把它作为该环境的示例起点；它不是其他频率、天线或地点的通用标定。
晶振修正可附加 `--ppm-error <实测值>`。采集文件 `.cu8` 和对应结果 `.jsonl` 都保留在
`data/rtl_iq_npu_demo/`，结果可以回溯到具体的原始 IQ 数据。

板端的 `rtl-sdr 2.0.2` 曾观察到 `-n` 给定有限样本数后仍持续输出。因此 Demo 不依赖
`-n` 停机：它从 `rtl_sdr -` 的 stdout 精确读取 `2 * 批次数 * 批大小 * 窗口长度` 字节，
然后结束录制进程。对应 `.rtl_sdr.log` 出现 `Signal caught, exiting!` 和 `User cancel,
exiting...` 是这条受控结束路径的正常诊断，不表示设备失效。

想比较批处理 DFT OM 与 CPU NumPy FFT 的耗时，可显式加入：

```bash
bash scripts/run_rtl_sdr_npu_demo.sh --source rtl --batches 32 --measure-cpu-reference
```

`cpu_numpy_fft_reference_ms` 只是优化评估字段。CPU FFT 是复杂度更低的算法，可能比这个固定
矩阵 DFT 模型快；这个旧教学命令的频谱 JSON 仍只来自 OM。这个 Demo 证明的是 310B NPU
路径真实可用，不能在没有实测数据时承诺端到端一定快于 CPU FFT。

### 2026-08-11 板端实测

在 `ascend8t` 上，RTL-SDR Blog V4（R828D）以自动增益、2.048 MS/s、中心频率 100 MHz
运行 `--source rtl --batches 8 --measure-cpu-reference`。该历史记录未保存天线/线缆上下文，
因此仅作为设备采集到 OM 的链路记录，不作为空口信号、接收质量或最终实时性验收。结果文件为板端
`data/rtl_iq_npu_demo/rtl_iq_npu_20260811T024807Z.jsonl`，对应 CU8 文件大小为 262,144
字节，即 131,072 个复数样本、64 ms 的射频窗口。驱动初始化和有限采集共计 687.895 ms；
这不是稳定流模式的处理延迟。

第一批的 `npu_inference_latency_ms` 为 1.674701 ms，批次代表 8 ms 的 IQ 数据；同时记录的
`cpu_numpy_fft_reference_ms` 为 1.808744 ms。该单次对照只用于说明记录格式和当前模型规模，
不能据此承诺不同批大小、频率、温度或负载下仍有同样速度。第一窗口的最大 OM 频率 bin 为
-104 kHz，相对 100 MHz 对应 99.896 MHz；这只表示当时接收链路中最强的离散 bin，不用于识别
任何电台或评价射频幅度。

### CPU/NPU 效率对照

使用已经保存的 CU8 文件，可以在不重新占用 RTL-SDR 的情况下运行可重复的计算基准：

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.benchmark_rtl_iq_efficiency \
  --input-cu8 data/rtl_iq_npu_demo/rtl_iq_20260811T024807Z.cu8 \
  --warmup 50 --iterations 300
```

基准将预处理后的 `[16, 2, 1024]` 批次留在主机内存，排除 RTL-SDR 启动、CU8 解码、去直流和
OM 加载时间。NPU 项仍包含 `Tensor` 创建、主机到设备搬运、OM 推理、设备到主机回传和输出复制。
它比较三条计算路径：

| 路径 | 含义 |
| --- | --- |
| CPU NumPy FFT | 全复数 FFT 与功率计算，代表应优先采用的常规 CPU 频谱算法 |
| CPU 稠密 DFT | 与 OM 相同的两个固定矩阵投影，用于比较同一算法 |
| Ascend OM | 板端实际 `aclruntime.InferenceSession` 端到端推理 |

2026-08-11 对 100 MHz 实测 IQ 的 8 个批次循环 300 次，得到如下 P50 延迟：

| 路径 | P50 延迟 | P95 延迟 | 相对 8 ms 批次的实时裕量 |
| --- | ---: | ---: | ---: |
| CPU NumPy FFT | 1.208 ms | 1.270 ms | 6.62x |
| Ascend OM | 1.618 ms | 1.641 ms | 4.95x |
| CPU 稠密 DFT | 6.168 ms | 6.421 ms | 1.30x |

同一基准入口另外直接调用 ARM `libfftw3f`（单线程）测得 P50/P95 为
`0.136/0.141 ms`，与 NumPy 和 OM 的输出最大绝对功率误差分别为
`2.24e-8` 和 `9.71e-5`。FFTW 的 `FFTW_MEASURE` 规划耗时约 11.3 s，属于一次性启动成本，
没有计入上面的重复执行延迟。

因此，FFTW CPU 比当前 OM 固定 DFT 快约 11.9x，NumPy CPU FFT 比 OM 快约 1.34x；但 OM
比同算法的 CPU 稠密 DFT 快约 3.81x。三条路径都满足本批次的实时预算。OM 模型首次初始化为
约 1.46 s，已从上表排除；一次性
短采集不应把这个启动成本遗漏。基准 JSON 同时保存数值误差和输入 SHA-256，方便复验。

### 验收和边界

一次有效的实机记录至少应满足：

1. `.jsonl` 的运行元数据中记录 `source: rtl`、CU8 文件路径、采样率、中心频率和 OM 路径；
2. 每个 `npu_spectrum_result` 的 `inference_backend` 是 `NPU (Ascend 310B)`；
3. 每个结果包含 NPU 推理耗时、峰值相对频率、绝对频率和模型输入输出形状；
4. 切换频点或插入已知窄带信号后，`peak_frequency_hz` 有相应变化。

RTL-SDR 的采样时钟精度、增益、镜像、直流泄漏、天线与环境干扰都需要单独校准。该 Demo
不提供计量级功率、占用带宽或法规合规结论，也不替代频谱仪。
