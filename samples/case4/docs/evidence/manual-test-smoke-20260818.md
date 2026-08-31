# 2026-08-18 人工测试发布 API 冒烟摘要

这是 `manual_test` profile 的脱敏功能冒烟，不是识别精度、全量数据集、性能排名、摄像头或触摸屏验收。输入是固定的几何合成 ROI，不含真实掌纹；原始服务日志、模板文件和密钥仍保留在板端私有目录。

## 环境

- 设备：Ascend 310B4 / 8T
- 精度：NPU `mixed_fp16`
- profile：`manual_test`
- 默认模型：`ccnet`
- 资产：六个 OM 按 `om_manifest.json` 的字节数和 SHA-256 校验
- `/api/health`：HTTP 200，`runtime_importable=true`、`template_store_ready=true`
- `/api/bootstrap`：HTTP 200，返回 CCNet 和五个 `manual_test_pending` CompNet

## 合成 ROI 闭环

| 模型 | 上传识别 | 注册 3 样本 | 模板查询 | 注册后识别 | 删除 | 模型耗时参考 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ccnet` | 200 | 200 | 200 | 200，命中 | 200 | 约 43.5 ms |
| `compnet_tongji_600` | 200 | 200 | 200 | 200，命中 | 200 | 约 6.5 ms |
| `compnet_iitd_460` | 200 | 200 | 200 | 200，命中 | 200 | 约 6.6 ms |
| `compnet_rest_358` | 200 | 200 | 200 | 200，命中 | 200 | 约 6.5 ms |
| `compnet_xjtu_flash_200` | 200 | 200 | 200 | 200，命中 | 200 | 约 6.5 ms |
| `compnet_xjtu_natural_200` | 200 | 200 | 200 | 200，命中 | 200 | 约 6.5 ms |

查询和删除均使用同一个 canonical model ID 与
`<model_id>__npu__mixed_fp16` namespace。删除后六个模型的模板查询均为空；服务重启后 health/bootstrap 仍返回 200，六模型列表和 `manual_test_pending` 状态保持一致。

## 未完成项目

- 真实人工掌纹图像测试未记录到源码包。
- 1280×720、1024×768、375×812 触摸屏排版检查待操作者确认。
- 摄像头 100 帧预览、拍照识别和设备释放待操作者确认。
- 未运行新的 PolyU 全量、性能排名或转换任务。

本摘要不能把五个 CompNet 的 `manual_test_pending` 改写为稳定准入，也不改变生产 profile 默认只使用 CCNet 的策略。
