# 候选权重下载与 310B1 评测记录

## 评测边界

本记录对应 `192.168.1.101`，硬件为 **Ascend 310B1 / 20T**。它与仓库已有的
310B4 / 8T 报告是两套独立证据，不能合并排序。板端运行环境为：

| 项目 | 实测值 |
| --- | --- |
| NPU | 310B1，20T |
| npu-smi | 25.2.0 |
| 健康状态 | `Alarm`；按项目规则不单独作为阻断条件 |
| Python | 3.9.2，`/usr/local/miniconda3/bin/python` |
| PyTorch / NumPy / OpenCV | 2.1.0 / 1.22.4 / 4.10.0 |
| PyACL | `import acl` 通过 |
| 实验目录 | `/home/HwHiAiUser/palmprint-candidate-campaign-20261008` |

所有板端操作均在独立目录进行，没有修改已有服务、生产 `models/registry.json` 或
生产模型准入状态。随机输入 smoke 只证明 OM 可以加载和执行，不代表识别准确率。

## 下载与复用审计

“复用”表示仓库中已经存在该资产，本次重新计算了字节数和 SHA-256；没有再次覆盖
原文件。模型和数据资产仍被 `.gitignore` 排除。

| 候选/资产 | 状态 | 文件 | 字节数 | SHA-256 |
| --- | --- | --- | ---: | --- |
| CCNet | 复用并核验 | `models/onnx/palm_ccnet.onnx` | 257311904 | `bfc5c6349239083a376a7f03d9f068ab2fddd3c6a09dad1fe5140779a1127012` |
| CompNet Tongji 600 | 复用并核验 | `models/checkpoints/pretrained compnet models/net_params_tongji_600.pth` | 21210404 | `e27c86179dad2a69e9d9c7d4365eae5a31c3ccb4770a891a4ea3c1844d9f8a9a` |
| CompNet IITD 460 | 复用并核验 | `models/checkpoints/pretrained compnet models/net_params_iitd_460.pth` | 20923684 | `2e0744f4d73070d274822aa7c566a020daedd428761e90d78d1aecee6acccd77` |
| CompNet REST 358 | 复用并核验 | `models/checkpoints/pretrained compnet models/net_params_rest_358.pth` | 20714788 | `9d188319ffb3adbcde6543baac5e2487df2b025e49cffe174ef6279990a60e58` |
| CompNet XJTU Flash 200 | 复用并核验 | `models/checkpoints/pretrained compnet models/net_params_if_200.pth` | 20391203 | `af4f50d21843e57bf4039f9508d2c5bc2aa3d7352947d6a3e447292588b4e924` |
| CompNet XJTU Natural 200 | 复用并核验 | `models/checkpoints/pretrained compnet models/net_params_in_200.pth` | 20391203 | `65d99dd95237c645f1ce2a9e6f06e307beb5a5035c264ff10d8e8342e6a267bb` |
| PPNet | 本地资产复用并核验；官方网盘来源仍未验证 | `models/candidates/ppnet/ppnet_params_tongji_600.pth` | 91736778 | `c542c9daf893ac7cacee01402b39a12367bb137313f9d7c8825287263f0d21bb` |
| Holzweber ResNet18 | 复用并核验 | `models/candidates/holzweber/resnet18_tongji_unfreezed.pt` | 46015245 | `3be90a03bba23ba24babb464bb5e134540b0ba69c6fe626fbbfe8eb687d9b06b` |
| Holzweber MobileNetV2 | 复用并核验 | `models/candidates/holzweber/mobilenetv2_tongji_unfreezed.pt` | 12220857 | `6d85aa585ada1a7c78d9f6f361d84212be1b493bbb459730d57cb72f26e75447` |
| Holzweber ROI-LANet | 复用并核验 | `models/candidates/holzweber/ROI_extractor_augmented_TJ-NTU.pt` | 65818921 | `01dc525aaf967d96be34915e01c3578f1968cd40f3a6aecd7f537067d14046ac` |
| Lin-Dxin ResNet18 pair | 复用并核验 | `models/candidates/lin_dxin/source2/model1.pt` | 44784977 | `e13c42c79b5dcaa4c8be25ab35efdfa36c9cc5df33bcdccc7ae1dfe7aab1686f` |
| Kenan palmar-vein CNN | 复用并核验 | `models/candidates/kenan_veins/model_identification_500users_60train40test.h5` | 756599808 | `2c1ff42e703f459112aa8d1ba831107035dc4288d32b76bfd40d38830f71e6c5` |
| FusionNet PCANet finger ROI 1 | 本次从 GitHub API 下载 | `models/candidates/fusionnet/PCANet_palm_iter_1_fing_1.mat` | 54217 | `3421120365d44e6db0816b49d6a667de89f4de9f997620067d6d1420008961a5` |
| FusionNet PCANet finger ROI 2 | 本次从 GitHub API 下载 | `models/candidates/fusionnet/PCANet_palm_iter_1_fing_2.mat` | 54791 | `946ed1a107fca6d04ecdc68e589d521a8213314a8fe3d0f1d19c93a7232a9756` |
| FusionNet PCANet finger ROI 3 | 本次从 GitHub API 下载 | `models/candidates/fusionnet/PCANet_palm_iter_1_fing_3.mat` | 54885 | `9caca9c60787292633222a637b7c83c35930115d378bc96d403db0c1f3ceacf8` |
| FusionNet PCANet finger ROI 4 | 本次从 GitHub API 下载 | `models/candidates/fusionnet/PCANet_palm_iter_1_fing_4.mat` | 54562 | `4b4c1adcadbce30ded7be2ccbd52616fd511507f5b134229ffa96afd5b26a762` |
| FusionNet PCANet possible ROI | 本次从 GitHub API 下载 | `models/candidates/fusionnet/PCANet_palm_iter_1_possible.mat` | 187634 | `cf9bc3600142c1c3bd7487ee334104e935e3ae359d9e3784a3d36cbe45d101aa` |

EE-PRNet 的 [官方 Dropbox 权重](https://www.dropbox.com/scl/fi/k2rg5brxh0ahxxjwk2wqg/EE_PRnet-epoch-60.mat?rlkey=dk2jaiqynbbb0shf6v8k1w9z4&dl=0)
和 AlignNet 的 [官方 Dropbox 权重](https://www.dropbox.com/scl/fi/lk3kgko3ulbv5hv99fo5s/AlignNet-epoch-25.mat?rlkey=jssrvfl83e5ugn9vuynzfckci&dl=0)
在本次网络环境中均连接超时，没有留下文件，也没有把网页响应当作模型。

以下候选在上游仓库树中没有找到可验证的预训练 checkpoint，因此不能声称已下载：

| 候选 | 当前状态 |
| --- | --- |
| MPSNet、PKLNet、CO3Net、TPA-CNN、RobustPalmROI、GLNet | 只有代码/论文或无可验证权重链接 |
| PalmWildNet、PalmNet、JPFA、DDH | 未发现可直接下载的权重 |
| EDCC | 传统代码基线，不是神经网络权重 |
| KBY Palmprint/Palmvein SDK | 厂商 opaque SDK，需要授权，无法导出透明权重 |
| Tencent PalmAI | 云服务/SDK 形态，没有可下载权重 |

上游来源和许可仍应以 [候选清单](../candidate_manifest.json) 为准。特别是
`NOASSERTION`、GPL、非商业许可和云服务条目，不因本地存在文件就获得再分发许可。

## 310B1 ACL 运行结果

所有下表模型均使用随机输入完成一次 ACL 加载、执行、输出有限性和资源释放检查；
`passed` 不代表识别质量。

| 模型 | 输入 -> 输出 | 单次执行约 ms | 结果 |
| --- | --- | ---: | --- |
| CCNet | `1x1x128x128 -> 1x2048` | 37.09 | passed |
| CompNet Tongji/IITD/REST/Flash/Natural | `1x1x128x128 -> 1x512` | 4.31–4.51 | passed |
| PPNet Tongji | `1x1x128x128 -> 1x512` | 8.12 | passed，研究候选 |
| Holzweber ResNet18 | `1x3x224x224 -> 1x600` | 7.14 | passed，闭集分类器 |
| Holzweber MobileNetV2 | `1x3x224x224 -> 1x600` | 10.20 | passed，闭集分类器 |
| Holzweber ROI-LANet | `1x3x56x56 -> 1x18` | 17.40 | passed，ROI 模型 |
| Lin-Dxin ResNet18 pair | 两路 `1x3x224x224 -> 512` | 10.99 | passed，多输入 |
| Kenan palmar-vein CNN | `1x1x128x128 -> 1x500` | 27.48 | passed，掌静脉分类器 |

每次运行的 ACL reset/finalize 均返回 0。原始 JSON 保存在板端实验目录，复制到本地
的忽略目录 `reports/runs/board310b1_20261008/`。

## NPU 性能结果

使用 Tongji 一张 ROI，预热 10 次、执行 100 次、重复 3 轮、CPU 线程 1；这里是纯
模型调用的中位数/95 分位数，不是完整摄像头流水线。

| 模型 | P50 ms | P95 ms | 约 FPS | 含预处理流水线 P50 ms |
| --- | ---: | ---: | ---: | ---: |
| CCNet | 34.41 | 35.83 | 29.0 | 35.32 |
| CompNet Tongji 600 | 3.42 | 3.51 | 292.2 | 4.74 |
| CompNet IITD 460 | 3.45 | 3.49 | 290.2 | 4.75 |
| CompNet REST 358 | 3.39 | 3.44 | 295.2 | 4.74 |
| CompNet XJTU Flash 200 | 3.40 | 3.47 | 294.1 | 4.72 |
| CompNet XJTU Natural 200 | 3.38 | 3.43 | 295.7 | 4.70 |

## Tongji 100 人识别结果

协议为每个身份两张 session-1 模板、session-2 查询，固定随机种子，100 个身份，
800 个查询。该结果是板端 310B1 / 20T 的子集结果，不与 310B4 / 8T 报告合并。

| 模型 | Rank-1 | EER | AUC | 校准阈值 FAR | 校准阈值 FRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| CCNet | 0.2063 | 0.4349 | 0.5958 | 0.4128 | 0.4550 |
| CompNet Tongji 600 | 0.9950 | 0.0150 | 0.9991 | 0.0086 | 0.0219 |
| CompNet IITD 460 | 0.9800 | 0.0250 | 0.9975 | 0.0395 | 0.0181 |
| CompNet REST 358 | 0.9750 | 0.0200 | 0.9986 | 0.0239 | 0.0169 |
| CompNet XJTU Flash 200 | 0.9825 | 0.0175 | 0.9979 | 0.0152 | 0.0175 |
| CompNet XJTU Natural 200 | 0.9775 | 0.0150 | 0.9990 | 0.0123 | 0.0181 |
| PPNet | 1.0000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |

PPNet 结果来自独立 research-only probe，不能直接替代正式候选评测：其上游官方网盘
来源尚未完成核验，且没有加入生产 registry。

## PolyU-B 100 人交叉域结果

指标报告在进程段错误前已经写出，但必须同时记录进程退出状态。IITD 正常退出；其余
候选均出现 `rc=139`，内核日志出现 `err_ret=-512`。因此下表仅是“崩溃前观察值”，
不是稳定验收结果，也不应据此正式排名。

| 模型 | Rank-1 | EER | AUC | 进程结果 |
| --- | ---: | ---: | ---: | --- |
| CCNet | 0.1417 | 0.4353 | 0.5856 | `rc=139` |
| CompNet Tongji 600 | 0.9979 | 0.0208 | 0.9984 | `rc=139` |
| CompNet IITD 460 | 0.9979 | 0.0135 | 0.9986 | `rc=0` |
| CompNet REST 358 | 0.9938 | 0.0208 | 0.9985 | `rc=139` |
| CompNet XJTU Flash 200 | 0.9938 | 0.0125 | 0.9993 | `rc=139` |
| CompNet XJTU Natural 200 | 0.9958 | 0.0198 | 0.9984 | `rc=139` |

板端 `npu-smi` 仍显示 310B1 `Health: Alarm`，但本次阻断依据是具体的 `rc=139`
和内核 `err_ret=-512`，而不是 Alarm 本身。需要先做 ACL/驱动生命周期复现，再把
PolyU 结果升级为稳定报告。

## 未进行质量排名的候选

- Holzweber ResNet18/MobileNetV2 是 600 类 Tongji 闭集分类器，不是已经定义好的
  512-D identity embedding；ROI-LANet 只负责定位，不能拿 logits/ROI 参数与 embedding
  Rank-1 混排。
- Lin-Dxin 是双输入比较网络，本次只完成多输入 ACL smoke；需要固定的成对协议后再算
  识别指标。
- Kenan 是掌静脉 NIR 分类器，不能用可见光掌纹 Tongji/PolyU 结果代替掌静脉数据集
  评测。
- FusionNet 文件是 MATLAB/PCANet 多模态特征融合资产，尚无可验证 ONNX/OM 输入契约。
- EE-PRNet/AlignNet 因 Dropbox 超时未获得文件；其余“无权重”候选没有可运行资产。

## 结论与下一步

在当前 310B1 / 20T 板上，**CompNet Tongji 600** 是目前最平衡的方案：Tongji 子集
Rank-1 0.995、EER 0.015，纯模型 P50 约 3.42 ms，并且 ACL smoke 通过。IITD 460
在 PolyU-B 的进程稳定性最好，但仍需要重复运行确认。CCNet 的当前 2048-D 权重在
两个数据域都明显落后，不建议继续作为首选。PPNet 的子集结果很好，但来源与正式准入
证据不足，只能保持研究候选。

在修复或定位 PolyU 评测的 `rc=139`/`err_ret=-512` 后，应重新跑 500 人全量协议、
补充多次性能重复和温度/生命周期证据；在此之前，不修改生产 registry，也不把这些
研究候选自动提升为正式模型。
