# GitHub 参考与文档地图

*Case7 工程入口：记录外部参考、固定版本和各工程文档的阅读顺序。*

---

## 📚 文档地图

| 文档 | 内容 | 适用场景 |
| --- | --- | --- |
| [01 COCO-CN 测试协议](./01-coco-cn-test-protocol.md) | 数据准备、固定清单、检索指标和性能协议 | 准备或复现实验 |
| [02 板端部署与验收](./02-ascend310b4-deployment-and-acceptance.md) | 310B 环境、发布、实测结果和证据边界 | 部署服务或审计结果 |
| [03 服务 API 与 ESP32 协议](./03-album-server-api-and-esp32-protocol.md) | 手机、触摸屏和设备 HTTP 合同 | 编写客户端或排障 |
| [04 PhotoPainter 接入](./04-photopainter-7in3-integration.md) | 固件、刷写、配对和 URL Rotation | 接入 PhotoPainter |
| [05 设备策略与渲染安全](./05-device-policy-rendering-and-security.md) | cron、选图、ETag、渲染和 LAN 边界 | 修改设备策略 |
| [06 PhotoPainter 验收](./06-photopainter-deployment-and-acceptance.md) | 实机验收清单和证据模板 | 连接真实硬件 |
| [07 触摸屏 UI](./07-touchscreen-ui-and-operations.md) | 10 寸屏操作和 kiosk | 操作本机相册 |
| [08 模型与 NPU 准入](./08-model-pipeline-and-npu-admission.md) | 下载、导出、ATC、ACL 和 hash 门禁 | 迁移或更新模型 |
| [09 索引与照片生命周期](./09-index-storage-and-photo-lifecycle.md) | SQLite、FAISS、上传、迁移和删除 | 管理图库数据 |
| [10 智能选图与天气](./10-smart-selection-and-weather.md) | 选择器、天气刷新和导航历史 | 调整显示策略 |
| [11 PhotoFrame 主动推送与固件协议](./11-photoframe-active-push.md) | direct push、URL Rotation、Demo 固件差异和证据边界 | 联调 PhotoFrame 设备（E1002 历史对照）或排查固件 |
| [12 MobileCLIP 跨板兼容性](./12-mobileclip-cross-board-compatibility.md) | 8T/310B4 与 20T/310B1 的 ATC、ACL 和四格矩阵 | 更换板卡或审计 OM 可移植性 |
| [13 PhotoPainter 串口读取 IP 与 Wi-Fi 配网](./13-photopainter-serial-ip-and-wifi.md) | Windows 串口、AP/STA 判断、DHCP 地址和网页验证 | 新设备配网或 `photoframe.local` 不可达时 |

完整理论、模型原理、架构图和代码导读统一位于仓库教程 [src/experiment/case7.md](../../../src/experiment/case7.md)。本目录文档只描述如何运行、配置和验收。

## 🔗 外部参考

| 项目 | 固定版本或用途 | Case7 采用方式 |
| --- | --- | --- |
| [Apple MobileCLIP](https://github.com/apple/ml-mobileclip) | `aecfb5453d022e9deff12f81a150ea8f35194baa` | MobileCLIP-S0 图像/文本编码器，固定 batch=1 导出并转换 |
| [OFA-Sys Chinese-CLIP](https://github.com/OFA-Sys/Chinese-CLIP) | `31863c707501bf1605d36842f43deb78793dbc5d` | RN50 中文图文编码器和 tokenizer 合同 |
| [Waveshare e-Paper](https://github.com/waveshareteam/e-Paper) | `epd7in3e.py` 协议参考 | 复用 E6 初始化、BUSY、刷新和休眠时序 |
| [Ascend samples](https://github.com/Ascend/samples) | PyACL 生命周期范式 | ACL 资源、dataset/buffer 和释放顺序 |
| [COCO-CN](https://arxiv.org/abs/1805.08661) | 中文 caption 与 MS-COCO 图像 | 唯一公开测试数据集 |
| [ESP32 PhotoFrame](https://github.com/aitjcize/esp32-photoframe) | PhotoPainter URL Rotation 与 `POST /api/display-image` | 固定上游发布固件；direct push 和 URL pull 分开配置，不猜测实机固件 |

## 🛡️ 迁移边界

- 生产服务只接受已准入的 Ascend OM，不回退 CPU/PyTorch 推理。
- 模型、ONNX、OM、tokenizer、图库和报告资产不提交 Git。
- 生产 ATC 和 ACL 只在目标板端执行；本机只做纯 Python、前端和静态检查。`Ascend310B1`/20T
  兼容性实验是独立证据，不与 8T 生产准入合并。
- E6 dry-run 证明协议和帧编码正确，不代表驱动板接线后的实屏刷新通过。
- 手机、触摸屏和设备接口只建议在可信局域网使用，禁止公网暴露。
