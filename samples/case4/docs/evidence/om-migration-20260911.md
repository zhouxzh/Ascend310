# OM 迁移记录（2026-09-11）

本记录只描述在 Ascend 310B4 / 8T 开发板上完成的研究资产迁移，不代表生产准入，也不改变 `models/registry.json`。板端只使用 ATC 和 ACL/NumPy smoke；没有在板端使用 PyTorch 运行推理。

## 迁移范围

板端原有生产/人工测试目录中的 `ccnet` 和五个 CompNet OM 保持不变；本次新增下面六个研究 OM。另有源码目录中的旧 `compnet_static_gabor_mixed_fp16.om` 在本次验证中单独复制到板端私有目录复测。当前板端验证清单共 13 个 OM 资产，但只有原有六个 embedding 模型由工作台服务加载。

首批具备可验证官方 checkpoint、可复现网络结构和明确输入输出契约的候选完成了板外 ONNX 导出与板端 ATC：

| 模型 | 任务 | 输入 | 输出 | ONNX | OM | ACL smoke |
| --- | --- | --- | --- | --- | --- | --- |
| `holzweber_resnet18_tongji` | Tongji 600 类分类器 | `[1,3,224,224]` | `[1,600]` logits | `d136a5eb46b2b46a864e5d047be9a004921d6fcf3c700f118532aad53801a7ca` | `d16953e44f1bd9c91348d8b9feb1dd0b19b60f372d7322b864e1350f5838587a` | pass |
| `holzweber_mobilenet_v2_tongji` | Tongji 600 类分类器 | `[1,3,224,224]` | `[1,600]` logits | `e02c070fbda570a019b7e3c77618681f954807921ece8fedf30239858c0f0879` | `793c89abe5f615d7a163c2effc223e0f24d6b14bf10f406efe9e1027aff196fa` | pass |
| `holzweber_roi_lanet` | ROI 回归器 | `[1,3,56,56]` | `[1,18]` theta | `1d3f8a63287a6e06e842672cdc2e8d3837ae6fa83c022e340ef0a7aa9991e7e3` | `405ff45fb05eda1cd1f3f797694c9ea3341ddc126ba19dced6dbd2ac0d9c76a9` | pass |
| `lin_dxin_resnet18_pair` | 双输入特征比较器 | 两个 `[1,3,224,224]` | 两个 `[512]` embedding | `aa9cf0763c9071d3a73cb76536616fce5009000af764ba7b13ff81059d3b64d6` | `897853d18d79033540225b030500c062bfdbc81d3093b28557517bb4a68abd99` | pass（专用双输入 runner） |
| `ppnet` | Tongji 特征编码器 | `[1,1,128,128]` | `[1,512]` embedding | `04687fb75de155f3fbdb5b8b4a65db4c1bbd79063dc0b7f7bc66a786766f470d` | `355ea31a34df8edacb458bb7fd1fc737742af8aa1650fbb5e6ef49f9d0b45b44` | pass |
| `kenan_cnn_palmar_veins` | 500 类掌静脉分类器 | `[1,1,128,128]` NCHW wrapper | `[1,500]` logits | `449b9529c847e0a8647c3f2dc1dd2a8adea3bcd34830fc725827f201645e2417` | `84b9ec803e3334a2068b80655297b541d8f55f347369f08bfc47fe2f0f74a159` | pass |

板端研究资产目录：

```text
/home/HwHiAiUser/Documents/case4/research/holzweber-20260911/
├── onnx/
├── om/
├── logs/
└── reports/
```

其他新增资产目录：

```text
/home/HwHiAiUser/Documents/case4/research/lin-dxin-20260911/
/home/HwHiAiUser/Documents/case4/research/ppnet-20260911/
/home/HwHiAiUser/Documents/case4/research/kenan-veins-20260911/
```

六个研究 OM 均由纯 ACL + NumPy 工具执行，输出有限，模型描述的输入输出形状与导出契约一致；每个研究 OM 后续又完成 10 次独立进程的 load/run/close/reset/finalize 循环。单次观测推理时间只作为运行时 smoke，不是识别精度或业务延时结论。Lin-Dxin 的双输入/双输出契约由专用纯 ACL/NumPy runner 验证，清理正常。

## 边界与未完成项

- 这些研究模型是分类器、ROI、双输入比较器、掌静脉模型或未准入特征路径，不是当前掌纹 embedding API 的已验收实现，不能直接加入 `/api/bootstrap`、模板命名空间或生产 registry。
- 生产仍只使用现有 CCNet/CompNet registry；本次没有修改 registry、候选准入状态或前端模型列表。
- 板端临时 checkpoint、导出脚本和 PyTorch 文件已删除；板端保留 ONNX、OM、ATC 日志和 smoke 报告。
- `EE-PRNet`、AlignNet 等候选没有在本次迁移中伪造转换：缺少可固定的实际权重、许可证/框架契约，或需要板外专用导出器。它们仍是 `blocked_*`/待研究资产，不能因为没有 OM 就写成“模型不支持”。
- Kenan 掌静脉 H5 已实际取得并转换；原始 ONNX 因含两个 `opset_import` 被 CANN 8.0 的 ATC 拒绝，删除未使用的 `ai.onnx.ml` 元数据后重新转换成功。板端 OM 仅用于掌静脉独立研究，不进入掌纹识别服务。
- ResNet/ViT/Swin/GAN 等论文或通用骨干名称本身不是可部署资产；只有真实 checkpoint、导出图、输入输出契约和板端 ATC/ACL 证据齐全时，才会新增 OM。

## 复核方式

板端研究资产的哈希可用：

```bash
sha256sum /home/HwHiAiUser/Documents/case4/research/holzweber-20260911/onnx/*
sha256sum /home/HwHiAiUser/Documents/case4/research/holzweber-20260911/om/*
```

服务健康检查仍返回原有六个已准入模型；研究 OM 不会被服务自动发现或加载。
