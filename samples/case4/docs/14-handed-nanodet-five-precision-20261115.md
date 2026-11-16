# 左右掌NanoDet五精度转换、数值与速度记录

## 比较范围与来源

本记录对应2026-11-15在`192.168.1.100`的Ascend310B4 / 8T板上完成的人工调试实验。CANN为8.0.0，驱动为25.2.0，环境为用户拥有的`ascend`，Python3.9.25、NumPy1.22.4，启用`PYTHONNOUSERSITE=1`。本次没有在20T执行相同实验，不合并不同板的性能或质量结论。

输入模型来自ace1训练仓库的seed43双类别导出，不是当前工作台的旧单类别模型，也不是COCO检测器：

```text
训练仓库：ace1:/home/zhong/Documents/ascend310b-palm-detector
运行目录：artifacts/runs/nanodet-plus-m-320-portable-handed__data-v001__seed-43__20261114-1300
文件：nanodet-plus-m-320-palm.onnx
ONNX SHA-256：35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991
输入：data，float32[1,3,320,320]
输出：output，float32[1,2125,34]
类别：left_palm、right_palm
```

该实验使用用户要求从ace1取得的离线训练导出及当前调试程序，不是GitHub/HF固定公开版本的发布复现。训练仓库当时HEAD为`115a87b9f5487b20b10acbba35a31f5ce742be3c`，存在导出、后处理和测试的未提交修改；因此以本次ONNX哈希和保存的参考文件区分模型，不能只用仓库HEAD标识整个导出流程。

[完整结构化记录](evidence/handed-seed43-five-precision-20261115/five-precision-record.json)包含全部五种模式状态、80组逐样本数值/框比较、每种成功模式的五轮计时、OM哈希及板端原始报告哈希。[CSV汇总](evidence/handed-seed43-five-precision-20261115/five-precision-summary.csv)便于排序和复核。两份文件不含原始图像、embedding、输入张量或输出张量。完整日志和参考张量保留在板端，不进入Git。

## ATC参数与转换结果

五种模式依次执行，不同时启动多份ATC。先加载Conda/CANN，再显式限制图/算子编译并发；OpenMP/BLAS线程数不是这两项的替代品：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export TE_PARALLEL_COMPILER=1
export MAX_COMPILE_CORE_NUMBER=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
cd /home/HwHiAiUser/Documents/Ascend310/workspaces/case4/handed-seed43-five-20261115
```

每个模式使用同一命令结构，`MODE`取下表五个值之一：

```bash
atc --framework=5 \
  --model=nanodet-plus-m-320-handed-seed43.onnx \
  --output="om/handed_seed43_${MODE}" \
  --input_format=NCHW --input_shape="data:1,3,320,320" \
  --soc_version=Ascend310B4 --precision_mode="$MODE" --log=error
```

| 模式 | ATC退出码 | OM字节数 | 转换观察 |
| --- | --- | --- | --- |
| `force_fp32` | 0 | 6,546,705 | `observed-pass` |
| `allow_fp32_to_fp16` | 0 | 6,494,500 | `observed-pass` |
| `force_fp16` | 0 | 6,305,833 | `observed-pass` |
| `must_keep_origin_dtype` | 255 | 不适用 | `observed-fail`：EZ3002，当前MaxPoolV3内核不支持该路径的DT_FLOAT输入，日志列出DT_FLOAT16支持 |
| `allow_mix_precision` | 0 | 6,327,060 | `observed-pass` |

这不是所有310B/CANN组合都不支持保留原始dtype的结论，只限定于本次模型、图和编译组合。失败模式未生成OM，不进行ACL、数值或速度比较；CSV空值表示不适用，不是零误差或零耗时。成功模式均完成ACL执行、资源释放、设备重置和运行时finalize。

## 最大与平均绝对误差

ace1使用ONNX Runtime的CPU执行提供者对20张固定参考图像生成输入张量及golden。图像以BGR直接双线性缩放到320×320，均值为`[103.53,116.28,123.675]`、标准差为`[57.375,57.12,58.395]`，与模型卡一致。同一批张量和golden传到8T用于四个成功OM。

对每个样本，先计算`abs(OM - ONNX)`，分别记录所有`2125×34`元素的最大值和均值。下表最大误差是20个样本最大误差的最大值；平均误差是20个样本MAE的算术平均。由于样本输出形状完全相同，它也等价于全部元素等权平均。末列是逐样本MAE的范围。绝对误差不是百分比精度损失，两个类别分数与32个DFL通道的尺度也不同，不能只用一个总MAE代替分组诊断。

| 模式 | 全部元素最大绝对误差 | 全部元素平均绝对误差MAE | 逐样本MAE最小—最大 |
| --- | --- | --- | --- |
| `force_fp32` | 3.66693258 | 0.09873291 | 0.07926156—0.11834587 |
| `allow_fp32_to_fp16` | 3.66544724 | 0.09874161 | 0.07928270—0.11835394 |
| `force_fp16` | 3.66544724 | 0.09864514 | 0.07916328—0.11829547 |
| `allow_mix_precision` | 3.66544724 | 0.09864514 | 0.07916328—0.11829547 |
| `must_keep_origin_dtype` | 不适用 | 不适用 | ATC失败 |

四个成功模式的`allclose(atol=0.02, rtol=0.02)`均为0/20。类别分数最大绝对误差依次为0.56950504、0.56962943、0.56951469、0.56951469；DFL组最大误差与各模式全部元素最大误差相同。本轮旧报告没有单独保存类别组MAE和DFL组MAE，不能由总MAE推导或补写；后续细分统计需重新取得对应输出。FP16与mix的汇总数值相同不等于已独立证明全部输出逐位相同。

## 框数量、IoU与左右掌一致情况

使用ace1的双类别参考解码器，分数阈值0.35、IoU NMS阈值0.60，按类别执行NMS，不启用工作台后续增加的嵌套包含抑制。比较时每张图按参考框顺序，贪心选取尚未匹配OM框中IoU最高的一项；不按类别预先筛选，也不设置最低匹配IoU。它是对照诊断，不是标准mAP评估。

| 模式 | 数量相同的图像 | 参考/OM总框数 | 建立匹配的框对 | 匹配框平均IoU | 匹配框最小IoU | 匹配对左右掌标签相同 |
| --- | --- | --- | --- | --- | --- | --- |
| `force_fp32` | 18/20 | 21/21 | 20 | 0.756573 | 0.409238 | 20/20 |
| `allow_fp32_to_fp16` | 18/20 | 21/21 | 20 | 0.756645 | 0.409341 | 20/20 |
| `force_fp16` | 18/20 | 21/21 | 20 | 0.754905 | 0.409614 | 20/20 |
| `allow_mix_precision` | 18/20 | 21/21 | 20 | 0.754905 | 0.409614 | 20/20 |

总框数相同不能掩盖逐图数量差异：21个参考框与21个OM框只建立20对匹配，各有1个未配对。20/20表示这些已匹配框的ONNX/OM类别标签一致，不是全部21个框一致，更不是与人工真值比较得到的左右手准确率。当前结果也不能单独证明工作台此前的同掌重复框原因。

## 相同计时边界的速度

每个成功模式在独立进程内执行，依次测试；使用同一个`sample_00`张量，30次预热、5轮、每轮100次。P50/P95汇总全部500次调用，原始报告还保留各轮P50/P95。计时包括输入拷贝、ACL同步执行和输出拷贝，不包括图像读取、缩放、归一化、DFL/NMS、摄像头、HTTP或浏览器显示。

| 模式 | P50毫秒 | P95毫秒 | P50倒数换算每秒调用数 |
| --- | --- | --- | --- |
| `force_fp32` | 18.188240 | 18.297081 | 54.98 |
| `allow_fp32_to_fp16` | 17.724285 | 17.856158 | 56.42 |
| `force_fp16` | 8.292429 | 8.394786 | 120.59 |
| `allow_mix_precision` | 8.481506 | 8.573828 | 117.90 |
| `must_keep_origin_dtype` | 不适用 | 不适用 | ATC失败 |

上述单并发及OpenMP/BLAS配置有ATC进程环境证据；性能测试进程的线程环境没有独立快照，不能将其补写成严格单线程性能实验。本轮FP16和mix的模型调用明显快于另两份成功OM；两者之间约2.3%的时延差仅是本次顺序测试观察，没有交换模式顺序、温度/频率对照和独立复测，不能据此断言FP16在所有场景更快。P50倒数不是摄像头端到端FPS。

## OM与报告身份

| 模式 | OM SHA-256 |
| --- | --- |
| `force_fp32` | `23e7db9776d06bcc646f4c6d3a0a44ccf6941225d6d78f743823acc1b3629ad2` |
| `allow_fp32_to_fp16` | `380c134f5d3b0c80febc6d0fa30c7b960148ca06f16763c0b6c9a3a84065a7ea` |
| `force_fp16` | `5b822a1ab1653a927912cd90c87bae2e78ae6cd57becd3590001ba6f4dd5066e` |
| `allow_mix_precision` | `773ae33c1dc4de078298e47234dc19a271caf4c2ec68aa888aeb2267ea8e5776` |

结构化记录的SHA-256为`048899cf2c45c87240ed572c79f00fa73c3bb72306995a5416c96040c00d4067`。四份独立原始JSON报告及ATC日志的哈希保存在该记录内。

板端证据根目录为：

```text
/home/HwHiAiUser/Documents/Ascend310/workspaces/case4/handed-seed43-five-20261115/
├── nanodet-plus-m-320-handed-seed43.onnx
├── convert-five.sh
├── benchmark-five.py
├── run-benchmark.sh
├── reference/samples.json、sample_00.npz至sample_19.npz
├── reference_decoder/
├── logs/single-worker/atc_<mode>.log、atc_<mode>.exit
├── om/handed_seed43_<mode>.om
└── reports/<mode>.json、five-precision-record.json、five-precision-summary.csv
```

重新比较必须在空闲目标板、同一`ascend`及CANN环境中执行`bash run-benchmark.sh <mode>`，随后执行`python record-results.py`更新结构化记录。输入与模型哈希先与本记录核对；若输入、解码器、计时边界或线程配置变化，应建立新实验，不能覆盖成同口径结果。

## 已知问题与人工判定边界

[统一问题台账](05-troubleshooting.md)记录`CASE4-ISS-20261115-04`：首次ATC依赖缺失、未限制编译并发时板端失联、`must_keep_origin_dtype`算子dtype失败及成功OM的数值差异。失联原因尚未取得内核/OOM证据，不能直接归因于内存耗尽。当前记录保留失败历史，不以重跑成功删除首轮失败。

本轮没有自动替换当前服务，也没有以误差门限阻止后续人工测试。可以把mix作为人工对照候选，但仍需显示真实左右掌、检测框及注册流程，由用户给出最终业务判定。最大误差、平均误差、框IoU和速度应同时看；现有数据不足以确定数值偏差的算子根因或宣布最终最佳精度模式。
