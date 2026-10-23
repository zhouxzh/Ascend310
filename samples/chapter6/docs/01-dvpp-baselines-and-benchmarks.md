# DVPP 样例与基准口径

## 运行前置

所有脚本都直接导入 PyACL，并要求能从 `acl` 访问 `media` 接口。板端应先加载 CANN 环境，再使用统一运行环境：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
```

从仓库根目录运行：

```bash
python samples/chapter6/check_cann.py
python samples/chapter6/venc/venc_minimal.py
python samples/chapter6/vdec/vdec_minimal.py
python samples/chapter6/vpc/vpc_minimal.py
python samples/chapter6/jpeg/jpeg_minimal.py
```

`vdec_minimal.py`、`vdec/bench_vdec.py` 和 `venc/bench_venc.py` 还需要 PyAV 所链接的 `libx264`、`libx265`。`vpc/bench_vpc.py` 还需要 OpenCV Python 包。缺少这些可选依赖时应将其归因于对应对照路径，而不是判定 DVPP 不可用。

## 环境诊断入口

`check_cann.py` 是最小前置检查，依次执行 ACL 初始化、绑定设备 0、创建并设置 context，然后打印 SoC、CANN 版本和 `acl.media` 中名字含 `venc` 的符号数量。

该脚本只能证明：

- Python 能导入当前 `acl` 模块；
- ACL 初始化和设备绑定成功；
- 运行时能报告 SoC 和版本；
- `acl.media` 暴露了 VENC 相关符号。

它不能证明 VENC 通道可以创建、可以编码、摄像头可用，或 WebRTC 能工作。任一步骤返回非零值时脚本以断言失败退出。

## 原始 API 最小样例

| 入口 | 输入 | 输出与成功信号 |
|---|---|---|
| `venc/venc_minimal.py` | 内存中确定性 `640x480` NV12 帧 | 分别创建 H.264 Baseline 和 H.265 Main 通道，打印关键帧字节数 |
| `vdec/vdec_minimal.py` | 同一帧经 CPU 编码得到的 H.264/H.265 码流 | 分别经 VDEC 解码为 NV12，打印平面形状和字节数 |
| `vpc/vpc_minimal.py` | 确定性 NV12 帧 | 执行 `640x480 -> 320x240` resize，以及中心裁剪后放大，打印输出字节数 |
| `jpeg/jpeg_minimal.py` | 确定性 `640x480` NV12 帧 | 执行 NV12 -> JPEGE -> JPEG -> JPEGD -> NV12 闭环，检查输出大小 |

### NV12 合同

这些脚本统一使用 PyACL 的 `PIX_FMT_NV12 = 1`：

- dtype 为 `uint8`；
- 紧凑平面形状为 `(height * 3 // 2, width)`；
- 前 `height` 行是 Y 平面，后 `height / 2` 行是交错 UV；
- 宽高必须为偶数；
- VENC/VPC 的缓冲区宽度按 16 字节对齐，高度 stride 通常按 2 对齐；
- 对齐区可能让设备缓冲区大于紧凑帧，读取有效像素时必须按实际 stride 处理。

JPEG 和 VPC 路径的成功判据主要是 API 返回值和缓冲区大小，不是逐像素等价。`jpeg_minimal.py` 打印的 `PASS` 只表示解码输出不小于按未对齐宽高计算的有效 NV12 大小，不能替代图像质量或颜色正确性验证。

### 编码流合同

`venc_minimal.py` 将 `entype=1` 作为 H.264 Baseline、`entype=0` 作为 H.265 Main。VENC 回调在独立线程中运行，样例通过 ACL report-processing 线程接收结果。码率参数使用 kbps，GOP 为 30 帧。样例强制首帧为 I 帧，并最多等待 5 秒回调结果。

回调超时返回空字节；脚本仍可能继续执行并打印“已编码关键帧”的零长度结果。因此日志中的通道创建成功、发送成功和非空码流是三个不同判据。

### 解码流合同

VDEC 输入必须是完整且符合所选标准的码流，不能替换为任意字节。样例先由 PyAV 编码一帧 H.264 或 H.265，再发送给 VDEC。VDEC 通道显式使用 `channel_id=0`，输出 NV12。

回调先检查 `ret_code`，再将设备输出复制到主机。超时或回调失败会返回空字节，并由上层打印“解码未产生输出”。输出 reshape 假定回调提供的缓冲区能按宽度整除；该样例不是流式解复用器。

## 基准入口

| 命令 | 对照对象 | 测试负载 |
|---|---|---|
| `python samples/chapter6/venc/bench_venc.py` | CANN VENC 与 PyAV `libx264`/`libx265` | 480p、720p、1080p、1440p、2160p；每档 3 帧预热、90 帧计时 |
| `python samples/chapter6/vdec/bench_vdec.py` | CANN VDEC 与 PyAV CPU 解码 | 同一分辨率集合；CPU 先生成 90 帧码流，再比较多线程和单线程解码 |
| `python samples/chapter6/vpc/bench_vpc.py` | DVPP VPC 与 OpenCV CPU resize | 同一分辨率集合；每档 3 帧预热、60 帧计时，输出为输入的一半尺寸 |

测试帧是确定的渐变、移动条和棋盘格，不是摄像头视频或业务数据集。结果只适用于此合成负载和所记录的运行环境，不能直接外推到真实视频质量、模型吞吐或端到端应用帧率。

VENC/VDEC 基准按分辨率捕获异常并打印 `SKIP`，因此进程退出码为 0 时仍可能存在未完成的档位。判断有效覆盖必须检查每一行，而不能只看“脚本结束”。VPC 基准没有相同级别的逐档容错，底层 API 或 OpenCV 异常会终止运行。

## 基准输出与证据边界

三个基准主要把表格写到标准输出，不自动生成带环境快照的 JSON。若需要发布性能结论，应另存原始日志，并同时记录：

- SoC 型号和计算档位、设备编号、CANN/driver/firmware 版本；
- Python、PyAV、OpenCV 和 codec 版本；
- 精确命令、分辨率、帧数、预热数、GOP 和码率；
- 每个分辨率是否成功，而不是只保留最快的一行；
- 是纯 DVPP API 吞吐、含主机与设备拷贝的路径，还是完整应用路径。

本次文档整理没有运行上述板端基准。
