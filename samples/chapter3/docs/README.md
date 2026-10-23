# 第3章样例文档索引

本目录说明 `samples/chapter3` 中首方训练脚本、算子校验脚本和已保存证据之间的关系。读者应先通过这里的入口确认设备、数据与输出合同，再进入具体专题。

## 文档地图

| 文档 | 内容 |
|---|---|
| [训练样例与输入输出合同](01-training-examples.md) | 合成数据、加州房价、MNIST、CIFAR-10 训练入口，依赖、命令、产物和失败边界 |
| [NPU 算子兼容性测试](02-operator-validation.md) | 通用算子和 AlexNet 分层测试的入口、容差、日志和停止条件 |
| [证据与限制](03-evidence-and-limitations.md) | 现有图片、日志和历史失败记录能证明什么，不能证明什么 |

## 代码范围

本说明覆盖 `AlexNet/`、`california_housing/`、`LeNet/`、`linear_regression/`、`ResNet/`、`test/` 和 `VGG/` 中的 Python 入口。`data/`、`kernel_meta/`、`.pytest_cache/`、`fusion_result.json` 和训练曲线属于运行输入或产物，不构成独立程序入口。

## 环境基线

仓库统一的板端执行环境是 conda 环境 `ascend`、Python `3.9.25`、`PYTHONNOUSERSITE=1`，并需要加载 CANN 环境。具体版本组合、驱动、固件和 CANN 必须在实际板卡上探测；本章没有锁定 `torch`、`torch_npu`、`torchvision`、`scikit-learn` 或 `pytest` 的版本。

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import torch, torch_npu; print(torch.npu.is_available())"
```

最后一条命令应输出 `True`。若导入失败、设备不可见或返回 `False`，停止执行 NPU 训练与 NPU 测试，并保留完整命令、退出码和 `ASCEND_LAUNCH_BLOCKING=1` 下的首个失败栈。

## 证据标记

文档使用以下标记：

- `documented`：可由源码、README 或仓库既有记录确认。
- `observed-pass`：有可追溯的运行记录，但未必由本次任务重跑。
- `observed-fail`：有可追溯的失败记录。
- `untested`：当前源码没有足够环境或运行记录支撑结论。

本章当前没有新增板端运行，因此不把历史图片或旧日志提升为当前源码的板端验收结果。
