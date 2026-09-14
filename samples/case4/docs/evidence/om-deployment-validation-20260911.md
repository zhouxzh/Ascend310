# OM 部署要求验证（2026-09-11）

本次验证在 `HwHiAiUser@192.168.8.178`、Ascend 310B4 / 8T、CANN 8.0.0、Python 3.9 上执行。板端只使用 ACL 和 NumPy；没有安装或使用 PyTorch。验证脚本位于板端私有目录 `~/Documents/case4/research/om-deployment-validation-20260911/`，完整循环报告不进入源码仓库（最终机器可读汇总为该目录的 `validation-final.json`）。

## 验证范围和门禁

每个 OM 执行 10 次独立进程循环，每次都重新执行 ACL 初始化、设备/context 创建、模型加载、一次代表性推理、输出有限性检查、资源释放、设备 reset 和 ACL finalize。该门禁回答“OM 是否能稳定运行和释放”，不替代模型数值一致性、数据集识别率、性能排名或人工摄像头验收。

设备快照：测试前后 `npu-smi` 均显示 `310B4`、温度约 `67–68°C`、大页 `15/15`；`Health: Alarm` 作为诊断字段记录，未单独阻断本次运行。测试前停止旧服务，循环结束后恢复服务 PID，并验证 `/api/health` 与 `/api/bootstrap`。

## 13 个 OM 的结果

| 模型 | 输入 -> 输出 | OM SHA-256（板端实测） | ACL 10 次 | 当前工作台部署判断 |
| --- | --- | --- | --- | --- |
| `ccnet` | `[1,1,128,128]` -> `[1,2048]` | `8465fbf483524a1b373618e5e67b2903c522a32f0fc3b3ee6a4b40881295b7f1` | 10/10 pass | 当前服务可运行；既有 registry 模型 |
| `compnet_tongji_600` | `[1,1,128,128]` -> `[1,512]` | `b412804c403d4dc30e251a7382e1d27efe666a7595954187c762c8c3b9400d8d` | 10/10 pass | manual-test 可运行；仍 `manual_test_pending` |
| `compnet_iitd_460` | `[1,1,128,128]` -> `[1,512]` | `64737e66b226c4b77a561d82c7af840c09488a63cff289c6af0ccf7d92d4e2da` | 10/10 pass | manual-test 可运行；仍 `manual_test_pending` |
| `compnet_rest_358` | `[1,1,128,128]` -> `[1,512]` | `e197b884cd0c22942ace51727743d287d544fb98b82b895a81edcef146c41844` | 10/10 pass | manual-test 可运行；仍 `manual_test_pending` |
| `compnet_xjtu_flash_200` | `[1,1,128,128]` -> `[1,512]` | `764d9d81c7844bf41ba2054f577a1e465d6b9139134fee4ded352ff64959052c` | 10/10 pass | manual-test 可运行；仍 `manual_test_pending` |
| `compnet_xjtu_natural_200` | `[1,1,128,128]` -> `[1,512]` | `54b4a5f102b6914591d8901ee2c2b6deed44b05e33145b8e5052fd5c41ab797a` | 10/10 pass | manual-test 可运行；仍 `manual_test_pending` |
| `holzweber_resnet18_tongji` | `[1,3,224,224]` -> `[1,600]` | `d16953e44f1bd9c91348d8b9feb1dd0b19b60f372d7322b864e1350f5838587a` | 10/10 pass | 研究分类器；不兼容当前 embedding API |
| `holzweber_mobilenet_v2_tongji` | `[1,3,224,224]` -> `[1,600]` | `793c89abe5f615d7a163c2effc223e0f24d6b14bf10f406efe9e1027aff196fa` | 10/10 pass | 研究分类器；不兼容当前 embedding API |
| `holzweber_roi_lanet` | `[1,3,56,56]` -> `[1,18]` | `405ff45fb05eda1cd1f3f797694c9ea3341ddc126ba19dced6dbd2ac0d9c76a9` | 10/10 pass | 研究 ROI 回归器；不兼容当前 embedding API |
| `lin_dxin_resnet18_pair` | 两个 `[1,3,224,224]` -> 两个 `[512]` | `897853d18d79033540225b030500c062bfdbc81d3093b28557517bb4a68abd99` | 10/10 pass | 研究双输入比较器；需独立 adapter |
| `ppnet` | `[1,1,128,128]` -> `[1,512]` | `355ea31a34df8edacb458bb7fd1fc737742af8aa1650fbb5e6ef49f9d0b45b44` | 10/10 pass | 研究特征路径；未完成准入和质量校准 |
| `compnet_static_gabor` | `[1,1,128,128]` -> `[1,512]` | `603eb9831d3e22da6de86c2addc9a203bd8693d08654e83d3909401df9889f7f` | 10/10 pass | 旧别名研究 OM；不加入 canonical registry |
| `kenan_cnn_palmar_veins` | `[1,1,128,128]` -> `[1,500]` | `84b9ec803e3334a2068b80655297b541d8f55f347369f08bfc47fe2f0f74a159` | 10/10 pass | 掌静脉分类器；不兼容掌纹 embedding API |

## 工作台 API smoke

恢复服务后，`/api/health` 返回 `status=ok`、`runtime_importable=true`、`model_ready=true`，`/api/bootstrap` 返回六个当前 manual-test 模型，研究 OM 没有被自动发现。使用板端生成的匿名合成 PNG（128×128）分别请求六个模型的上传识别接口，并显式传入测试阈值 `0.75`，六个请求 HTTP 均为 200，模型推理均完成；由于模板库为空，业务结果均为“拒识 | 当前模型模板库为空”，这是预期结果，不是模型或 API 错误。

另测到：CompNet 请求省略 `threshold` 时会返回 503“模型缺少校准阈值”。因此五个 CompNet 目前只能算 OM/ACL 和显式阈值 smoke 通过，前端默认路径仍未满足完整部署要求；需要先完成模型专属阈值校准并写入 registry，再进行下一轮 UI/API 验收。服务仍保持 `ccnet` 默认，五个 CompNet 保持 `manual_test_pending`。

## 是否满足部署要求

结论分两层：

1. **OM 运行时层：满足。** 13/13 OM 均能在目标 310B4 上完成 10/10 独立 ACL 生命周期，输出 shape/dtype 可读且输出有限，未观察到本次循环的异常退出、设备 reset 或清理失败。
2. **当前掌纹工作台正式准入层：CCNet 可按默认路径运行，CompNet 仍未完成部署门禁。** 六个模型的 OM、SHA 和输入契约正常；CCNet 的默认 API 路径可用，CompNet 只有显式阈值 smoke 可用，缺少模型专属校准阈值。六个研究 OM 不加入 `/api/bootstrap`、模板命名空间或 `models/registry.json`：分类器、ROI、掌静脉和双输入比较器需要各自 adapter、数据集指标和 UI/API 协议；PPNet 虽输出 512 维，也尚无校准阈值、数值一致性和识别质量准入证据。

本次没有运行新的 PolyU/Tongji 全量质量评测、500 次性能基准、摄像头验收或人工模板注册，因此不能把本报告写成“所有模型已经正式部署”。生产 registry 未修改；板端完整报告和每轮 JSON 仍保留在上述私有目录。
