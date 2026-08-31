# 索引存储与照片生命周期

*SQLite 元数据、逐模型 FAISS 索引、上传校验、迁移和原图保护。*

---

## 💾 数据真源

SQLite 保存照片与 embedding 元数据：

| 表 | 关键字段 | 作用 |
| --- | --- | --- |
| `photos` | `id`、`filepath`、`sha256`、`available`、`width`、`height`、`capture_time`、`tags` | 原图和生命周期状态；`tags` 仅保留为旧库兼容元数据 |
| `embeddings` | `(photo_id, model_id)`、`dimension`、`vector` | 每模型归一化向量 |
| `display_state` | 显示 profile、current photo、pause、revision | 本机/设备显示恢复 |
| `display_history` | profile、photo、slot、sequence | 上一张和重复抑制 |
| `jobs` | job id、status、progress、error | 上传和索引任务 |

每个模型单独使用 `IndexIDMap2(IndexFlatIP)`，FAISS 只是可重建检索缓存；SQLite 的照片 ID 和 embedding 数量用于启动校验。

## 📥 导入校验

服务接受允许目录递归扫描和 multipart 多图上传。每张照片必须通过：

- 真实路径在受管目录内，拒绝目录逃逸和符号链接逃逸；
- 普通网页/API 上传不设置文件字节数或单次张数上限；解码后的单张图片仍不得超过 50 MP，以控制 8T 板端内存峰值；
- Pillow/OpenCV 能解码，MIME 和扩展名一致；
- 计算 SHA-256，内容重复时复用已有记录；
- 优先读取 EXIF `DateTimeOriginal`，缺失时使用服务器上传时间。

正常上传不接收或写入手工标签。`photos.tags` 保留是为了兼容已有数据库和迁移记录，
不是当前的自动标签功能；新上传照片默认没有该类元数据。

普通上传照片保存到系统用户目录 `~/Pictures/ai-album/imports/`，multipart 临时文件保存到
`~/Pictures/ai-album/.upload-tmp/`，随后进入单线程三模型索引任务。任务通过
`/api/jobs/{job_id}` 查询，失败不写入不完整 embedding。仓库的 `shared/photos/` 只用于 COCO-CN
和旧记录的兼容读取，不是个人照片上传目录。

## 🔄 增量、迁移与缺失

旧 `photo_metadata.json` 和 `photo_index.faiss` 不删除。首次升级只导入安全路径元数据，然后使用已准入模型补齐向量。外部原图消失时保留路径、hash 和历史元数据，只把 `available` 标为 false；服务不自动删除记录。

清空索引需要二次确认，只清除 SQLite embedding 和 FAISS 文件，永远不删除原图。受管上传的显式删除只影响原图和相关向量，COCO-CN 数据集和共享报告不受影响。

## 🚫 无派生图片缓存

系统不创建缩略图目录、持久化 JPEG、EPDGZ、E6 帧、Redis 或第二份图片缓存。图库图片按请求读取和编码；FAISS 文件只缓存向量检索，可由 SQLite 重建。
