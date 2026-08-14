# 07 昇腾 310B 异构信号处理评估与 SDR-NPU 指引

本章给出 Case 5 在一块昇腾 310B 开发板上的可复现实测结果。结论只适用于本次记录的
板卡、软件版本、输入形状和测量方法，不外推到其他昇腾型号。

## 1. 测量边界

VOLK、FFTW、ONNX Runtime CPU 和 OM 的计时都从主机输入开始，到主机输出结束。NPU
计时包括输入 Tensor 创建、H2D、OM 执行、D2H 和输出复制，不包括模型初始化。每个样本
预热 50 次、测量 300 次、重复 3 轮；CPU 进程固定到当前允许集合中的一个 ARM 核。向量
长度为 1024，batch 为 1、16、64，等效采样率按 RTL-SDR 的 2.048 MHz 计算。实现入口是：

```bash
python -m time_frequency_dashboard.benchmark_volk_npu \
  --warmup 50 --iterations 300 --repeats 3
```

板端记录的环境为：`orangepiaipro`、ARM64、CANN toolkit 8.0.0（package
7.6.0.1.220）、Ascend 310B4、Python 3.9.2、ONNX Runtime 1.19.2、NumPy 1.26.4、VOLK
2.5.1。VOLK 当前启用 `generic_orc;neon_orc;neonv8`，机器级 dispatcher 为 `neon_orc`；
没有 SVE，因此本文不作 SVE 声明。输入由固定种子生成，JSON 同时保存输入 SHA256、每个
内核的可用实现、ONNX/OM/ATC/CANN provenance、NPU 状态和温度前后值。本轮 NPU 温度
为 83 C 到 81 C；这不是功耗或散热能力结论。

## 2. VOLK 与 NPU 对照

四个内核的 ONNX 对照图只使用标准逐点算子和 `ReduceSum`，分别对应 `Mul+Add`、复数展开
的 `Mul/Add/Sub` 和实数/复数归约。下表引用板端
`data/volk_npu_benchmark/volk_npu_full_v2_20260812.json`：所有数字是 3 轮各 300 次、合并
900 次测量的 P50（ms），预热不计 CPU 时间，ORT 明确固定单线程和顺序执行。NPU 数值列是
与 NumPy 参考的 `rtol=1e-2, atol=1e-3` 检查；NPU 计时包含 Tensor 创建、H2D、OM、D2H
和主机输出复制。

| 内核 | batch | generic | NEON | dispatcher | ORT CPU | OM NPU | 最佳 VOLK/OM | 数值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| magnitude squared | 1 | 0.001395 | 0.000542 | 0.001395 | 0.036230 | 0.204033 | 0.0027x | 通过 |
| magnitude squared | 16 | 0.027167 | 0.009625 | 0.026979 | 0.117293 | 0.372628 | 0.0258x | 通过 |
| magnitude squared | 64 | 0.106396 | 0.053021 | 0.106802 | 0.397754 | 0.826840 | 0.0641x | 通过 |
| multiply conjugate | 1 | 0.002396 | 0.001313 | 0.002375 | 0.053511 | 0.236533 | 0.0056x | 通过 |
| multiply conjugate | 16 | 0.049157 | 0.024333 | 0.045688 | 0.280065 | 0.551411 | 0.0441x | 通过 |
| multiply conjugate | 64 | 0.207344 | 0.144062 | 0.206198 | 1.089634 | 2.321644 | 0.0621x | 通过 |
| real dot product | 1 | 0.001562 | 0.000438 | 0.001396 | 0.035292 | 0.197627 | 0.0022x | 通过 |
| real dot product | 16 | 0.023563 | 0.007500 | 0.012750 | 0.110803 | 0.309409 | 0.0242x | 未通过 |
| real dot product | 64 | 0.091729 | 0.036438 | 0.054458 | 0.329555 | 0.569015 | 0.0640x | 未通过 |
| complex dot product | 1 | 0.002563 | 0.001208 | 0.002625 | 0.056688 | 0.229564 | 0.0053x | 通过 |
| complex dot product | 16 | 0.045334 | 0.019479 | 0.033396 | 0.272273 | 0.454942 | 0.0428x | 未通过 |
| complex dot product | 64 | 0.204021 | 0.104125 | 0.147334 | 0.994665 | 1.044822 | 0.0997x | 未通过 |

这里的“最佳 VOLK/OM”是最佳 VOLK P50 除以 OM P50，低于 1 表示 NPU 更慢。四类内核的
最大比值仅为 `0.0997x`（complex dot product, batch=64），即 OM 仍约慢 10 倍；两个归约
内核的 batch 16/64 还未通过默认数值门限。NPU 没有在四类内核上达到 1.2 倍收益门槛，因此
全部标记为 CPU/SIMD；小批量逐点运算和归约由主机 Tensor 与搬运开销主导。

## 3. FFTW 的位置

同一块板、同一 1024 点窗口的已有 RTL-SDR 基准为：FFTW3 `0.136/0.141 ms`（P50/P95），
NumPy FFT `1.208/1.270 ms`，固定稠密 DFT `6.168/6.421 ms`，OM 固定 DFT
`1.618/1.641 ms`。因此 FFTW 比 OM 固定 DFT 快约 11.9 倍；OM 只比“相同稠密 DFT 算法”
快约 3.81 倍。不能把 NPU DFT 结果表述为 FFTW 的替代品。FFT、去直流、窗口和轻量滤波
默认留在 ARM + FFTW/VOLK。

## 4. 模型准入结果

模型导出和 ATC 在独立临时环境/板端完成，权重不提交仓库；每个版本化 manifest 记录 URL、
revision、许可证、上游权重 SHA256、采样/预处理约定、ONNX/OM SHA256、ATC 命令和 CANN
版本。实时入口会在加载前重新校验 ONNX/OM 哈希、`accepted`、数值通过、来源合同、NPU
窗口预算和 live-demo eligibility；JSONL 也固化这些工件哈希和 CANN 版本。`accepted` 是
模型/NPU 准入，不等价于主机全流水线实时通过。加载方式为：

```bash
python -m time_frequency_dashboard.model.materialize_inference_manifest \
  --candidate torchsig_xcit --atc-evidence <file>.atc.json \
  --output-shape 61
```

### 上游版本与可移植边界

所有下载均通过 `CASE5_CLASH_PROXY` 完成，权重只在隔离的临时 PyTorch 环境读取或导出，
不提交到 Case 5。部署代码本身不导入 PyTorch。下表是本轮实际固定的上游版本：

| 上游 | revision / SHA256 | 许可证状态 | 本阶段职责与结论 |
| --- | --- | --- | --- |
| [TorchSig](https://github.com/TorchDSP/torchsig) | `58bf300c912ac6094a17e1720c48be9a8897ceee`; `xcit.ckpt` `c92ee780c080c1a22dabfa0b15049991dee94e6fe840bc0c8376a6485c720e0c` | MIT（v1.1.0 的 pyproject/README） | 数据生成、信道损伤、评估集与 XCiT 权重来源；XCiT 已完成 ONNX/OM 测试但未通过数值/实时准入。 |
| [torchsig-models](https://github.com/TorchDSP/torchsig-models) | `0c30fc6579e58c01428cb11f724c7412cb9b207e` | MIT | 仅用于阅读 XCiT、EfficientNet1D、YOLO 结构；未把训练依赖迁入部署包。 |
| [gr-spectrumdetect](https://github.com/TorchDSP/gr-spectrumdetect) | `868cb381e1fdd7d13ad70ecaf271e5060c43308d`; `11s.pt` `09b774d8f90aad1ad1947df2d26ebac191ef0d400d1ac38c58bf23a91bf26df2` | MIT；导出 ONNX 元数据另报 Ultralytics AGPL-3.0 | 频谱图预处理、`xcit.ckpt`/`11s.pt` 迁移基准。其 Blackman、功率、`fftshift`、dB、黑热图和 RGB 复制预处理已在 CPU/FFTW 实现。 |
| [agentic-spectrumdetect](https://github.com/TorchDSP/agentic-spectrumdetect) | `a503a6935eb560f50b005462218e4b1e5cfc1d77` | MIT | 只分析 CUDA、TensorRT、vLLM 的分层架构；这些 NVIDIA 专用运行时不直接移植到 310B。 |
| [CVNET-rf](https://huggingface.co/sohelimi/cvnet-rf) | `df32f6cd9bb033835465928307610bba1c376708`; Real `8a6861dec969d01bcf0198ef2e0fc52c0938c6dacf613ca8b1078eba449ff53c`，Complex `30447995f7fc2931fc24cea415a78301bab1353bdb33dd017a73279a1c582aa1` | 模型卡声称 MIT，源码/权重合同需单独确认 | `[B,2,1024]`、24 类；两种 checkpoint 都能安全读取，但官方推理模型键与权重键不匹配，保留为阻断。 |
| [SignalIQ CLDNN](https://huggingface.co/alirezaaminzadeh/radio-modulation-classifier) | `839f80a8de05d5f3506aa326a8c870bf77be180e`; `model_for_hub.pt` `0318db9df50a0b971449ac0146de2dc35691c858c35ec2e33dfa9d1a5be6b055` | 模型卡声称 MIT，forward 合同未公开 | `[B,2,1024]`、11 类；重建图只用于 ONNX/ATC 可行性，不能证明上游准确率。 |

`gr-spectrumdetect` 的可移植部分是固定形状频谱预处理、ONNX 图和后处理；`agentic-spectrumdetect`
中的 CUDA kernel、TensorRT engine 和 vLLM 服务不是 CANN 算子替代品，不能在 310B 上直接运行。

分类模型要求 logits 余弦相似度不低于 0.999、Top-1 一致率不低于 99%，并同时满足
`rtol=1e-2, atol=1e-3` 的有限值检查；实时检测还要求 NPU P95 不超过一个输入窗口。当前
板端的关键结果如下（报告位于 `data/model_admission/`）：

| 候选 | 形状 | NPU P50/P95 (ms) | CPU ORT P50 (ms) | NPU/CPU | 数值/实时 | 准入 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| TorchSig XCiT | `[1,2,1024]` | 21.457/21.560 | 375.406 | 17.50x | 数值未通过，预算 2 ms 不满足 | 拒绝 |
| TorchSig XCiT | `[16,2,1024]` | 386.773/387.432 | 4109.737 | 10.63x | 数值未通过，预算 32 ms 不满足 | 拒绝 |
| SignalIQ CLDNN（重建图） | `[1,2,1024]` | 19.371/19.432 | 19.462 | 1.00x | 数值通过，但预算 0.5 ms 不满足且源合同未完成 | 拒绝 |
| SignalIQ CLDNN（重建图） | `[16,2,1024]` | 23.989/24.074 | 153.249 | 6.39x | 数值报告有误差，预算 8 ms 不满足且源合同未完成 | 拒绝 |
| TorchSig YOLO11，基础 OM | `[1,3,1024,1024]` | 133.528/133.835 | 2546.006 | 19.07x | box 坐标数值未通过，预算 512 ms 满足 | 拒绝 |
| TorchSig YOLO11，混合精度 + 解码保精度 | `[1,3,1024,1024]` | 141.422/141.705 | 2560.730 | 18.11x | 数值通过，预算 512 ms 满足 | 推荐 |
| TorchSig YOLO11 | `[16,3,1024,1024]` | 2986.525/2990.200 | 30803.558 | 10.31x | 数值未通过，预算 8192 ms 满足 | 拒绝 |

YOLO 第一次 ATC 使用错误的输入名 `input_tensor`，板端日志为 `E10016 Opname[input_tensor]
not found`；改用导出图真实输入名 `images` 后 ATC 成功。这类修正只允许修正图/输入合同，
不能用它绕过数值准入。YOLO 导出图含 88 个 `Conv`，以及 `Mul`、`Sigmoid`、`Concat`、
`Reshape`、`Split`、`MaxPool`、`Transpose`、`MatMul`、`Softmax`、`Resize`、`Div`、`Slice`
和 `Sub`；ATC 对该静态图成功返回，因而本次不存在“某个算子不支持导致 OM 失败”的结论。

基础 OM 的 1,182,720 个输出中只有 367 个超出默认容差，且全部位于 box 的第 2、3 个坐标
通道；类别置信度通道最大误差为 `8.14e-4`。按官方的精度调优机制，使用
`allow_mix_precision` 并对原始 ONNX 图末端的 23 个 DFL 解码/输出节点使用 `keep_dtype`，
保留主干、Detect 的 `cv2/cv3` 分支和类别支路的混合精度。此 OM 的 SHA256 为
`b7f6b5dd940ef906447a66f2f8ebc310b18236fd889ec955ddb59497d4077a80`；正式 50 次预热、
300 次测量的 `allclose` 通过，最大绝对误差 `3.8271` 对应参考坐标约 `408.29`，低于
`0.001 + 0.01*abs(reference)` 的允许误差 `4.0839`。

以下命令只能在 310B 板端执行，重建已验证的局部精度策略：

```bash
python -m time_frequency_dashboard.model.generate_yolo_keep_dtype \
  --onnx models/generated/inference/candidates/torchsig_yolo11_b1.onnx \
  --output models/generated/inference/candidates/yolo11_head_keep_dtype.cfg
python -m time_frequency_dashboard.model.compile_inference_candidate \
  --onnx models/generated/inference/candidates/torchsig_yolo11_b1.onnx \
  --input-shape 1,3,1024,1024 --input-name images \
  --output-prefix models/generated/inference/candidates/torchsig_yolo11_b1_mix_headkeep \
  --precision-mode allow_mix_precision \
  --keep-dtype models/generated/inference/candidates/yolo11_head_keep_dtype.cfg \
  --check-report data/model_admission/torchsig_yolo11_b1_mix_headkeep/atc_check.json
```

该方案没有使用 `force_fp32`。官方 CANN 文档说明默认 ATC 精度模式为 `force_fp16`，
并允许 `allow_mix_precision` 与 `keep_dtype` 一起只保留个别原图节点的精度；本实验采用
的正是该局部策略。[CANN 8.0 `precision_mode` 文档](https://www.hiascend.com/document/detail/en/canncommercial/800/devaids/atc/atlasatcparam_16_0068.html)
和 [CANN 8.0 `keep_dtype` 文档](https://www.hiascend.com/document/detail/en/canncommercial/800/devaids/atc/atlasatcparam_16_0074.html)
给出了参数的适用范围与配置格式。YOLO 导出元数据报告 Ultralytics AGPL-3.0，重新分发前
仍需单独完成许可证审查。

## 5. CVNET-rf 与 SignalIQ 的结论

CVNET-rf 使用固定 revision `df32f6cd9bb033835465928307610bba1c376708`。RealCNN 和
ComplexCNN 都安全读取了官方 checkpoint（SHA256 分别为
`8a6861dec969d01bcf0198ef2e0fc52c0938c6dacf613ca8b1078eba449ff53c`、
`30447995f7fc2931fc24cea415a78301bab1353bdb33dd017a73279a1c582aa1`），但官方 `inference.py` 的模型键与 checkpoint
键不匹配：RealCNN 期望旧的 `net.*`，实际权重是 `stem/block1..4/fc1/fc2`；ComplexCNN
的 `conv*` 期望也与复数权重、复数 BN 和 modReLU 键不匹配。因此本阶段记录为“源代码/权重
合同阻断”，没有重写网络来掩盖失败，也没有宣称 24 类识别可用。

`alirezaaminzadeh/radio-modulation-classifier` 只公开了 state_dict，未公开与之对应的
forward/pooling 源码。临时重建 CLDNN 仅用于 ONNX/ATC 算子可行性检查，不能证明原模型
准确率；它因此保持 rejected。二者都没有进入 `rtl_sdr_npu_inference` 默认选择。

## 6. RTL-SDR 实时 NPU 链路

### Dashboard integration scope

The PySide6 dashboard uses this same RTL-SDR service as a separate SDR
workspace. It intentionally keeps CPU and NPU roles visible: CU8 decoding,
DC removal, queueing, and FFTW Blackman time-frequency construction remain on
the ARM CPU; an accepted fixed-shape OM runs the raw-IQ classifier or spectrum
detector on the NPU. A time-frequency preview must therefore be labelled as
CPU FFTW model input, not NPU FFT.

Only `accepted` raw-IQ manifests whose hashes and live checks pass are offered
by the UI. Hantek and RTL-SDR are mutually exclusive and a page change does not
open hardware. These are application safety contracts. The local development
machine can unit-test them, but it cannot validate ACL, OM, CANN, or real
RTL-SDR behaviour; those claims require an Ascend 310B board run.

共享入口为 `python -m time_frequency_dashboard.rtl_sdr_npu_inference`，其板端封装为
`bash scripts/run_rtl_sdr_npu_inference.sh`，支持 `rtl`、`cu8` 和 `synthetic`。PySide6 SDR
工作区通过同一个 `RtlSdrService` 运行这条路径。CPU 负责 CU8 解码、raw-IQ 分类模型的逐窗口去直流/归一化、与
`gr-spectrumdetect` 一致的 FFTW Blackman 频谱图、Top-K 和 NMS；
NPU 只负责已准入 OM。队列有界，满载时丢弃最旧批次，并在 JSONL 中记录采集序号、IQ 偏移、
模型版本、后端、置信度、推理/端到端延迟和丢批数。`end_to_end_ms` 从当前批次的主机读取/生成
开始，到输出校验和 Top-K/NMS 完成；`post_capture_pipeline_ms` 从主机收齐该批 IQ 到同一终点。
它们分列记录采集、RTL 原始 CU8 归档写入、解码、队列等待、预处理、主机到主机的 OM 推理边界
（Tensor/H2D/OM/D2H/输出复制）和后处理（输出校验 + Top-K/NMS）；不包含模型初始化或 JSONL
行的序列化/落盘。它不是 RF/ADC 首样本到结果的时延，因为 RTL-SDR 的 FIFO、USB、驱动缓冲和
设备侧时间戳不可观测。没有 OM、输入形状不符、NPU 失败或没有
“accepted + NPU-window-budget + live_demo_eligible” manifest 时立即报错，绝不静默 CPU fallback。

对于频谱检测，v3 manifest 使用结构化、版本化的 `input.preprocessing` 合同，不再依赖描述文字。
合同精确锁定 CU8 I/Q 解码（offset/scale=127.5）、不去直流、1024 点无重叠周期 Blackman、
FFTW forward、功率、每图峰值归一化、fftshift+垂直翻转、dB min-max 黑热图和 RGB 复制；任一
字段不同都会在加载阶段拒绝。`npu_p95_ms` 只证明主机到主机的 OM 推理边界满足输入窗口预算；短时流水线窗口检查
必须来自固定采样率下至少两批的运行，要求 producer/completed 数量相等、零丢批，且
`post_capture_pipeline_max_ms` 不超过窗口预算，由 `run_summary.pipeline_realtime` 给出。
附着工具会另行计算连续管线结论：必须声明 `antenna_connected` 或 `lab_cabled`，观测至少 600 秒，
并满足上述完整交付和窗口预算条件；两批短运行只能通过短时检查，不能通过连续验收。

`torchsig_yolo11_b1_mix_headkeep.accepted_v3.manifest.json` 是当前唯一通过的第三方模型，默认
选择规则仍只按已准入的 NPU 相对 CPU 收益、NPU P95 和路径排序。该 v3 manifest 由板端升级工具
补齐上游权重 SHA256、采样约定、结构化预处理合同、NPU 窗口预算字段和 CANN `version.info` 后，
才可进入严格实时入口。v2 曾通过一批真实 OM 合成 IQ 严格加载，但不含 v3 的可执行预处理合同，
不能作为当前运行入口。

2026-08-12 的 v3 实测先对已有 2.0 MiB CU8 录制回放一批：后采集流水线为 286.926 ms，小于
512 ms 窗口，但因只有一批，`pipeline_realtime` 按规则保持 false。随后在尚未接天线/射频线的
状态下，RTL-SDR Blog V4 枚举为 device 0，并在 100 MHz、2.048 MS/s、PPM 0 下连续采集三批。
这只验证 USB 接收机采集、CPU 预处理和 OM 推理的链路 smoke，不能当作空口信号、检测准确率或
最终 RTL-SDR 业务实时性验收。该次 3/3 完成、零丢批，后采集流水线 P50/P95/max 为
261.653/282.670/285.006 ms，低于 512 ms 窗口；`pipeline_realtime` 为 true。最后把 JSONL
绑定到新的 sibling v4 manifest，并以 `--verify-attached` 重新打开源 v3、JSONL 和 CU8 后复算通过。
报告 SHA256 为 `4e07dc7a65bd774120c6ae73e59cce5f71412ecbbb5061bf3fa0595952ca3bf9`，CU8 SHA256 为
`12cf56210cae45c66f3bb18fed253f16a33e91fda6410b8dc527e0bd915a9f5c`，v4 manifest SHA256 为
`c55588a689d8754ae9dd68d734b9e0e3162b4a82358e73c91ee62ccc1286cba8`。第三批的
`capture_acquisition_ms` 为 482.161 ms，不能当作精确射频采样时钟，因为它受 RTL-SDR 主机缓冲影响；
主机到主机 OM 推理边界为 140.821 ms。`structurally_validated_self_report` 只防止工件、固定窗口、
报告和捕获文件的意外不一致；没有外部签名或硬件远程证明，不能被表述为防篡改的硬件执行证明，也不
作为模型/NPU 准入或自动选择门槛，更不替代十分钟整条、已接天线的 RTL-SDR 主机流水线验收。
实时入口会将操作员声明的 `--rf-input-context` 写入 JSONL：未接天线使用 `disconnected`，已接天线
使用 `antenna_connected`，实验室受控线缆使用 `lab_cabled`。这是可审计的运行上下文，不是自动化的
天线、信号存在或标签准确性验证。

运行结束后，`python -m time_frequency_dashboard.rtl_sdr_run_report --inference-jsonl
<run>/inference.jsonl --output <run>/qc_summary.json` 会在不调用 RTL-SDR、FFTW 或 NPU 的条件下，
重新流式读取该 JSONL 所绑定的 CU8，验证 footer 的字节数和 SHA256、metadata/每批/footer 的
`NPU (Ascend 310B)` 后端记录，并统计 I/Q DC、端点削顶率、逐项 P50/P95/max 主机时延和检测标签
计数。它明确标记为“字节级采集质量和已记录主机时延”，不验证接收幅度校准、天线性能、空口标签或
识别准确率。不同增益下的独立 CU8 只能用 `--capture-only` 单独比较，工具禁止把另一份捕获文件
混入一个已绑定的 JSONL 时延报告，也不允许覆盖 JSONL、CU8 或已有 QC 报告。

### 2026-08-12 已接天线的连续验收与接收质量

在 `ascend8t`（Ascend 310B4、CANN toolkit 8.0.0/package 7.6.0.1.220）上，RTL-SDR Blog V4
（R828D，SN `00000001`）先通过 `rtl_test -t` 枚举，驱动报告 29 档可用增益。`rtl_power` 以
88--108 MHz、0 dB、约 78.125 kHz bin 的单次扫描也成功完成，说明设备、天线和 FM 频段能量接收
路径可用；它不是频率或幅度校准，也不代表模型识别正确。

第一轮自动增益、100 MHz、2.048 MS/s、`antenna_connected` 的 600.076 s 运行完成 1,170/1,170
个 512 ms 输入批次，零丢批，后采集 P50/P95/max 为 252.825/256.210/289.436 ms。它的 CU8 I/Q
端点占比约为 15.02%，因此实时性结论有效，但自动增益不作为本环境的推荐接收设置。为排查而做的
1 秒独立 CU8 对照显示：请求 40 dB（驱动量化到 40.2 dB）无端点削顶、I/Q 标准差约 21.3；请求
45 dB（43.9 dB）已约 0.35% 削顶。该对照仅用于选择增益，不能与别轮 JSONL 的处理时延混合。

最终验收使用固定请求增益 40.2 dB、中心频率 100 MHz、采样率 2.048 MS/s、PPM 0、
`--rf-input-context antenna_connected`、队列容量 4 连续运行 600.145 s。它完成 1,170/1,170 批次、
零丢批；后采集流水线 P50/P95/max 为 **252.440/255.858/282.768 ms**，均小于 512 ms 固定窗口，
因此短时窗口检查和“已接天线、至少十分钟”的连续管线规则均为 true。逐批 NPU 边界
（Tensor/H2D/OM/D2H/输出复制）P50/P95/max 为 **139.425/139.881/141.935 ms**；FFTW 频谱图预处理
P50/P95 为 76.404/79.410 ms。完整采集为 2,453,667,840 bytes（1,226,833,920 个复样本），I/Q
端点比例分别为 `5.22e-8`/`5.87e-8`，DC 偏置约 -0.130 CU8，表明该次记录没有自动增益轮的明显
字节级过载。

这次报告的 SHA256 是 `dd2cb2d79db628e63792054446ee1593400ded07829167e4bd7bc069ff18832d`，CU8
SHA256 是 `f4a719c5588c88ecd0f3ec81d3ae4f353c6b00eec5482ce45964d26277b50d9e`；对应 v4 证据清单为
`torchsig_yolo11_b1_mix_headkeep.accepted_v4.antenna-10m-gain40.manifest.json`（SHA256
`fed877099f550bf165595ccac63e607b5a555d67ef187b9f54a0103af6b58427`）。`--verify-attached` 重新读取
源 v3、ONNX/OM、JSONL 和 CU8 后通过；严格汇总也确认 1,170 条核心 NPU/后采集时延字段均未缺失；
在代码加严后，板端再次执行全量 pytest 为 **113 passed、1 skipped**，并通过
`python -m compileall -q time_frequency_dashboard`。新的严格 QC 从同一份 10 分钟 CU8 流式重算后，
再次确认 1,170 条批记录的 metadata/每批/footer 后端都是 `NPU (Ascend 310B)`、核心 NPU 与后采集
时延字段均完整，且字节数和 SHA256 不变。运行期间
`npu-smi` 温度观测为 82--83 C，前后均显示 `Health=Alarm`；ACL/OM 未报错，但本实验不诊断或清除
该硬件健康告警，也不报告功耗。

这证明的是当前板卡、当前接线/天线环境和这组固定参数下，真实 RTL-SDR 到 NPU 的主机流水线能持续
满足窗口预算。`end_to_end_ms` P50/P95/max 为 738.105/742.299/1424.696 ms，包含受主机/USB 缓冲影响的
采集部分，不能当作 RF/ADC 首样本到结果的设备侧时延。YOLO 输出共记录 9,264 个 `bpsk` 检测框，
但这些真实 IQ 没有地面真值，故不能从此推断空口调制类别、检测率或识别准确率。

2026-08-11 的旧版演示记录了合成 IQ 的 FFTW Blackman 频谱图预处理 97.817 ms、OM
136.927 ms，以及真实 RTL-SDR Blog V4（100 MHz、2.048 MS/s）的一批 2.0 MiB CU8 的
CU8 解码 32.258 ms、FFTW 96.781 ms、OM 137.677 ms、无丢批。这些记录证明采集和推理
链路曾连通，但其旧 `end_to_end_ms` 定义早于当前严格计时实现，不能作为当前处理端到端延迟
或实时预算的精确引用。新版实测从主机开始读取一个完整批次到 Top-K/NMS 完成，并另列 RTL
归档写盘时间；它仍不等同于 RF/ADC 首样本到结果的总时延，因为 USB/FIFO/驱动缓冲没有可观测
的设备侧时间戳。NMS 输出的检测框只证明模型、预处理和真实采集链路连通；未标注 IQ 不用于
宣称调制识别准确率。

XCiT 与 CLDNN 仍然 rejected；已有的固定 DFT
`time_frequency_dashboard.rtl_sdr_npu_demo` / `scripts/run_rtl_sdr_npu_demo.sh` 仍是更小批量
频谱计算的独立 NPU 教学链路。它不被 `RtlSdrService` 或 PySide6 SDR 工作区调用，不能作为
当前实时分类/检测路径的性能或功能说明。

运行时稳定性单独记录：当前推荐 YOLO11 batch=1 OM 使用 v3 清单连续运行 600.061 s、
4,180 次，P50/P95/max 为 141.233/141.551/144.048 ms，输出无 NaN/Inf。`npu-smi` 温度从
80 C 升至 83 C；测试后空闲读数为 77 C。工具在测试前后仍报告 `Health=Alarm`，但 ACL/OM
没有返回错误。这只能证明本轮持续推理未中断，不构成对该硬件健康告警的诊断或清除。历史的
XCiT 600.379 s 记录保留为被拒绝候选的稳定性参考，不能替代默认 YOLO11 的验收。

## 7. 选择规则

按以下顺序决定实现位置：

1. 先以最佳 CPU 基线（通常是 VOLK NEON 或 FFTW）测 P50/P95，并计算输入窗口的实时预算。
2. 再测包含 H2D、OM、D2H 和结果复制的 NPU 端到端时间，检查有限值、误差和持续运行稳定性。
3. 只有数值通过、P95 不积压，且相对最佳 CPU 至少 1.2 倍吞吐或能显著释放 CPU，才标记
   “推荐”；接近 1 倍标记“条件适用”。

通常的默认分工是：

- CPU/VOLK/FFTW：采集、CU8 解码、去直流、重采样、轻量滤波、FFT、窗口和归约小批次；
- NPU：固定形状、大 batch 的 Conv/MatMul/神经网络，尤其是 CPU 基线明显受限时；
- 不适合直接搬到 NPU：每个样本一次调用的逐点运算、频繁变形状、低算术强度和需要立即
  标量反馈的环路。

不要测试或推断功耗；本章只记录性能、数值、丢批和温度。功耗结论必须另接外部功率计。
