# 案例 6 测试问题记录

> 本文件是案例 6 的统一问题台账。当前代码没有独立的板端测试报告或带日期的失败日志，以下只登记可复现的运行边界，不把限制伪装成已观测故障。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 尚无带日期的板端测试 |
| 开发板 | 未记录 |
| SoC / 档位 | 8T / 20T 必须分别记录 |
| CANN / 驱动 | 未记录 |
| Python / 框架 | `requirements.txt` 声明 torch、torchvision、onnx、OpenCV、Gradio |
| 模型 / revision | `models/resnet18_scene.pth/.onnx/.om`；当前未记录训练来源 |
| 输入与命令 | `python3 app.py`；`python3 prepare_models.py` |
| 原始证据 | 尚未提供 |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| 暂无 | - | - | - | 没有带日期的原始失败证据 | - |

## 已登记的限制，不是已观测故障

- 无训练 PTH 时，CPU 分类器使用 ImageNet backbone + 随机 5 类 head，预测可能错误。
- `get_soc_version()` 在找不到 `npu-smi` 时回退到 `Ascend310B4`，不能替代真实目标板探测。
- NPU 初始化失败会自动回退 CPU；必须保留日志和 `use_npu` 状态。
- `setup.sh` 尚未使用仓库统一的 `ascend` conda/`PYTHONNOUSERSITE=1` 流程。
- Gradio `--share` 和 `0.0.0.0` 是扩大访问面的配置，不是安全部署。

## 记录规则

出现真实失败时按统一模板补 `CASE6-ISS-YYYYMMDD-NN` 条目，至少包含环境、复现命令、原始错误、判定层级、根因状态和回归结果。不能把“CPU 回退可用”写成“NPU 验证通过”。
