# Ascend 板端实测结果

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

本页集中保存 8T、8T2 和 20T 上的模型转换、精度、速度及跨 SoC 兼容性实测记录。

## Ascend 8T 混合精度实测

在 Orange Pi AI Pro 8T 上使用以下环境重新转换同一个 `Violin.onnx`：

| 项目 | 实测值 |
| :--- | :--- |
| 芯片 | Ascend `310B4` |
| NPU 驱动 | `23.0.0` |
| CANN | `8.3.RC1`，内部版本 `8.3.0.1.200` |
| Python | Anaconda `base`，Python 3.9.2 |
| ONNX SHA256 | `82d6191868d36f967e8739887edba8e911e2bba6e09a63b514c5f3b8380996a5` |
| 基准输入 | 1024 帧，随机种子 `20260721` |

该板的 `npu-smi info` 显示 `Health: Alarm`，但设备可见，两个 OM 都能通过
`ais_bench` 完成 1024 帧推理，因此本次测试没有把该状态单独判定为转换或推理
失败。完整环境记录保存在 `reports/ascend8t/environment.txt`。

板端只有约 7.4 GiB 内存且没有 swap。ATC 使用默认并发和
`TE_PARALLEL_COMPILER=4` 时均被内核 OOM killer 终止，退出码为 137；内核日志
明确记录被杀死的进程为 `atc.bin`。将 TBE 编译限制为单进程后转换成功：

```bash
cd ~/Documents/case3

TE_PARALLEL_COMPILER=1 OMP_NUM_THREADS=1 \
  bash tools/convert_onnx_to_om.sh \
  --model models/ddsp_vst/Violin.onnx \
  --output models/om/ascend8t/Violin_force_fp16 \
  --soc-version Ascend310B4

TE_PARALLEL_COMPILER=1 OMP_NUM_THREADS=1 \
  bash tools/convert_onnx_to_om.sh \
  --model models/ddsp_vst/Violin.onnx \
  --output models/om/ascend8t/Violin_mixed_float16 \
  --soc-version Ascend310B4 \
  --precision-mode-v2 mixed_float16
```

两次成功转换的摘要都为 `ATC_EXIT_CODE=0`、`OM_UPDATED=yes`、
`OPERATOR_COMPATIBILITY=no incompatibility pattern found` 和 `ERROR_LINES=none`：

| OM | 精度模式 | 大小 | SHA256 |
| :--- | :--- | ---: | :--- |
| `Violin_force_fp16.om` | ATC 默认 `force_fp16` | 4,025,452 字节 | `f582481242426fd6c01b57b40509efcefe8ec0477dd09d63bceae2bdaa8dc21c` |
| `Violin_mixed_float16.om` | `precision_mode_v2=mixed_float16` | 4,041,013 字节 | `c5d634372d29ae659aa9dcd877a42d907c494196ecf673b21889edc1757b45b4` |

两个 OM 使用与 20T 测试相同的 ONNX Runtime FP32 基准。下表给出 NRMSE；数值
越低表示越接近 ONNX 输出：

| 输出 | FP16 Teacher-forced | 混合精度 Teacher-forced | FP16 Closed-loop | 混合精度 Closed-loop | 混合精度闭环余弦相似度 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 幅度 `amplitude` | 0.1988% | 0.0555% | 0.1803% | 0.0646% | 0.999999793 |
| 有效谐波幅度 `amplitude * harmonics` | 0.2228% | 0.0766% | 0.2326% | 0.0880% | 0.999999617 |
| 噪声系数 `noise_amps` | 0.3316% | 0.1722% | 0.3515% | 0.1916% | 0.999998164 |
| GRU 状态 `state_out` | 0.0925% | 0.0542% | 0.1840% | 0.0880% | 0.999999613 |

所有报告数值均为有限值，没有 NaN 或 Inf。FP16 与混合精度的闭环平均推理时间
分别为 0.782 ms 和 0.788 ms，本次混合精度约慢 0.75%，但四类关键输出的闭环
NRMSE 均低于 FP16。8T 上 FP16 的误差指标与此前 20T 实测完全一致，说明相同输入
下的 FP16 数值行为保持一致。

在 20T 的 CANN 8.0 环境中，`mixed_float16` 曾在 TBE 预编译阶段失败，旧版
`allow_mix_precision` 模式还出现过 ATC 崩溃，没有生成混合精度 OM。本次结果证明
`310B4 + CANN 8.3.RC1` 在限制编译并发后可以成功生成并运行该混合精度模型；由于
芯片型号和 CANN 版本同时变化，不能仅根据这次对比把改善归因于其中某一个因素。

完整产物位于：

```text
models/om/ascend8t/                    # OM、ATC 日志和兼容性摘要
reports/ascend8t/                      # 环境、OOM 证据、精度 JSON 和 SHA256 清单
```

## Ascend 8T 全部音色模型批量实测

`tools/run_all_ascend8t_models.sh` 在板端按阶段生成参考、转换模型、运行精度测试、
执行纯 NPU 基准并汇总结果。脚本默认启用断点续跑，只有源 ONNX 哈希、OM、ATC
摘要和转换参数都有效时才跳过已有结果：

```bash
cd ~/Documents/case3
bash tools/run_all_ascend8t_models.sh --phase all
```

也可以通过 `--phase references|convert|precision|benchmark|summary` 分阶段执行。
本次仍使用 `TE_PARALLEL_COMPILER=1`，11 个 ONNX 都采用 1024 帧、随机种子
`20260721` 的独立 FP32 参考。精度闭环重复 5 次；纯 NPU 基准明确设置
`--batchsize 1 --warmup_count 100 --loop 1000`。

本次 11 个音色的 FP16 和 `mixed_float16` 共 22 个 OM 全部转换成功，22 份摘要
均为 `ATC_EXIT_CODE=0`，没有算子不兼容或转换重试。FP16 OM 大小为
4,025,568 至 4,025,588 字节，混合精度 OM 为 4,041,043 至 4,041,063 字节；
混合精度文件中位增幅约 0.384%。

下表给出 closed-loop NRMSE，格式为“FP16 / 混合精度”：

| 模型 | 幅度 | 有效谐波幅度 | 噪声系数 | GRU 状态 |
| :--- | ---: | ---: | ---: | ---: |
| Bassoon | 0.1842% / 0.0668% | 0.2360% / 0.0908% | 0.3345% / 0.1778% | 0.1673% / 0.1045% |
| Clarinet | 0.1647% / 0.0636% | 0.2863% / 0.0935% | 0.3941% / 0.2046% | 0.2076% / 0.1015% |
| Flute | 0.2222% / 0.0730% | 0.2333% / 0.0851% | 0.4340% / 0.1997% | 0.1892% / 0.0985% |
| Melodica | 0.2215% / 0.0675% | 0.2088% / 0.0766% | 0.3600% / 0.1589% | 0.1587% / 0.0796% |
| Saxophone | 0.1943% / 0.0476% | 0.2169% / 0.0693% | 0.3197% / 0.1717% | 0.1759% / 0.0959% |
| Sitar | 0.1472% / 0.0555% | 1.0621% / 0.0639% | 0.3021% / 0.1588% | 0.1559% / 0.0854% |
| Trombone | 0.1543% / 0.0428% | 0.2015% / 0.0661% | 0.3386% / 0.1658% | 0.1578% / 0.0838% |
| Trumpet | 0.1690% / 0.0627% | 0.2369% / 0.0847% | 0.3486% / 0.1803% | 0.1864% / 0.0976% |
| Tuba | 0.4758% / 0.4670% | 0.5640% / 0.5291% | 0.6167% / 0.5863% | 0.4659% / 0.4388% |
| Violin | 0.1803% / 0.0646% | 0.2326% / 0.0880% | 0.3515% / 0.1916% | 0.1840% / 0.0880% |
| Vowels | 0.2216% / 0.0706% | 0.2662% / 0.0891% | 0.3369% / 0.1863% | 0.1864% / 0.1022% |

混合精度在 11 个模型的四类闭环输出上全部优于 FP16。跨模型 NRMSE 降幅中位数
分别为：幅度 64.17%、有效谐波 64.25%、噪声 47.43%、GRU 状态 46.94%。
Teacher-forced 单步测试同样在所有模型和所有输出上更准确，其四类降幅中位数依次
为 72.05%、70.82%、48.66% 和 37.77%。

Sitar 的 FP16 有效谐波闭环 NRMSE 为 1.0621%，混合精度降至 0.0639%，是本次
改善最明显的模型。Tuba 的 teacher-forced 误差也明显下降，但闭环降幅只有约
1.86% 至 6.18%，说明其长期误差主要由状态回灌后的误差累积决定，而不只是单次
计算精度。Tuba 混合精度四类闭环 NRMSE 仍均低于 0.6%。

速度结果如下。NPU 列来自 `ais_bench` 的 compute-time 中位数；闭环列是包含
Python 输入组织、输出复制和状态回灌的 5 次平均单帧延迟中位数。变化率为正表示
混合精度更慢：

| 模型 | FP16 NPU | 混合 NPU | NPU 变化 | FP16 闭环 | 混合闭环 | 闭环变化 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bassoon | 0.263 ms | 0.266 ms | +1.14% | 0.786 ms | 0.788 ms | +0.31% |
| Clarinet | 0.261 ms | 0.261 ms | 0.00% | 0.788 ms | 0.783 ms | -0.71% |
| Flute | 0.259 ms | 0.262 ms | +1.16% | 0.782 ms | 0.797 ms | +1.91% |
| Melodica | 0.262 ms | 0.266 ms | +1.53% | 0.776 ms | 0.793 ms | +2.18% |
| Saxophone | 0.266 ms | 0.259 ms | -2.63% | 0.783 ms | 0.788 ms | +0.63% |
| Sitar | 0.267 ms | 0.271 ms | +1.50% | 0.776 ms | 0.783 ms | +0.89% |
| Trombone | 0.261 ms | 0.261 ms | 0.00% | 0.789 ms | 0.792 ms | +0.37% |
| Trumpet | 0.261 ms | 0.261 ms | 0.00% | 0.772 ms | 0.780 ms | +1.01% |
| Tuba | 0.261 ms | 0.260 ms | -0.38% | 0.783 ms | 0.786 ms | +0.33% |
| Violin | 0.264 ms | 0.268 ms | +1.52% | 0.793 ms | 0.794 ms | +0.14% |
| Vowels | 0.270 ms | 0.260 ms | -3.70% | 0.778 ms | 0.793 ms | +2.00% |

纯 NPU 中位延迟变化的跨模型中位数为 0.00%，范围为 -3.70% 至 +1.53%；闭环
延迟变化中位数为 +0.63%，范围为 -0.71% 至 +2.18%。这些差异只有数微秒，接近
单次测试波动，不能据此认定混合精度有稳定的速度优势。综合结果是：对这 11 个
结构相同、权重不同的控制模型，`mixed_float16` 以约 0.384% 的文件增幅和基本
不变的推理速度，换取了一致且通常明显的精度改善。

批量结果位于：

```text
models/om/ascend8t/all_models/          # 22 个 OM、ATC 日志和源哈希
reports/ascend8t/all_models/            # 参考、精度、速度、环境和汇总
reports/ascend8t/all_models/summary.md  # 人工可读汇总
reports/ascend8t/all_models/summary.csv # 逐模型表格
reports/ascend8t/all_models/summary.json# 完整机器可读结果
```

## Ascend 8T2 驱动与 CANN 不匹配组合实测

在另一块 Orange Pi AI Pro 8T（SSH 名称 `ascend8t2`）上，使用同一份
`Violin.onnx` 测试了默认 FP16 和 `mixed_float16` 转换。该系统没有安装或升级
任何软件，实测环境如下：

| 项目 | 实测值 |
| :--- | :--- |
| 芯片 | Ascend `310B4` |
| NPU 驱动 / `npu-smi` | `25.2.0` |
| CANN 安装目录版本 | `8.0.0` |
| CANN 组件内部版本 | `7.6.0.1.220` |
| ATC | `/usr/local/Ascend/ascend-toolkit/latest/bin/atc` |
| Python | Anaconda `base`，Python 3.9.2 |
| ONNX SHA256 | `82d6191868d36f967e8739887edba8e911e2bba6e09a63b514c5f3b8380996a5` |

板端使用 `Ascend310B4`、`TE_PARALLEL_COMPILER=1` 和 `OMP_NUM_THREADS=1` 串行
执行测试：

```bash
cd ~/Documents/case3_ascend8t2_test
bash tools/run_ascend8t2_conversion_test.sh
```

FP16 使用 ATC 默认精度模式，混合精度显式使用
`--precision_mode_v2=mixed_float16`。两次转换结果均为 `ATC_EXIT_CODE=0`、
`OM_UPDATED=yes`、`OPERATOR_COMPATIBILITY=no incompatibility pattern found` 和
`ERROR_LINES=none`：

| OM | 大小 | SHA256 | `ais_bench` 加载 |
| :--- | ---: | :--- | :--- |
| `Violin_force_fp16.om` | 4,017,839 字节 | `88af5d25e97f4da36ef8424e18c684e1c351a4ba3ba17b0f3c28331383fef87a` | 成功 |
| `Violin_mixed_float16.om` | 4,032,854 字节 | `5f2f6864b393faf5e3d3b836eccfa8994a9e300574e070556adae5a0341fdda8` | 成功 |

`ais_bench` 对两份 OM 都完成了 ACL 初始化、打开设备、加载模型、一次预热和一次
推理，然后正常卸载模型。单次 NPU compute time 分别为 0.595 ms 和 0.548 ms；
该样本量只用于确认推理路径可运行，不能用于判断两种精度的速度差异。

本次结果与此前 20T 的 CANN 8.0 混合精度失败不同：在这块 `310B4` 上，驱动
`25.2.0` 与 CANN `8.0.0`（组件 `7.6.0.1.220`）虽然属于已知不匹配组合，但
`Violin.onnx` 的 FP16 和 `mixed_float16` 都可以完成 ATC 转换并启动 OM 推理。
这只能证明当前模型和命令路径可用，不能据此认定驱动与 CANN 已全面兼容；其他
模型、算子、长时间推理、精度和稳定性仍需分别测试。`npu-smi` 的
`Health: Alarm` 也被保留在环境记录中，没有因本次短推理成功而忽略。

完整产物位于：

```text
models/om/ascend8t2/          # 两份 OM、ATC 原始日志和兼容性摘要
reports/ascend8t2/            # 环境、dry-run、ais_bench 日志和 SHA256 清单
```

## Ascend 20T 同脚本重测结果

2026-07-21 在重装后的 `ascend20t` 上使用与 8T2 相同的脚本和同一份
`Violin.onnx` 重测。脚本通过环境变量切换目标和 SoC，不改变输入形状、精度模式或
日志判定逻辑：

```bash
cd ~/Documents/case3_ascend20t_retest
TARGET_ID=ascend20t_retest SOC_VERSION=Ascend310B1 \
  bash tools/run_ascend8t2_conversion_test.sh
```

本次预检确认芯片为 `310B1`，驱动为 `25.2.0`，CANN 安装目录版本为 `8.0.0`，
组件内部版本为 `7.6.0.1.220`。`npu-smi` 显示 `Health: Alarm`，板端有约
23 GiB 内存且没有 swap。ONNX SHA256 仍为
`82d6191868d36f967e8739887edba8e911e2bba6e09a63b514c5f3b8380996a5`。
ATC 使用 `TE_PARALLEL_COMPILER=1` 和 `OMP_NUM_THREADS=1`。

首次执行曾因 `conda activate base` 异常而在 ATC 前停止。重启开发板后，Anaconda
`base` 已能稳定激活，Python 路径为 `/usr/local/miniconda3/bin/python`，版本为
3.9.2；随后 FP16 和 `mixed_float16` 都实际进入了 ATC：

| 精度模式 | ATC 结果 | 失败阶段 | OM |
| :--- | :--- | :--- | :--- |
| 默认 FP16 | `E90000`，失败后进程未自行退出 | TBE/CannKB 初始化，forkserver `unexpected EOF` | 未生成 |
| `precision_mode_v2=mixed_float16` | `E90000`，失败后由 180 秒超时结束 | TBE/CannKB 初始化，forkserver `unexpected EOF` | 未生成 |

两个模式的堆栈都从 Python `multiprocessing.forkserver.read_signed()` 抛出
`EOFError('unexpected EOF')`，随后出现 `Failed to initialize TeConfigInfo`、
`Failed to initialize TeFusion` 和 `OpsManager initialize failed`。失败发生在 TBE
适配器初始化阶段，尚未进入具体算子预编译，因此不能归类为某个 ONNX 算子不受
支持，也不是混合精度独有错误。测试结束后确认没有遗留 `atc.bin` 或 Python
forkserver 进程。

本次重测结论是：当前 `310B1 + 驱动 25.2.0 + CANN 8.0.0` 环境既不能生成默认
FP16 OM，也不能生成 `mixed_float16` OM。此前 2026-07-20 的混合精度测试能够
继续到具体 TBE 算子预编译后失败；本次重启后的失败位置更早，说明该不匹配环境的
行为并不稳定。相同驱动和 CANN 组件在 `ascend8t2` 的 `310B4` 上两种模式都成功，
因此不能只根据版本号把差异归因于单一因素。

完整证据位于：

```text
models/om/ascend20t_retest/Violin_force_fp16.atc.log
models/om/ascend20t_retest/Violin_mixed_float16.atc.log
reports/ascend20t_retest/environment.txt
reports/ascend20t_retest/retest_summary.txt
reports/ascend20t_retest/SHA256SUMS.txt
```

## 8T 混合精度 OM 在 20T 上运行

为了验证在 `ascend8t` 上为 `Ascend310B4` 编译的混合精度 OM 是否能在
`ascend20t` 的 `Ascend310B1` 上运行，使用 `ais_bench` 做了分层对照：

1. 先运行 20T 本机已有的 B1 FP16 `Violin.om`，确认当前 ACL/NPU 运行链正常。
2. 运行 `ascend8t2` 使用 CANN 8.0.0 生成的 B4 FP16 和 B4
   `mixed_float16` Violin OM，隔离精度模式影响。
3. 运行原始 `ascend8t` 使用 CANN 8.3.RC1 生成的全部 11 个 B4
   `mixed_float16` OM。

20T 运行环境为驱动 `25.2.0`、CANN 目录版本 `8.0.0`、组件版本
`7.6.0.1.220`。全部模型同步后均核对 SHA256。每个完整模型测试使用 batch 1、
10 次预热和 100 次推理：

| 模型 | 结果 | NPU 中位时间 | P99 时间 |
| :--- | :--- | ---: | ---: |
| Bassoon | 成功 | 0.177 ms | 0.290 ms |
| Clarinet | 成功 | 0.170 ms | 0.229 ms |
| Flute | 成功 | 0.179 ms | 0.245 ms |
| Melodica | 成功 | 0.169 ms | 0.249 ms |
| Saxophone | 成功 | 0.166 ms | 0.212 ms |
| Sitar | 成功 | 0.177 ms | 0.295 ms |
| Trombone | 成功 | 0.173 ms | 0.295 ms |
| Trumpet | 成功 | 0.176 ms | 0.281 ms |
| Tuba | 成功 | 0.179 ms | 0.218 ms |
| Violin | 成功 | 0.171 ms | 0.280 ms |
| Vowels | 成功 | 0.169 ms | 0.208 ms |

11/11 个模型都完成 ACL 初始化、打开设备、加载模型、100 次推理和正常卸载，
运行日志中的错误行数量为 0。各模型中位时间的中位数为 0.173 ms，范围为
0.166 至 0.179 ms；这些时间只用于辅助证明模型确实执行，不作为与 8T 的正式
性能对比。

因此，就当前 11 个 DDSP-VST 模型而言，**8T/B4 上生成的混合精度 OM 可以在
20T/B1 上运行**。这与 20T 的 ATC 转换失败并不矛盾：当前 20T 在
TBE/CannKB 初始化阶段无法生成 OM，但 ACL 运行时仍可加载并执行已经编译好的
OM。该结论只覆盖这些实测模型和 B4 到 B1 的方向，不能推广为任意 B4 OM、任意
算子或未来 CANN 版本都兼容；本次也没有在 20T 上重新执行 ONNX 数值精度对比。

完整结果位于：

```text
reports/cross_soc/status.tsv             # 原生 B1、B4 FP16 和两种 B4 混合精度对照
reports/cross_soc/all_mixed/status.tsv   # 11 个混合精度模型结果和延迟
reports/cross_soc/all_mixed/             # 每个模型的 ais_bench 原始日志
reports/cross_soc/summary.md              # 汇总结论
```

## 20T 全模型 FP16 与混合精度对比

2026-07-21 将当前 `models/om/fp16` 和 `models/om/mixed_precision` 中的 22 个
OM 全部同步到正式模型目录：

```text
ascend20t:~/Documents/case3/models/om/fp16/
ascend20t:~/Documents/case3/models/om/mixed_precision/
```

独立测试副本和工具位于
`ascend20t:~/Documents/case3_ascend20t_all_models/`，避免运行报告污染正式模型目录。

这些 OM 均来自 `ascend8t` 的 `Ascend310B4 + CANN 8.3.RC1` 编译结果，本次只在
20T 上验证运行时精度和速度，**没有在 20T 上重新执行 ATC**。同步内容还包括 11
个对应 ONNX 和 11 个 ONNX FP32 基准；11 个 ONNX、22 个 OM 和 11 个基准共 44
个关键文件的本地/板端 SHA256 全部一致。

20T 环境为 `Ascend310B1`、驱动 `25.2.0`、CANN 目录版本 `8.0.0`、组件版本
`7.6.0.1.220`，测试程序运行在 `/usr/local/miniconda3` 的 Anaconda `base`
环境。ONNX FP32 基准使用 ONNX Runtime 1.20.1、1024 帧和 seed `20260721`；
重新生成的 Violin 基准与旧基准的控制输入、状态和全部输出逐元素完全一致。

测试口径如下：

- 精度：teacher-forced 和 closed-loop 各比较一次；closed-loop 另重复 5 次计时。
- 误差：记录 amplitude、有效谐波幅度 `amplitude * harmonics`、noise 和 GRU state
  的 NRMSE、余弦相似度、最大绝对误差和 P99 误差。
- 纯 NPU 速度：`ais_bench --batchsize 1 --warmup_count 100 --loop 1000`。
- 完整性：22/22 个 OM 均成功加载、推理和卸载；22/22 份精度 JSON 均无 NaN/Inf，
  44 个测试日志没有 `error`、`failed`、异常或崩溃记录。

### 速度结果

下表的 delta 按 `(mixed / FP16 - 1) * 100%` 计算，负值表示混合精度更快：

| 模型 | FP16 NPU 中位数 | Mixed NPU 中位数 | NPU delta | FP16 闭环中位数 | Mixed 闭环中位数 | 闭环 delta |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bassoon | 0.383 ms | 0.183 ms | -52.22% | 0.808 ms | 0.811 ms | +0.34% |
| Clarinet | 0.383 ms | 0.180 ms | -53.00% | 0.816 ms | 0.827 ms | +1.44% |
| Flute | 0.389 ms | 0.179 ms | -53.98% | 0.814 ms | 0.824 ms | +1.29% |
| Melodica | 0.388 ms | 0.175 ms | -54.90% | 0.802 ms | 0.821 ms | +2.39% |
| Saxophone | 0.386 ms | 0.177 ms | -54.09% | 0.816 ms | 0.812 ms | -0.50% |
| Sitar | 0.388 ms | 0.179 ms | -53.87% | 0.819 ms | 0.823 ms | +0.48% |
| Trombone | 0.385 ms | 0.181 ms | -52.99% | 0.802 ms | 0.815 ms | +1.61% |
| Trumpet | 0.387 ms | 0.176 ms | -54.52% | 0.801 ms | 0.822 ms | +2.65% |
| Tuba | 0.389 ms | 0.177 ms | -54.50% | 0.803 ms | 0.817 ms | +1.70% |
| Violin | 0.390 ms | 0.184 ms | -52.82% | 0.802 ms | 0.820 ms | +2.29% |
| Vowels | 0.387 ms | 0.176 ms | -54.52% | 0.802 ms | 0.822 ms | +2.52% |

跨模型 NPU delta 中位数为 **-53.98%**，范围为 -54.90% 至 -52.22%。为排除单次
波动，又对 Violin 做了 3 轮相同口径的成对复测：FP16 中位时间分别为
0.382/0.389/0.388 ms，混合精度为 0.178/0.182/0.181 ms，结果稳定。

不过，状态回灌闭环的 delta 中位数为 **+1.61%**，范围为 -0.50% 至 +2.65%，
两种模型在应用路径中基本持平。`ais_bench` 的 NPU compute time 与 Python、ACL
调用和状态回灌组成的闭环墙钟时间不是同一测量边界，因此不能把约 54% 的纯 NPU
差值直接解释为 DDSP 应用端获得同等加速。

### 精度结果

下表为 closed-loop NRMSE，单元格顺序是 `FP16 / mixed_float16`：

| 模型 | Amplitude | 有效谐波幅度 | Noise | GRU state |
| :--- | ---: | ---: | ---: | ---: |
| Bassoon | 0.1842% / 0.0668% | 0.2360% / 0.0908% | 0.3345% / 0.1778% | 0.1673% / 0.1045% |
| Clarinet | 0.1647% / 0.0636% | 0.2863% / 0.0935% | 0.3941% / 0.2046% | 0.2076% / 0.1015% |
| Flute | 0.2222% / 0.0730% | 0.2333% / 0.0851% | 0.4339% / 0.1997% | 0.1892% / 0.0985% |
| Melodica | 0.2215% / 0.0675% | 0.2088% / 0.0766% | 0.3600% / 0.1589% | 0.1587% / 0.0796% |
| Saxophone | 0.1943% / 0.0476% | 0.2169% / 0.0693% | 0.3197% / 0.1717% | 0.1759% / 0.0959% |
| Sitar | 0.1472% / 0.0555% | 1.0621% / 0.0639% | 0.3021% / 0.1588% | 0.1559% / 0.0854% |
| Trombone | 0.1543% / 0.0428% | 0.2015% / 0.0661% | 0.3386% / 0.1658% | 0.1578% / 0.0838% |
| Trumpet | 0.1690% / 0.0627% | 0.2369% / 0.0847% | 0.3486% / 0.1803% | 0.1864% / 0.0976% |
| Tuba | 0.4758% / 0.4669% | 0.5639% / 0.5291% | 0.6166% / 0.5862% | 0.4658% / 0.4388% |
| Violin | 0.1803% / 0.0646% | 0.2326% / 0.0880% | 0.3515% / 0.1916% | 0.1840% / 0.0880% |
| Vowels | 0.2216% / 0.0706% | 0.2662% / 0.0891% | 0.3369% / 0.1863% | 0.1864% / 0.1022% |

混合精度对 11/11 个模型的四类输出都得到更低 NRMSE。跨模型中位数如下：

| 输出 | Teacher-forced NRMSE 降幅 | Closed-loop NRMSE 降幅 |
| :--- | ---: | ---: |
| Amplitude | 72.36% | 64.17% |
| 有效谐波幅度 | 70.70% | 64.25% |
| Noise | 48.09% | 47.43% |
| GRU state | 37.77% | 46.94% |

Tuba 的改善幅度明显小于其余模型，但四类误差仍全部下降。与同一批 OM 在原 8T
上的报告相比，176 个 NRMSE 指标的相对差异中位数为 -0.00045%，范围为 -5.17%
至 +3.85%，99 项变小、77 项变大，没有观察到迁移到 20T 后的单向精度恶化。

测试期间 `npu-smi` 保持 `Health: Alarm`，系统 load average 约为 18.6，且 root 的
`apport` 进程持续占用约 98.6% 的一个 CPU 核。该异常负载未阻止 22 个模型完成
推理，但闭环墙钟时间只能视为当前系统状态下的实测值；纯 NPU 结果通过 Violin
复测确认后仍保持稳定。

完整结果位于：

```text
reports/ascend20t/all_models_runtime/summary.md               # 人类可读汇总
reports/ascend20t/all_models_runtime/summary.json             # 全部聚合字段
reports/ascend20t/all_models_runtime/summary.csv              # 逐模型扁平表
reports/ascend20t/all_models_runtime/precision/               # 22 份精度 JSON 和原始日志
reports/ascend20t/all_models_runtime/benchmarks/              # 22 份 ais_bench 原始日志
reports/ascend20t/all_models_runtime/benchmark_validation/    # Violin 三轮速度复测
reports/ascend20t/all_models_runtime/environment.txt          # 环境和负载边界
reports/ascend20t/all_models_runtime/SHA256SUMS.txt            # 结果文件哈希
```

复现命令：

```bash
cd ~/Documents/case3_ascend20t_all_models
bash tools/run_ascend20t_prebuilt_models.sh --force
```

## MIDI-DDSP FP16 与混合精度实测

MIDI-DDSP 的 Expression 和 Synthesis parameters 网络已在 `ascend8t2` 上完成
FP16 与 `mixed_float16` 的精度、重复性、纯 NPU 速度和 PyACL 端到端速度测试。
4 个 OM 均成功推理且没有 NaN/Inf，但混合精度在两个网络上分别比 FP16 慢
13.47% 和 3.23%。

Synthesis 网络包含随机采样，运行间波动与对 TensorFlow/ONNX 参考的误差处于
同一量级，因此不能把它的逐点差异全部解释为精度损失。完整测试口径、数值表、
复现命令和原始报告路径见 [MIDI-DDSP OM 精度与速度实测](midi-ddsp-benchmark.md)。

MIDI-DDSP OM 的实时 MIDI 文件播放已经在 `ascend8t2` 和漫步者 M25 上完成：
使用 `midi/ode-to-joy-violin.mid`、混合精度 Expression/Synthesis OM、48 kHz
双声道输出，262/262 个音频块播放完成，`underruns=0`、`overruns=0`。Synthesis
块中位渲染时间为 55.17 ms，块时长为 128 ms。完整链路和限制见
[MIDI-DDSP OM 实时 MIDI 合成测试](midi-ddsp-realtime.md)。
