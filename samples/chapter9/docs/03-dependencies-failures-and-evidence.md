# 依赖、失败语义与证据边界

## 软件依赖

历史记录中的原量化组合是：

- CANN 8.3.RC1
- aarch64
- Python 3.11
- ONNX 1.14.0
- ONNX Runtime 1.16.0
- AMCT ONNX 0.23.2

运行时依赖还包括：

- 板端 PyACL，用于 OM 推理；
- `Pillow`，用于读取 PNG/JPG/BMP；
- `datasets` 和 Hugging Face 访问，用于下载 Tiny-ImageNet；
- `atc`，用于 FP32/FP16/INT8 OM 转换；
- shell、CSV 和标准库，用于 sweep。

仓库当前运行规则要求使用 Python 3.9.25 的 `ascend` 环境。Python 3.11 只属于上述历史组合，不能当作当前环境的通过证据。AMCT 还必须与 CANN 精确配套；在 Python 3.9.25 上重跑并记录解释器 ABI、包来源和版本前，当前状态保持 `untested`。

## 共享工具路径

`chapter9_utils.py` 从 `samples/chapter8/perf_utils.py` 复用计时、报告和确定性 RGB 输入工具，并把 `chapter8` 目录加入 `sys.path`。这个路径与工具实际位置一致。

导入检查可在普通开发机上完成；它只证明 Python 模块可以解析，不证明 ATC、AMCT、OM 或验证集流程已经运行。

影响范围包括所有导入 `chapter9_utils.py` 的脚本和工具，可能使它们在建模型之前就因 `ModuleNotFoundError` 退出。由于本次任务限定只写文档，没有修改这个路径。解决时应先明确章节迁移后的唯一共享模块位置，并同步验证章节 8 和 9，不能仅靠临时设置外部 `PYTHONPATH` 当作修复。

## 输入与参数失败

- `03_prepare_quantization.py`：`--samples < 0` 抛 `ValueError`；ONNX 或校准清单缺失时抛 `FileNotFoundError`。
- `04_calibrate_quantization.py`：缺少 config、updated model 或校准清单时在量化前失败。
- `01_compare_outputs.py`：负样本数、非正 top-k 或负预热数会被拒绝；模型或清单缺失时失败。
- `02_perf_compare.py`：运行数必须大于 0；标签数必须与模型数一致。
- `05_validate_accuracy.py`：验证清单必须严格包含两列，标签转整数失败时报行号；重复标签、标签/模型数不一致和输出类数不一致都会中止。
- sweep：请求样本数超过校准池、ATC 失败、AMCT 失败或验证失败时，`set -euo pipefail` 终止后续点。

异常信息通常指出缺失文件或补救命令，但不一定清理本次已生成的中间产物。重跑前应检查对应工作目录，避免把部分 AMCT 文件误认为完整产物。

## 数据与模型失败

- 清单存在但图片路径无效：错误可能延迟到打开图片时发生，而不是清单解析阶段。
- 图片既不是三通道 HWC RGB：`load_rgb_frame()` 抛 `ValueError`。
- ONNX opset 转换失败：步骤 03 提示可用 `--amct-opset 0`，但这只有在当前 AMCT 确实支持原 opset 时才成立。
- 找不到 deploy ONNX：步骤 04 搜索工作目录中的 `*deploy*.onnx`，找不到就失败。
- 输出形状无法推断类别数：步骤 05 失败，不会猜测类别数。
- OM 输出类数不一致：步骤 05 失败。

不要通过修改标签、跳过模型或把输出 padding 当类别来“修复”失败。先确认模型输出和标签空间一致。

## 测试缺口

`samples/chapter9` 没有 pytest、单元测试目录或板端 CI harness。当前可做的低成本检查是：

```bash
python -m py_compile *.py tools/*.py
python -m json.tool outputs/output_compare.json > NUL
```

第二条仅适用于已存在的输出 JSON。语法检查不会执行 `chapter9_utils.py`，因此不能发现上述缺失模块的运行时导入错误。

板端验收至少要覆盖：

- ONNX 和图像来源可追溯；
- opset 与 AMCT/CANN 版本匹配；
- deploy ONNX 的输入合同正确；
- ATC 每一步的退出码和产物哈希；
- FP32/FP16/INT8 OM 能由 PyACL 加载；
- `01` 的输出形状和数值报告；
- `02` 的性能报告；
- `05` 的至少一个小样本 smoke 和完整验证集报告；
- sweep 每个点的独立产物与汇总行一一对应。

本次文档工作没有执行这些检查，全部为 `untested`。

## 既有输出报告

工作区 `outputs/` 中保留的 JSON 可以解析，并带有生成时间、hostname、platform、Python、命令和设备号。它们记录过输出对齐、性能、10000 张验证集和多个校准样本数点。

使用这些报告时必须注意：

- `model/`、`data/` 和 `outputs/` 均被 Git 忽略，不能把它们当作已发布资产；
- 报告没有记录 CANN、driver、firmware、SoC 计算档位和 Git revision；
- `accuracy_compare.json` 的验证清单路径自报为旧的 `samples/chapter8/data/val_list.txt`；
- `perf_compare.json` 的校准输入也自报为旧的 chapter8 路径；
- 报告中的模型路径和当前 chapter9 位置不完全一致。

因此这些数据可作为历史排查线索，但必须重新建立“当前源码、当前模型哈希、当前数据集、当前设备环境”的对应关系后，才能作为当前 chapter9 结果发布。

## 证据分级

| 检查 | 证据含义 |
|---|---|
| ONNX 下载完成 | 文件传输成功，不是模型完整性或兼容性 |
| AMCT 校准完成 | CPU 校准和 deploy ONNX 生成成功 |
| ATC 退出码 0 | 转换成功，不是 NPU 推理成功 |
| `01_compare_outputs.py` | 无标签的数值一致性，不是准确率 |
| `02_perf_compare.py` | 指定输入和协议下的性能，不是普适性能排名 |
| `05_validate_accuracy.py` | 指定验证集上的 Top-1/Top-k |
| sweep | 多个校准子集分别建模和评估后的敏感性 |
| 历史 JSON | `documented`，除非本轮重跑并记录环境，否则不是 `observed-pass` |

发布量化结论时应分开写明 artifact integrity、ATC conversion、ACL smoke、numerical consistency、accuracy 和 performance，不能用一个阶段的结果替代后续阶段。
