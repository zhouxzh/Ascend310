# 扩展模型族测试报告（2026-09-11）

本报告补充 `candidate_manifest.json` 的 28 项候选，覆盖用户提出的 ResNet、VGG、DenseNet、MobileNetV2、EfficientNetV2、ViT、Swin、ROI、分类器、掌静脉、GAN/扩散和 Palm-ID 等模型族。报告只提交脱敏摘要；完整板端日志保留在板端私有目录。

## 结论先行

- 当前工作台仍只提供 6 个 NPU/mixed-FP16 掌纹 embedding：CCNet 和五个 CompNet。
- 本次新增的通用骨干测试使用 `torchvision` 的 `weights=None`，因此只证明架构是否能被当前板端运行时尝试加载，不能证明掌纹识别效果，也不能生成可用模板。
- 由于 Ascend 310B4 / CANN 8.0 当前出现 `EZ3002` 算子不支持、进程超时和 semaphore 资源泄漏，通用骨干测试没有形成可部署的 NPU 结论。该结果归类为运行时阻断，不归因于模型精度。
- 论文级专用模型没有在本仓库找到可核验 checkpoint、输入输出契约或再分发许可，均记录为 `blocked_asset_or_contract`。这不是“模型效果差”，而是无法进行可复现测试。
- `models/registry.json` 未修改，生产和人工测试模型集合未扩大。

## 测试环境和协议

| 字段 | 值 |
| --- | --- |
| 设备 | Ascend 310B4 / 8T |
| 地址 | `HwHiAiUser@192.168.8.178` |
| Python | `/usr/local/miniconda3/bin/python`，3.9 |
| PyTorch / torchvision | 2.1.0 / 0.16.0 |
| NPU 插件 | `torch_npu`，`torch.npu.is_available()=True` |
| 输入 | 随机 `float16`/`float32`，`[1,3,224,224]` |
| 权重 | `weights=None`，随机初始化；没有隐式下载 |
| 协议 | warmup 5，循环 10；每个候选独立进程；单个失败不得推断其他候选 |

该输入与工作台的灰度 `[1,1,128,128]` 掌纹 ROI 契约不同，所有通用骨干均需要专用灰度/通道、尺寸和 embedding head 适配器。

## 通用骨干 NPU smoke

| 模型族 | 候选 | 结果 | 证据和限制 |
| --- | --- | --- | --- |
| ResNet | `resnet18_imagenet_backbone` | `blocked_runtime_smoke` | 独立进程出现 `EZ3002 MaxPoolWithArgmaxV1`，`DT_FLOAT`/`TransData` 不支持；没有掌纹权重 |
| ResNet | `resnet50_imagenet_backbone` | `blocked_runtime_smoke` | 同一板端运行时/算子错误；不是识别精度结论 |
| VGG | `vgg16_imagenet_backbone` | `blocked_runtime_smoke` | 同一板端运行时/算子错误；不是识别精度结论 |
| DenseNet | `densenet121_imagenet_backbone` | `blocked_runtime_smoke` | 同一板端运行时/算子错误；不是识别精度结论 |
| MobileNetV2 | `mobilenet_v2_imagenet_backbone` | `blocked_runtime_resource` | 进程超时并报告 30 个 leaked semaphore；停止后 NPU 内存回落 |
| EfficientNetV2-S | `efficientnet_v2_s_imagenet_backbone` | `not_completed_after_runtime_block` | 在前序运行时阻断和资源异常后停止，未产生独立有效结果 |
| ViT-B/16 | `vit_b_16_imagenet_backbone` | `not_completed_after_runtime_block` | 同上；不能将未执行写成通过 |
| Swin-T | `swin_t_imagenet_backbone` | `not_completed_after_runtime_block` | 同上；不能将未执行写成通过 |

对 `resnet18` 另行使用 FP16 输入进行了 120 秒超时复测，仍未生成结果，并再次出现 semaphore 泄漏警告。首次同进程批量运行中曾打印部分 `passed` 字样，但首个模型的异步 NPU 错误污染了进程，所有该批次结果作废，不纳入结论。

## 专用模型和方法测试

这些项目逐项核对了候选名称、任务类型、来源和当前仓库资产。由于缺少真实 checkpoint、固定导出参数或任务适配器，没有用通用随机骨干替代它们。

| 类别 | 项目 | 结果 | 阻断原因 |
| --- | --- | --- | --- |
| ROI | ROI3Net | `blocked_asset_or_contract` | 无核验 checkpoint/ROI adapter |
| ROI/对齐 | PalmALNet | `blocked_asset_or_contract` | 需要 CROI/对齐输入输出契约；无本地权重 |
| ROI/关键点 | PKLNet、Holzweber ROI-LANet、AlignNet ROI-LANet、RobustPalmRoi | `blocked_source` 或 `blocked_license` | 来源不可复现、权重许可或输出契约不完整 |
| 分类器 | 3D IA-ResNet、ECA-MNet、PPNet、TPA-CNN、Holzweber ResNet18、Lin-Dxin ResNet18 pair | `blocked_asset_or_contract`（对应清单项保留原状态） | 分类 logits/左右手任务不能直接作为 512-D embedding；缺少可核验权重或许可 |
| 混合 embedding | Palm-ID、PVCodeNet、MSPHNet | `blocked_asset_or_contract` | 论文描述的是多模型/混合流程，无可复现本地 checkpoint 和 adapter |
| 集成/蒸馏 | MEAL | `blocked_asset_or_contract` | 方法论文，不是本项目可加载的掌纹 checkpoint |
| 掌静脉 | Kenan CNN、MPSNet | `blocked_asset_or_contract`（对应清单项为 `blocked_license`/`blocked_source`） | 需要 NIR 输入和独立 vein 契约；权重/数据许可未核验 |
| 生成模型 | Palm-GAN、PD-GAN、Diff-Palm、GenPalm | `blocked_asset_or_contract` | 生成/增强用途，不是身份 embedding 服务；缺少本地权重和生成协议 |
| 训练方法 | NAS palmprint、self-supervised EfficientNet | `blocked_asset_or_contract` | 搜索/训练方案不是固定部署模型 |
| 其他 | EDCC、KBY Palmprint/Palmvein SDK、Tencent PalmAI | `not_applicable` | CPU 算法、授权 SDK 或云 API，没有可转换的本地 NPU 模型 |

公开论文只能作为方法和任务的来源证据，不能替代权重、许可、ONNX/OM、ACL smoke 和任务指标。比如 Palm-ID 是 ViT+CNN 的移动端混合流程，ROI3Net/PalmALNet 解决 ROI/对齐，Palm-GAN/Diff-Palm 用于生成数据；它们都不是当前 API 可以直接加载的单一 512-D embedding OM。

## 现有六个 OM 候选状态

CCNet 和五个 CompNet 的 mixed-FP16 OM 已按字节数和 SHA-256 核验，确定性 ROI 的 ACL smoke 输出维度和有限性均通过。但六个独立诊断 trace 都记录 `err_ret=-512`/`driver_release_failure`，因此当前终态仍为设备运行时阻断：

```text
ccnet                     acl_failed (smoke passed; diagnostic blocked)
compnet_tongji_600        acl_failed (smoke passed; diagnostic blocked)
compnet_iitd_460          acl_failed (smoke passed; diagnostic blocked)
compnet_rest_358           acl_failed (smoke passed; diagnostic blocked)
compnet_xjtu_flash_200    acl_failed (smoke passed; diagnostic blocked)
compnet_xjtu_natural_200  acl_failed (smoke passed; diagnostic blocked)
```

这不等于六个模型的识别算法已经失败，也不等于已经完成生产验收。生产 registry 仍保持 CCNet-only，五个 CompNet 仍为 `manual_test_pending`。

## 可复现证据和后续门禁

- 28 项机器可读终态：[`candidate-final-status-20260911.json`](candidate-final-status-20260911.json)。
- 既有候选 smoke 和诊断摘要：[`candidate-campaign-20260910.md`](candidate-campaign-20260910.md)。
- 本次通用骨干原始 JSON 仅保留在板端 `/tmp/extended_model_campaign_20260911.json` 及其日志；因为包含运行时错误和资源信息，不作为公开原始日志发布。
- 若要把某个通用骨干变成真正的掌纹候选，必须先固定掌纹训练权重和许可，定义灰度 ROI 与 embedding head，导出并核验 ONNX/OM，再在板端完成 ACL、数值、任务质量、性能和生命周期门禁。
- 运行时错误复核前不继续扩大 NPU 架构批量测试；不升级驱动、CANN 或固件，不修改生产 registry。
