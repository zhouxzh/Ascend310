# Case7 工程文档总索引

_先按任务选择一个入口。本文档只负责导航；理论、命令细节和验收证据分别保留在对应源稿与专题文档中。_

---

## 📍 三个必须知道的入口

| 入口 | 用途 | 适合谁 |
| --- | --- | --- |
| [`../README.md`](../README.md) | 启动 310B 服务、上传照片、打开触摸屏和最短排障 | 第一次运行的人 |
| [`../../../src/experiment/case7.md`](../../../src/experiment/case7.md) | 架构、模型、NPU 迁移、数据流、E6 和代码导读 | 教材读者、开发者 |
| 本页 | 按任务定位下面的工程文档 | 所有人 |

## 🧭 按任务查找

| 现在要做什么 | 首选文档 | 还需要看 |
| --- | --- | --- |
| 查外部项目和采用边界 | [00 GitHub 参考与迁移边界](00-github-research-and-porting-plan.md) | [15 CLIP 检索参考](15-clip-image-search-reference-and-optimization.md) |
| 准备或上传 COCO-CN | [01 COCO-CN 测试协议](01-coco-cn-test-protocol.md) | [09 索引与照片生命周期](09-index-storage-and-photo-lifecycle.md) |
| 部署 310B 服务 | [02 板端部署与验收](02-ascend310b4-deployment-and-acceptance.md) | `scripts/deploy_ascend8t.sh`、`scripts/run_smart_album_service.sh` |
| 调用手机/API | [03 API 与 ESP32 协议](03-album-server-api-and-esp32-protocol.md) | [05 策略、渲染与安全](05-device-policy-rendering-and-security.md) |
| 操作 10 寸触摸屏 | [07 触摸屏 UI 与操作](07-touchscreen-ui-and-operations.md) | [09 索引与照片生命周期](09-index-storage-and-photo-lifecycle.md) |
| 读不到照片或上传失败 | [07 触摸屏 UI 与操作](07-touchscreen-ui-and-operations.md) | [09 索引与照片生命周期](09-index-storage-and-photo-lifecycle.md) |
| 注册 Waveshare / Seeed 设备 | [14 唤醒与发现](14-wake-and-discover-esp32-photoframes.md) | [13 串口读取 IP 与 Wi-Fi](13-photopainter-serial-ip-and-wifi.md)、[04 PhotoPainter 接入](04-photopainter-7in3-integration.md) |
| 设备轮播、ETag、JPEG | [05 策略、渲染与安全](05-device-policy-rendering-and-security.md) | [03 API 与 ESP32 协议](03-album-server-api-and-esp32-protocol.md) |
| 设备主动推送或固件不匹配 | [11 PhotoFrame 主动推送](11-photoframe-active-push.md) | [04 PhotoPainter 接入](04-photopainter-7in3-integration.md) |
| 读取 PhotoPainter IP | [13 串口读取 IP 与 Wi-Fi](13-photopainter-serial-ip-and-wifi.md) | [14 唤醒与发现](14-wake-and-discover-esp32-photoframes.md) |
| 设备睡眠后重新发现 | [14 唤醒与发现](14-wake-and-discover-esp32-photoframes.md) | [13 串口读取 IP 与 Wi-Fi](13-photopainter-serial-ip-and-wifi.md) |
| 修改日期、天气、重复抑制和选图 | [10 智能选图与天气](10-smart-selection-and-weather.md) | [05 策略、渲染与安全](05-device-policy-rendering-and-security.md) |
| 下载、导出、ATC、ACL 准入 | [08 模型流水线与 NPU 准入](08-model-pipeline-and-npu-admission.md) | [12 8T/20T 跨板兼容性](12-mobileclip-cross-board-compatibility.md) |
| 更换 8T/20T 开发板 | [12 8T/20T 跨板兼容性](12-mobileclip-cross-board-compatibility.md) | [02 板端部署与验收](02-ascend310b4-deployment-and-acceptance.md) |
| E6 dry-run 或真实刷新排查 | [06 PhotoPainter 部署与验收](06-photopainter-deployment-and-acceptance.md) | 教程的 E6 章节；真实刷新仍需硬件证据 |

## 🗂️ 当前操作文档

下面这些文档描述当前 Case7 的运行方式。操作时优先从这里进入；文中的 IP、版本和路径以文档开头的环境表及最新板端报告为准。

| 编号 | 文档 | 负责的问题 |
| --- | --- | --- |
| 01 | [COCO-CN 固定测试协议](01-coco-cn-test-protocol.md) | 唯一公开测试集、固定清单和检索指标 |
| 02 | [Ascend 310B4 部署与验收](02-ascend310b4-deployment-and-acceptance.md) | 310B 服务、模型、索引和当前板端状态 |
| 03 | [相册服务器 API 与 ESP32 协议](03-album-server-api-and-esp32-protocol.md) | 手机、触摸屏、设备 HTTP 合同 |
| 04 | [PhotoPainter 7.3 英寸接入](04-photopainter-7in3-integration.md) | Waveshare 固件、方向和 URL Rotation |
| 05 | [设备策略、渲染与安全](05-device-policy-rendering-and-security.md) | 轮播 cron、选图、ETag、JPEG 和 LAN 边界 |
| 07 | [触摸屏 UI 与操作](07-touchscreen-ui-and-operations.md) | 照片优先界面、上传、kiosk 和触摸排障 |
| 08 | [模型流水线与 NPU 准入](08-model-pipeline-and-npu-admission.md) | HF 镜像、ONNX、ATC、ACL、hash 和单线程约束 |
| 09 | [索引存储与照片生命周期](09-index-storage-and-photo-lifecycle.md) | SQLite、FAISS、去重、EXIF、删除和原图位置 |
| 10 | [智能选图与天气](10-smart-selection-and-weather.md) | 日期、天气、语义评分和切图延迟 |
| 13 | [PhotoPainter 串口读取 IP 与 Wi-Fi](13-photopainter-serial-ip-and-wifi.md) | 串口日志、DHCP、网页验证和地址变化 |
| 14 | [唤醒与发现两类 ESP32 电子相册](14-wake-and-discover-esp32-photoframes.md) | 实体唤醒、mDNS/IP 验证和配对 |

## 🧾 历史证据与兼容实验

以下文档不是当前默认操作入口。它们保留历史板卡、旧固件、旧 IP、主动推送或跨板实验的原始边界，不能把其中的示例地址或结论直接套到新设备。

| 文档 | 内容 | 使用方式 |
| --- | --- | --- |
| [06 PhotoPainter 部署与验收](06-photopainter-deployment-and-acceptance.md) | 实机验收批次、E6 边界和历史 E1002 对照 | 查证据，按日期阅读 |
| [11 PhotoFrame 主动推送与固件协议](11-photoframe-active-push.md) | direct push、URL Rotation、Demo 固件和旧实验 | 先确认固件端点，再做只读验证 |
| [12 MobileCLIP 8T/20T 跨板兼容性](12-mobileclip-cross-board-compatibility.md) | 310B4/8T 与 310B1/20T 的隔离矩阵 | 只用于板卡兼容性审计 |
| [15 CLIP 图像检索参考与优化](15-clip-image-search-reference-and-optimization.md) | 博客和开源仓库的算法参考 | 理解设计取舍，不是部署命令 |

## 🧠 理论与工程边界

- 架构图、模型结构图、NPU 迁移链路、数据模型、E6 原理和代码导读只维护在 [`src/experiment/case7.md`](../../../src/experiment/case7.md)。工程文档只给出运行所需的摘要和链接。
- 生产服务是 FastAPI，固定示例端口为 `7860`；任何出现 Gradio 的地方只代表历史参考或外部项目，不是当前运行依赖。
- 生产推理只接受已准入 OM，不能用 CPU/PyTorch fallback 替代 NPU 结果。照片、模型、SQLite、FAISS 和报告是运行资产，不应复制到 Git。
- `observed-pass`、`observed-fail`、`inferred` 和 `untested` 是证据状态，不是产品宣传标签；只有指定硬件、版本、输入和命令下的实测才属于 `observed-pass`。

## 🛠️ 文档维护

1. 先改代码或原始报告，再更新本文档和案例 README 的摘要。
2. 新增专题使用两位数字前缀；若只是历史记录，在标题和首段写明日期、硬件和“历史”边界。
3. 不删除旧证据文件，不把历史 IP、旧端口、固件端点或性能数字复制到当前操作章节。
4. 修改 `src/experiment/case7.md` 后运行 `pnpm docs:build`；不要手改 `latex/`、`.vuepress/.temp/` 或 `.vuepress/dist/`。

完整仓库的文档入口见 [`仓库文档总索引`](../../../docs/README.md)，全案例审查见 [`case-reviews/00-index.md`](../../../docs/case-reviews/00-index.md)。

