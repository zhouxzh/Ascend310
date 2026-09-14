# 照片库与 NPU 索引

_说明照片上传、系统目录保存、三模型索引、搜索和删除边界。_

---

本文说明照片从上传到索引、搜索和删除的生命周期。

## 存储位置

普通用户照片不存放在 Case7 发布目录中：

```text
~/Pictures/ai-album/imports/       受管原图
~/Pictures/ai-album/.upload-tmp/   上传临时文件
```

`samples/case7/photos/`、`data/` 和 `shared/` 只用于兼容旧数据或公开测试资产。不要把个人照片直接复制到这些目录，以免发布、清理或同步操作误处理私人照片。

## 上传方式

网页上传支持单张、多张和文件夹选择。接口使用 `multipart/form-data`，字段名为 `files`：

```bash
curl -F "files=@/path/to/photo.jpg" \
  http://<BOARD_IP>:7860/api/photos/upload
```

返回 `job_id` 后轮询：

```bash
curl http://<BOARD_IP>:7860/api/jobs/<job_id>
```

任务状态会经过上传接收、文件校验、内容哈希、照片入库、三模型编码和 FAISS 更新。前端分别显示文件传输进度和 NPU 索引进度。

当前不设置单张字节数或单次文件数量上限。实际限制来自浏览器、局域网带宽、可用磁盘和单线程索引速度；单张图片解码后的像素数不得超过 50 MP。只接受 JPEG、PNG、BMP 和 WebP 等代码中登记的图片扩展名。

## 校验与去重

上传时服务会：

1. 检查扩展名和临时路径是否在受管目录内。
2. 读取图片头并尝试解码。
3. 检查宽度、高度和像素总数。
4. 计算 SHA-256。
5. 按内容哈希去重。
6. 读取 EXIF `DateTimeOriginal` 作为拍摄时间；没有 EXIF 时使用服务器上传时间。
7. 将新原图写入 `~/Pictures/ai-album/imports/`。

损坏图片、路径逃逸、符号链接逃逸和不支持的格式会被拒绝或标记为跳过，不会进入 NPU 编码队列。重复图片不会新建照片记录。

## 索引结构

SQLite 保存照片元数据、可用状态、上传时间、拍摄时间、尺寸、MIME 类型和索引任务状态。每个模型拥有独立的 FAISS `IndexIDMap2(IndexFlatIP)`：

```text
photos(photo_id, filepath, sha256, width, height, capture_time, available, ...)
embeddings(photo_id, model_id, vector, normalized)
indexes/<model_id>.faiss
```

`photo_id` 是 SQLite 与 FAISS 的 ID 映射，不同模型的向量绝不能混合。服务启动时会校验照片 ID、向量维度和数量；缺失外部原图只标记 `available=false`，不会自动删除元数据。

## 搜索与模型路由

- 中文、日文或韩文查询自动选择 Chinese-CLIP。
- 其他文本查询自动选择 MobileCLIP。
- 图片相似搜索使用用户明确选择的模型。
- ResNet50 仅提供经典相似图模式。
- 不把 MobileCLIP、Chinese-CLIP 和 ResNet50 向量放进同一个 FAISS 索引。

## 删除与保护

删除照片必须带二次确认：

```bash
curl -X DELETE \
  'http://<BOARD_IP>:7860/api/photos/<photo_id>?confirm=true'
```

删除只改变相册元数据和索引状态，不删除其他原图，也不清空整个 `Pictures` 目录。清空索引和删除原图是两个不同操作；Case7 默认不提供删除整库原图的接口。

## 派生文件边界

预览 JPEG、设备 JPEG 和 E6 帧都按请求在内存中生成。服务不创建持久化缩略图、JPEG、EPDGZ 或 E6 帧缓存。若发现发布目录出现个人图片或派生图片文件，应先停止清理操作并检查 `SMART_ALBUM_PHOTO_DIR`、`SMART_ALBUM_IMPORT_DIR` 和启动参数。
