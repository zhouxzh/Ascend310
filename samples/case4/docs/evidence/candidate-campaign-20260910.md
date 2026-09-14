# 候选测试预检摘要（2026-09-10）

本摘要只记录脱敏的测试状态。它不替代数值一致性、数据集质量或性能报告。

## 本地清单审计

- `candidate_manifest.json`：28 项，schema 校验通过。
- `candidate_campaign local --all`：为 28 项生成完整报告结构和状态索引。
- 当前静态状态计数：`audit_pass=6`、`blocked_license=10`、`blocked_source=8`、`not_applicable=4`。
- 当前生产 registry：保持 CCNet-only，未自动加入候选。
- 当前运行时候选集合：6 项（CCNet + 五个 CompNet）；其余 22 项已从 API/UI 候选矩阵归档，不会被服务加载。
- 新增的报告目录：`reports/candidates/`，被 `.gitignore` 排除。
- 标准库 `unittest`：候选编排器和清单测试通过；工作区运行时未安装 `pytest`，因此 pytest 门禁未执行。

## 开发板只读预检

- 目标：`HwHiAiUser@192.168.8.178`。
- 设备：Ascend `310B4`，aarch64，`npu-smi 25.2.0` 可用。
- 观察：`Health: Alarm`，温度 `71°C`，功耗 `0.0 W`，NPU 内存 `1968 / 15610 MB`，大页 `15 / 15`。
- Python/ACL：`/usr/local/miniconda3/bin/python` 与 `/usr/local/Ascend/ascend-toolkit/latest/python/site-packages/acl.so` 可导入。
- 服务：`127.0.0.1:7860` 当前未监听。
- 预期发行目录：`/home/HwHiAiUser/Documents/case4/releases/manual-test-1.0.0-20260818` 和 `/home/HwHiAiUser/Documents/palmprint-recognition` 均未发现 `models/om` 目录。

这一段只描述部署前的只读预检；部署发行目录并同步 OM 后，已按 `docs/01-model-evaluation-and-deployment-plan.md` 的单候选 smoke 和 10 次生命周期流程继续，结果见下节。

## 当前板端 smoke 结果

在独立 staging 目录 `/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910` 中，六个 OM 均按 `om_manifest.json` 完成字节数和 SHA-256 校验。使用不含生物特征的确定性 128x128 合成 ROI 执行单候选 NPU smoke；CCNet 运行 1 次，五个 CompNet 各运行 10 次独立 load/run/close。

| candidate | 输出 | 推理耗时观察 | 生命周期 | 状态 |
| --- | --- | ---: | --- | --- |
| `ccnet` | 2048-D，有限 | 43.59--44.55 ms（10 次） | reset/finalize 返回 0；诊断 trace 新增 `err_ret=-512` 和 driver cleanup 事件 | `blocked_faults` |
| `compnet_tongji_600` | 512-D，有限 | 6.52--7.36 ms | 10/10，reset/finalize 返回 0 | `passed` |
| `compnet_iitd_460` | 512-D，有限 | 6.53--7.28 ms | 10/10，reset/finalize 返回 0 | `passed` |
| `compnet_rest_358` | 512-D，有限 | 6.51--7.29 ms | 10/10，reset/finalize 返回 0 | `passed` |
| `compnet_xjtu_flash_200` | 512-D，有限 | 6.50--7.31 ms | 10/10，reset/finalize 返回 0 | `passed` |
| `compnet_xjtu_natural_200` | 512-D，有限 | 6.52--7.31 ms | 10/10，reset/finalize 返回 0 | `passed` |

报告保存在板端 staging 目录的 `reports/candidates/<candidate>/<run-id>/`；原始 ACL 输出和设备日志不复制到源码仓库。一次早期 `ccnet` 预检因编排器错误要求未随资产包提供的 checkpoint/ONNX 而显示 `blocked_asset`，修正为 OM-only runtime gate 后重新运行通过；该旧记录保留在板端审计索引中，不计入最终状态。

最初的直接 smoke 摘要中，五个 CompNet 尚未完成诊断 trace；CCNet 的诊断 trace（`reports/system/ccnet-traced_20260910_200528.json`）在温度 67--68°C、内存从 2551 MB 回落到 2551 MB、大页从 15/15 临时升高后回落的同一时段，发现新增 `err_ret=-512` 与 `Kthread_create not up to expectations`，collector 状态为 `blocked_faults`。随后已在正确 CANN 环境补做六项独立 trace，最终状态见下节。该事件记录为设备/运行时诊断阻断，不归因于某个模型；这些 smoke 结果也不证明 PolyU/Tongji 质量指标、100 样本数值一致性、500 次性能协议或正式生产准入，生产 registry 仍为 CCNet-only。

## 正确环境下的 ACL 重测

2026-09-11 在同一 staging 发行目录重新加载 conda 与 CANN 环境，使用确定性 `128x128` 合成 ROI，逐候选执行独立进程的 10 次 `load/run/close`。报告写入板端私有目录 `/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/candidates-corrected/`；旧的未加载 CANN 环境失败记录保留，不作为最新结果。

| candidate | cycles | 输出 | 推理耗时范围 | reset/finalize | 当前结论 |
| --- | ---: | --- | ---: | --- | --- |
| `ccnet` | 10/10 | 2048-D，有限 | 43.584--44.517 ms | 0 / 0 | `passed`（ACL smoke） |
| `compnet_tongji_600` | 10/10 | 512-D，有限 | 6.513--7.615 ms | 0 / 0 | `passed`（ACL smoke） |
| `compnet_iitd_460` | 10/10 | 512-D，有限 | 6.528--7.383 ms | 0 / 0 | `passed`（ACL smoke） |
| `compnet_rest_358` | 10/10 | 512-D，有限 | 6.502--7.297 ms | 0 / 0 | `passed`（ACL smoke） |
| `compnet_xjtu_flash_200` | 10/10 | 512-D，有限 | 6.496--7.345 ms | 0 / 0 | `passed`（ACL smoke） |
| `compnet_xjtu_natural_200` | 10/10 | 512-D，有限 | 6.512--7.266 ms | 0 / 0 | `passed`（ACL smoke） |

这次重测修正了早期报告中因环境未激活而产生的 `PyACL is unavailable` 假失败。它没有覆盖温度关联采集、完整数据集质量、100 样本数值门禁、500 次性能协议或 PolyU 全量任务，因此六个候选仍分别处于生产准入之外；CompNet 继续标记为 `manual_test_pending`，CCNet 继续作为生产默认和回滚模型。

## 六个候选的诊断 trace 终态

随后对六个候选分别运行 `collect_npu_trace.py --interval 1`，命令本身均以退出码 0 完成，模型输出和资源释放仍正常；但六个 trace 都检测到同一时间段新增的 `err_ret=-512` 与 `driver_release_failure`（板端 `Kthread_create not up to expectations`/driver cleanup 类事件）。因此六个候选的最终诊断状态统一为 `acl_failed`（设备运行时阻断），而不是模型精度失败：

| 候选 | trace 状态 | blocking_categories |
| --- | --- | --- |
| `ccnet` | `blocked_faults` | `driver_release_failure`, `err_ret_minus_512` |
| `compnet_tongji_600` | `blocked_faults` | `driver_release_failure`, `err_ret_minus_512` |
| `compnet_iitd_460` | `blocked_faults` | `driver_release_failure`, `err_ret_minus_512` |
| `compnet_rest_358` | `blocked_faults` | `driver_release_failure`, `err_ret_minus_512` |
| `compnet_xjtu_flash_200` | `blocked_faults` | `driver_release_failure`, `err_ret_minus_512` |
| `compnet_xjtu_natural_200` | `blocked_faults` | `driver_release_failure`, `err_ret_minus_512` |

原始 trace 只保留在板端 `/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/system/`。在设备运行时问题解决并重新完成诊断门禁前，生产 registry 保持 CCNet-only，manual-test 服务仍可用于受控人工功能测试。

## Staging API smoke

在同一 staging 目录使用已加载 CANN 环境短暂启动 `manual_test` profile，随后停止明确的 staging PID。`GET /api/health` 返回 `status=ok`、`transport_ready=true`、`runtime_importable=true`、`model_ready=true`，并列出六个 `admitted_model_ids`；`GET /api/bootstrap` 返回六个模型且默认 `ccnet`；静态 `/` 返回发布 bundle。健康检查本身仍标记 `inference_smoke=not_run`，真实推理证据以本文件上一节的 ACL smoke 为准。
