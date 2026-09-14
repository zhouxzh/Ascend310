# 03 - 全候选模型审计与测试状态

本文件对应 `candidate_manifest.json` 的 28 项完整清单。当前运行时 active candidate 只有 CCNet 和五个 CompNet；其余 22 项从 API/UI 候选矩阵归档，不参与部署。测试按任务类型分轨：embedding、分类器、ROI、掌静脉和 SDK/代码不共享输入输出契约。状态由
`python -m tools.offline.candidate_campaign local --all` 生成的脱敏索引补充；它不修改
`models/registry.json`，也不把候选加入生产或 `manual_test` 模型选择。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| `audit_pass` | 来源、任务元数据和当前声明资产通过静态审计，尚未代表 ONNX、ATC、ACL 或质量通过 |
| `blocked_source` | 来源仍是分支或标签，未固定不可变 commit |
| `blocked_license` | 许可证或权重/数据再分发条件未明确 |
| `blocked_asset` | checkpoint、ONNX 或 OM 缺失、不是实际文件或哈希不一致 |
| `needs_reimplementation` | 尚无可复现导出器或任务适配器 |
| `not_applicable` | 闭源 SDK、云服务或 EDCC 不存在可转换 NPU 模型契约 |
| `research_ready` | 适用任务的全部研究门禁和板端证据通过；仍不等于生产准入 |

缺失资产、许可证或板端前置条件必须保持阻断状态，不能写成模型不支持或测试通过。

## 28 项候选矩阵

| ID | 任务 | 模态 | 当前范围 | 静态状态 | 下一门禁 |
| --- | --- | --- | --- | --- | --- |
| `ccnet` | embedding | palmprint | active | `audit_pass` | ACL smoke、数值、质量、生命周期 |
| `compnet_tongji_600` | embedding | palmprint | conversion_candidate | `audit_pass` | 310B4 ACL、质量和生命周期 |
| `compnet_iitd_460` | embedding | palmprint | conversion_candidate | `audit_pass` | 310B4 ACL、质量和生命周期 |
| `compnet_rest_358` | embedding | palmprint | conversion_candidate | `audit_pass` | 310B4 ACL、质量和生命周期 |
| `compnet_xjtu_flash_200` | embedding | palmprint | conversion_candidate | `audit_pass` | 310B4 ACL、质量和生命周期 |
| `compnet_xjtu_natural_200` | embedding | palmprint | conversion_candidate | `audit_pass` | 310B4 ACL、质量和生命周期 |
| `ppnet` | classifier | palmprint | conversion_candidate | `blocked_source` | 固定 commit、获取并核验权重 |
| `holzweber_resnet18_tongji` | classifier | palmprint | conversion_candidate | `blocked_source` | 固定 commit、权重和数据许可 |
| `holzweber_roi_lanet` | ROI | palmprint | audit_only | `blocked_source` | 固定 commit、ROI adapter |
| `lin_dxin_resnet18_pair` | classifier | palmprint | conversion_candidate | `blocked_license` | 获得代码/权重许可并固定来源 |
| `ee_prnet` | embedding | palmprint | conversion_candidate | `blocked_license` | 获得 MatConvNet 权重和许可 |
| `alignnet_roi_lanet` | ROI | palmprint | audit_only | `blocked_license` | 获得权重许可和 ROI 契约 |
| `kenan_cnn_palmar_veins` | vein_embedding | palm_vein | separate_modality | `blocked_license` | 核验 Keras LFS 权重及 NIR 数据许可 |
| `edcc` | code | palmprint | active | `not_applicable` | 仅 CPU 离线基线 |
| `mpsnet` | vein_embedding | palm_vein | audit_only | `blocked_source` | 固定 commit、NTUST 数据协议和权重 |
| `pklnet` | ROI | palmprint | audit_only | `blocked_source` | 固定 commit、可用 checkpoint 和 adapter |
| `co3net` | embedding | palmprint | audit_only | `blocked_license` | 明确仓库许可、权重和契约 |
| `tpa_cnn` | classifier | palmprint | audit_only | `blocked_source` | 固定 commit并获取 checkpoint |
| `robust_palm_roi` | ROI | palmprint | audit_only | `blocked_license` | 明确许可证、权重和输出契约 |
| `glnet` | embedding | palmprint | audit_only | `blocked_license` | 恢复可验证源码、权重和许可证 |
| `palmwildnet` | embedding | palmprint | audit_only | `blocked_license` | 核验 checkpoint 和数据集条款 |
| `palmnet` | embedding | palmprint | audit_only | `blocked_source` | 固定 GPL 源码 revision 并重实现导出 |
| `fusionnet` | embedding | palmprint_plus_finger_texture | audit_only | `blocked_source` | 固定 revision；多模态不进入单掌纹矩阵 |
| `jpfa` | embedding | palmprint | audit_only | `blocked_license` | 明确 TensorFlow 源码/权重许可 |
| `ddh` | embedding | palmprint | audit_only | `blocked_license` | 明确 TensorFlow 源码/权重许可 |
| `kby_palmprint_sdk` | SDK | palmprint | audit_only | `not_applicable` | 只做授权 SDK API smoke |
| `kby_palmvein_sdk` | SDK | palm_vein | audit_only | `not_applicable` | 只做授权 SDK API smoke |
| `tencent_palm_ai` | SDK | palmprint_plus_palm_vein | audit_only | `not_applicable` | 仅云服务适用性记录 |

上述是完整源码资产审计基线；运行时只保留 6 个 active candidate，22 个 blocked/not-applicable 项目不会被服务加载。板端运行结果必须通过候选专属报告覆盖，不能用表中静态状态替代。

## 板端候选测试终态

截至 2026-09-11，28 项均有独立报告目录和完整的报告文件契约。六个具备实际 OM 的 embedding 候选已在 Ascend 310B4 / 8T、正确 conda/CANN 环境中完成 10 次独立 ACL load/run/close；输出维度、有限性和 `acl_reset_device`/`acl_finalize` 均通过。该结果是 `ACL smoke` 证据，不是完整质量或生产准入结果。随后六项独立诊断 trace 均观察到同一类 `err_ret=-512`/driver cleanup 事件，因此六项最终诊断状态为 `acl_failed`（设备运行时阻断），而非模型质量失败。

其余 22 项已经完成适用性测试：8 项因来源 revision 未固定为 `blocked_source`，10 项因代码/权重/数据许可未明确为 `blocked_license`，4 项（EDCC、两个授权 SDK、Tencent 云服务）没有可转换的 NPU 模型契约而为 `not_applicable`。这些状态是可复现的测试终态，不是“尚未查看”的审计占位；只有补齐对应前置条件后才会创建下一版本测试任务。

六个运行候选的最新板端 ACL 报告位于板端私有目录 `/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/candidates-corrected/`，源码仓库只保留脱敏摘要。生产 `models/registry.json` 仍然只包含 CCNet；五个 CompNet 保持 `manual_test_pending`。

## 已核验资产

CCNet 和五个 CompNet 的 checkpoint/ONNX/混合 FP16 OM 已在当前工作区按 manifest 字节数和 SHA-256 核对。五个 CompNet 仍保持 `manual_test_pending`，不因 OM 存在而进入生产 registry。FP32 origin 路径是诊断信息，不是生产精度。

## 报告位置与边界

候选编排器为：

```bash
python -m tools.offline.candidate_campaign inventory --all
python -m tools.offline.candidate_campaign local --all
python -m tools.offline.candidate_campaign report --all
```

每项报告位于被 Git 忽略的 `reports/candidates/<candidate-id>/<run-id>/`，包含来源许可、资产哈希、契约、本地参考、ATC、ACL、数值、质量、性能、生命周期和最终状态文件。原始日志、真实图像、数据集、模板和完整板端诊断只保留在板端私有目录。

生产 registry 仍只保存已准入 NPU embedding。候选接口 `/api/candidates` 只读展示清单和最新状态，不启动离线比较任务。

## 扩展模型族测试

针对通用 ResNet、VGG、DenseNet、MobileNetV2、EfficientNetV2、ViT、Swin，以及搜索结果中的 ROI、分类器、掌静脉、GAN/扩散和 Palm-ID，已生成独立的分任务报告：
[`evidence/extended-model-campaign-20260911.md`](evidence/extended-model-campaign-20260911.md)。通用骨干在板端只使用 `weights=None` 做 NPU 架构 smoke；当前 CANN/torch_npu 测试通道出现算子错误、超时和资源泄漏，结果标为运行时阻断。专用论文模型因缺少可核验权重、许可或输入输出适配器而阻断。以上项目没有进入 `candidate_manifest.json` 的生产状态，也没有修改 `models/registry.json`。
