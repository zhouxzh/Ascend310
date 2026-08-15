# Case 5：昇腾 310B 实时信号采集与时频分析仪表盘

本仓库实现一个面向昇腾 310B 的教学型实时分析仪。它不是把所有信号处理都搬到 NPU：采集、数据整理、窗口函数和轻量频谱预处理仍在 CPU 上完成；固定 DFT 或已准入的神经网络 OM 由 Ascend 310B NPU 执行。界面必须明确显示实际后端，不能把 CPU 结果写成 NPU 结果。

项目有两条相互独立、但共享设备互斥和可追溯原则的实验路径：

- **Hantek 路径：** Hantek 6022BE 通过 libsigrok 采集 CH1/CH2；固定 Hann 窗 DFT OM 输出 0--20 kHz 的频谱功率。当前实机证据是 CH1 接 `CAL` 低压方波和 NPU DFT。Little Bee B1 的 CH2 幅相标定尚未完成。
- **RTL-SDR 路径：** RTL-SDR 采集复数 CU8 IQ；CPU 以 FFTW 构造模型规定的时频输入，已准入 OM 在 NPU 上做信号区域检测。该路径证明采集、预处理和 NPU 检测连通；没有带标签的空口数据时，不能宣称调制识别准确率。

本 README 是运行入口。`docs/` 中的文件只解释某个硬件、设计约束或实测证据，不再各自维护一套相互冲突的启动步骤。

## 阅读导航

| 文件 | 何时阅读 | 解决的问题 |
| --- | --- | --- |
| [01 Hantek 6022BE](docs/01_hantek6022be.md) | 接线、编译或 USB 采集前 | libsigrok、桥接协议、量程和设备独占 |
| [02 Little Bee B1](docs/02_little_bee_b1.md) | 把电流探头接到 CH2 前 | 去零、V/A 换算、标定缺口和安全限制 |
| [03 系统架构与数据合同](docs/03_architecture.md) | 理解窗口、队列和 CPU/NPU 分工时 | 两条数据流、固定形状和追溯字段 |
| [04 前端说明](docs/04_frontend_design.md) | 使用或维护 1920x1080 触摸界面时 | 页面职责、状态语义、布局和截图范围 |
| [05 第三方代码与许可证](docs/05_third_party_licenses.md) | 引入或发布第三方实现前 | 来源、许可证和再发布义务 |
| [06 RTL-SDR 服务说明](docs/06_rtl_sdr_npu_demo.md) | 运行 IQ DFT Demo 或实时检测前 | CU8、FFTW、OM、JSONL 和 QC |
| [07 异构处理与模型准入](docs/07_ascend310b_heterogeneous_signal_processing.md) | 重新生成候选模型或解释性能时 | 数值准入、模型边界和板端证据 |

## 目录职责

```text
case5/
├── time_frequency_dashboard/   # 采集、处理、NPU、会话和 Qt 界面
│   ├── acquisition/            # Hantek/sigrok、USB 诊断和帧协议
│   └── model/                  # ONNX/OM 生成、验证、manifest 和准入工具
├── docs/                       # 编号专题说明；不替代本 README 的操作顺序
├── scripts/                    # 板端编译、启动和基准脚本
├── tests/                      # pytest 单元与显式启用的硬件测试
├── models/                     # 板端生成的 ONNX、OM、manifest；默认不提交
├── build/                      # 编译出的 sigrok 桥；默认不提交
└── data/                       # Hantek 会话、CU8、JSONL 和 QC；默认不提交
```

所有板端命令都假定项目根目录为 `~/Documents/case5`。本地 Windows 工作区可做 Python 语法和单元测试，但不能运行 CANN、ATC、ACL、OM 或真实 USB/RTL-SDR 验收。

## 固定分析契约

| 路径 | 采集与输入 | CPU 的确定性工作 | NPU 的核心工作 | 主要产物 |
| --- | --- | --- | --- | --- |
| Hantek 6022BE | `BridgeFrameV1` 双通道 float32；1 MS/s；窗口 `[1,2,10000]` | 桥 stdout 解码、连续窗口、CH2 声明换算、逐窗去直流 | 固定 Hann DFT OM，输出 `[1,2,201,1]` | 原始 `.c5raw`、`analysis.jsonl`、`summary.json` |
| RTL-SDR 检测 | CU8 复数 IQ；当前模型为 `[1,3,1024,1024]`，2.048 MS/s | CU8 解码、归档、FFTW Blackman 时频图、队列和后处理 | 已准入 OM 的神经网络检测 | `.cu8`、`inference.jsonl`、`qc_summary.json` |
| RTL-SDR DFT Demo | CU8 IQ；`[16,2,1024]` | CU8 解码、逐窗复数去直流和记录 | 固定 Hann 窗复数 DFT OM，输出 `[16,1024]` | Demo CU8、JSONL 和频谱记录 |

Hantek 的 201 个频点覆盖 0--20 kHz，间隔 100 Hz。显示层将线性能量转换为 `10*log10(max(E, 1e-12) / 1 V^2)`；它是相对 `1 V^2` 的未校准显示，不是 dBV、dBFS 或 dBm。

Hantek 分析队列容量为 2，RTL-SDR 推理队列默认容量为 4；队列满时丢弃旧任务以限制延迟，并写入会话记录。Hantek 的桥接序号只能证明用户态桥输出连续，不能证明设备 ADC 在 USB 回调间无缺口。RTL-SDR 的主机计时也不等于 RF/ADC 首样本到结果的设备侧延迟。

## 板端准备

### 1. 先确认运行边界

以下操作只在已部署 CANN 的 Ascend 310B 板端执行。项目脚本不会执行 `sudo`、安装系统包、强杀外部程序或重新生成不在当前步骤需要的模型。

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -c "import numpy, onnx, onnxruntime, aclruntime; print('CANN Python environment ready')"
```

`aclruntime`、CANN 和 ATC 必须来自板端既有安装；不要在本仓库或本地 Windows 中用 pip 安装替代它们。上面的导入失败时，应先修复板端 CANN/Conda 环境，再继续模型步骤。

### 2. 由管理员手动安装的系统程序

| 程序或库 | 用途 | 是否必需 |
| --- | --- | --- |
| `libsigrok-dev` | Hantek `hantek-6xxx` 驱动和 C 桥编译接口 | Hantek 路径必需 |
| `sigrok-cli` | 只用于人工排查 | Hantek 排查建议安装 |
| `gcc`、`pkg-config` | 编译和定位 libsigrok 桥依赖 | Hantek 路径必需 |
| `libfftw3-single3` | RTL-SDR 检测模型的 CPU FFTW 预处理 | RTL-SDR 检测必需 |
| `rtl-sdr` | 提供 `rtl_sdr`、`rtl_test` 等接收机工具 | RTL-SDR 路径必需 |
| `pulseview` | 人工观察 Hantek `CAL` 波形 | 可选；不能与仪表盘同占设备 |
| `libfftw3-dev` | 独立 FFTW C 基准的头文件和开发库 | 仅基准可选 |
| `libxcb-cursor0` | Qt/X11 缺少光标库时修复启动错误 | 按错误信息安装 |

Debian/Ubuntu 板端的最小安装命令如下，由用户手动执行：

```bash
sudo apt-get update
sudo apt-get install -y libsigrok-dev sigrok-cli gcc pkg-config libfftw3-single3 rtl-sdr
```

若需要人工看 `CAL`，另行安装 `pulseview`。Qt X11 报缺少 `libxcb-cursor0` 时再安装该包；只做独立 FFTW C 基准时再安装 `libfftw3-dev`。不要用 `sudo` 启动仪表盘。

### 3. 安装本项目的 Python 用户态依赖

`requirements-board.txt` 只包含 PyUSB、PySide6 和 PyQtGraph；它不安装 CANN、ACL、NumPy、ONNX 或 ONNX Runtime。模型生成和数值验证依赖的 `numpy`、`onnx`、`onnxruntime` 应已经存在于板端受管的 `base` 环境中，前一节的 import 检查就是它们的准入条件。`requirements-dev.txt` 是本地模型/测试依赖清单，不用来替代板端 CANN 环境。

```bash
cd ~/Documents/case5
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m pip install -r requirements-board.txt
```

### 4. 检查 USB 和设备互斥

关闭 PulseView、`sigrok-cli`、GQRX、GNU Radio、SDR++、`rtl_test` 和旧仪表盘。诊断只枚举设备，不打开接口或上传固件：

```bash
cd ~/Documents/case5
python -m time_frequency_dashboard.acquisition.usb_diagnostics
```

预期能看到 Hantek 和/或 RTL-SDR 的枚举信息。若 Hantek 显示 `writable=False`，由管理员按 `scripts/udev/60-case5-hantek6022.rules` 配置当前用户权限后物理拔插设备。若 PulseView 退出后设备仍为 `1d50:608e`，同样先物理拔插，再启动下一次 sigrok 会话。

## 模型生成

### Hantek 固定 DFT OM

先编译采集桥，再在板端生成并验证 Hantek 的固定 DFT OM：

```bash
cd ~/Documents/case5
bash scripts/build_sigrok_capture_bridge.sh
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.model.prepare_models
python -m time_frequency_dashboard.model.verify_npu_model
```

成功条件是桥文件 `build/sigrok_capture_bridge` 存在，且验证程序完成 ONNX/OM 数值比较。只有 OM 成功加载并实际推理，界面才可显示 `NPU (Ascend 310B)`；缺 OM、CANN 或初始化失败时显示 `NPU unavailable`，不会显示 CPU FFT 代替频谱。

### RTL-SDR 固定 DFT 教学 OM（可选）

这条路径用于理解复数 IQ 和固定 DFT，不是仪表盘的神经网络检测路径：

```bash
cd ~/Documents/case5
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.model.prepare_rtl_iq_model
python -m time_frequency_dashboard.model.verify_rtl_iq_model
```

详见 [06 RTL-SDR 服务说明](docs/06_rtl_sdr_npu_demo.md)。小型 FFT 的实际工程基线通常是 CPU FFTW；这个 OM 用于验证固定 DFT 的 NPU 部署，不应被表述为 FFTW 的替代品。

### RTL-SDR 神经网络检测 OM

正式实时入口只接受带来源、ONNX/OM SHA256、数值验证和窗口预算的 `accepted` manifest。现有候选、ONNX/ATC 命令、数值门槛及准入记录见 [07 异构处理与模型准入](docs/07_ascend310b_heterogeneous_signal_processing.md)。若 `models/generated/inference/` 下没有通过准入的 manifest，停止在这里，不要用未验证模型或 CPU fallback 启动真实检测。

当前推荐 OM 输入为 `[1,3,1024,1024]`，2.048 MS/s 下一个完整模型窗口为 0.512 s。它的训练配置使用单类信号区域监督；界面中显示的候选标签不构成 51 类调制识别能力或准确率声明。

## 仪表盘启动与实验步骤

### 实验 1：从 Hantek `CAL` 到 NPU DFT

**目的。** 证明 Hantek USB 采集、固定窗口、OM 推理和触摸界面是一条真实链路。

1. 在未接市电和未知回路前，把 CH1 探头接到 6022BE 的低压 `CAL` 输出；CH2 保持未接或接入已确认安全的低压源。
2. 关闭所有占用 Hantek 的外部程序，按上文完成 USB 诊断、桥编译和 Hantek OM 验证。
3. 启动界面：

   ```bash
   cd ~/Documents/case5
   bash scripts/run_dashboard.sh --sigrok-bridge build/sigrok_capture_bridge
   ```

4. 在 Hantek 页面点击“连接”，等待状态显示真实设备和 `NPU (Ascend 310B)`；在“波形”页应看到 CH1 方波，在“频谱与瀑布”页应看到 1 kHz 及其奇次谐波附近的 OM 频谱行。
5. 点击“停止”或退出应用后，等待设备释放，才可再打开 PulseView、`sigrok-cli` 或 RTL-SDR 路径。

这一步只验证 CH1 `CAL` 的连通和 NPU DFT。它不验证 CH2、探头幅值精度、带宽、功率因数或设备侧无间隙采样。运行期若 OM 输出出现 NaN、Inf 或形状错误，本次运行必须记录为失败；不能把它笼统写成 `NPU unavailable` 或改用 CPU 推理。

### 实验 2：Little Bee B1 接入 CH2（扩展）

仅当被测回路已确认隔离、接地、量程和电流探头工作方式时才进行。完成 Little Bee 自身去零、选择模式、记录匝数和灵敏度后，再把 SMA-BNC 输出接到 CH2。当前青色档 `1 V/A`、1 匝参数只是声明换算；没有本机零点、增益、幅相和同步标定前，只能观察趋势，不能报告计量级电流、相位、功率或功率因数。详细步骤和限制见 [02 Little Bee B1](docs/02_little_bee_b1.md)。

### 实验 3：合成 IQ 的固定 DFT 冒烟测试（可选）

在不占用真实 RTL-SDR 的情况下，先运行可控的合成信号：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_demo.sh --source tone --batches 2
```

预期输出包含 `NPU (Ascend 310B)`、批次数和频谱结果。它证明复数 DFT OM 的数值链路，不证明无线接收或神经网络检测。

### 实验 4：真实 RTL-SDR 检测

先停止 Hantek，确认没有 GQRX、GNU Radio、SDR++、`rtl_test` 或其他接收机进程占用 RTL-SDR。`rtl_test -t` 只能用于枚举，完成后必须退出。选择现有 `accepted` manifest 后，运行：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_inference.sh \
  --source rtl \
  --manifest models/generated/inference/candidates/<accepted-manifest>.json \
  --center-frequency 100000000 \
  --gain-db 40.2 \
  --rf-input-context antenna_connected \
  --duration-seconds 10
```

`--manifest` 可省略，让服务从 `models/generated/inference/` 递归选择已准入模型；有多个候选时建议显式指定。`--rf-input-context` 只能取 `unknown`、`disconnected`、`antenna_connected` 或 `lab_cabled`，必须如实填写。`40.2 dB` 只是 `ascend8t` 在 100 MHz 当前天线环境的示例，不是通用增益标定。

对当前检测模型，10 s 请求会向上规划为 20 个完整窗口、10.240 s 和约 40 MiB CU8；实时 `rtl` 来源禁止 `--max-batches`，避免人为截断被误记为完成。预期 CLI 最终报告 NPU 后端、完成批次、丢批数和 JSONL 路径。仪表盘的 SDR 页面使用同一服务：I/Q 页展示未校准 IQ，时频页的底图是 CPU FFTW 模型输入，检测框才是 OM 输出。

Hantek 与 RTL-SDR 由 `InstrumentCoordinator` 互斥管理。切换前必须停止当前运行并等待采集队列、会话写入和 NPU runner 释放；应用不会强制关闭外部程序。

## 测试、验收与会话复核

### 常规测试

本地和板端都可运行纯 Python/Qt 离屏检查：

```bash
python -m pytest -q
python -m compileall -q time_frequency_dashboard
```

真实硬件集成测试只在具备 Hantek、编译桥和已验证 Hantek OM 的 310B 板端执行：

```bash
CASE5_RUN_HARDWARE_TESTS=1 python -m pytest -q tests/test_hardware_capture_and_inference.py
```

它会先采集真实双通道窗口，再对同一窗口调用 OM。未设置环境变量时测试被跳过，不能据此宣称硬件通过。

### 复核一次 RTL-SDR 运行

运行结束后，使用只读报告工具检查同一 CU8 与 JSONL 的绑定、记录的后端和时延字段：

```bash
python -m time_frequency_dashboard.rtl_sdr_run_report \
  --inference-jsonl data/rtl_sdr_npu_inference/<run>/inference.jsonl \
  --output data/rtl_sdr_npu_inference/<run>/qc_summary.json
```

该报告不重新运行 RTL-SDR、FFTW 或 NPU；它只复核字节级 CU8、JSONL 和已记录的主机时延。连续管线验收还需要如实标注 `antenna_connected` 或 `lab_cabled`、至少 600 s、零丢批且每批后采集处理不超过窗口预算。10 s 运行只能作为短时检查。

### 最小验收表

| 验收项 | 必须看到的证据 | 不能据此声称的结论 |
| --- | --- | --- |
| Hantek `CAL` | CH1 真实方波、OM 频谱/瀑布、`NPU (Ascend 310B)` | CH2 标定、幅值精度、带宽或市电安全 |
| 固定 IQ DFT | 合成或真实 CU8 的 OM 频谱和 JSONL | FFTW 被替代或无线识别准确率 |
| RTL-SDR 检测 | accepted manifest、真实 CU8、JSONL、NPU 后端和 QC | 无标签空口信号的调制类别/检测准确率 |
| 前端 | 1920x1080 下真实后端和状态可读 | 其他屏幕尺寸或物理英寸标定 |

常见故障优先按以下顺序处理：设备 busy 时先停止占用程序；`writable=False` 时修复 udev；桥不存在时重新编译；Hantek 初始化显示 `NPU unavailable` 时重新验证 OM/CANN；RTL-SDR 运行期报错时保留对应 JSONL 错误并检查 manifest、FFTW、形状或 NaN/Inf，不使用 CPU fallback。专题排查见 [01 Hantek](docs/01_hantek6022be.md)、[06 RTL-SDR](docs/06_rtl_sdr_npu_demo.md) 和 [07 模型准入](docs/07_ascend310b_heterogeneous_signal_processing.md)。

## 安全边界

- Hantek 6022BE 两通道共地，不是隔离输入。首阶段只接 `CAL` 或确认隔离的低压实验回路；未确认接地、量程和隔离方式前禁止测量市电。
- Little Bee B1 没有 CAT 测量等级。只让一根绝缘导线穿过磁环；不要夹市电裸线或把磁耦合误解为任意共模隔离。
- PulseView、`sigrok-cli` 与仪表盘不能同时占用 Hantek；GQRX、GNU Radio、SDR++ 与本项目不能同时占用 RTL-SDR。
- 模拟输入、CU8 回放和 `--sdr-developer-sources` 只用于开发；不能替代真实采集和 OM 验收。
- 未完成的 Little Bee 幅相标定、Hantek 无间隙采样证明和带标签 SDR 评测都必须明确保留为限制，而不是写成已实现能力。
