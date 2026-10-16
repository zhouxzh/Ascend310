# 第5章 MindSpore 开发样例

本目录配套 `src/book/chapter5.md`，在 `Ascend 310B4 / 8T` 开发板上用全量
MindSpore 走通一条最小闭环：

1. 核验环境与导入来源（`check_mindspore_env.py`）。
2. 观察 dtype 与算子路径的边界（`01_tensor_ops.py`）。
3. 用 `nn.Cell` 与 `ops` 搭建网络（`02_cell_network.py`）。
4. 构造可复现的数据管道（`03_dataset_pipeline.py`）。
5. 对比 PyNative 与静态图（`04_graph_mode.py`）。
6. 用 `value_and_grad` 与优化器完成一次训练（`05_autograd_train.py`）。
7. 导出并重新加载 CKPT 与 MindIR（`06_checkpoint_mindir.py`）。
8. 验证 MindSpore Lite 的加载边界（`07_lite_inference.py`）。

样例只使用合成数据：不下载权重、不访问网络、不安装/升级/删除任何软件包。
每个脚本结束时打印一行机器可读的 `RESULT {...}`，便于批量运行与留证。

## 运行前提

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/Documents/Ascend310/samples/chapter5
python check_mindspore_env.py
```

本章记录到的验证环境：

| 项目 | 值 |
| --- | --- |
| 板卡 | `orangepiaipro`，`Ascend 310B4 / 8T`，`npu-smi` 显示 `Health: Alarm`（已知板况） |
| 系统 / 架构 | Linux，`aarch64` |
| CANN | `8.0.0`（`runtime/toolkit/opp` 均为 `7.6.0.1.220:8.0.0`） |
| Python | `3.9.2`，`/usr/local/miniconda3/bin/python` |
| MindSpore | `2.4.10`（用户 site，`~/.local/lib/python3.9/site-packages`） |
| MindSpore Lite | `2.2.11` |
| MindNLP | `0.4.1` |
| NumPy | `1.22.4` |

两点必须确认：

- **解释器选对**。系统自带 `python3` 是 3.10.12，其中没有 MindSpore；样例使用
  `/usr/local/miniconda3/bin/python`。用 `python check_mindspore_env.py` 打印的
  `python_executable` 与各模块导入路径确认实际加载的是哪一份。
- **不要设置 `PYTHONNOUSERSITE=1`**。已验证的 wheel 安装在用户 site，隔离后会出现
  假的“缺包”结论；同一台板上 conda 目录里可能残留另一个版本的 `dist-info`。

## 1. 环境核验

```bash
python check_mindspore_env.py                 # 含 Ascend 设备加法探测
python check_mindspore_env.py --skip-device-probe
```

输出解释器、各模块版本与导入路径，并在独立子进程中做一次 `ops.add` 设备探测，
最后写入 `outputs/env-report.json`。当前板端结果：`mindspore 2.4.10` 与
`mindspore_lite 2.2.11` 均从用户 site 加载，`acl` 来自 CANN，设备加法探测通过。

## 2. 张量与算子路径

```bash
python 01_tensor_ops.py
```

四个探测各在独立子进程中执行，因此某个算子路径让进程消失时仍能记录退出码：
`fp32` 逐元素加法、`fp32` 矩阵乘、`fp16` 矩阵乘、CPU `fp32` 矩阵乘基准。
当前板端结果：`fp32` 加法通过；**`fp32` 矩阵乘被 cube 单元拒绝**
（`EZ1001 ... not support under keep type mode`）；`fp16` 矩阵乘通过，且与 CPU
`fp32` 基准在 `1e-2` 内一致。

## 3. 网络：`nn.Cell` 与 `ops`

```bash
python 02_cell_network.py
python 02_cell_network.py --skip-probes      # 只跑可用的 fp16 网络
```

先给出本板可用的写法：显式 `fp16` 参数 + `ops.matmul`（见
`chapter5_models.py` 中的 `Fp16Linear`），再用独立子进程记录三个边界写法：
`nn.Dense` 的 `fp32` 前向、`Dense→ReLU→Dense` 的 `fp32` 前向，
以及 `nn.Dense(...).to_float(ms.float16)`。当前板端结果：`fp16` 网络前向通过
（输出形状 `[1, 1]`）；两条 `fp32` 前向在取回数值时都被 `EZ1001`
（`aclnnMmGetWorkspaceSize call failed`）拒绝；`to_float(ms.float16)` 多次运行的
结局不一致：正常返回、以 `SIGSEGV`（返回码 `-11`）结束、以及超过探测预算不返回
都出现过，因此不能作为交付路径。

探测都在父进程建立 Ascend 上下文**之前**执行。父进程一边跑 Ascend 算子、一边启动
子进程做设备计算时，实测会出现子进程长期不返回，失败现象从“报 `EZ1001`”退化为
“没有回包”；把探测前置后结论重新可复现。

## 4. 数据管道

```bash
python 03_dataset_pipeline.py
```

`GeneratorDataset` → `map`（归一化）→ `batch`，用固定随机源检查批数与首批数据
可复现性。当前板端结果：`64` 个样本、`batch_size=8` 得到 `8` 个批次，批形状
`[8, 4]`，同种子重建后首批数据完全一致。

一个容易踩的坑：`map` 的 Python 函数要返回**显式 dtype 的 numpy 数组**。返回未定型的
标量会得到 `Failed to create tensor from numpy array, data type is unknown`。

## 5. PyNative 与静态图

```bash
python 04_graph_mode.py --mode pynative
python 04_graph_mode.py --mode graph
```

同一计算在两种模式下分别执行一次 `ms.jit` 编译函数与普通函数，期望值 `13.0`。
当前板端结果：两种模式均得到 `13.0`。

`ms.jit` 需要真实源码文件：在 `python -c` 里调用会报
`Mindspore can not compile temporary source code in terminal`，所以样例必须以文件
形式运行。

## 6. 自动微分与训练

```bash
python 05_autograd_train.py
python 05_autograd_train.py --steps 50 --log-every 10
```

用 `value_and_grad` + `nn.Adam` 训练 `Fp16Linear`，数据为固定种子的合成线性回归。
当前板端结果：`200` 步、学习率 `0.05`，loss 从 `4.644531` 降到约 `7e-06`；
同一脚本三轮耗时分别为 `74.68 s`、`33.46 s` 与 `30.28 s`，差异与板卡温度
（`80 ℃` → `95 ℃`）相关，因此这些耗时只能当作连通性证据，不能当性能数字。
脚本还会在独立子进程里探测 `nn.Dense` 的 `fp32` 梯度路径，实测以 `EZ1001` 结束
（也曾超过探测预算不返回），按 `observed-fail` / `blocked` 记录，不影响主训练结论。

## 7. CKPT 与 MindIR

```bash
python 06_checkpoint_mindir.py
```

保存 CKPT、重新加载到新网络做数值对齐，并导出 MindIR，记录字节数与 SHA-256。
当前板端结果：CKPT `60` 字节（`sha256: ca20a69f...`），重新加载后的最大绝对误差
`0.0`；MindIR `2796` 字节（`sha256: 12ade3bb...`）。

## 8. MindSpore Lite 边界

```bash
python 07_lite_inference.py
```

先用 CPU 后端确认 MindIR 工件本身是否被当前 Lite 接受（这一步会给出可读的版本
诊断），再用 Ascend 后端在独立子进程中尝试构图。当前板端结果：MindSpore `2.4.10`
导出的 MindIR 是 version 2，而 Lite `2.2.11` 只支持到 version 1，两个后端都在格式
校验阶段被拒绝：

```text
This software can only support the maximum mind ir version: 1,
please install the latest version to support the mind ir version: 2
```

这属于**运行时版本配套问题**，不是 310B 硬件不支持 MindSpore Lite。

## 一次跑完全部样例

```bash
python tools/run_board_examples.py      # 或 bash tools/run_board_examples.sh
python tools/collect_board_env.py       # 采集 npu-smi / CANN / 内存 / 包版本快照
```

`run_board_examples.py` 顺序执行全部样例，汇总每条 `RESULT` 到
`outputs/board-run-<时间戳>.json`，并在有样例没有给出判定时返回非零退出码。

## 目录说明

```text
check_mindspore_env.py      环境与导入来源核验
01_tensor_ops.py            dtype 与算子路径边界
02_cell_network.py          nn.Cell 网络与失败写法对照
03_dataset_pipeline.py      数据集与预处理管道
04_graph_mode.py            PyNative / Graph 模式对照
05_autograd_train.py        自动微分、优化器与训练循环
06_checkpoint_mindir.py     CKPT / MindIR 导出与数值对齐
07_lite_inference.py        MindSpore Lite 加载边界
chapter5_models.py          样例共享的 fp16 线性层与合成数据
chapter5_utils.py           RESULT 协议、子进程隔离与报告工具
tools/run_board_examples.py 批量运行与结果汇总
tools/collect_board_env.py  板端环境快照
outputs/                    运行产物（已由仓库忽略，不入库）
```

全部结果来自 `Ascend 310B4 / 8T`。`Ascend 310B1 / 20T` 上的 MindNLP 大模型结论
属于另一套证据，见 `samples/case9/docs/`，两者不得合并或互相换算。
