# Case7 工程文档

_按任务查找 Case7 当前部署、照片、显示、设备、模型、API 和验收说明。_

---

这里是 Case7 的工程文档入口。按任务选择文档，不需要按文件名顺序通读。

## 文档地图

| 任务 | 文档 |
| --- | --- |
| 部署、启动、重启和健康检查 | [01-deployment-and-operation.md](01-deployment-and-operation.md) |
| 上传、存储、索引、去重和删除 | [02-photo-library-and-index.md](02-photo-library-and-index.md) |
| 10 寸触摸屏、本机显示和 E6 dry-run | [03-touchscreen-and-display.md](03-touchscreen-and-display.md) |
| Waveshare/Seeed 唤醒、发现、注册和拉图 | [04-esp32-device-management.md](04-esp32-device-management.md) |
| MobileCLIP、Chinese-CLIP、ResNet 和 NPU 准入 | [05-models-and-npu.md](05-models-and-npu.md) |
| 手机、触摸屏和 ESP32 HTTP 接口 | [06-api-reference.md](06-api-reference.md) |
| 测试、验收和故障排查 | [07-validation-and-troubleshooting.md](07-validation-and-troubleshooting.md) |

## 先读哪份

- 第一次运行：先读 [`../README.md`](../README.md)，再读 [01 部署与运行](01-deployment-and-operation.md)。
- 首页没有照片：直接读 [02 照片库与索引](02-photo-library-and-index.md)。
- 触摸屏操作：读 [03 触摸屏与本机显示](03-touchscreen-and-display.md)。
- 注册 ESP32：先唤醒设备，再读 [04 ESP32 设备管理](04-esp32-device-management.md)。
- 模型转换：读 [05 模型流水线与 NPU 准入](05-models-and-npu.md)。
- 写程序调用接口：读 [06 HTTP API](06-api-reference.md)。
- 出现异常：按 [07 验证与故障排查](07-validation-and-troubleshooting.md) 的顺序排查。

## 内容边界

| 内容 | 唯一路径 |
| --- | --- |
| 可执行运行步骤 | [`samples/case7/README.md`](../README.md) |
| Case7 理论、架构和模型原理 | [`src/experiment/case7.md`](../../../src/experiment/case7.md) |
| 当前工程命令和接口 | 本目录 01-07 |
| 跨案例审查 | [`../../../docs/README.md`](../../../docs/README.md) |

本目录只记录当前代码和当前推荐流程。旧板卡、旧 IP、旧 CANN 版本和早期设备发送实验不作为当前操作依据。

## 维护规则

- 新增主题文档使用两位数字前缀和描述性英文文件名。
- README 只做导航，不复制专题正文。
- 命令、端口、路径和 API 必须与当前代码一致。
- 真实板端数据、个人照片、模型二进制、数据库、FAISS 和日志不提交到书稿。
- 修改文档后在仓库根目录执行 `git diff --check` 和 `pnpm docs:build`。
