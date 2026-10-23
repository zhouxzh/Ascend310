# 案例 6 测试问题记录

> 本文件是案例 6 的统一问题台账。当前代码没有独立的板端测试报告或带日期的失败日志，以下只登记可复现的运行边界，不把限制伪装成已观测故障。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-10-23 |
| 开发板 | Orange Pi Ai Pro，Ascend310B4 / 8T |
| SoC / 档位 | Ascend310B4 / 8T |
| CANN / 驱动 | `.212`: CANN 7.0 / 23.0.0；`.249` 与 20T: CANN 8.0 / 25.2.0 |
| Python / 框架 | conda `ascend` Python 3.9.25；Torch 2.1.0、TorchVision 0.16.0、ONNX 1.19.1 |
| 模型 / revision | `models/resnet18_scene.pth/.onnx/.om`；ImageNet backbone + 随机 5 类头，无训练来源 |
| 输入与命令 | `python prepare_models.py --onnx-only`；ATC 使用 `TE_PARALLEL_COMPILER=1` |
| 原始证据 | ONNX `1×3×224×224 → 1×5`；`.212`/20T ATC success 且 `use_npu=true`；20T OM SHA-256 `5b4e080fcc1d294bd894614181aadd8173a06738cd6dbb1f8c76f4d79de5161d` |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CASE6-ISS-20261023-01` | `resolved` | `blocker` | 2026-10-23 | ATC 默认并行编译在 8T 上触发 OOM/整板失去响应 | `TE_PARALLEL_COMPILER=1` 后 `.212`/CANN7 与 20T/CANN8 均生成 OM 并通过 NPU 分类；脚本已固化该限制，`.249` 待恢复后复测 |

### CASE6-ISS-20261023-01：ATC 并行编译耗尽板端资源

首次在 `.249`（CANN 8.0 / driver 25.2.0）直接运行
`python prepare_models.py`，ATC 超过 600 秒仍未返回，随后 SSH 持续
`banner exchange` 超时，OM 未生成。改在 `.212`（CANN 7.0 / driver
23.0.0）复测，默认 ATC 被系统以退出码 `137` 杀死，日志显示多个
`ForkServerPoolWorker` 同时退出，属于编译并发内存压力。

限制 ATC 编译进程后，同一 ONNX 在约 144 秒成功：

```bash
TE_PARALLEL_COMPILER=1 atc \
  --model=models/resnet18_scene.onnx \
  --framework=5 \
  --output=models/resnet18_scene \
  --soc_version=Ascend310B4 \
  --input_format=NCHW \
  --input_shape=input:1,3,224,224
```

`prepare_models.py` 已通过 `setdefault("TE_PARALLEL_COMPILER", "1")`
固化该默认值。生成 OM 后，`.212`/CANN7 的合成道路图分类返回
`use_npu=true`；20T/CANN8 约 254 秒完成 setup/ATC，生成 22 MB OM
（SHA-256 `5b4e080fcc1d294bd894614181aadd8173a06738cd6dbb1f8c76f4d79de5161d`）
并返回 `use_npu=true`。该模型没有训练标签数据，置信度不构成场景分类质量
结论。`.249`/CANN8/8T 仍需恢复 SSH/串口后复测。

`setup.sh` 也已在 `.212` 实跑通过：它只激活 conda `ascend`、设置
`PYTHONNOUSERSITE=1`、加载 CANN，再执行 `python -m pip install -r` 和模型
准备。`requirements.txt` 已对齐统一环境的 Gradio 4.44.0、NumPy 1.26.4、
ONNX 1.19.1、OpenCV headless 4.10.0.84、Torch 2.1.0 和 TorchVision
0.16.0，避免 pip 尝试升级到未验证的 OpenCV 5。

## 已登记的限制，不是已观测故障

- 无训练 PTH 时，CPU 分类器使用 ImageNet backbone + 随机 5 类 head，预测可能错误。
- `get_soc_version()` 在找不到 `npu-smi` 时回退到 `Ascend310B4`，不能替代真实目标板探测。
- NPU 初始化失败会自动回退 CPU；必须保留日志和 `use_npu` 状态。
- `setup.sh` 现在强制使用 `ascend` Python 3.9 并设置 `PYTHONNOUSERSITE=1`，不再调用系统 pip 或 base。
- Gradio `--share` 和 `0.0.0.0` 是扩大访问面的配置，不是安全部署。

## 记录规则

出现真实失败时按统一模板补 `CASE6-ISS-YYYYMMDD-NN` 条目，至少包含环境、复现命令、原始错误、判定层级、根因状态和回归结果。不能把“CPU 回退可用”写成“NPU 验证通过”。
