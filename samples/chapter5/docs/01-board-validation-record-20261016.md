# 01 第5章样例板端验证记录（2026-10-16）

本文件记录 `samples/chapter5/` 样例在真实开发板上的一次完整验证，供 `src/book/chapter5.md`
正文数字回溯。所有结论只适用于本文列出的硬件与软件组合。

## 验证目标与范围

验证目标是把第 5 章的样例从“能读”推进到“能在 310B 开发板上给出判定”：

1. 环境、版本与导入来源可核验；
2. 张量、算子、网络、数据管道、静态图、自动微分、导出与端侧加载各有可重复的最小样例；
3. 每个样例给出机器可读的 `RESULT` 行，失败与阻断都保留原始日志。

不在本次范围内：大模型权重下载与加载、端侧性能测量、量化、MindSpore Lite Ascend
后端的图构建（本轮在版本校验阶段就被拦下，未进入后端）。

## 硬件与软件环境

| 项目 | 实测值 |
| --- | --- |
| 主机名 / 架构 | `orangepiaipro` / `aarch64` |
| NPU | `Ascend 310B4 / 8T`，`npu-smi 25.2.0` 显示 `Health: Alarm` |
| NPU 温度 | 会话开始 `80 ℃`，长时间探测后升到 `95 ℃` |
| CANN | `8.0.0`（`runtime`/`toolkit`/`opp` 均为 `7.6.0.1.220:8.0.0`） |
| Python | `3.9.2`，`/usr/local/miniconda3/bin/python` |
| MindSpore | `2.4.10`，用户 site `~/.local/lib/python3.9/site-packages` |
| MindSpore Lite | `2.2.11` |
| MindNLP | `0.4.1` |
| NumPy | `1.22.4` |
| 内存 / 磁盘 | `15610 MB` 总量（约 `14 GB` 可用）；`/` 剩余 `34 GB` |

系统自带的 `python3` 是 `3.10.12`，其中没有 MindSpore；全部命令显式使用
`/usr/local/miniconda3/bin/python`。conda 目录中存在另一个版本的 `dist-info`，
实际生效的是用户 site 中的 `2.4.10`，因此记录导入路径比记录版本号更重要。

## 运行命令与产物

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/chapter5
python tools/collect_board_env.py
python tools/run_board_examples.py
```

| 产物 | 板端路径 |
| --- | --- |
| 环境快照 | `outputs/board-env-20261016-175654.json` / `.txt` |
| 全量样例汇总 | `outputs/board-run-20261016-195122.json` |
| 各样例报告 | `outputs/env-report.json`、`01_tensor_ops.json` … `07_lite_inference.json` |
| 训练与导出工件 | `outputs/chapter5_linear.ckpt`、`outputs/chapter5_linear.mindir` |

`outputs/` 由仓库忽略，报告留在板上作为原始证据，不入库。

## 全量样例运行判定

| 样例 | 判定 | 关键数据 |
| --- | --- | --- |
| `check_mindspore_env.py` | `pass` | MindSpore/Lite 均来自用户 site，设备加法探测通过 |
| `01_tensor_ops.py` | `pass` | `fp16` 矩阵乘通过；`fp32` 矩阵乘 `EZ1001`；与 CPU `fp32` 基准在 `1e-2` 内一致 |
| `02_cell_network.py` | `pass` | `fp16` 网络前向通过，输出形状 `[1, 1]` |
| `03_dataset_pipeline.py` | `pass` | `64` 样本、`batch 8` → `8` 个批次，批形状 `[8, 4]`，同种子可复现 |
| `04_graph_mode.py --mode pynative` | `pass` | `@ms.jit` 与普通函数均返回 `13.0` |
| `04_graph_mode.py --mode graph` | `pass` | 同上，`13.0` |
| `05_autograd_train.py` | `pass` | loss `4.644531` → 约 `7e-06`，`200` 步耗时 `33.46 s`（另有 `74.68 s` 与 `30.28 s` 两轮） |
| `06_checkpoint_mindir.py` | `pass` | CKPT `60` 字节、MindIR `2796` 字节、重载对齐误差 `0.0` |
| `07_lite_inference.py` | `blocked` | MindIR version `2` 高于 Lite 支持的 version `1`，CPU 与 Ascend 后端都被格式校验拒绝 |

`02_cell_network.py` 前两次执行中，边界探测只检查输出形状，得到“通过”；该判定已被
后续修正（见下一节），不作为证据使用。

## fp32 路径的重复测量

同一块板上对 `fp32` 路径做了三组独立测量，每组都在独立子进程中执行。

只检查形状与取回数值的对照（每组 `2` 次）：

| 探测 | 结果 | 结论 |
| --- | --- | --- |
| `nn.Dense` `fp32`，只打印 `shape` | `2/2` 通过 | 假阳性 |
| `nn.Dense` `fp32`，加 `.asnumpy()` | `2/2` 失败，`EZ1001` / `aclnnMmGetWorkspaceSize call failed` | `observed-fail` |
| `ops.matmul` `fp32`，只打印 `shape` | `2/2` 通过 | 假阳性 |
| `ops.matmul` `fp32`，加 `.asnumpy()` | `2/2` 失败，`EZ1001` / `aclnnMatmulGetWorkspaceSize call failed` | `observed-fail` |

重复性抽样（均取回数值）：

| 探测 | 结果 |
| --- | --- |
| `ops.matmul` `fp32` | `5/5` 失败 |
| `ops.matmul` `fp16` | `3/3` 通过 |
| `nn.Dense` `fp32` 前向 | `3/3` 失败 |
| `Dense→ReLU→Dense` `fp32` 前向 | `3/3` 失败 |
| `nn.Dense` `fp32` + `value_and_grad` | `2/2` 失败 |

`nn.Dense(4, 2).to_float(ms.float16)` 的结局不稳定，多次运行分别观察到：正常返回、
打印结果后以 `SIGSEGV`（返回码 `-11`）结束、无输出退出、超过 `120 s` 探测预算不返回。
因此该写法在本组合下不可作为交付路径。

`05_autograd_train.py` 内的 `fp32` 梯度探测在最终一轮以 `EZ1001`
（`The soc version does not support bf16 / fp32 for calculations`）结束，记录为
`observed-fail`；更早一轮在 `120 s` 预算内没有回包，记录为 `blocked`。

## 训练、导出与端侧加载

训练（`Fp16Linear` + `nn.Adam`，合成线性回归，固定随机种子）：

| 项目 | 实测值 |
| --- | --- |
| 步数 / 学习率 | `200` / `0.05` |
| loss | `4.644531` → 约 `7e-06` |
| 耗时 | `74.68 s` / `33.46 s` / `30.28 s`（三轮同一脚本） |
| 判定 | `observed-pass`（耗时差异与板况有关，不作为性能结论） |

导出与对齐：

| 工件 | 字节数 | SHA-256 | 验证 |
| --- | --- | --- | --- |
| CKPT | `60` | `ca20a69f138045ad41b910e4a7cffddb031eb21adf36740e9e22561f056f0d80` | 重载后最大绝对误差 `0.0` |
| MindIR | `2796` | `12ade3bbaafff37951ffadb2739eb82bae10cc52d94f2ebf51fbd5fb7c81b4d1` | 由 `07_lite_inference.py` 做后端加载 |

端侧加载：Lite `2.2.11` 对 CPU 后端与 Ascend 后端都返回

```text
This software can only support the maximum mind ir version: 1,
please install the latest version to support the mind ir version: 2
```

这发生在 MindIR 格式版本校验阶段，**没有进入 Ascend 后端**。因此本记录既不支持
“Lite 在 310B4 上可用”，也不支持“Lite 在 310B4 上不可用”，只能判定为版本不配套
导致的 `blocked`。

## 观测到的异常与处理

| 现象 | 定位 | 处理 |
| --- | --- | --- |
| 首轮 `02_cell_network.py` 未给出判定，主进程直接以 `EZ1001` 结束 | 网络示例用了 `nn.Dense` 的 `fp32` 路径 | 改成显式 `fp16` 参数的可运行网络，把 `fp32` 写法降级为独立探测 |
| 首轮 `03_dataset_pipeline.py` 报 `data type is unknown` | `map` 返回了未定型的 numpy 标量 | 返回显式 dtype 数组，标量包成单元素数组 |
| 边界探测只查 `shape` 时“通过” | 没有物化计算结果 | 探测统一加 `.asnumpy()`，并在正文记录该对照实验 |
| 父进程已在跑 Ascend 算子时，子进程探测长时间不返回 | 设备上下文竞争 | 探测移到父进程建立上下文之前；`run_isolated` 改为文件捕获输出并对整个进程组超时终止 |
| 长时间探测后 NPU 温度升到 `95 ℃`，`Health: Alarm` | 板况与散热 | 记录在案；清理测试进程后 `npu-smi` 显示设备内存回落到 `1575 MB`，全量套件结束时为 `89 ℃` / `2927 MB` |

## 证据边界与未执行项

- 本文全部数字来自 `Ascend 310B4 / 8T`；仓库中 `Ascend 310B1 / 20T` 的 MindNLP 与大
  模型结论（`samples/case9/docs/`）属于另一套证据，不得与本记录合并或互相换算。
- 未执行：大模型权重下载与端侧推理、placement 配置、端侧性能测量、量化。
- `npu-smi` 的 `Health: Alarm` 是这块板的已知状态，本身不作为通过或失败的门；判定
  依据是每个样例各自的命令、退出码与数值结果。
