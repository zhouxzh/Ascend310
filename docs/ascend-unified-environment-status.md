# Ascend 310B 统一 Python 环境状态

本记录对应目标：所有案例统一使用 conda 环境 `ascend`，禁止案例回退
`base` 或 `~/.local`，待全案例与目标板验收通过后再生成不可变锁文件并
清理旧环境。

## 统一环境契约

```text
name: ascend
path: /home/HwHiAiUser/.conda/envs/ascend (20T and 8T)
Python: 3.9.25
ABI: cpython-39-aarch64-linux-gnu
user-site: disabled with PYTHONNOUSERSITE=1
```

CANN、NPU driver、固件、ALSA/PulseAudio 和系统 eSpeak 由开发板系统
提供，不复制进 conda。

CANN 8.0.RC3 官方安装文档支持 Python 3.7.x 至 3.10.x。本项目结合已经
过板端验证的 MindSpore/MindNLP 组合，选择 Python 3.9.25，而不是 3.9.2
的系统 base，也不把本机 Python 3.11 运行结果扩大为官方兼容结论。

官方来源：
<https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/80RC3alpha003/softwareinst/instg/instg_0009.html>

## 当前 20T 实测

```text
SoC: Ascend310B1 / 20T
CANN: 8.0.0
npu-smi / driver: 25.2.0
Health: Alarm（已知设备状态，不是本次环境失败）
ascend Python: 3.9.25
ascend ABI: cpython-39-aarch64-linux-gnu
ascend PyACL: source CANN 后 import ok
```

统一环境中的主要版本：
| 组件 | 版本 |
| --- | --- |
| MindSpore / MindNLP | 2.4.10 / 0.4.1 |
| FastAPI / Pydantic / httpx | 0.115.2 / 2.5.3 / 0.28.1 |
| NumPy / Pandas / Pillow | 1.26.4 / 2.0.3 / 10.4.0 |
| OpenCV headless | 4.10.0.84 |
| PyTorch / TorchVision | 2.1.0 / 0.16.0 |
| ONNX / ONNX Runtime | 1.19.1 / 1.19.2 |
| FAISS CPU | 1.7.4 |
| sherpa-onnx | 1.13.6 |
| aclruntime（CANN/AIS Bench） | 0.0.2 |
| aiortc / PyAV / aiohttp | 1.9.0 / 12.3.0 / 3.10.10 |
| Gradio | 4.44.0 |
| PySide6 / pyqtgraph | 6.5.3 / 0.13.7 |
| cryptography | 42.0.8 |

`aiortc` 声明可接受 `cryptography>=42.0.0`，最终环境中保留 case4 要求的
42.0.8；case4 仍需用完整工作树重新验收，不能仅凭 import 通过固化版本。

本轮 20T 快照保存在板端：

```text
/home/HwHiAiUser/Documents/samples/case9/reports/ascend-pip-freeze-20261022.txt
lines: 152
SHA-256: f9665801b0b78ca0c1560bd83f229df18541ffbece6b294e21b93075e9c521b5
```

该快照只用于后续审计，不是最终锁文件；case2/4/6/8 和 8T 尚未完成验收。

`pip check` 当前仅报告 Ascend `op-compile-tool 0.1.0` 把标准库
`getopt`、`inspect`、`multiprocessing` 错列为 pip 依赖。三项均由 Python
3.9 标准库提供，不应为了消除这条元数据错误而从 PyPI 安装同名包。

## 当前 8T 实测

```text
SoC: Ascend310B4 / 8T
hostname / IP: orangepiaipro / 192.168.8.212
CANN: 7.0.0
npu-smi / driver: 23.0.0
Health: Alarm（已知设备状态）
ascend Python: 3.9.25
ascend ABI: cpython-39-aarch64-linux-gnu
ascend PyACL / aclruntime: import ok
ACL device/context smoke: passed
```

8T 的 `ascend` 环境由清华 Anaconda 镜像创建，因为默认 conda 通道不可达。
其余核心包与 20T 对齐：FastAPI、NumPy、OpenCV、Torch、ONNX Runtime、
FAISS、aiortc、PyAV、Gradio、PySide6、Pandas、MIDI、USB、sherpa-onnx 等
均已导入通过。

8T 已复制 20T 的 MindSpore 2.4.10 / MindNLP 0.4.1 进行测试，但
MindSpore 2.4.10 与 CANN 7.0 ABI 不兼容：

```text
MindSpore 2.4.10 expects Ascend software package 7.5/7.6; detected 7.0
libmindspore_ascend.so.2: undefined symbol: _ZNK2ge11GraphBuffer7GetSizeEv
Unsupported device target Ascend
```

随后使用 8T 镜像已有的 MindSpore 2.2.14，并把包复制进 `ascend`；在
`PYTHONNOUSERSITE=1` 下 MindSpore 2.2.14 + MindNLP 0.3.1 可导入，
`device_target=Ascend` context 设置成功。MindNLP 0.4.1 与 MindSpore
2.2.14 在 Qwen2 构图时存在 `Range` 参数类型错误，0.3.1 已验证越过该构图
点。插件统一环境门现已为 `passed`。

因此跨板策略是“同一环境名、同一 Python/隔离规则和通用依赖；MindSpore
与 MindNLP 版本按 CANN/driver 配套选择”。20T 使用
MindSpore 2.4.10 + MindNLP 0.4.1，8T/CANN 7.0 使用
MindSpore 2.2.14 + MindNLP 0.3.1。最终锁文件必须按板级兼容组分开记录，
不能把两个 binary ABI 混成一个版本。

8T 环境快照：

```text
/home/HwHiAiUser/ascend-pip-freeze-8t-20261023.txt
lines: 151
SHA-256: c809983564ddc183851228cd8c8e6e5ea85d1cc885bfa0b331203e85a39b7a74
```

`pip check` 在 8T 上只报告 `op-compile-tool` 把标准库
`getopt`、`inspect`、`multiprocessing` 错列为 pip 依赖，与 20T 的已知
元数据问题一致。

8T 旧板 `.212` 硬件限制：

- 唯一摄像头为 `2bdf:0289 SN0002 1080P USB Camera`。
- 加入 `video` 组后曾枚举到 `/dev/video0` 和 MJPG/YUYV/NV12 格式。
- 实际打开后 UVC 控制查询连续返回 `-110`，设备先 reset/disconnect，
  随后 `/dev/video*` 消失；当前视频采集门失败，不只是麦克风失败。
- 当前没有录音/播放声卡，Pulse 只有 `auto_null`；本轮无法测试麦克风或喇叭。

8T case9 的 Qwen1.5-0.5B 模型已通过分块传输并校验：

```text
model.safetensors bytes: 1239173352
SHA-256: 72453a8ccb338811935ab95a3a6ffa86b586807bf5b3dc327f28b5389b5636e6
release preflight: passed
```

在线加载使用临时 B4 board profile 做有界 smoke，测试后配置已恢复并与备份
SHA-256 一致。模型完成 tokenizer 和权重定位后，MindSpore 初始化失败：

```text
Device HBM total size: 7912181760
Device HBM free size: 2870607872
```

失败时没有 Python 进程可终止；之后 `npu-smi` 已用仍为 3973/7545 MB。
因此这是设备基线 HBM 占用/回收边界，不是文件损坏或 Python 导入失败；
未生成任何 token，不能记为 8T 在线推理通过。

8T 软件测试结果：

| 案例 | 结果 | 边界 |
| --- | --- | --- |
| case3 | `252 passed, 5 failed, 2 skipped` | 5 个失败为未上传的 Piano-DDSP 模型 bundle |
| case7 | `245 passed, 1 failed` | 1 个失败为未上传的候选转换资产 |
| case2 | `detection_app.py --help` 通过 | 无 camera/model 实跑 |
| case6 | `app.py --help` 通过 | 无 camera/model 实跑 |
| case8 | WebRTC 与 OM camera 入口 `--help` 通过 | 无 camera/model/VENC 实跑 |
| case9 | `62 passed`；MindSpore 2.2.14 Ascend context 通过；Qwen 模型完整传输并通过 SHA | 在线加载被 NPU 基线 HBM 占用阻断（3973/7545 MB，无 Python 进程）；未生成 token |
| case4 | ascend 环境模块导入通过；ACL device/context smoke 通过 | camera smoke 因 `/dev/video*` 消失失败；模型资产未部署 |

## 第二块 8T 硬件板 `.249`

2026-10-23 将 UGREEN 摄像头和 Jieli USB 喇叭换到第二块 8T：

```text
IP / MAC: 192.168.8.249 / c0:74:2b:fd:df:83
SoC: Ascend310B4 / 8T
CANN / driver: 8.0.0 / 25.2.0
Health: Alarm（已知状态）
ascend: Python 3.9.25，完整环境门 passed
UGREEN: 0c45:2283，MJPG 最高 1080p30
Jieli: 4c4a:4155，重启前 card 2 playback
UGREEN audio: card 1 capture
```

环境是从 20T 按相同绝对路径复制的完整 `ascend` 包，压缩包和展开后
MindSpore/CANN context 均验证通过。插件新增了“启动策略 user-site 隔离”
与“裸解释器 user-site 状态”的区分，避免把未设置环境变量的诊断解释器
误判为案例启动策略失败；插件全量测试通过。

case4 aarch64 摄像头发现修复：

```python
_V4L2_FORMAT_WIDTH_OFFSET = 8
```

原代码在 `struct v4l2_format` 偏移 4 读取 width/height，得到 `(0, 1280)`，
因此把真实 UGREEN 摄像头过滤掉。修复后：

- `tests/board/test_camera_capture.py`: `10 passed`
- CameraCapture 1280x720 三帧成功，平均约 `315.287 ms`
- 重启后再次自动发现 `/dev/video0`，三帧成功，平均 `319.413 ms`
- case4 API `/api/health`、`/api/bootstrap` 成功
- 正式相机会话 open/frame/close 成功
- 返回 `X-Capture-Format: MJPG`，`1280x720` 原始帧，`960x540` JPEG 预览

原始音频门：

- UGREEN `plughw:1,0` 16 kHz 双声道 2 秒采集成功，WAV 128044 bytes。
- Jieli `plughw:2,0` 440 Hz 正弦播放成功。
- 中文 eSpeak WAV 通过 `pasuspender -- aplay -D plughw:2,0` 播放成功。
- 重启后 Jieli 没有重新枚举；需要重新插拔喇叭后继续板载语音测试。

`.249` 软件测试：

Case3 的完整 runtime overlay 已发布到 Hugging Face：

```text
repo: zhouxzh/piano-ddsp-ascend310
tag: case3-runtime-v1.0.1
commit: bd04b503920860896fbbf504993046ea099b45f8
files: 166
SHA256SUMS: e178f3db214437e45a00660f509b4b19611b3a1d44cb554f37c732292a347660
```

GitHub Actions run `39926902169` 从官方 HF 冷下载并验证全部 166 项 checksum。
HF 仓库已精简为单个 `main` branch 和单个 `case3-runtime-v1.0.1` tag；历史
source tag 已删除，source 通过 commit `c41911aa7de454aeacf0b3edbb2d06a0801fb3ff`
固定。

| 案例 | 结果 | 边界 |
| --- | --- | --- |
| case1 | `/api/health` 返回 `ready=true`、`camera_ready=true`；真实相机抓拍成功；合成图 clockin 返回 `match=false` | 两个生产 OM 已加载；无授权人脸样本，不宣称身份匹配准确率 |
| case2 | SSD MobileNetV2-0.5 OM 完成 vtest 检测与跟踪；输出 MP4 分别为 17.1 MB、18.4 MB | 单模型链路；其余 14 个候选模型未逐一性能验收 |
| case3 | `257 passed, 2 skipped`；24 个 runtime OM checksum 通过；真实 `gru_ir_96_64` OM 一帧推理通过；WebUI runtime contract 通过 | Piano-DDSP bundle、MIDI-DDSP stateful bundle 和 DDSP-VST 模型已部署；真实 MIDI/音频/触屏人工验收单列 |
| case4 | 生产 CCNet OM hash 校验通过；health/bootstrap ready；真实识别请求两次成功，模型 46.93/44.16 ms，热路径总 48.07 ms | 模板库为空时返回 `no_templates`；未构造真实掌纹 EER/Rank-1 |
| case7 | `246 passed`；3 个 admitted 模型全部 ready；一张合成图完成 3 个 NPU embedding；中文/英文文本搜索均命中 | 中文查询走 Chinese-CLIP，英文走 MobileCLIP；未使用真实个人照片 |
| case8 | OM camera 5 帧、4 次推理、NPU 90.31 ms；WebRTC + NPU + CPU libx264 建立 `connected`，14.3 FPS、NPU 87.5 ms | CANN VENC 仍因已知 CANN 8.0/driver 25.2 `507018` 失败，不能用 CPU 编码替代硬编结论 |
| case9 | `62 passed`；release preflight passed | 在线 Qwen 加载时板卡 SSH 失联，未生成 token |
| case6 | `.212`/CANN7 与 20T/CANN8 均完成 ONNX、ATC、OM 和 NPU 分类，`use_npu=true`；20T OM SHA-256 `5b4e080fcc1d294bd894614181aadd8173a06738cd6dbb1f8c76f4d79de5161d` | `.249`/CANN8/8T 待恢复复测；模型无训练分类头，分类精度不作声明 |

Qwen 模型在 `.249` 已完整传输并通过同一 SHA-256：

```text
72453a8ccb338811935ab95a3a6ffa86b586807bf5b3dc327f28b5389b5636e6
```

重启后 HBM 基线降至 1371/7546 MB。第一次无内存上限启动 Qwen worker 时，
板卡保持 ICMP 可达但 SSH banner 超时；物理重启后恢复。为复现并限制风险，
worker 新增可选环境变量：

```text
ASCEND_EDGE_CHAT_MAX_DEVICE_MEMORY=3GB|2GB
```

两次有界启动均 fail-closed，未再卡死，最后一次错误为：

```text
Free memory size is less than half of total memory size.
Device HBM total size: 7912599552
Device HBM free size: 1223524352
```

失败发生在模型构图进入 MindSpore Ascend runtime 时，没有生成 token。失败后
HBM 又会保留到下一次重启才能释放。因此该次在线推理记为
`observed-fail (memory/runtime initialization)`，不是文件损坏，也不能据此
宣称 8T 模型不兼容。可以继续前需要串口日志或调整 MindSpore context/HBM
策略。

2026-10-23 又在 `.212`（CANN 7 / MindSpore 2.2.14）用 Qwen1.5-0.5B
做了独立重试。7 个 artifact 均通过字节数和 SHA-256 校验，worker 已开始
构造 MindNLP/Qwen2 模型；在 Ascend kernel runtime 初始化时仍失败：

```text
Device HBM total size: 7912181760
Device HBM free size: 2649354240
Free memory size is less than half of total memory size
```

进程已退出，没有 health/token 输出，也未发现残留 worker。该结果把边界
精确到 MindSpore kernel runtime 的 HBM 初始化，不是 tokenizer、权重文件或
网络下载失败。

## 已执行验证

| 案例 | 验证结果 | 边界 |
| --- | --- | --- |
| case1 | `.249` 统一环境：`/api/health ready=true`、相机抓拍 HTTP 200、clockin HTTP 200 | 合成图 `match=false`；无真实授权人脸准确率 |
| case2 | `.249` 统一环境：SSD OM 检测和跟踪完整处理 vtest | 单模型、单视频协议 |
| case3 | `.249` 统一环境：`257 passed, 2 skipped`、真实 Piano OM 和 WebUI API | 真实音频演奏仍需 Jieli 重枚举后的人工验收 |
| case4 | `.249` 统一环境：CCNet OM ready，真实识别热路径 48.07 ms | 空模板库；无真实掌纹指标 |
| case5 | `243 passed, 1 skipped` | 20T 统一环境单元测试；真实采集/OM 仍单列 |
| case6 | `.212`/CANN7 与 20T/CANN8：setup、ONNX、ATC、OM、`SceneClassifier use_npu=true` 通过 | 模型无训练分类头；`.249`/8T CANN8 待恢复复测 |
| case7 | `.249` 统一环境：`246 passed`、3 个 NPU embedding、中文/英文文本搜索命中 | 合成照片，无个人照片 |
| case8 | `.249` 统一环境：OM camera 与 WebRTC/NPU 通过；VENC `507018` fail-closed | CPU libx264 只证明降级视频链路，不证明硬件 VENC |
| case9 | `57 passed`；20T release acceptance 与语音可听通过；8T `62 passed` | 8T Qwen 在线加载被 HBM/runtime 初始化计数阻断 |

2026-10-23 17:27 左右，case6 ATC 运行超过 600 秒后，`.249` 的 TCP 22
端口仍可连接，但 SSH banner 持续超时；ICMP/端口可达不等于系统可管理。
现场没有识别到可登录串口，因此没有执行断电或破坏性恢复。该次 case6
ATC 记为 `observed-fail (resource/runtime stall)`，OM 和 app NPU 均未验收。
只有 SSH/串口恢复并确认 ATC 进程与 HBM 后，才能重试。

改在 `.212`（CANN 7.0/driver 23.0.0）复测后，默认 ATC 因多个
`ForkServerPoolWorker` 并发而被系统以退出码 `137` 杀死；以
`TE_PARALLEL_COMPILER=1` 重跑约 144 秒成功生成 OM。合成道路图分类返回
`use_npu=true`。同月在 20T/CANN 8 上执行同一 `setup.sh` 和固定
`TE_PARALLEL_COMPILER=1` 路径，约 254 秒生成 22 MB OM，NPU 分类返回
`use_npu=true`。因此 ATC 并发修复已在 CANN 7 和 CANN 8 软件组上复现，
但 20T 结果不能替代 `.249` 的 8T 复测。`prepare_models.py` 已把该并发
限制写成默认值；模型仍是 ImageNet backbone 加随机 5 类头，不能据此声明
场景分类质量。

case8 的 VENC 结果与
[已知问题](../samples/case8/docs/05-known-issues-and-version-compatibility.md)
一致：CANN 8.0/driver 25.2/firmware B309 在通道创建阶段返回 `507018`。
同一版本的 OM 推理正常；显式 CPU libx264 仅作为页面声明的降级路径，
不能替代 CANN VENC 硬件验收。

## 依赖锁候选

两个板级兼容组已经从实际 `ascend` 环境生成 `pip freeze`：

| 组 | 来源 | 行数 | SHA-256 |
| --- | --- | --- | --- |
| CANN 8.0 / driver 25.2 | 20T `ascend-pip-freeze-20t-20261023-after-jieba.txt` | 153 | `d5fb5facfa9031fb28a9c64c88a6c0b84d227cb9459b98929b2fa8b86f42c662` |
| CANN 7.0 / driver 23.0 | 8T `.212` `ascend-pip-freeze-8t-20261023-after-jieba.txt` | 152 | `3354e353fef4c1552cd7c4f58f490036d1c7a85163ba4cb0a6b4ae5d05aaa763` |

冻结快照中的 `aclruntime`、`auto_tune`、`dataflow`、`hccl`、`op_compile_tool`
等条目来自 CANN 安装目录或临时 wheel 路径，属于系统运行时，不进入可直接
从 PyPI 安装的 requirements。两个兼容组的主要差异为：

```text
mindspore: 2.4.10 (CANN8) / 2.2.14 (CANN7)
mindnlp:   0.4.1  (CANN8) / 0.3.1  (CANN7)
cryptography: 42.0.8 / 50.0.2
```

其余通用包以当前统一环境为准。已生成候选锁：

```text
dependencies/ascend-py39-cann80.lock
  SHA-256: 1c40f765af29e1ce0fa81425008cff18da93ab536b874f75323f905d3ea37a02
dependencies/ascend-py39-cann70.lock
  SHA-256: 0b1867ebce32d110a484bffe73afbda380d16954cd8aa8d7af6dd9998f207fa9
```

两份锁均已在对应板端通过 `pip install --dry-run`。CANN 7 组补齐了
MindNLP 0.3.1 所需的 `jieba==0.42.1`；CANN 8 组同步该版本。
`pip check` 仅剩 `op-compile-tool` 将 Python 标准库误报为 pip 依赖的问题。
正式锁文件仍需在剩余外部硬件门关闭后确认，并同时保留组名、CANN/driver/SoC、
wheel 来源和系统提供包白名单。

## 环境清理预检

| 板卡 | `ascend` | 旧 conda 环境 | `~/.local` | user-site 独有包 |
| --- | --- | --- | --- | --- |
| 8T `.212` | 2.8 GB | 无 | 971 MB | `mindspore-lite==2.2.11`；另有 `asttokens` 旧版本 |
| 20T | 3.3 GB | `case9-ms240-cann80` 1.8 GB；`case9-voice` 128 MB | 2.3 GB | `download`、`mindspore-lite`；另有 35 个旧版本 |

当前案例代码在 `PYTHONNOUSERSITE=1` 下均由 `ascend` 满足；
`mindspore-lite` 只出现在 case9 归档文档，不被当前 case9 代码导入。
清理仍不执行：它必须等 VENC、case9 8T runtime、Jieli 音频和 case6
CANN 8 复测完成后，避免失去回滚材料。

## 服务迁移

case9 的 worker、gateway、UI、voice-agent 均实际从以下解释器运行：

```text
/home/HwHiAiUser/.conda/envs/ascend/bin/python3.9
```

旧环境 `case9-ms240-cann80` 和 `case9-voice` 暂时保留为回滚，不得在全部
案例通过前删除。`~/.local` Python 包也暂不清理。

## 系统依赖变化

为了让统一环境中的 `sounddevice` 可用，20T 安装了系统 PortAudio：

```text
libportaudio2
portaudio19-dev
```

CANN 8.0 的 `aclruntime 0.0.2` 只存在于 base，且是
`cpython-39-aarch64-linux-gnu` 扩展。为了保持在 `ascend` 中运行且不依赖
base，已把该 `.so` 和 dist-info 复制到 `ascend`；source CANN 后
`import acl` 与 `import aclruntime` 均通过。

Ubuntu 依赖解析将 `libjack-jackd2-0` 替换为兼容提供者 `libjack0`，并升级
了 `libasound2`。安装后已复查 `sounddevice`、PulseAudio、UGREEN 麦克风、
Jieli 输出和 case9 voice-agent，均正常。

## 尚未完成

1. 恢复 `.249` SSH/串口，使用已固化的 `TE_PARALLEL_COMPILER=1` 复测 case6 CANN 8 ATC。
2. case8 CANN VENC 等供应商修复 firmware/driver；当前硬件路径为 `507018`。
3. 重新插拔 Jieli，完成 case3 真实音频与 case9 8T 板载语音验收。
4. case5 缺少真实 SDR/采集前端，保留 20T 单元测试结论。
5. case9 8T Qwen 在线加载需要解决 MindSpore Ascend runtime/HBM 初始化边界。
6. 剩余硬件门解决后执行 `pip freeze`/哈希锁定，再清理两个旧 case9
   环境和 `~/.local` 重复包。
