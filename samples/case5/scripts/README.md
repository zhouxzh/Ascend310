# 运行脚本

脚本都从 `~/Documents/case5` 执行，系统包由用户手动安装，脚本不会调用 sudo。频谱检测
模型运行需要 `libfftw3-single3`；独立 FFTW C 基准还需要 `libfftw3-dev`。

| 脚本 | 职责 | 需要 sudo |
| --- | --- | --- |
| `build_sigrok_capture_bridge.sh` | 使用系统 libsigrok 编译 Hantek 二进制采集桥 | 否 |
| `check_usb_device.sh` | 只读 PyUSB/libusb 枚举，不打开设备 | 否 |
| `run_dashboard.sh` | 加载 CANN、激活 `base`、启动 Qt 仪表盘 | 否 |
| `start_dashboard.py` | 从项目根目录启动 Python 入口 | 否 |
| `measure_sigrok_streaming.sh` | 编译并运行 libsigrok 回调吞吐计数器 | 否 |
| `benchmark_spectrum_efficiency.py` | 板端 NPU DFT 与 FFTW 对照基准 | 否 |
| `run_rtl_sdr_npu_demo.sh` | 加载 CANN、激活 `base`、运行 RTL-SDR IQ 批处理 OM Demo | 否 |
| `run_volk_npu_benchmark.sh` | 生成固定 VOLK ONNX/OM 并比较 generic/NEON/dispatcher、ORT、OM | 否 |
| `run_rtl_sdr_npu_inference.sh` | 加载已准入 manifest，运行严格 RTL-SDR NPU 分类/检测入口 | 否 |
| `python -m time_frequency_dashboard.rtl_sdr_run_report` | 只读汇总已完成 RTL JSONL/CU8 的时延、直流和削顶统计 | 否 |

## 推荐顺序

```bash
cd ~/Documents/case5
python -m time_frequency_dashboard.acquisition.usb_diagnostics
bash scripts/build_sigrok_capture_bridge.sh
bash scripts/run_dashboard.sh --sigrok-bridge build/sigrok_capture_bridge
```

`run_dashboard.sh` 加载 CANN 并激活 `base`；`start_dashboard.py` 只从项目根目录调用 Python
入口。默认 Hantek 会话写入 `data/hantek_sessions/`，SDR CU8/JSONL 写入
`data/rtl_sdr_npu_inference/`。可传入 `--sessions`、`--sdr-output-root`、
`--sdr-models-dir` 覆盖目录；`--sdr-developer-sources` 才会显示 CU8/合成开发输入。

桥接程序的 C 源码位于
`time_frequency_dashboard/acquisition/native/sigrok_capture_bridge.c`。它使用 libsigrok
的 `hantek-6xxx` 驱动负责固件、USB、采样率和量程；stdout 是 BridgeFrameV1 二进制流，
stderr 是诊断。不要把 stdout 重定向到终端或文本解析器。

## RTL-SDR NPU Demo

该 Demo 不启动 GRC 或 Qt，也不占用 Hantek。它使用已安装的 `rtl_sdr` 抓取有限 CU8 IQ
数据，再由 Conda `base` 中的 `aclruntime` 执行 OM。关闭 GQRX、GNU Radio、SDR++ 和其他
RTL-SDR 使用者后运行：

```bash
cd ~/Documents/case5
bash scripts/run_rtl_sdr_npu_demo.sh --source tone --batches 2
bash scripts/run_rtl_sdr_npu_demo.sh --source rtl --center-frequency 100000000 --batches 8
```

先按 [06 RTL-SDR IQ 批处理 NPU 频谱 Demo](../docs/06_rtl_sdr_npu_demo.md) 生成并验证 OM。

## 异构评估与严格检测入口

`run_volk_npu_benchmark.sh` 固定使用 1024 点、batch `1/16/64`、每轮 50 次预热和 300 次
测量。它会在板端生成 VOLK 对照 OM，且所有后端以合并的 900 个测量样本计算 P50/P95；不要
把此前旧统计口径的 JSON 与新输出混合比较。

```bash
cd ~/Documents/case5
bash scripts/run_volk_npu_benchmark.sh \
  --output data/volk_npu_benchmark/volk_npu_$(date -u +%Y%m%dT%H%M%SZ).json
```

第三方模型实时入口只接受经过数值、来源合同和 NPU 窗口预算准入的 manifest，并在启动前校验
ONNX/OM SHA256。旧 manifest 可在板端补齐当前的来源、采样约定和结构化预处理合同：

```bash
python -m time_frequency_dashboard.model.upgrade_inference_manifest \
  --manifest models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep.accepted.manifest.json \
  --candidate torchsig_yolo11 \
  --output models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep.accepted_v3.manifest.json
bash scripts/run_rtl_sdr_npu_inference.sh \
  --source rtl \
  --manifest models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep.accepted_v3.manifest.json \
  --gain-db 40.2 \
  --rf-input-context antenna_connected \
  --duration-seconds 10
python -m time_frequency_dashboard.model.attach_pipeline_realtime_evidence \
  --manifest models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep.accepted_v3.manifest.json \
  --inference-jsonl data/rtl_sdr_npu_inference/<run>/inference.jsonl \
  --output models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep.accepted_v4.pipeline-verified.manifest.json
python -m time_frequency_dashboard.model.attach_pipeline_realtime_evidence \
  --manifest models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep.accepted_v4.pipeline-verified.manifest.json \
  --verify-attached
```

结果 JSONL 会记录工件哈希、CANN 版本、RTL 设备/增益/PPM、队列丢批，以及捕获、归档写盘、
解码、预处理、NPU、后处理和处理端到端延迟。检测 manifest 必须匹配结构化 FFTW 频谱图合同；
`run_summary.pipeline_realtime.pipeline_real_time_passed=true`、生产/完成批数相等、零丢批且
主机后采集最大处理时延不超窗口，才可得到该短时窗口检查的通过结论。附件工具会重算 JSONL 的
结论，校验固定输入形状和采样率导出的窗口预算，并绑定源 v3 manifest、模型工件、CU8 与合同哈希，
写出新的 v4 清单而不改写采集时的 v3。`--verify-attached` 会重新打开这些文件后再计算一次；只有
`antenna_connected`/`lab_cabled`、至少 600 秒、零丢批且每批不超窗口的记录会标为连续管线通过。
实时 `rtl` 运行将 `--duration-seconds` 作为下限，并向上取整到完整模型窗口：当前
`[1,3,1024,1024]`、2.048 MS/s 模型每窗口 `0.512 s`，所以 `10 s` 对应计划 20 批、`10.240 s`
和 `40 MiB` CU8。JSONL 记录该请求和计划，UI 容量显示以及磁盘预检也以计划值为准。实时来源不接受
`--max-batches`，避免人为截断被标为正常完成；只有 `cu8` 或 `synthetic` 开发来源可用它做单批
采集/推理 smoke test，且不构成流水线实时通过结论。未标注的真实 IQ 只验证链路，不可用于准确率结论。
`--rf-input-context` 是操作员声明字段：未接天线时请写 `disconnected`，接天线后再写
`antenna_connected`；它不替代信号标签或接收质量测量。

完成一次运行后，使用下面的只读命令复核同一份记录的 CU8 SHA256、记录的时延分位数、I/Q 直流
偏置和端点削顶率：

```bash
python -m time_frequency_dashboard.rtl_sdr_run_report \
  --inference-jsonl data/rtl_sdr_npu_inference/<run>/inference.jsonl \
  --output data/rtl_sdr_npu_inference/<run>/qc_summary.json
```

若只是比较固定增益下的独立短采集，不能拿它套用另一轮 JSONL 的处理时延；改用
`--capture-only --capture-cu8 data/<capture>.cu8` 只生成字节级采集质量报告。
联合报告要求 JSONL footer 有匹配的 CU8 字节数和 SHA256，元数据、每批和 footer 都为
`NPU (Ascend 310B)`，且每条记录都有 NPU 与后采集时延；`--output` 不能指向原始 JSONL、CU8，
也不会覆盖已有报告文件。

2026-08-12 的 `ascend8t` 实测表明，100 MHz/2.048 MS/s 下请求 40.2 dB（驱动实际 40.2 dB）可避免
自动增益记录中约 15% 的 CU8 端点值；固定增益的 10 分钟 NPU 管线完成 1,170 批、零丢批、后采集
P95 为 255.858 ms（512 ms 窗口）。这是该天线和环境下的起始设置，不应泛化为其他频段或地点的
增益标定。

## 吞吐计数

需要 `libsigrok-dev`、`gcc`、`pkg-config`；频谱检测服务还需要 `libfftw3-single3`：

```bash
CASE5_SIGROK_DURATION_MS=10000 bash scripts/measure_sigrok_streaming.sh
```

该脚本只统计 libsigrok 模拟回调交付的样本，不启动 Qt、不输出 CSV、不做 NPU 推理。
它不能证明跨 USB 回调无间隙，也不能替代硬件集成测试。

## 权限

若 USB 诊断显示 `writable=False`，由管理员手动安装规则：

```bash
sudo cp scripts/udev/60-case5-hantek6022.rules /etc/udev/rules.d/
sudo systemctl restart systemd-udevd
```

随后关闭所有占用程序、物理拔插示波器，再重新运行 USB 诊断。不要使用 sudo 启动 Qt 程序。
