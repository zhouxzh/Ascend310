# 实时信号分析仪表盘

Case 5 是一个面向昇腾 310B 的边缘时频分析教学案例。Hantek 6022BE 通过
USB 和 **libsigrok 的 `hantek-6xxx` 驱动**提供 CH1/CH2 两通道数据帧；当前已验证的是
CH1 的 `CAL` 方波连通与固定 DFT NPU 链路。Little Bee B1 的 CH2 电流换算仍是声明参数下的
扩展任务，尚未完成真实接入和本机标定。CPU 负责搬运和确定性预处理，Ascend 310B NPU 负责
固定 DFT 模型推理，PySide6/PyQtGraph 负责触摸显示。SDR 工作区独立只暴露已经过采集与 NPU
验证的 RTL-SDR 路径。未来设备必须先提供同等级的真实采集和 NPU 验收，才可以加入界面。

## 阅读导航

| 顺序 | 内容 |
| --- | --- |
| 01 | [Hantek 6022BE、sigrok 驱动和实机采集](docs/01_hantek6022be.md) |
| 02 | [Little Bee B1、电流换算和安全边界](docs/02_little_bee_b1.md) |
| 03 | [sigrok 数据流、连续窗口和线程边界](docs/03_architecture.md) |
| 04 | [Qt/PyQtGraph 前端设计](docs/04_frontend_design.md) |
| 05 | [第三方来源和 GPL-3.0-only 说明](docs/05_third_party_licenses.md) |
| 06 | [RTL-SDR 实时 NPU 服务与旧 DFT 教学路径](docs/06_rtl_sdr_npu_demo.md) |
| 07 | [昇腾 310B 异构信号处理评估与 SDR-NPU 指引](docs/07_ascend310b_heterogeneous_signal_processing.md) |

## 目录职责

```text
case5/
├── time_frequency_dashboard/                 # 采集、处理、NPU、会话和 Qt 界面
│   └── acquisition/native/                   # libsigrok 二进制桥源码
├── docs/                                     # 编号教学文档
├── scripts/                                  # 板端编译、启动和实测脚本
├── tests/                                    # pytest 测试
├── models/                                   # 板端生成的 ONNX/OM 和元数据
├── build/                                    # 板端桥接可执行文件（不提交）
└── data/                                     # 运行时会话和实测日志（不提交）
```

## 固定分析契约

- sigrok 实际采样率必须返回 `1,000,000 S/s`；桥接帧包含序号、主机接收时间、采样率、
  削顶标志和交错双通道 `float32` 样本。
- Python 将连续流分成不重叠的 `[2, 10,000]` 窗口，输入 OM 为 `[1, 2, 10,000]`，
  输出为 `[1, 2, 201, 1]`，频率轴为 0--20 kHz、100 Hz 间隔。
- NPU 输出的原始线性能量写入 `analysis.jsonl`；界面才转换为
  `10*log10(max(E, 1e-12) / 1 V²)`，标注“dB（相对 1 V²，未校准）”。
- Little Bee 的去零由探头本身完成；在完成真实接入和标定前，仪表盘对 CH2 的青色档、1 MHz、1 V/A、1 匝换算只作为配置与趋势参考。
- 分析队列容量为 2，存储和内存有界；任何丢帧都会在状态栏和会话中记录。

libsigrok 0.5.2 的 Hantek 驱动没有设备端 FIFO 溢出、采样序号或跨 USB 回调缺口元数据。
桥接序号只能证明用户态 stdout 没有丢帧，不能把数据宣传为经过设备证明的无间隙 ADC 流。

## 板端准备

以下步骤只在 Ascend 310B 的 `base` Conda 环境执行。本地 Windows 工作区不安装 CANN、
ATC、ACL、OM 或硬件驱动。系统包由用户手动安装，程序不会执行 sudo。

### 1. 必要系统包

sigrok 采集桥需要 `libsigrok-dev`、`sigrok-cli`、`gcc` 和 `pkg-config`；频谱检测模型的
FFTW 预处理还需要运行库 `libfftw3-single3`。如果板端尚未安装，
请手动执行：

```bash
sudo apt-get update
sudo apt-get install -y libsigrok-dev sigrok-cli gcc pkg-config libfftw3-single3
```

触摸屏 Qt X11 若报告缺少 `libxcb-cursor0`，再手动执行：

```bash
sudo apt-get install -y libxcb-cursor0
```

Python 用户态依赖写在 `requirements-board.txt`：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m pip install -r requirements-board.txt
```

其中 `PySide6` 与 `PyQtGraph` 是仪表盘所需的用户态依赖；它们不包含 CANN、ACL 或 OM
运行时。

### 2. 编译 sigrok 桥

关闭 PulseView、`sigrok-cli` 和旧仪表盘，进入固定目录：

```bash
cd ~/Documents/case5
bash scripts/build_sigrok_capture_bridge.sh
```

桥接程序会链接系统 libsigrok，输出到 `build/sigrok_capture_bridge`。它负责 libsigrok
固件上传、Hantek 量程/采样率配置和单一连续 session；Python 不直接操作 USB。

### 3. 生成和验证 OM

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.model.prepare_models
python -m time_frequency_dashboard.model.verify_npu_model
```

模型生成使用 NumPy 和 ONNX 原生算子，不依赖 PyTorch。只有 OM 成功加载并实际推理后，
UI 才显示 `NPU (Ascend 310B)`。

### 4. RTL-SDR 实时 NPU 服务和旧 DFT 教学路径

PySide6 SDR 工作区和无界面 CLI 共同使用 `RtlSdrService`。当前共享入口是
`python -m time_frequency_dashboard.rtl_sdr_npu_inference`，或等价的
`bash scripts/run_rtl_sdr_npu_inference.sh`；两者复用采集、预处理、OM、JSONL 和 CU8
归档代码。界面不会自动连接或开始采集。

SDR 工作区只显示 RTL-SDR（不显示 Airspy），且只加载 `accepted` 的 raw-IQ OM 模型。每次启动
都会重新核验 manifest、ONNX/OM 哈希、实时部署检查和输入合同；没有 CPU 推理 fallback。Hantek
与 RTL-SDR 互斥，必须在当前设备和 NPU 资源完全释放后才可切换。应用不会自动终止外部的 GQRX、GNU
Radio、SDR++ 或 PulseView。

在已完成模型准入和 OM 板端验证的 Ascend 310B 上，可通过共享服务运行有限时长的 RTL-SDR 采集：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_inference.sh \
  --source rtl \
  --center-frequency 100000000 \
  --rf-input-context unknown \
  --duration-seconds 10
```

当存在多个已准入模型时，使用 `--manifest <accepted-manifest.json>` 明确选择。`cu8` 和
`synthetic` 仅用于 CLI 开发或复现；正常界面不会显示这两类输入。详细的数据合同、CPU/NPU
职责与验收边界见 [06 RTL-SDR 实时 NPU 服务与旧 DFT 教学路径](docs/06_rtl_sdr_npu_demo.md) 和
[07 昇腾 310B 异构信号处理评估与 SDR-NPU 指引](docs/07_ascend310b_heterogeneous_signal_processing.md)。
运行时应将 `--rf-input-context` 如实改为实际的天线或受控线缆状态；`unknown` 不是硬件验收声明。

#### 旧版固定 DFT 教学路径（不供 SDR 工作区或共享服务使用）

`time_frequency_dashboard.rtl_sdr_npu_demo` 和
`scripts/run_rtl_sdr_npu_demo.sh` 保留为固定 Hann 窗复数 DFT 的独立教学与基准路径。它的
`[16, 2, 1024]` 输入、完整频谱输出和延迟字段不代表当前 SDR 工作区的分类/检测模型路径。

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.model.prepare_rtl_iq_model
python -m time_frequency_dashboard.model.verify_rtl_iq_model
bash scripts/run_rtl_sdr_npu_demo.sh --source tone --batches 2
```

其已保存 IQ 的 CPU/NPU 对照同样只服务于旧 DFT 教学路径：

```bash
python -m time_frequency_dashboard.benchmark_rtl_iq_efficiency \
  --input-cu8 data/rtl_iq_npu_demo/rtl_iq_20260811T024807Z.cu8 \
  --warmup 50 --iterations 300
```

该对照显示小型 FFT 应优先使用 CPU FFTW；固定 DFT OM 不应被表述为 FFTW 的替代品。

### 5. USB 检查与启动

先确认没有其他程序占用设备：

```bash
cd ~/Documents/case5
python -m time_frequency_dashboard.acquisition.usb_diagnostics
```

如果设备状态是 PulseView 的 `1d50:608e`，停止 PulseView 后物理拔插一次，再运行诊断。
若 `writable=False`，按 `scripts/udev/60-case5-hantek6022.rules` 配置权限；不要用 sudo
启动仪表盘绕过权限。

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
bash scripts/run_dashboard.sh --sigrok-bridge build/sigrok_capture_bridge
```

Hantek 会话默认写入 `data/hantek_sessions/`；RTL-SDR CU8/JSONL 默认写入
`data/rtl_sdr_npu_inference/`。可用 `--sessions`、`--sdr-output-root` 和
`--sdr-models-dir` 改为其他目录。正常 SDR 页只显示实时 RTL-SDR；只有显式传入
`--sdr-developer-sources` 才会显示 CU8 回放和合成 IQ，并持续标记为开发输入。

连接后先用探头接 6022BE `CAL` 标准输出，确认 CH1 方波、dB 曲线和 NPU 瀑布。只有在被测回路
的隔离和允许测量范围已确认，并完成 Little Bee 的去零、模式、匝数、幅值、相位与同步标定后，才可
将其接入 CH2 解释为电流。硬件无触发功能，软件不提供虚假的硬件触发按钮。

## sigrok 连续采样实测

吞吐计数器 `scripts/measure_sigrok_streaming.c`/`.sh` 只订阅 libsigrok 模拟回调，丢弃样本值，
不输出 CSV 或 GUI。2026-08-09 在 `ascend8t`、libsigrok 0.5.2 上，24 MS/s 请求实测为单通道
20.843 MS/s、双通道 20.011 MS/s/CH；30/48 MS/s 请求没有进一步提高有效速率。原始记录位于
板端 `~/Documents/case5/data/sigrok_throughput/`。

这证明 sigrok session 可以持续向主机交付样本，但不证明跨 USB 回调无间隙。驱动的大回调会使
数据以块状到达；本案例桥接通过滑动 `limit_time` 目标约 40 ms，改善 UI 更新和窗口延迟，
仍将设备端 FIFO/采样缺口标记为“未知”。

## 测试

本地只运行 Python/Qt 离屏测试：

```bash
python -m pytest -q
python -m compileall -q time_frequency_dashboard
```

真实 USB + OM 测试必须在板端执行：

```bash
export CASE5_RUN_HARDWARE_TESTS=1
python -m pytest -q tests/test_hardware_capture_and_inference.py
```

## 安全边界

6022BE 两通道共地，不是隔离输入。首阶段只测 `CAL`；后续扩展仅能接入已经确认隔离且适合该
探头的教学回路。未确认探头量程、接地和隔离方式前禁止接市电。Little Bee B1 的零点、增益、频响和相位尚未完成计量
标定，程序不输出计量级功率、相位或功率因数结论。

## 07. 异构信号处理与模型准入

VOLK/FFTW、ONNX Runtime CPU、Ascend OM 和 TorchSig/CVNET-rf/SignalIQ 候选模型的实测方法、
数值准入规则、板端版本、完整性能表和 RTL-SDR 实时 NPU 链路见
[07 昇腾 310B 异构信号处理评估与 SDR-NPU 指引](docs/07_ascend310b_heterogeneous_signal_processing.md)。
该文档中的模型清单由板端 `*.manifest.json` 生成；未通过准入的模型不会被默认实时入口加载。
目前通过准入的是固定形状 `[1,3,1024,1024]` 的 TorchSig YOLO11 频谱检测 OM。实时入口会校验
ONNX/OM 哈希、来源/数值/NPU 窗口预算字段和结构化 FFTW 频谱图合同；持续零丢批且主机流水线
不超窗口预算是独立的实时验收。完成 07 文档的板端模型准备后，默认入口会递归选择该 accepted
manifest：

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.rtl_sdr_npu_inference \
  --source rtl --center-frequency 100000000 --gain-db 40.2 \
  --rf-input-context antenna_connected --duration-seconds 10
```

实时 RTL-SDR 的 `--duration-seconds` 是采集下限，不会截断固定模型窗口。服务会按模型输入的
原始 IQ 样本数向上取整为完整批次，并在 JSONL 中同时记录请求时长、计划时长、计划样本数和计划
批数。当前 `[1,3,1024,1024]`、2.048 MS/s 的检测模型每批为 `0.512 s`；请求 `10 s` 会计划
20 批、`10.240 s` 和 `40 MiB` CU8。仪表盘的容量提示和启动前磁盘检查均使用这个整窗计划。
实时 `rtl` 来源不接受 `--max-batches`，以免把人为截断的采集误记为正常完成；该参数只可用于
开发来源的 CU8 回放或 synthetic 测试。

该命令可产生至少两批的短时流水线窗口检查；随后用
`python -m time_frequency_dashboard.model.attach_pipeline_realtime_evidence`
将完整 JSONL 绑定到新的 v4 sibling manifest，并用 `--verify-attached` 重新核验源 v3、JSONL 和
CU8 哈希。`--rf-input-context` 必须如实记录天线或实验线缆状态；只有 `antenna_connected`/`lab_cabled`、
至少 600 秒、零丢批且每批不超窗口的运行才会被标记为连续管线通过。它是结构化自报的可复核记录，
不是签名的硬件证明。未标注的
无线信号仍只证明真实 IQ 采集、预处理和 NPU 检测链路，不对其作准确率声明。

完成运行后，可用只读汇总器重新散列同一份 CU8，并输出记录的延迟分位数、I/Q 直流偏置和端点削顶率：

```bash
python -m time_frequency_dashboard.rtl_sdr_run_report \
  --inference-jsonl data/rtl_sdr_npu_inference/<run>/inference.jsonl \
  --output data/rtl_sdr_npu_inference/<run>/qc_summary.json
```

汇总器会拒绝与 JSONL footer 的字节数或 SHA256 不一致的 CU8 文件，并要求元数据、每个批次和
footer 都明确记录 `NPU (Ascend 310B)`。若只比较不同增益下的独立短采集，必须使用
`--capture-only --capture-cu8 <file>`；这种质量检查不能与另一轮的 NPU 延迟混写。联合报告要求
每条记录都有 NPU 与后采集时延，且拒绝把输出路径设为原始 JSONL、CU8 或已有报告，防止覆盖证据。

2026-08-12 在 `ascend8t` 的已接天线实测中，100 MHz、2.048 MS/s、固定请求增益 40.2 dB 连续
600.145 s 完成 1,170/1,170 批次、零丢批，后采集 P50/P95/max 为 252.440/255.858/282.768 ms，
小于 512 ms 窗口；这组记录的字节级 I/Q 端点率低于 `6e-8`。自动增益同样能跑通管线，但端点率约
15%，不作为该环境的推荐接收设置。完整证据范围和限制见
[07 指引](docs/07_ascend310b_heterogeneous_signal_processing.md)。
