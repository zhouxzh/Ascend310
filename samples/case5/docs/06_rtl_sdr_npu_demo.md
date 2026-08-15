# 06 RTL-SDR 实时 NPU 服务与固定 DFT 教学路径

本文件解释 RTL-SDR 的两种不同实验入口。安装、完整操作顺序和停止条件以 [README](../README.md) 为准；模型来源、性能和准入证据见 [07 异构处理与模型准入](07_ascend310b_heterogeneous_signal_processing.md)。

## 先区分两种路径

| 路径 | 入口 | 输入与输出 | 作用 | 不应混同为 |
| --- | --- | --- | --- | --- |
| 固定 IQ DFT Demo | `rtl_sdr_npu_demo` / `run_rtl_sdr_npu_demo.sh` | `[16,2,1024]` IQ -> `[16,1024]` 线性功率 | 复数 IQ、固定 DFT 和 OM 数值教学 | SDR 工作区的神经网络检测 |
| 实时 NPU 检测 | `rtl_sdr_npu_inference` / `run_rtl_sdr_npu_inference.sh` | 当前 `[1,3,1024,1024]` 时频图 -> 检测输出 | accepted manifest 下的真实 IQ、FFTW 预处理、OM 和 JSONL | NPU FFT、调制识别准确率 |

两者都通过 `aclruntime.InferenceSession` 调用 OM，但模型、窗口、输出和验收标准不同。小型 FFT 的高效基线是 CPU FFTW；固定 DFT OM 是部署教学示例，不是 FFTW 的替代品。

## 真实检测服务的工作方式

`RtlSdrService` 同时被 PySide6 SDR 工作区和无界面 CLI 调用，避免两套采集/推理实现产生不同结果。实时入口开始前依次校验：

1. manifest 的 `accepted` 状态、来源合同、ONNX/OM SHA256、输入形状和采样率；
2. 模型声明的 NPU P95 窗口预算和 live-demo eligibility；
3. CU8 归档所需磁盘空间、FFTW Blackman 预处理和真实 NPU 后端；
4. 只有这些检查都通过，才打开 `rtl_sdr`。

检测数据流为：

```text
RTL-SDR CU8 I/Q
  -> 完整固定窗口与 .cu8 归档
  -> CPU：CU8 解码、FFTW Blackman、功率、fftshift、dB/RGB 模型输入
  -> NPU：accepted OM 神经网络推理
  -> CPU：输出有限值/形状检查、解码、argmax 后按预测类别分组 NMS
  -> inference.jsonl、只读 QC、仪表盘快照
```

时频图是 CPU FFTW 预处理的真实模型输入，检测框才来自 OM。检测模型输出的候选分类分数先取单一预测类别，再对该预测类别分组做 NMS；它不是“对所有评分通道各自做 NMS”。当前接受的 YOLO 权重训练采用单类信号区域监督。即使部署映射中存在多个候选标签，也不能把无标签真实 IQ 上显示的标签写成已验证的多类调制识别能力。

## 固定 IQ DFT 教学路径

固定 DFT Demo 使用 CU8 交错 I/Q，采样率为 2.048 MS/s；每次 OM 输入包含 16 个、每个 1024 点的复数窗口，即 8 ms IQ。模型用固定 Hann 窗正弦/余弦投影矩阵表达 DFT，ONNX 只使用 `Reshape`、`MatMul`、`Mul` 与 `Add`，不依赖 ONNX FFT 或 PyTorch。

在已完成板端 OM 准备后，先用合成音测试：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_demo.sh --source tone --batches 2
```

随后才可在关闭 GQRX、GNU Radio、SDR++、`rtl_test` 等占用者后使用真实接收机：

```bash
bash scripts/run_rtl_sdr_npu_demo.sh \
  --source rtl \
  --center-frequency 100000000 \
  --gain-db 40.2 \
  --batches 8
```

不传 `--gain-db` 时使用自动增益。`40.2 dB` 是 `ascend8t` 在 100 MHz、当时天线环境下避免明显端点削顶的示例，不是其他频段、天线或地点的校准建议。`--ppm-error` 只能填写已经通过独立方法测得的修正值。

Demo 不依赖 `rtl_sdr -n` 结束：它精确读取所需的 `2 * batch_count * batch_size * window_samples` 字节后主动结束录制子进程。因此 `.rtl_sdr.log` 中的 `Signal caught, exiting!` 或 `User cancel, exiting...` 可能是受控结束诊断，不一定表示设备故障。

## 正式实时检测

实时检测需要现有的 `accepted` manifest。一个实际连接天线的短时运行示例：

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

`--rf-input-context` 是操作员记录，不会自动发现天线、信号或真值；可取 `unknown`、`disconnected`、`antenna_connected`、`lab_cabled`。真实 `rtl` 来源把 `--duration-seconds` 视为下限，并禁止 `--max-batches`。当前 `[1,3,1024,1024]`、2.048 MS/s 模型每个完整窗口为 0.512 s，因此 10 s 请求规划为 20 批、10.240 s 和约 40 MiB CU8。

`cu8` 回放和 `synthetic` 仅用于开发；PySide6 页面默认不展示，除非显式传入 `--sdr-developer-sources`。它们不能构成真实采集、天线接收或连续实时验收。

## 产物与后验检查

每轮服务在 `data/rtl_sdr_npu_inference/<run>/` 保存原始 `.cu8`、`inference.jsonl`、`rtl_sdr.log` 和元数据。每个批次记录采集序号、IQ 偏移、模型/工件哈希、后端、队列丢批、预处理、NPU 和后处理时延。

运行完成后，用只读报告工具复核：

```bash
python -m time_frequency_dashboard.rtl_sdr_run_report \
  --inference-jsonl data/rtl_sdr_npu_inference/<run>/inference.jsonl \
  --output data/rtl_sdr_npu_inference/<run>/qc_summary.json
```

报告会检查 JSONL 绑定的 CU8 字节数和 SHA256、metadata/批次/footer 的 `NPU (Ascend 310B)` 后端记录，并汇总 I/Q 直流偏置、端点削顶率和已记录主机时延。它不调用硬件、FFTW 或 OM，也不证明天线性能、RF 幅度校准、空口标签或模型准确率。

短时运行至少需要完整批次、零丢批和后采集处理不超过窗口，才可以通过短时管线检查。连续管线证据还要求 `antenna_connected` 或 `lab_cabled`、至少 600 s、零丢批和每批预算都通过；10 s 示例不具备这个资格。

## 失败语义与边界

- 启动前 manifest、哈希、FFTW 或 NPU 预检失败时，服务不打开 RTL-SDR，也不回退 CPU 推理。
- 服务启动后若 OM 返回 NaN、Inf、错误形状或其他运行时错误，该次 run 终止为 `failed` 并记录错误；它不同于初始化阶段的 `NPU unavailable`。
- 无标签真实 IQ 的检测框只能证明接收、预处理和 OM 链路工作，不能证明特定调制类别、检测率或识别准确率。
- 同一 RTL-SDR 不能同时被外部接收程序和本服务占用；Hantek 与 RTL-SDR 在本应用内也严格互斥。
