# CLIP 图像检索参考与 Case7 优化

*本文记录外部参考实现、适用边界和当前 Case7 的可复现实装。*

---

## 参考来源

本次实现参考两份公开材料：

- Andy 的博客《基于 CLIP 模型特征搭建简易的个人图像搜索引擎》介绍了递归导入图片、提取 CLIP 图像/文本特征、保存尺寸和日期等元数据，以及归一化向量相似度排序[^1]
- `atarss/clip-image-search` 将导入和查询拆成两个阶段。`import_images.py` 递归扫描目录并记录文件尺寸、日期和特征；`server.py` 对查询向量与图片特征做 L2 归一化后的内积，并先应用宽高、扩展名等 MongoDB 条件[^2]

该仓库为 MIT License；本项目只采用其公开的检索思路和代码结构，不复制其 MongoDB、Gradio、PyTorch 常驻模型或实验性 OCR 运行时[^3]。

## Case7 的对应实现

| 检索阶段 | Case7 当前实现 |
| --- | --- |
| 受管导入 | `photo_index.py` 递归发现、解码验证、SHA-256 去重，并把个人原图写入系统 `Pictures/ai-album/imports` |
| 元数据 | SQLite `photos` 表保存宽高、扩展名/MIME、EXIF 拍摄时间、上传时间、人脸数和可用状态 |
| 图像特征 | MobileCLIP、Chinese-CLIP、ResNet50 分别由已准入 OM 经 PyACL 执行；每个模型是独立向量空间 |
| 向量存储 | SQLite `embeddings` 是真源；每模型 `IndexIDMap2(IndexFlatIP)` 是可重建缓存，ID 与 `photos.id` 一一对应 |
| 文本检索 | `/api/search/text` 按中日韩字符自动选择 Chinese-CLIP，否则选择 MobileCLIP |
| 元数据预筛选 | 可选最小宽度、最小高度、格式和人脸条件先在 SQLite 中生成候选，再在候选向量上计算内积 |
| 输出 | FastAPI 返回照片 ID、文件名、分数和受控预览 URL；不暴露原始路径 |

无过滤条件时，搜索直接使用常驻的 FAISS 索引。带过滤条件时不把全库 FAISS top-k 结果先截断后过滤，因为这样可能把符合条件的图片排除在 top-k 之外；服务会从 SQLite 取出合格照片的向量并完整排序候选集合。这是查询正确性优先的路径，后续若图库扩大，可在保持同一合同的前提下增加带过滤器的专用 FAISS 分片。

## API 使用

普通语义查询保持兼容：

```bash
curl -X POST http://127.0.0.1:7860/api/search/text \
  -H 'Content-Type: application/json' \
  -d '{"query":"雪景","model":"auto","top_k":12}'
```

需要元数据预筛选时，在同一个请求中加入条件：

```bash
curl -X POST http://127.0.0.1:7860/api/search/text \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"房子",
    "model":"auto",
    "top_k":12,
    "min_width":1920,
    "min_height":1080,
    "extensions":["jpg","png"],
    "face_filter":"no_people"
  }'
```

响应的 `filters` 字段是服务器实际采用的条件。`results[*].score` 只在返回的 `model_id` 空间内用于排序；MobileCLIP、Chinese-CLIP 和 ResNet50 的分数不得混排或比较。

## NPU 约束

参考仓库在 CPU/CUDA 上让 PyTorch 模型常驻内存；Case7 的生产约束不同：模型由 `embedding_backend.py` 校验注册表、OM SHA-256、输入字节数和输出维度后才加载，NPU 访问由单锁串行化。FAISS 和元数据过滤在 CPU 执行，不能把这两个步骤描述为 NPU 推理。上传索引仍按单线程顺序生成三种模型 embedding，避免 310B 内存峰值不可控。

## 验证边界

本地验证应覆盖：

- 不带过滤条件的 FAISS 快速路径与既有结果合同
- 宽高、格式、人脸筛选后的候选集合正确性
- 空候选集合返回空结果而不是回退到全图库
- 非法扩展名、负尺寸和非法人脸筛选值被拒绝
- 归一化向量无 NaN/Inf，模型空间不混合

检索质量仍以 COCO-CN 固定清单的 Recall@1/3/5 报告为准；元数据预筛选是结果约束能力，不替代模型准入、Recall 或板端性能证据。OCR、多模态跨模型融合和向量数据库迁移都不属于本次优化。

## 参考资料

[^1]: Andy. *基于 CLIP 模型特征搭建简易的个人图像搜索引擎*. https://andy9999678.me/blog/archives/239
[^2]: atarss. *clip-image-search*（`import_images.py`、`server.py`）. https://github.com/atarss/clip-image-search
[^3]: atarss. *clip-image-search LICENSE*. https://raw.githubusercontent.com/atarss/clip-image-search/main/LICENSE
