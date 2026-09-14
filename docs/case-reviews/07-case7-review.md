# Case7 文档结构审查

本审查只覆盖 Case7 文档组织，不替代板端模型、API、触摸屏或电子纸验收报告。

## 当前结论

Case7 已将运行手册、工程专题和理论教程分开：

- [`samples/case7/README.md`](../../samples/case7/README.md) 负责从零运行；
- [`samples/case7/docs/README.md`](../../samples/case7/docs/README.md) 负责工程文档导航；
- [`src/experiment/case7.md`](../../src/experiment/case7.md) 负责理论教程。

原有 00-15 混合文档已合并为 7 份主题文档。旧板卡、旧 IP、旧 CANN 版本和早期设备发送记录不再作为当前操作依据。

## 当前文档主题

| 文档 | 主题 |
| --- | --- |
| 01 | 部署、启动和健康检查 |
| 02 | 照片保存、上传和索引 |
| 03 | 触摸屏、本机显示和 E6 dry-run |
| 04 | ESP32 设备 profile、唤醒、发现和注册 |
| 05 | 模型流水线、ATC 和 NPU 准入 |
| 06 | 当前 HTTP API |
| 07 | 测试、验收和故障排查 |

## 验证边界

本文不把文档重组视为功能验收。真实板端 CANN、ATC、ACL、模型质量、性能、触摸屏、ESP32 刷新和 E6 实屏结果，必须按新文档中的对应章节单独验证。
