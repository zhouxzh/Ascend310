# 01 Hantek 6022BE：sigrok 驱动和实机采集

本案例只使用 sigrok 作为 Hantek 6022BE 的程序采集后端。PulseView 是人工观察和
排查工具；`time_frequency_dashboard` 通过项目内的 C 桥直接调用同一个 libsigrok
`hantek-6xxx` 驱动，不解析 PulseView 截图或 `sigrok-cli` 文本。

## 硬件与接线

6022BE 是 USB 2.0 双通道示波器。首轮验证把 CH1 探头接到机身 `CAL` 标准测试输出，
这只证明 USB、固件和波形链路连通；截图和波形不能作为带宽、幅值精度或 NPU 验收。
CH2 预留给 Little Bee B1 的安全低压电流输出，电流探头说明见
[`02_little_bee_b1.md`](02_little_bee_b1.md)。两个通道共地，禁止在未确认隔离和量程时
测量市电。

## 手动安装的系统包

在板端 `base` 环境运行程序前，用户需要手动安装：

```bash
sudo apt-get update
sudo apt-get install -y libsigrok-dev sigrok-cli gcc pkg-config libfftw3-single3
```

前四个包的用途分别是 libsigrok API/驱动、人工命令行排查、编译桥接程序和读取编译参数；
`libfftw3-single3` 为已准入的 RTL-SDR 时频检测模型提供 CPU 端 FFTW 预处理运行库。
触摸屏若缺 Qt X11 光标库，再安装：

```bash
sudo apt-get install -y libxcb-cursor0
```

Python 依赖不需要 sudo：

```bash
conda activate base
python -m pip install -r requirements-board.txt
```

## 编译和启动

```bash
cd ~/Documents/case5
bash scripts/build_sigrok_capture_bridge.sh
python -m time_frequency_dashboard.acquisition.usb_diagnostics
```

诊断程序只枚举设备，不打开接口、不上传固件。关闭 PulseView 后，如果 `lsusb` 仍显示
`1d50:608e`，物理拔插一次，让下一次 sigrok 扫描重新加载 `fx2lafw` 易失固件。

加载 CANN、激活 `base` 后启动：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python -m time_frequency_dashboard.model.prepare_models
python -m time_frequency_dashboard.model.verify_npu_model
bash scripts/run_dashboard.sh --sigrok-bridge build/sigrok_capture_bridge
```

UI 的 Hantek 量程只能选择 sigrok 驱动真实支持的 `1、0.5、0.25、0.1 V/div`。采样率固定
为 1 MS/s，桥接会读回设备实际值；如果不是 1 MS/s，帧会被拒绝，不会送进固定 OM。

## sigrok 桥协议

`time_frequency_dashboard/acquisition/native/sigrok_capture_bridge.c` 调用 libsigrok：

1. 扫描 Hantek 6022BE，并在需要时上传 `fx2lafw` 固件；
2. 开启 CH1、CH2，设置采样率和两路 V/div；
3. 用一个长期运行的 session 接收两个 `SR_DF_ANALOG` 回调；
4. 配对通道、转换为 float32，并输出 `BridgeFrameV1` 到 stdout，诊断写 stderr。

BridgeFrameV1 的头部包括魔数、版本、序号、主机单调时间、实际采样率、样本数、通道数、
削顶标志和 payload 长度；payload 是交错的 `[N, CH1, CH2]` 小端 float32。Python 端以
任意 stdout 分片解码，检查序号连续和采样率，再应用探头倍率。

libsigrok 0.5.2 的 Hantek 驱动默认按大 USB transfer 回调。桥接只有在 CH1/CH2 都收到新的
模拟样本并完成配对后，才更新滑动 `SR_CONF_LIMIT_MSEC`，默认目标约 40 ms；这减少 UI 的
突发延迟而不重复启动设备。每个通道最多保留一个 Linux 最大 USB 传输对应的未配对样本（约
6,291,456 点）；任一通道长期落后时，
桥接会报错停止，而不会无限制占用内存或把不同步的样本静默拼接。设备
仍不提供 FIFO 溢出或跨回调采样缺口信息，因此序号连续不等于物理 ADC 无间隙。

## 实测记录

板端吞吐脚本：

```bash
CASE5_SIGROK_DURATION_MS=10000 bash scripts/measure_sigrok_streaming.sh
```

2026-08-09 实测（libsigrok 0.5.2、sigrok-cli 0.7.2、USB 2.0 高速）：

| 请求 | 单通道有效速率 | 双通道每通道有效速率 |
| ---: | ---: | ---: |
| 1 MS/s | 0.993 MS/s | 0.991 MS/s |
| 8 MS/s | 7.769 MS/s | 7.635 MS/s |
| 16 MS/s | 15.169 MS/s | 14.742 MS/s |
| 24 MS/s | 20.843 MS/s | 20.011 MS/s |
| 30 MS/s | 20.450 MS/s | 19.688 MS/s |
| 48 MS/s | 20.393 MS/s | 19.615 MS/s |

这是主机收到的 libsigrok 模拟样本回调速率，不是 USB 总线字节率，也不是无间隙 ADC
时钟证明。原始 JSONL 保存在板端 `~/Documents/case5/data/sigrok_throughput/`。

## 常见问题

- `Resource busy`：关闭仪表盘、PulseView 和 `sigrok-cli`，一次只允许一个程序占用设备。
- `cannot set ... V/div`：使用 UI 提供的四个 sigrok 量程，不要传驱动未公开的其他档位。
- `sigrok capture bridge not found`：重新运行 `bash scripts/build_sigrok_capture_bridge.sh`。
- `writable=False`：按 udev 规则处理当前用户权限，不要 sudo 启动仪表盘。
- `NPU unavailable`：先按 README 生成并验证 OM；CPU 不会被伪装成 NPU 结果。
