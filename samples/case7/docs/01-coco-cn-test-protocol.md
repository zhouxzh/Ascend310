# COCO-CN 固定测试协议

*Case7 唯一公开图片数据集的准备、完整性、检索和性能复现实验。*

---

## 📦 数据边界

Case7 只使用 COCO-CN 作为公开图片测试语料。标注压缩包从 `https://hf-mirror.com` 下载，固定版本为 `coco-cn-version1805v1.1`，SHA-256 为 `6c126cd8455363a404806e452ec75066a8fc96d73922d9357d993fcdd1d40b8a`。

准备脚本读取 COCO-CN test split 中有中英文 caption 的记录，并固定生成 500 张图像、20 条中文查询和 20 条英文查询。异常图片、路径逃逸、EXIF 和 E6 测试只使用合成 fixture，不计入检索指标。

## 🔧 准备与验证

在板端 conda/CANN 环境执行：

```bash
export HF_ENDPOINT=https://hf-mirror.com
python scripts/prepare_coco_cn.py prepare --split test --limit 500
python scripts/prepare_coco_cn.py verify
```

若板端 CA 无法验证 HF 镜像证书，只对该命令显式使用 `--insecure-hf-tls`。该选项不得写入全局环境，也不得用于其他 HTTPS 请求。

输出目录：

```text
shared/photos/datasets/coco_cn_case7/images/
shared/reports/datasets/coco_cn_case7_manifest.json
```

manifest 是后续测试的唯一真源，每条记录包含 COCO-CN ID、来源 URL、许可证、作者、文件 SHA-256、尺寸、字节数、中英文 caption 和中文标签。这里的中文标签是 COCO-CN 数据集标注，不是相册上传页面的手工 `tags` 字段。后续运行禁止重新随机抽样。

## 🔍 检索验收

```bash
python scripts/evaluate_coco_cn.py \
  --manifest shared/reports/datasets/coco_cn_case7_manifest.json \
  --output shared/reports/datasets/coco_cn_case7_retrieval.json
```

| 查询 | 路由模型 | 最低门槛 |
| --- | --- | ---: |
| 中文 20 条 | Chinese-CLIP RN50 | Recall@3 >= 0.80 |
| 英文 20 条 | MobileCLIP-S0 | Recall@3 >= 0.80 |

报告必须记录 Recall@1、Recall@3、Recall@5、每条查询的相关图片 ID、模型 ID、索引摘要和延迟 P50/P95。未通过 ACL 数值准入的模型不得生成生产检索报告。

## 📊 性能协议

```bash
python scripts/benchmark_case7.py \
  --manifest shared/reports/datasets/coco_cn_case7_manifest.json \
  --api-url http://127.0.0.1:7860
```

固定参数为单线程、20 次预热、100 次计时、3 轮重复。分别记录图像编码、文本编码、FAISS 检索、相似图 API、自动路由 API 和服务重启后的首个请求。报告必须保存 CPU/RSS/NPU 状态和原始路径，不得预填目标数字。

## 🧾 证据边界

模型 ONNX/ATC/ACL、数据集 hash、索引统计、Recall、性能、API、触摸屏和 E6 dry-run 是独立证据。`Health: Alarm`、ONNX 成功或 E6 dry-run 都不能替代其他验收项。
