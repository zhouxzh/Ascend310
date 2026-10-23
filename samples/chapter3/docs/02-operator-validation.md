# NPU 算子兼容性测试

`test/` 和 `AlexNet/test/` 是两条不同的 pytest 路径。前者比较通用张量算子与网络层，后者按 AlexNet 的前向顺序比较 FP16/FP32 层输出。两者都要求真实 `torch_npu` 设备，不使用模拟器。

## 通用算子测试

入口文件：

- [conftest.py](../test/conftest.py)：开始会话时清空 [pytest_realtime_log.txt](../test/pytest_realtime_log.txt)，逐项写入开始和结束状态。
- [test_float16_ops.py](../test/test_float16_ops.py)：FP16 加法和矩阵乘法。
- [test_float32_ops.py](../test/test_float32_ops.py)：FP32 加法和矩阵乘法。
- [test_nn_layers_float16.py](../test/test_nn_layers_float16.py)：FP16 线性层、卷积、BatchNorm、激活、池化、Dropout、Flatten 等。
- [test_nn_layers_float32.py](../test/test_nn_layers_float32.py)：对应的 FP32 网络层集合。

从仓库根目录运行：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
pytest -q samples/chapter3/test
```

测试在 NPU 不可用时通过 `@pytest.mark.skipif(not torch.npu.is_available())` 跳过。若 `import torch_npu` 本身失败，收集阶段会报错且不会进入跳过判断。

## 数值合同

| 测试组 | 比较对象 | 相对/绝对容差 |
|---|---|---|
| FP16 加法、矩阵乘法 | CPU FP32 结果与 NPU FP16 结果 | `rtol=1e-2`, `atol=1e-2` |
| FP16 网络层 | CPU FP32 结果与 NPU FP16 结果 | 默认 `rtol=1e-2`, `atol=1e-2` |
| FP16 `nn.Linear` | 同上 | `rtol=1e-1`, `atol=1e-1` |
| FP32 加法、矩阵乘法 | CPU FP32 与 NPU FP32 | 默认 `rtol=1e-3`, `atol=1e-3` |
| FP32 网络层 | 同上 | `rtol=1e-3`, `atol=1e-3` |
| AlexNet FP16 分层 | CPU FP32 与同步权重的 NPU FP16 | `rtol=1e-2`, `atol=1e-2` |
| AlexNet FP32 分层 | CPU FP32 与同步权重的 NPU FP32 | `rtol=1e-3`, `atol=1e-3` |

通用层测试为大批量全连接层使用较小 batch，并对 BatchNorm、Dropout 切换到 `eval()`，目的是减少显存占用并固定统计行为。FP16 `nn.Linear` 的容差明显宽于其他层；通过测试只能说明该输入规模下满足该容差，不能说明所有形状和值域都兼容。

每个通用层测试方法结束时执行 `torch.npu.empty_cache()`。这只回收缓存，不证明设备上下文或进程资源已经完全释放。

## AlexNet 分层测试

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
pytest -q samples/chapter3/AlexNet/test
```

测试覆盖：

- `test_layer1()` 至 `test_layer5()`：卷积、ReLU、平均池化。
- `test_classifier()`：Flatten、两个 `6400→4096`/`4096→4096` 全连接层、ReLU、Dropout 和 `4096→10` 输出层。
- `test_full_model()`：`1×3×224×224` 输入上的整模型前向。

每个测试先对 Conv/Linear 做 Kaiming 初始化，再深拷贝并同步权重。Dropout 使用 `eval()`，所以测试比较的是确定性前向。AlexNet 路径没有 `torch.npu.is_available()` 跳过：缺少 `torch_npu` 时会在导入阶段直接抛出 `RuntimeError`。该路径也没有 `teardown_method()`，大量重复运行时要自行监控设备内存。

## 已保存测试证据

[2026-01-02 的 pytest 实时日志](../test/pytest_realtime_log.txt)是历史运行记录，不是本次任务重跑：

- `observed-pass`：日志记录了 104 个 `STARTING` 项，其中 91 个以 `PASSED` 结束。
- `observed-fail`：13 个以 `FAILED` 结束。
- 日志没有 `TEST SESSION SUMMARY`，也没有最终 pytest 退出码、失败断言详情、设备型号、CANN/驱动版本或源码提交号。
- 13 个失败全部落在 FP16 `AvgPool2d` 的部分变体和 FP32 `MaxPool2d` 的变体，但日志本身没有给出根因。不能仅凭用例名断言是形状、舍入、算子缺失还是测试代码错误。

[LeNet 的踩坑记录](../LeNet/errror.md)另有一条带完整栈的历史 `observed-fail`：FP32 `MaxPoolWithArgmaxV1` 在 `AIcoreEngine` 上报告不支持，支持的输入类型包含 `DT_FLOAT16`。它只能说明记录中的环境、输入和算子组合失败，不能外推为所有 310B、CANN 或池化配置。

同目录的空 [ge_check_op.json](../test/ge_check_op.json)和值为 `null` 的 fusion 文件不能作为算子融合或图编译通过证据。

## 失败定位顺序

1. 运行 `python -c "import torch, torch_npu; print(torch.npu.is_available())"`，区分环境导入与算子执行问题。
2. 以 `ASCEND_LAUNCH_BLOCKING=1` 重跑单个 node id，取得精确的首个失败算子。
3. 保存完整 pytest 输出、设备快照、CANN/驱动/固件版本、Python 与包版本、源码提交号。
4. 保留原失败日志，补充修复后的同命令回归结果，不覆盖历史记录。

一次加法、矩阵乘法或单层通过只证明该用例的数值一致性。它不能证明 AlexNet 端到端训练、任务准确率、吞吐或长时间稳定性。
