# 昇腾310B chapter2–9 双板运行对照记录（2026-10-17）

本记录只比较本次在两块真实开发板上、使用同一份样例源码和同一套测试命令得到的结果。所有时间都是墙钟时间，不是纯 NPU 算子时间；8T 与 20T 是两个独立证据集，不能合并成一张性能排名表。

## 1. 结论

- 本次可执行的章节样例中，chapter3、chapter5、chapter8、chapter9 出现了可测量的双板差异。
- **不能得出“20T 全面快于 8T”的结论。** 本次 chapter3 NPU 训练和 chapter5 MindSpore 子集反而在 20T 上更慢；chapter8 的 ResNet18 NPU 基线则在 20T 上更快。
- chapter6 的 VENC 在两块板上都以 `507018` 失败，属于同一路径失败，不是板卡差异。
- chapter2 在补充华为云 ResNet50 ONNX 后，两板均完成 ATC 和 `msame` 推理；chapter4 的完整推理仍缺 ResNet18/SSD/SSDLite 资产。
- chapter7 的三个脚本完成了 TBE DSL 层面验证；日志明确说明 Ascend C 自定义内核还需要 TBE 算子注册/编译框架，不能把脚本打印 PASSED 写成已完成硬件算子编译。

## 2. 范围与环境

### 2.1 章节范围

`samples/` 中没有 `chapter1` 目录；本次按以下九行记录：

| 章节 | 实际入口 | 本次状态 |
| --- | --- | --- |
| chapter1 | 无样例 | `N/A` |
| chapter2 | 华为云 ONNX 下载、ATC、`msame` 推理 | 通过，真实推理已完成 |
| chapter3 | `chapter3/linear_regression/` | NPU 训练与 CPU 基线可运行 |
| chapter4 | `chapter4/check_ascend_device/` | ACL 设备检查可运行；完整推理缺资产 |
| chapter5 | 合成数据 MindSpore 样例集 | 环境、数据集、图模式、训练子集可运行 |
| chapter6 | `check_cann`、VPC、VENC | VPC 通过；VENC 失败 |
| chapter7 | TBE、Ascend C、Tiling 脚本 | DSL 验证通过 |
| chapter8 | ResNet18 性能脚本 | CPU、Queue、NPU 基线有结果 |
| chapter9 | FP32/FP16 OM 对比 | 下载数据后对比与性能均通过 |

测试源码来自本地教材检出（HEAD `968ea9d`）；部署目录为每块板的 `/home/HwHiAiUser/Ascend310/samples`。原始日志按板卡和批次保存在本文件同级的 [`chapter-board-runs-20261017/README.md`](chapter-board-runs-20261017/README.md)。

### 2.2 板端环境

| 项目 | 8T 板 | 20T 板 |
| --- | --- | --- |
| SoC | `Ascend310B4` | `Ascend310B1` |
| 计算档位 | 8T | 20T |
| 系统 | Ubuntu 22.04.3 LTS，aarch64 | Ubuntu 22.04.3 LTS，aarch64 |
| 内核 | 5.10.0+ | 5.10.0+ |
| CANN | 8.0.0 | 8.0.0 |
| 驱动 | 25.2.0 | 25.2.0 |
| `npu-smi` | 25.2.0 | 25.2.0 |
| Python | 3.9.2，`/usr/local/miniconda3/bin/python` | 3.9.2，`/usr/local/miniconda3/bin/python` |
| 设备健康 | Alarm，记录为已知板况 | Alarm，记录为已知板况 |
| 探测时温度 | 78 C | 70 C |
| 探测时 NPU 内存 | 2064 / 15610 MB | 4743 / 23673 MB |

版本判定均为 `observed-match / observed-pass`。Alarm 只作为诊断背景，未单独作为测试失败原因。

## 3. 双板结果

时间单位均为毫秒，来自测试脚本的墙钟计时。`N/A` 表示没有可比较的完整执行结果，不表示硬件不支持。

| 章节/测试 | 8T 结果 | 8T 时间 | 20T 结果 | 20T 时间 | 20T 相对 8T |
| --- | --- | ---: | --- | ---: | ---: |
| chapter1 | 无样例 | N/A | 无样例 | N/A | N/A |
| chapter2 Huawei ONNX 下载 | 通过 | 22,251 | 通过 | 9,156 | 不可直接比较（首次缓存/链路差异） |
| chapter2 ATC 转换 | 通过 | 154,826 | 通过 | 208,820 | 慢 34.9% |
| chapter2 `msame` 推理 | 通过 | 5,181 | 通过 | 5,348 | 慢 3.2% |
| chapter2 `msame` Inference time | 13.232 ms | — | 11.748 ms | — | 20T 快 11.2% |
| chapter3 NPU 50 epochs | 通过 | 56,908 | 通过 | 78,532 | 慢 38.0% |
| chapter3 CPU 基线 | 通过 | 11,784 | 通过 | 13,166 | 慢 11.7% |
| chapter4 ACL 设备检查 | 通过 | 1,595 | 通过 | 1,581 | 快 0.9% |
| chapter5 环境检查 | 通过 | 61,724 | 通过 | 62,837 | 慢 1.8% |
| chapter5 选定子集（03/04/05/06） | 全部通过 | 258,832 | 全部通过 | 344,313 | 慢 33.0% |
| chapter6 CANN/DVPP 检查 | 通过 | 4,857 | 通过 | 5,306 | 慢 9.2% |
| chapter6 VPC | 通过 | 5,001 | 通过 | 5,249 | 慢 5.0% |
| chapter6 VENC | 失败 `507018` | 5,121 | 失败 `507018` | 5,398 | 相同失败 |
| chapter7 TBE | 通过 | 5,646 | 通过 | 5,843 | 慢 3.5% |
| chapter7 Ascend C DSL | 通过 | 5,582 | 通过 | 5,753 | 慢 3.1% |
| chapter7 Tiling | 通过 | 5,569 | 通过 | 5,920 | 慢 6.3% |
| chapter8 CPU 预处理 | 通过 | 1,161 | 通过 | 1,450 | 慢 24.9% |
| chapter8 Queue 模拟 | 通过 | 1,088 | 通过 | 1,119 | 慢 2.9% |
| chapter8 NPU 基线 | 通过 | 5,052 | 通过 | 5,289 | 脚本墙钟慢 4.7% |
| chapter9 数据下载 | 通过 | 93,156 | 通过 | 105,317 | 慢 13.1% |
| chapter9 FP32/FP16 对比+性能（任务工具） | 400 样本通过 | 16,850 | 400 样本通过 | 16,861 | 慢 0.1% |

## 4. 逐章记录

### chapter2：ResNet quick start

- 目录：`chapter2/sample_resnet_quick_start/`。
- 使用插件的 `ascend_model_download` 从华为云 OBS 下载 ResNet50 ONNX；8T 用 `Ascend310B4`，20T 用 `Ascend310B1`，两板 ATC 均返回成功。
- `msame` 在两板均完成一次真实推理，退出码为 0；推理报告：

```text
8T  Inference time: 13.232 ms
20T Inference time: 11.748 ms
```

- 这证明 chapter2 的下载、ATC 和推理链路均可执行；模型文件和 OM 保留在板端运行目录，不进入 Git。
- 证据：8T/20T 的 ATC/msame 原始结果见 [`tool-matrix.json`](chapter-board-runs-20261017/tool-execution/tool-matrix.json)；输入图片和 bin 仍为样例自带资产。

### chapter3：PyTorch + torch_npu

- 8T 完成 50 epochs，最终输出 `R=118.63301849365234`、`b=0.7558678388595581`，本次任务工具墙钟 56,908 ms。
- 20T 在 120 秒预算内只到 32/50 epochs，延长后完成，最终输出同一组 `R`、`b`，本次任务工具墙钟 78,532 ms。
- 说明：训练包含预热、编译和板卡温度背景；该差异只能作为本次墙钟观察，不是纯算子基准。
- 证据：8T [`chapter3_npu_long.log`](chapter-board-runs-20261017/8t/run2/chapter3_npu_long.log)，20T [`chapter3_npu_long.log`](chapter-board-runs-20261017/20t/run2/chapter3_npu_long.log)。

### chapter4：PyACL

- `check_ascend_device.py` 在两块板均输出 `Found 1 Ascend devices.`。
- ResNet18、SSD、SSDLite 的完整推理未执行：本次最小部署只复制了自包含的设备检查目录，完整模型和数据集不在板端运行集中。
- 证据：8T [`chapter4_check.log`](chapter-board-runs-20261017/8t/run1/chapter4_check.log)，20T [`chapter4_check.log`](chapter-board-runs-20261017/20t/run1/chapter4_check.log)。

### chapter5：MindSpore

- 环境检查在两块板均通过：MindSpore 2.4.10，设备加法 `ascend add fp32: [2.0, 4.0]`；MindSpore Lite 导入因符号不匹配失败，属于运行时配套问题。
- 选定子集 `03_dataset_pipeline`、`04_graph_mode`（两种模式）、`05_autograd_train`、`06_checkpoint_mindir` 在两块板均通过。
- 完整 `run_board_examples.py` 不是干净的对比：8T 以 570,426 ms、exit 1 结束（`07_lite_inference` 为 `blocked`），20T 在 600,023 ms 超时；`01_tensor_ops` 和 `02_cell_network` 在不同板上的 120 秒预算内均出现超时，不能把整轮退出码当作板卡性能。
- 证据：8T [`chapter5_board.log`](chapter-board-runs-20261017/8t/run1/chapter5_board.log)、[`chapter5_subset.log`](chapter-board-runs-20261017/8t/run2/chapter5_subset.log)；20T [`chapter5_board.log`](chapter-board-runs-20261017/20t/run1/chapter5_board.log)、[`chapter5_subset.log`](chapter-board-runs-20261017/20t/run2/chapter5_subset.log)。

### chapter6：DVPP

- `check_cann.py` 在两块板均通过，VENC API 计数为 38。
- VPC 最小样例在两块板均通过。
- VENC 最小样例在两块板均报：

```text
venc_create_channel failed: 507018 (0x7bc8a)
alloc encoder node buffer failed
```

- 这是同一 API 路径的可复现失败，不能归因于 20T/8T 算力差异。
- 证据：8T [`chapter6_venc.log`](chapter-board-runs-20261017/8t/run1/chapter6_venc.log)、[`chapter6_vpc.log`](chapter-board-runs-20261017/8t/run1/chapter6_vpc.log)；20T 对应 `20t/run1/` 同名文件。

### chapter7：自定义算子

- `add_tbe/run.py`、`add_ascendc/run.py`、`mat_add_tiling/run.py` 在两块板均通过 TBE DSL 验证。
- 日志明确写出 Ascend C kernel source 还需要 TBE 算子注册/编译框架；本次没有执行 `msopgen` 或安装自定义算子包，因此不能把它记为“Ascend C 算子已在 NPU 上编译并执行”。
- 证据：8T [`chapter7_tbe.log`](chapter-board-runs-20261017/8t/run1/chapter7_tbe.log)、[`chapter7_ascendc.log`](chapter-board-runs-20261017/8t/run1/chapter7_ascendc.log)、[`chapter7_tiling.log`](chapter-board-runs-20261017/8t/run1/chapter7_tiling.log)；20T 对应 `20t/run1/` 同名文件。

### chapter8：性能优化

- CPU 工作区复用加速：8T `2.034x`，20T `1.994x`。
- Queue 模拟加速：8T `1.818x`，20T `1.815x`。
- ResNet18 NPU 基线 10 次：

| 指标 | 8T | 20T | 变化 |
| --- | ---: | ---: | ---: |
| Execute mean | 4.9787 ms | 3.7255 ms | 20T 低 25.2% |
| End-to-end mean | 6.0424 ms | 4.9569 ms | 20T 低 18.0% |
| Execute P95 | 4.9868 ms | 3.8607 ms | 20T 低 22.6% |
| End-to-end P95 | 6.0720 ms | 5.1078 ms | 20T 低 15.9% |

- 这是本次 10 次运行的结果，不是完整性能基准；不能外推到其他模型、精度或系统负载。
- 证据：8T [`chapter8_baseline.log`](chapter-board-runs-20261017/8t/run1/chapter8_baseline.log)；20T [`chapter8_baseline.log`](chapter-board-runs-20261017/20t/run1/chapter8_baseline.log)。

### chapter9：量化与精度

- 首次执行失败是因为 Tiny-ImageNet PNG 尚未下载；补充 `download_tiny_imagenet.py train --per-class 2` 后，两块板均完成 FP32/FP16 对比。
- 400 个样本的 top1 match rate 均为 `100.00%`，mean top5 overlap 均为 `4.9975`，max abs diff 均为 `0.008928298950195312`。
- 通过新 `ascend_board_run` 执行“对比 + 性能”两步，8T 用时 16,850 ms，20T 用时 16,861 ms；本次 10 次运行的 Execute mean：

| 精度 | 8T | 20T |
| --- | ---: | ---: |
| FP32 | 6.8647 ms | 5.4605 ms |
| FP16 | 6.2755 ms | 6.0932 ms |

- 样本数只有 400，性能只跑 10 次；这是连通性和双板差异记录，不是最终精度/性能结论。
- 证据：8T [`chapter9_compare.log`](chapter-board-runs-20261017/8t/chapter9/chapter9_compare.log)、[`chapter9_perf.log`](chapter-board-runs-20261017/8t/chapter9/chapter9_perf.log)；20T 对应 `20t/chapter9/` 同名文件；新工具执行记录见 [`chapter9-task-tool.txt`](chapter-board-runs-20261017/tool-execution/chapter9-task-tool.txt)。

## 5. 差异结论

1. **训练/框架路径**：chapter3 NPU 和 chapter5 MindSpore 子集在本次 20T 上都比 8T 慢，说明算力档位不能直接推出所有工作负载更快。训练脚本包含预热、编译、数据管线和板端后台负载，必须按相同协议重测才能解释。
2. **推理路径**：chapter8 ResNet18 和 chapter9 模型执行在 20T 上更快，尤其 chapter8 的 Execute mean 低约 25%；该结果支持“该模型/该精度路径在 20T 上有优势”，不支持泛化为所有 chapter。
3. **相同失败**：chapter5 的 MindSpore Lite 版本边界、chapter6 的 VENC `507018` 在两板相同；这些是环境/接口路径失败，不是 8T 与 20T 的性能差异。
4. **资产边界**：chapter2 已通过华为云下载和 ATC 补齐；chapter4 的完整 ResNet18/SSD/SSDLite 资产仍未纳入本次最小部署，因此只完成了 ACL 设备检查。

## 6. 复现与证据

测试脚本按每块板写入 `~/Ascend310/chapter-runs/`，每个测试保存独立 `.log` 和 `summary.tsv`。本次证据的原始目录：

- 8T：`chapter-board-runs-20261017/8t/run1/`、`run2/`、`chapter9/`
- 20T：`chapter-board-runs-20261017/20t/run1/`、`run2/`、`chapter9/`
- 新工具重跑：`chapter-board-runs-20261017/tool-execution/full-matrix.txt`、`tool-matrix.json`、`chapter9-task-tool.txt`、`tool-smoke2.json`

`full-matrix.txt` 的全量 harness 在 chapter9 两行失败，因为该旧 harness 没有把 chapter8 helper 加入 `PYTHONPATH`；修正后的 chapter9 对比/性能通过 `chapter9-task-tool.txt` 单独执行并通过。chapter2 的完整 ATC/`msame` 结果见 `tool-matrix.json`。

板端环境快照见各批次 `run1/meta.txt`；测试命令和结果见 `summary.tsv`。所有未完成的章节都保留为 `N/A`、`blocked` 或 `untested`，没有把辅助命令的通过写成完整程序通过。
