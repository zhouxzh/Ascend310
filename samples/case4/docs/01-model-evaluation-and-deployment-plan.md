# 01 - 模型评测与人工测试发布方案

## 1. 目标

当前版本冻结为人工测试发布。服务只使用 NPU `mixed_fp16` embedding；不在板端运行 CPU、EDCC、origin 或离线候选比较。

`production` profile 只返回正式准入模型。`manual_test` profile 在每次请求前重新校验六个模型的输入输出契约、OM/ONNX/checkpoint 字节数和 SHA-256，然后允许人工功能测试。五个 CompNet 的 `manual_test_pending=true` 必须显示在 API 和界面中。

## 2. 模型范围

| 模型 | profile | 精度 | 人工测试状态 |
| --- | --- | --- | --- |
| CCNet | production/manual_test | mixed_fp16 | 默认/回滚模型 |
| compnet_tongji_600 | manual_test | mixed_fp16 | manual_test_pending |
| compnet_iitd_460 | manual_test | mixed_fp16 | manual_test_pending |
| compnet_rest_358 | manual_test | mixed_fp16 | manual_test_pending |
| compnet_xjtu_flash_200 | manual_test | mixed_fp16 | manual_test_pending |
| compnet_xjtu_natural_200 | manual_test | mixed_fp16 | manual_test_pending |

模型 ID 必须使用上述 canonical ID。模板命名空间固定为 `<model_id>__npu__mixed_fp16`。

## 3. 资产和契约门禁

人工测试前必须通过：

- `om_manifest.json` 中的文件大小和 SHA-256；
- registry/candidate manifest 中的输入形状 `[1,1,128,128]`；
- 灰度输入和候选声明的预处理范围；
- 512 维、cosine embedding 输出；
- 模型专属阈值和 NPU/mixed-FP16 设置；
- `/api/health`、`/api/bootstrap` 和静态 bundle 检查。

人工测试不重新生成 OM、不升级 CANN、不改变驱动，也不自动下载数据集。

## 4. 六模型人工测试

在 `PALMPRINT_PROFILE=manual_test` 下逐一执行：

1. 切换模型并确认 bootstrap 返回同一 canonical ID。
2. 上传合成 ROI 或匿名测试图，确认识别请求返回 HTTP 200 和模型 ID。
3. 注册三个模板，查询模板，使用同一模型识别命中。
4. 删除模板，确认查询为空；重启服务后确认数据状态符合预期。
5. 记录 OM SHA、请求时间、HTTP 状态、模型耗时和模板命名空间。

本阶段不运行新的 PolyU 排名、全量性能排名或转换任务。已有评测结果仅作为来源和契约审计信息，不作为本次人工通过结论。

## 5. 通用故障处理

| 现象 | 处理 |
| --- | --- |
| PyACL 导入失败 | 在同一 shell 重新激活 conda/CANN，确认 `sys.executable` 和 `acl.__file__` 来自同一环境 |
| 模型缺失或 SHA 不匹配 | 停止服务，按受控资产来源获取文件并核验 `om_manifest.json`，不在运行时覆盖文件 |
| 模板未找到 | 核对模型 ID、`npu/mixed_fp16`、模板 key 和模板目录，确认注册/识别使用同一 namespace |
| 摄像头卡顿 | 检查设备占用、MJPG、实际分辨率；先降低到 960×540 或 1280×720 |
| 健康检查通过但推理失败 | 再做一次真实上传/摄像头 smoke；health 不主动加载 NPU |
| ACL 异常退出、设备 reset、进程意外退出 | 停止测试并保存 PID、时间、模型和报告摘要；重启当前版本，必要时回退 CCNet；冻结版本，不升级 |
| `Health: Alarm` | 只记录状态，不能单独推断模型失败或成功；继续依据实际请求结果判定 |

具体日志只保存在板端私有证据目录，不在公开文档中展开历史时间线或个人设备信息。

## 6. 版本冻结

人工测试期间不修改 `APP_VERSION`、release ID、依赖锁、驱动、CANN 或准入策略。发现问题时只建立问题记录并回退到 CCNet；人工测试全部完成后，再单独规划下一版本修复和重新验收。
