# 左右掌NanoDet完整test集：CUDA ONNX与8T OM对比

## 结论

- ACE2的CUDA ONNX和8T上四种成功OM均完成了完整test集推理：1600张图、1601个标注对象。
- 8T OM保留了较高的AP50和左右掌类别判断能力，但框定位质量明显低于CUDA ONNX：mAP50–95下降约0.1149，AP75下降约0.228，AP50仅下降约0.0053。
- 数值差异主要来自DFL框回归通道。分类通道平均绝对误差约0.00094，DFL通道约0.0957；因此问题应优先定位到框坐标回归，而不是左右分类通道。
- `force_fp16`与`allow_mix_precision`产生了相同的COCO预测文件哈希及任务指标。本次数据没有显示mix模式带来额外质量提升；FP16路径P50约8.61ms，mix约8.77ms，mix略慢。
- `must_keep_origin_dtype`无法生成OM。20T按用户要求未运行，未复制数据、未转换模型、未执行推理。

## 测试范围

| 项目 | 内容 |
| --- | --- |
| 参考后端 | ACE2，ONNX Runtime 1.23.2 / CUDAExecutionProvider |
| 目标板 | `192.168.1.100`，Ascend310B4 / 8T |
| 目标环境 | CANN8.0.0、驱动25.2.0、Python3.9.25、`ascend`环境 |
| 模型 | ACE1 seed43双类别NanoDet |
| ONNX SHA-256 | `35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991` |
| test标注SHA-256 | `5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a` |
| test规模 | 1600张图、1601个标注对象 |
| 20T | `untested`，按用户要求不占用 |

模型输入为`float32[1,3,320,320]`，输出为`float32[1,2125,34]`，类别为`left_palm`和`right_palm`。ONNX与OM使用同一预处理、解码和指标实现。

test标签来自MediaPipe自动生成，并非独立人工审核真值。下文的AP、掌侧正确率、漏检和重复框只描述这份held-out test集，不表示摄像头现场准确率或人工左右掌准确率。

## 测试口径

- 预处理：BGR直接缩放到320×320，使用训练均值和标准差，不做letterbox。
- 输出解释：前2个通道为类别概率，后32个通道为4组8-bin DFL logits；不进行二次sigmoid。
- COCO预测：最低分数0.05，每图最多100框，类别内NMS IoU 0.60。
- 任务统计：分数阈值0.35、空间IoU 0.50；先做空间匹配，再判断左右掌类别。
- 数值误差：比较每个OM与ACE2 CUDA ONNX的`1600×2125×34`输出，分别统计分类通道和DFL通道。
- OM计时：H2D、ACL执行、设备同步和D2H；不包含JPEG读取、预处理、DFL/NMS或指标计算。

ACE2运行profile记录494,709个CUDA节点，未发现CPU节点回退。8T四份成功OM均为独立ACL进程，逐模式完成资源释放、设备重置和finalize。

## 任务质量

### AP对比

Δ列为8T OM减去CUDA ONNX，数值为负表示低于参考。

| 后端 / 模式 | mAP50–95 | ΔmAP | AP50 | AP75 | ΔAP75 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CUDA ONNX | 0.675698 | 基准 | 0.985598 | 0.826200 | 基准 |
| 8T `force_fp32` | 0.560804 | -0.114894 | 0.980243 | 0.597313 | -0.228887 |
| 8T `force_fp32` + `high_precision_for_all` | 0.560804 | -0.114894 | 0.980243 | 0.597313 | -0.228887 |
| 8T `allow_fp32_to_fp16` | 0.560754 | -0.114943 | 0.980232 | 0.597444 | -0.228756 |
| 8T `force_fp16` | 0.560799 | -0.114898 | 0.980262 | 0.598347 | -0.227853 |
| 8T `allow_mix_precision` | 0.560799 | -0.114898 | 0.980262 | 0.598347 | -0.227853 |

CUDA ONNX的尺度指标为：APsmall `0.401547`、APmedium `0.681125`、APlarge `0.708372`。左掌和右掌mAP50–95分别为`0.682079`和`0.669317`。

### 检测任务计数

| 后端 / 模式 | 掌侧正确 / 空间匹配 | 漏检 | 重复框 | 非重叠假阳性 |
| --- | ---: | ---: | ---: | ---: |
| CUDA ONNX | 1572 / 1578 | 23 | 4 | 22 |
| 8T `force_fp32` | 1558 / 1564 | 37 | 2 | 50 |
| 8T `force_fp32` + `high_precision_for_all` | 1558 / 1564 | 37 | 2 | 50 |
| 8T `allow_fp32_to_fp16` | 1558 / 1564 | 37 | 2 | 50 |
| 8T `force_fp16` | 1558 / 1564 | 37 | 2 | 50 |
| 8T `allow_mix_precision` | 1558 / 1564 | 37 | 2 | 50 |

CUDA ONNX匹配框的掌侧正确率为`0.996198`。8T四模式的匹配框掌侧正确率均为`0.996163`。左右掌匹配结果几乎不变，但8T多出14个漏检和28个非重叠假阳性。

## 数值一致性

| 模式 | 全部元素MAE | 分类通道MAE | DFL通道MAE | 全部元素最大误差 | `allclose`通过图数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `force_fp32` | 0.090235 | 0.000944 | 0.095815 | 6.173584 | 0 / 1600 |
| `force_fp32` + `high_precision_for_all` | 0.090235 | 0.000944 | 0.095815 | 6.173584 | 0 / 1600 |
| `allow_fp32_to_fp16` | 0.090243 | 0.000944 | 0.095824 | 6.169454 | 0 / 1600 |
| `force_fp16` | 0.090158 | 0.000943 | 0.095734 | 6.161641 | 0 / 1600 |
| `allow_mix_precision` | 0.090158 | 0.000943 | 0.095734 | 6.161641 | 0 / 1600 |

`allclose`容差为`atol=0.02`、`rtol=0.02`。五份成功OM输出均为有限值，没有NaN或Inf。总MAE和最大误差由DFL通道主导；分类通道误差比DFL低约两个数量级。

## 推理时延

| 模式 | P50 | P95 |
| --- | ---: | ---: |
| `force_fp32` | 18.230723 ms | 18.380286 ms |
| `force_fp32` + `high_precision_for_all` | 18.317447 ms | 18.438402 ms |
| `allow_fp32_to_fp16` | 18.152368 ms | 18.278088 ms |
| `force_fp16` | 8.614614 ms | 8.712170 ms |
| `allow_mix_precision` | 8.771138 ms | 8.853794 ms |

ACE2 CUDA ONNX的参考时延为P50 `3.001598 ms`、P95 `3.162096 ms`，仅用于记录参考环境，不参与8T模式排名。`high_precision_for_all`相对默认FP32没有质量改善，且P50增加约0.087ms。8T的FP16类路径约为FP32类路径的两倍快；`force_fp16`比`allow_mix_precision`快约1.8%。本次没有温度、频率或交错顺序控制，这是观察值，不是稳定性能排名。

## 未完成项

| 组合 | 状态 | 原因 |
| --- | --- | --- |
| 8T `must_keep_origin_dtype` | `observed-fail` | ATC退出255；`MaxPoolV3`在当前模型/CANN8/310B4路径要求FP16输入，`DT_FLOAT`没有可用AIcore内核。未生成OM。 |
| 20T全部模式 | `untested` | 用户明确要求不使用20T开发板。本轮没有向20T复制数据、转换OM或执行推理。 |

ATC使用`TE_PARALLEL_COMPILER=1`和`MAX_COMPILE_CORE_NUMBER=1`。成功OM的SHA-256如下：

| 模式 | OM SHA-256 |
| --- | --- |
| `force_fp32` | `23e7db9776d06bcc646f4c6d3a0a44ccf6941225d6d78f743823acc1b3629ad2` |
| `allow_fp32_to_fp16` | `380c134f5d3b0c80febc6d0fa30c7b960148ca06f16763c0b6c9a3a84065a7ea` |
| `force_fp16` | `5b822a1ab1653a927912cd90c87bae2e78ae6cd57becd3590001ba6f4dd5066e` |
| `allow_mix_precision` | `773ae33c1dc4de078298e47234dc19a271caf4c2ec68aa888aeb2267ea8e5776` |
| `force_fp32` + `high_precision_for_all` | `4c18232a7b367bec1a374d2f9d2e8c6fbe7d9beef99468879e33893e6de7f499` |

## 算子实现优先级探针

默认FP32转换使用ATC默认的`high_performance`实现策略，日志中出现`tiling offset out of range, index: 32`告警。为检查该策略是否解释精度差异，本轮新增一份`force_fp32 + --op_select_implmode=high_precision_for_all` OM。

高精度OM的ATC日志没有该tiling告警，但1600张图的COCO预测SHA-256与默认FP32完全相同：`9c4329353f08a3eec95e810416071866bfee2fc06543b32b38f7a2912bedf73c`。其raw误差汇总、mAP、AP75、左右掌任务统计也逐项相同。因此，对这份ONNX、CANN8和8T组合，`high_precision_for_all`没有改善精度；默认日志中的tiling告警本身不能作为当前DFL误差的充分根因解释。完整对照见[高精度策略探针汇总](evidence/handed-seed43-fulltest-8t-20261115/high-precision-probe-summary.json)。

## 历史checkpoint对照

ACE1原有完整test记录来自`model_best.ckpt`的PyTorch评估，不是本轮ONNX结果。历史指标为mAP50–95 `0.674634`、AP50 `0.986122`、AP75 `0.830005`；本次CUDA ONNX为`0.675698`、`0.985598`、`0.826200`，两者接近但不相同。

ACE1另有20张test图的PyTorch/CPU ONNX对照，20/20解码类别和框匹配，最大原始绝对误差约`1.05e-5`。该对照只证明导出样例接近，不替代1600张CUDA全量结果，也不用于8T任务质量结论。

## 证据

- [ACE2与8T脱敏汇总](evidence/handed-seed43-fulltest-8t-20261115/onnx-vs-8t-summary.json)，SHA-256：`882d5eee063d6f10a767f5db4bf86f823e216cfec1687f559f841f724bad807c`
- [对比CSV](evidence/handed-seed43-fulltest-8t-20261115/fulltest-comparison.csv)
- 各8T模式的ACL报告和任务指标位于[证据目录](evidence/handed-seed43-fulltest-8t-20261115/)及对应模式子目录。
- ACE2和8T受控工作区保留原始预测、逐图诊断、reference pack、ATC日志和逐模式报告。

首次8T流式评测曾在FP32进度输出处因遗留`reference`变量停止；该轮没有有效任务结果，修复后从FP32完整重跑。失败历史已登记在[问题台账](05-troubleshooting.md)，不归因于模型或某个精度模式。

## 使用边界

本轮没有切换Case4服务模型，也没有把任何模式标为生产准入。结果只适用于seed43模型、这份MediaPipe自动标注test集、ACE2 CUDA环境和310B4 / 8T组合。

若继续接入注册流程，还需人工检查摄像头画面中的以下行为：单掌/双掌检测、框位置、左右掌类别、双手重叠、误检、漏检，以及“选择左掌却放置右掌”时的实际表现。
