# Piano-DDSP 实时系统

Piano-DDSP 是 `case3` 中独立于 MIDI-DDSP 和 DDSP-VST 的实时系统。它以 250 Hz
逐帧执行常驻 OM，用 NumPy/SciPy 保存谐波相位、噪声 overlap、神经状态、重采样和混响
历史。硬件 MIDI、网页钢琴和 MIDI 文件进入同一个单调时钟调度器，不生成整曲 WAV 后
再播放。

## 固定来源

- 训练源码提交：`1f7cf65ff9c58968bc3b605ee571db928d1ac37a`
- Hugging Face：`zhouxzh/piano-ddsp-ascend310`
- 发布标签：`model-suite-v1.0.0`
- 固定 HF 提交：`2199df0a55953a0d2469d59ab2f23a8bef8eb314`
- 合同：FP32、opset 13、batch 1、16 声部、250 Hz、16 kHz

开发电脑使用纯标准库下载器：

```bash
python tools/download_piano_ddsp_onnx.py
```

它校验标签提交、文件大小和 SHA256，使用 `.part`、HTTP Range 和原子重命名，并默认
排除 checkpoint、优化器和 `.pt`。HF 不可用时只能显式执行：

```bash
python tools/download_piano_ddsp_onnx.py --source ace2
```

报告会记录每个文件的真实来源；ace2 文件仍必须匹配同一发布哈希。

## 模型和 bundle

第一版包含 `paper_ir`、`film_fdn`、`calibrated_ir` 和
`calibrated_film_ir`。完成板端音质、性能和稳定性比较前，catalog 和 UI 不提供推荐标记。

```text
models/piano_ddsp/
|-- model-suite-v1.0.0/
|-- references/model-suite-v1.0.0/paper_ir/
|   |-- reference-10000.npz
|   |-- reference.wav
|   `-- report.json
|-- model-suite-v1.0.0-gru-unrolled/
|-- bundles/model-suite-v1.0.0-gru-unrolled-fp32-origin/
|   |-- manifest.json
|   |-- environment.json
|   |-- models/
|   `-- logs/
`-- active-bundle.json
```

`prepare_piano_ddsp_models.py` 只能在 aarch64 且已有 ATC 的板端运行。转换固定为
`Ascend310B4` FP32，并显式设置 `precision_mode_v2=origin`，先处理 `paper_ir`。
ATC 子进程固定使用 `MULTI_THREAD_COMPILE=0`、`TE_PARALLEL_COMPILER=1` 和
`enable_graph_parallel=0`，避免在开发板上并行编译算子。
CANN 8.0.0 的原生 `DynamicGRUV2` 只提供 FP16 kernel，因此真实 FP32 基线使用在固定
源码上静态展开、且与原始 ONNX 连续逐帧对照 10,000 帧通过的 `gru-unrolled` 变体。
每个 OM 会被 PyACL 实际加载以核对 I/O
名称、形状、类型和字节数，结果写入 manifest。完整 bundle 不允许再次写入；回退只原子
切换 active 指针。

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Documents/case3
python prepare_piano_ddsp_models.py --variant gru-unrolled --models paper_ir
```

这些命令只激活已有环境。不得在板端安装、升级或删除包，也不得修改 CANN、Conda、
PulseAudio、系统配置或 shell 启动文件。缺少依赖时停止并保留日志。

日常启动使用 `python scripts/run_webui.py`。入口在板端检测不到 `acl` 时，会通过现有
CANN `set_env.sh` 和 Conda `base` 重新执行自身；FastAPI 启动的 Piano worker 会继承
同一组 `PYTHONPATH`、`LD_LIBRARY_PATH` 和 `ASCEND_*` 变量。2026-07-29 实测主进程
报告 `acl:true`，`paper_ir` worker 成功加载 OM 并运行 3944 次推理，NPU P99 为
1.46 ms，停止后 worker、`aplay` 和 PCM 均已释放。

## 实时语义

- 固定 16 槽和 MIDI 21-108；依次选择最低空闲槽、最老释放槽、最老活动槽。
- 同音重复击键复用原槽；每个来源分别持有音符和 CC64-67。
- CC64 控制延音；松键声部包络 60 ms；CC123/All Notes Off 为 120 ms 全局淡出。
- 松键音高保留 250 帧供 `extended_pitch` 使用。
- 学习 IR 只在加载时分区；FDN 九个控制量只在加载时生成 24,000 点 IR 和动态 wet。
- RNG 固定为 `seed + voice_index`；Panic 清除神经、MIDI、DSP、重采样和混响历史。

运行时由 FastAPI 启动独立 `python -m piano_ddsp_runtime.worker` 子进程。stdin/stdout
使用 NDJSON 命令和状态流，后端持续排空管道并监控心跳。浏览器 WebSocket 断开只释放
该浏览器来源的音符和虚拟踏板，不停止硬件 MIDI、MIDI 文件、录音或板端音频。

## API

| 接口 | 用途 |
| :--- | :--- |
| `GET /api/v1/realtime/catalog` | 统一实时音色目录，包含 Piano-DDSP 与 DDSP-VST patch |
| `GET /api/v1/realtime/status` | 当前统一会话状态、设备、播放器、录音和指标 |
| `POST /api/v1/realtime/start` | 获取共享资源并启动所选 patch |
| `POST /api/v1/realtime/stop` | 120 ms 淡出并有序释放资源，幂等 |
| `POST /api/v1/realtime/panic` | 清空全部状态但保持会话 |
| `PATCH /api/v1/realtime/parameters` | 块边界热更新；模型/年份执行完整切换 |
| `WS /api/v1/realtime/events` | MIDI、播放器、录音、监听、状态和指标 |

Piano-DDSP 与 DDSP-VST、MIDI-DDSP 作业和扬声器测试共用 `ResourceCoordinator`，统一实时
接口负责会话生命周期。浏览器监听默认关闭，使用容量为两块的独立丢弃队列，不能反压音频线程。

## 延时与输出

| 档位 | DSP 块 | 预缓冲 | USB 软件目标 |
| :--- | :--- | :--- | :--- |
| low | 4 帧 / 16 ms | 1 块 | 70-80 ms |
| balanced | 8 帧 / 32 ms | 1 块 | 不超过 100 ms |
| safe | 16 帧 / 64 ms | 2 块 | 不超过 150 ms |

默认 `balanced`。蓝牙禁用 `low`；`balanced`/`safe` 至少使用 220/300 ms 输出缓冲，
页面明确显示 A2DP 的额外延时。有界音频队列统计 overrun、underrun、clipped samples、
队列、设备、sink 和 MIDI 到首个有效 PCM 的软件延时。录音保存实际 48 kHz 双声道输出。

Orange Pi AIpro 板载 PulseAudio `platform-sound` 不作为 Piano-DDSP 输出。2026-07-29
的黑匣子记录显示 Pulse 的 `alsa-sink-ascen` 在 `snd_pcm_sync_ptr` 获取自旋锁时触发
NMI watchdog hard lock，最终造成 AP OS panic 和整板重启。Piano-DDSP 专用设备 API
会过滤该 Pulse sink 及 `default`、`pulse`、`dmix` 等可回落到它的 ALSA 别名。
同日对板载 `hw:0,0` 的 48 kHz 双声道 PortAudio 测试又只写入 1024 帧便阻塞，
内核持续报告 `dma period irq error interval 23ms`，停止接口因底层写调用不返回而超时。
ALSA 虽接受两个声道，厂商自带脚本却只验证单声道，因此板载 3.5 mm 不属于
Piano-DDSP 支持的实时立体声输出。重复故障后，板载设备已改为 `alsa_mono` 兼容
后端：在输出端下混为单声道，并通过可单独终止的 `aplay` 子进程播放；它不计入
低延时或立体声音质验收。0.5 秒板端冒烟已完整播放并正常关闭 PCM，且没有新增
DMA IRQ 错误；10 秒流在 36864 帧处主动停止也返回 `stopped` 并释放 PCM。
正式演奏仍应使用 USB 声卡/音箱或蓝牙。完整判断和证据见
[音频输出文档](audio-output.md)。黑匣子原始证据保存在
`reports/piano-ddsp/board-crash-20260729/`。

## 验证

本地参考必须从固定提交的干净 worktree 生成。`reference-10000.npz` 保存 10,000 帧输入、
控制和逐帧状态；音频比较注入相同白噪声，避免不同 RNG 算法造成假偏差。

板端运行：

```bash
python tools/validate_piano_ddsp_om.py \
  --bundle models/piano_ddsp/bundles/model-suite-v1.0.0-gru-unrolled-fp32-origin/manifest.json \
  --reference models/piano_ddsp/references/model-suite-v1.0.0/paper_ir/reference-10000.npz \
  --report reports/piano-ddsp/paper-ir-smoke.json --frames 100

python tools/validate_piano_ddsp_om.py \
  --bundle models/piano_ddsp/bundles/model-suite-v1.0.0-gru-unrolled-fp32-origin/manifest.json \
  --reference models/piano_ddsp/references/model-suite-v1.0.0/paper_ir/reference-10000.npz \
  --report reports/piano-ddsp/paper-ir-10000.json --frames 10000 --activate
```

要求无 NaN/Inf，F0 NRMSE 不超过 `1e-5`，其他控制量和状态 NRMSE 不超过 `0.003`，
单帧 NPU P99 小于 4 ms。100 帧命令仅用于确认模型可加载和基本数值路径，报告会明确
标记为不具备发布资格；只有 10,000 帧通过的报告才能写入 active bundle 指针，catalog
和 worker 也只接受该合格模型。完整验收还包括八帧块 P99、EDIFIER USB 软件总延时、16 音
和弦、快速/重复音、CC64-67、设备拔插、模型切换、录音和 10 分钟稳定性。

### 2026-07-29 板端结果

不可变 active bundle 为
`model-suite-v1.0.0-gru-unrolled-fp32-origin`。四个模型均完成 10,000 帧 OM 连续对照，
无 NaN/Inf，F0、连续控制量和逐帧状态均通过上述阈值：

| 模型 | 单帧 NPU P99 | 10,000 帧数值对照 |
| :--- | ---: | :--- |
| `paper_ir` | 1.25 ms | 通过 |
| `film_fdn` | 1.19 ms | 通过 |
| `calibrated_ir` | 1.23 ms | 通过 |
| `calibrated_film_ir` | 1.34 ms | 通过 |

固定白噪声 DSP 对照中最低谐波 SNR 为 65.10 dB，最低 wet 输出 SNR 为 68.84 dB。
`paper_ir` 的 `balanced` 八帧完整块 P99 为 23.219344 ms，50 秒窗口无 underrun，满足
24 ms 计算预算；由于该测量使用后来证实不安全的板载音频，只记录计算结果，不计入 USB
总延时验收。真实进程内 `paper_ir -> film_fdn -> paper_ir` 的 ACL 加载、预热、Panic、
停止和释放已单独通过，停止后 ACL 生命周期探针全部成功。

同日 Playwright 在实际板端 WebUI 上完成 11 项测试，覆盖 1366x768 和 390x844，无页面
横向溢出或关键控件重叠。当前开发板未连接 EDIFIER M16 Pro 和 MIDIPLUS TINY，因此
USB balanced 不超过 100 ms、原始 MIDI、物理拔插、现场演奏和带外设的 10 分钟稳定性
仍是明确的未验收项；完成这些项目及四模型听感比较前不设置推荐模型。
