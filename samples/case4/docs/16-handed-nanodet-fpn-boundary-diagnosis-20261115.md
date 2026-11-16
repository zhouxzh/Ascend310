# 左右掌NanoDet的FPN边界数值定位

## 结论

在Ascend310B4 / 8T上，对seed43双类别NanoDet进行ONNX与OM中间张量对比后，**首个数量级明显放大的边界位于FPN第一次上采样之后**：

```text
/fpn/reduce_layers.2/act/LeakyRelu_output_0
    -> /fpn/upsample/Resize_output_0
```

该`Resize`使用双线性插值和`half_pixel`坐标变换。其输入边界的平均绝对误差约为`2.68e-5`，经过第一次Resize后变为`7.11e-3`，约扩大265倍。后续top-down融合继续把误差放大，最终40×40 FPN输出的平均误差达到`2.94e-2`。

这把问题从“最终DFL输出误差”缩小到FPN上采样路径。随后在第二块独立的Ascend310B4 / 8T（`192.168.1.108`）上运行了同一独立Resize图，三份固定输入的MAE为`0.0060597`、`0.0071574`、`0.0081154`，总体MAE为`0.0071108`，最大误差为`0.219785`；与`.100`完整FPN第一次Resize边界的MAE`0.0071112`、最大误差`0.220018`基本一致。

这证明该误差可在两个独立310B4/8T板上由独立Resize图复现，问题已从“整图融合偶发差异”收敛到该Resize合同的310B4/CANN8执行路径。它仍不等同于“算子不支持”：ATC转换和ACL执行均成功，需继续确认是`half_pixel + linear`语义实现差异、输入/输出格式转换，还是该CANN版本的Resize数值实现偏差。

## 固定合同

| 项目 | 值 |
| --- | --- |
| 模型 | seed43双类别NanoDet |
| ONNX SHA-256 | `35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991` |
| 目标板 | Ascend310B4 / 8T，MAC `c0:74:2b:fd:dd:7a` |
| CANN / 驱动 | 8.0.0 / 25.2.0 |
| ATC | `force_fp32`，`Ascend310B4`，`TE_PARALLEL_COMPILER=1`，`MAX_COMPILE_CORE_NUMBER=1` |
| CUDA参考 | ACE2，ONNX Runtime CUDAExecutionProvider |
| 样本 | test图像ID `8781`、`8886`、`9266`，分别来自完整test误差分布的最小、中位和最大MAE样本 |
| 输入 | `float32[1,3,320,320]`，与完整test评测使用同一预处理张量 |
| 比较规则 | ONNX CUDA golden与8T OM逐元素比较，`atol=0.001`、`rtol=0.001` |

三个样本不用于重新估计mAP或任务质量；它们只用于定位数值误差最早出现的图边界。完整test任务指标仍见[CUDA与8T OM对比](15-handed-nanodet-fulltest-8t-20261115.md)。

## 骨干边界

| 边界 | Shape | 最大绝对误差 | 平均绝对误差 | 通过allclose的样本 |
| --- | --- | ---: | ---: | ---: |
| Conv1输出 | `[1,24,160,160]` | 0.000463 | 0.0000133 | 3 / 3 |
| Conv1 LeakyReLU输出 | `[1,24,160,160]` | 0.000344 | 0.0000112 | 3 / 3 |
| MaxPool输出 | `[1,24,80,80]` | 0.000311 | 0.0000221 | 3 / 3 |
| stage2出口 | `[1,116,40,40]` | 0.002555 | 0.0000657 | 0 / 3 |
| stage3出口 | `[1,232,20,20]` | 0.001680 | 0.0000442 | 1 / 3 |
| stage4出口 | `[1,464,10,10]` | 0.001086 | 0.0000196 | 3 / 3 |

骨干中在stage2出口已有小幅超出当前严格容差的误差，但它的平均误差仅为`6.57e-5`，远小于完整模型最终DFL误差约`9.57e-2`。MaxPool并不是本次探针中的首个大幅偏差位置。

## FPN边界

| 边界 | Shape | 最大绝对误差 | 平均绝对误差 | 通过allclose的样本 |
| --- | --- | ---: | ---: | ---: |
| reduce层40×40输出 | `[1,96,40,40]` | 0.002736 | 0.0000870 | 0 / 3 |
| reduce层20×20输出 | `[1,96,20,20]` | 0.001037 | 0.0000533 | 2 / 3 |
| reduce层10×10输出 | `[1,96,10,10]` | 0.000631 | 0.0000268 | 3 / 3 |
| 第一次Resize输出，10×10至20×20 | `[1,96,20,20]` | 0.220018 | 0.007111 | 0 / 3 |
| 第一次Concat输出 | `[1,192,20,20]` | 0.220018 | 0.003582 | 0 / 3 |
| 第一次top-down Add输出 | `[1,96,20,20]` | 0.238765 | 0.009591 | 0 / 3 |
| 第二次Resize输出，20×20至40×40 | `[1,96,40,40]` | 0.387894 | 0.018284 | 0 / 3 |
| 第二次Concat输出 | `[1,192,40,40]` | 0.387894 | 0.009185 | 0 / 3 |
| 第二次top-down Add输出 | `[1,96,40,40]` | 1.104874 | 0.029356 | 0 / 3 |

第一次Resize的ONNX属性为：

```text
mode=linear
coordinate_transformation_mode=half_pixel
nearest_mode=floor
```

Concat不会创造新的数值误差，但会在更多通道中稀释平均值；两个top-down融合块则继续放大误差。该误差传播模式与完整模型中“分类通道几乎一致、DFL框回归明显偏离”的现象一致：40×40分支对小目标和严格IoU定位更敏感。

## 当前判定

| 假设 | 结论 | 证据 |
| --- | --- | --- |
| 输入预处理不一致 | `observed-fail` | 完整test逐图输入SHA-256已在8T与CUDA参考间核对一致。 |
| 动态shape导致误差 | `observed-fail` | ONNX和OM均为静态`[1,3,320,320]`。 |
| MaxPool是首个大偏差点 | `observed-fail` | MaxPool输出MAE仅`2.21e-5`。 |
| FPN第一次Resize是第一个显著偏差边界 | `observed-pass` | 其输入MAE`2.68e-5`，输出MAE`7.11e-3`。 |
| Resize单算子不支持或实现错误 | `hypothesis` | 独立Resize图在第二块Ascend310B4/8T复现MAE约`0.0071108`、最大误差约`0.219785`；仍需区分坐标变换语义与CANN实现精度问题。 |
| 默认ATC高性能算子策略是唯一根因 | `observed-fail` | `high_precision_for_all`与默认FP32的完整预测、mAP和raw误差相同。 |
| 对Resize节点使用`keep_dtype`可恢复精度 | `observed-fail` | `allow_mix_precision`与仅列`/fpn/upsample/Resize`的`keep_dtype`版本生成不同OM，但三份固定输入的MAE、最大误差和allclose结果完全相同。 |
| 检测头卷积额外放大FPN误差 | `untested` | 检测头多输出CUDA golden已准备，尚未在8T转换。 |
| 静态算术改写可安全替换完整图中的Resize | `observed-fail` | 独立Resize图在CUDA上几乎等价，但嵌入完整模型后1600张输入仅19张满足`2e-6`容差，最大输出差`0.0065784`；改写模型被拒绝，未上8T。 |

## 下一步

1. 节点级`keep_dtype`没有改变独立Resize结果；保持数学语义的静态算术改写在独立图上有效，但完整模型CUDA等价性失败，因此不能作为部署修复。
2. 运行已经准备好的检测头探针，判断FPN误差是否在四个`gfl_cls`卷积中再次跃升；该步骤只服务于进一步定位，不会恢复当前精度。
3. 若需要继续寻找可部署恢复方案，优先对独立Resize做最小融合控制或隔离CANN版本A/B；若CANN实现差异保持不变，应训练/导出不含该敏感`half_pixel` Resize合同的部署模型。不得直接改插值模式、坐标变换或用test集拟合补偿。

## 证据

- [骨干探针manifest](evidence/handed-seed43-fpn-boundary-20261115/backbone/manifest.json)和[骨干汇总](evidence/handed-seed43-fpn-boundary-20261115/backbone/summary.json)
- [FPN探针manifest](evidence/handed-seed43-fpn-boundary-20261115/fpn/manifest.json)和[FPN汇总](evidence/handed-seed43-fpn-boundary-20261115/fpn/summary.json)
- [FPN细化probe manifest](evidence/handed-seed43-fpn-boundary-20261115/fpn_topdown/manifest.json)和[FPN细化汇总](evidence/handed-seed43-fpn-boundary-20261115/fpn_topdown/summary.json)
- [第二块8T独立Resize manifest](evidence/handed-seed43-fpn-boundary-20261115/secondary-8t-resize/manifest.json)和[ACL汇总](evidence/handed-seed43-fpn-boundary-20261115/secondary-8t-resize/summary.json)
- [节点级keep_dtype配置](evidence/handed-seed43-fpn-boundary-20261115/secondary-8t-keep-dtype/resize1.keep_dtype.cfg)、[混合精度基线](evidence/handed-seed43-fpn-boundary-20261115/secondary-8t-keep-dtype/allow_mix_precision/summary.json)和[仅保护Resize的结果](evidence/handed-seed43-fpn-boundary-20261115/secondary-8t-keep-dtype/allow_mix_precision_keep_resize/summary.json)
- [完整图静态改写元数据](evidence/handed-seed43-fpn-boundary-20261115/full-static-rewrite/rewrite-metadata.json)和[CUDA全量等价检查](evidence/handed-seed43-fpn-boundary-20261115/full-static-rewrite/cuda-equivalence.json)
- 完整模型的数值、任务质量和性能对比见[第15号记录](15-handed-nanodet-fulltest-8t-20261115.md)。
