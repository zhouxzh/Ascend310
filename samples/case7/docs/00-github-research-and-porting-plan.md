# GitHub 参考与迁移边界

*Case7 工程入口：记录外部参考、固定版本和各工程文档的阅读顺序。*

---

## 📚 阅读入口

本文档保留为 GitHub 参考与迁移边界说明，不再重复列出全部工程文档。按任务寻找文档请使用 [Case7 工程文档总索引](README.md)；完整理论、模型原理、架构图和代码导读统一位于 [src/experiment/case7.md](../../../src/experiment/case7.md)。

## 🔗 外部参考

| 项目 | 固定版本或用途 | Case7 采用方式 |
| --- | --- | --- |
| [Apple MobileCLIP](https://github.com/apple/ml-mobileclip) | `aecfb5453d022e9deff12f81a150ea8f35194baa` | MobileCLIP-S0 图像/文本编码器，固定 batch=1 导出并转换 |
| [OFA-Sys Chinese-CLIP](https://github.com/OFA-Sys/Chinese-CLIP) | `31863c707501bf1605d36842f43deb78793dbc5d` | RN50 中文图文编码器和 tokenizer 合同 |
| [Waveshare e-Paper](https://github.com/waveshareteam/e-Paper) | `epd7in3e.py` 协议参考 | 复用 E6 初始化、BUSY、刷新和休眠时序 |
| [Ascend samples](https://github.com/Ascend/samples) | PyACL 生命周期范式 | ACL 资源、dataset/buffer 和释放顺序 |
| [COCO-CN](https://arxiv.org/abs/1805.08661) | 中文 caption 与 MS-COCO 图像 | 唯一公开测试数据集 |
| [ESP32 PhotoFrame](https://github.com/aitjcize/esp32-photoframe) | PhotoPainter URL Rotation 与 `POST /api/display-image` | 固定上游发布固件；direct push 和 URL pull 分开配置，不猜测实机固件 |
| [atarss/clip-image-search](https://github.com/atarss/clip-image-search) | CLIP 导入、元数据过滤和余弦排序参考 | 只采用检索分层思想，不复制 MongoDB/Gradio/OCR |
| [Andy 的 CLIP 图像搜索博客](https://andy9999678.me/blog/archives/239) | 个人图库导入、特征存储和跨模态分数解释 | 用于教程中的算法动机与分数边界 |

## 🛡️ 迁移边界

- 生产服务只接受已准入的 Ascend OM，不回退 CPU/PyTorch 推理。
- 模型、ONNX、OM、tokenizer、图库和报告资产不提交 Git。
- 生产 ATC 和 ACL 只在目标板端执行；本机只做纯 Python、前端和静态检查。`Ascend310B1`/20T
  兼容性实验是独立证据，不与 8T 生产准入合并。
- E6 dry-run 证明协议和帧编码正确，不代表驱动板接线后的实屏刷新通过。
- 手机、触摸屏和设备接口只建议在可信局域网使用，禁止公网暴露。
