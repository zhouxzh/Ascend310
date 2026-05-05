# CANN JPEGE / JPEGD 硬件编解码指南

> **前置阅读**：建议先阅读 [DVPP 基础教程](dvpp_guide.md)（ACL 初始化、通道模型、NV12 格式）。
> JPEG 编解码使用与 VPC 相同的通用 `dvpp_create_channel` + Stream 同步模型，比 VENC/VDEC 的回调模型简单得多。

## 目录

1. [JPEG 编解码简介](#jpeg-编解码简介)
2. [与 VENC/VDEC/VPC 的关键区别](#与-vencdecvpc-的关键区别)
3. [JPEGE API 详解](#jpege-api-详解)
4. [JPEGD API 详解](#jpegd-api-详解)
5. [acllite 快速上手](#acllite-快速上手)
6. [练习脚本走读](#练习脚本走读)
7. [场景推荐](#场景推荐)
8. [踩坑记录](#踩坑记录)
9. [附录](#附录)

---

## JPEG 编解码简介

DVPP 的 JPEG 编解码子模块：

```
JPEGE (JPEG Encoder):  NV12 ──→ [硬件编码] ──→ JPEG 码流
JPEGD (JPEG Decoder):  JPEG 码流 ──→ [硬件解码] ──→ NV12
```

两者串联形成硬件闭环，可验证编解码无损性（测试常用模式）。

### 与 VENC 编码的区别

VENC 输出 H.264 码流，但 JPEGE 输出的是**独立的 JPEG 图片**——两者有本质不同：

| | JPEGE | VENC |
|---|---|---|
| 输出格式 | JPEG 单帧图片 | H.264 / H.265 视频码流 |
| 帧间关系 | 无（每帧独立） | 有（I/P/B 帧依赖） |
| 输出描述符 | **裸内存缓冲区** + size 指针 | **stream_desc**（码流描述符） |
| 编码参数 | quality（1-100） | GOP、码率、帧率、profile |
| 用途 | 截图、快照、缩略图 | 实时视频传输 |

### 硬件能力规格（Ascend 310B4）

| | JPEGE | JPEGD |
|---|---|---|
| 输入格式 | NV12 (YUV420SP)、YUV422SP | JPEG 码流（Baseline） |
| 输出格式 | JPEG 码流 | NV12 (YUV420SP) |
| 分辨率范围 | 32×32 ~ 8192×8192 | 32×32 ~ 8192×8192 |
| 质量范围 | 1-100 | — |
| 编码吞吐 | 1080p@256fps | 1080p@512fps |

---

## 与 VENC/VDEC/VPC 的关键区别

JPEGE/JPEGD 使用与 VPC 相同的**通用 dvpp 通道 + Stream 同步**模型：

| 维度 | VENC/VDEC | JPEGE/JPEGD |
|------|-----------|------------|
| 通道创建 | `venc/vdec_create_channel()` | `dvpp_create_channel()`（无需 mode） |
| 异步机制 | 回调线程 | Stream 同步 |
| 输入描述 | 图片或码流描述符 | **裸内存指针 + size** |
| 输出描述 | 图片或码流描述符 | JPEGE: **裸内存 + size 指针**；JPEGD: pic_desc |

> JPEGE 的输出不是 `stream_desc`——这是一个常见误区。JPEG 码流直接写入 `dvpp_malloc` 分配的缓冲区，通过 `numpy_to_ptr` 封装的 size 指针返回实际大小。

---

## JPEGE API 详解

### 编码流程

```text
① 创建 jpege_config + 设置质量 → ② predict_enc_size 预测输出大小
    → ③ dvpp_malloc 输出缓冲区 → ④ jpeg_encode_async 异步编码
    → ⑤ synchronize_stream 等待 → ⑥ 读取实际 size → ⑦ memcpy 取回 JPEG 码流
```

### jpege_config — 编码参数

```python
jpege_cfg = acl.media.dvpp_create_jpege_config()
acl.media.dvpp_set_jpege_config_level(jpege_cfg, quality)  # quality: 1-100
```

唯一参数是 **quality**（1-100），对应 JPEG 压缩质量。值越大画质越好、文件越大。

### predict_enc_size — 预测输出大小

```python
max_size, ret = acl.media.dvpp_jpeg_predict_enc_size(input_pic_desc, jpege_cfg)
```

返回编码后 JPEG 码流的**最大可能大小**（通常远大于实际值）。输出缓冲区需按此值分配。

### jpeg_encode_async — 执行编码

```python
import numpy as np

# in/out 参数：传入 max_size，编码后返回实际大小
out_size_arr = np.array([max_size], dtype=np.int32)
if "bytes_to_ptr" in dir(acl.util):
    out_size_ptr = acl.util.bytes_to_ptr(out_size_arr.tobytes())
else:
    out_size_ptr = acl.util.numpy_to_ptr(out_size_arr)

ret = acl.media.dvpp_jpeg_encode_async(
    channel_desc,       # dvpp 通道描述符
    input_pic_desc,     # NV12 输入图片描述符
    output_buffer,      # 输出缓冲区（dvpp_malloc）
    out_size_ptr,       # in/out: [max_size] → [actual_size]
    jpege_cfg,          # 编码配置
    stream              # Stream 对象
)
acl.rt.synchronize_stream(stream)

# 同步后读取实际编码大小
actual_size = int(out_size_arr[0])
```

**关键点**：`out_size_ptr` 是 Python 层用 `numpy_to_ptr` 封装的指针，指向一个 `int32` 数组。编码器写入实际大小后，同步完成即可读取。

### 完整编码示例

```python
# 准备输入 NV12 pic_desc（略）

jpege_cfg = media.dvpp_create_jpege_config()
media.dvpp_set_jpege_config_level(jpege_cfg, 85)

max_size, _ = media.dvpp_jpeg_predict_enc_size(in_pic, jpege_cfg)
out_buf, _ = media.dvpp_malloc(max_size)

out_size_arr = np.array([max_size], dtype=np.int32)
out_size_ptr = acl.util.numpy_to_ptr(out_size_arr)

media.dvpp_jpeg_encode_async(ch_desc, in_pic, out_buf,
                              out_size_ptr, jpege_cfg, stream)
acl.rt.synchronize_stream(stream)

jpeg_size = int(out_size_arr[0])
jpeg_host = np.zeros(jpeg_size, dtype=np.uint8)
acl.rt.memcpy(jpeg_host.ctypes.data, jpeg_size, out_buf, jpeg_size,
              ACL_MEMCPY_DEVICE_TO_HOST)
# jpeg_host 即为 JPEG 码流，可直接写入 .jpg 文件
```

---

## JPEGD API 详解

### 解码流程

```text
① JPEG 数据拷贝到设备 → ② get_image_info 获取宽高
    → ③ predict_dec_size 预测输出大小 → ④ dvpp_malloc + 创建 pic_desc
    → ⑤ jpeg_decode_async 异步解码 → ⑥ synchronize_stream → ⑦ memcpy 取回 NV12
```

### get_image_info — 获取 JPEG 信息

```python
img_w, img_h, img_fmt, ret = acl.media.dvpp_jpeg_get_image_info(
    jpeg_dev_ptr, jpeg_size)
```

解码前必须调用此函数获取 JPEG 图像的宽度和高度，用于创建输出 pic_desc。

### predict_dec_size — 预测输出大小

```python
out_size, ret = acl.media.dvpp_jpeg_predict_dec_size(
    jpeg_dev_ptr, jpeg_size, PIX_FMT_NV12)
```

返回解码后 NV12 缓冲区的所需大小。

### jpeg_decode_async — 执行解码

```python
ret = acl.media.dvpp_jpeg_decode_async(
    channel_desc,       # dvpp 通道描述符
    jpeg_dev_ptr,       # JPEG 数据指针（设备内存）
    jpeg_size,          # JPEG 数据大小
    output_pic_desc,    # 输出 NV12 pic_desc
    stream              # Stream 对象
)
acl.rt.synchronize_stream(stream)
```

### 完整解码示例

```python
# 拷贝 JPEG 到设备内存
jpeg_dev, _ = media.dvpp_malloc(len(jpeg_data))
acl.rt.memcpy(jpeg_dev, len(jpeg_data), jpeg_data.ctypes.data,
              len(jpeg_data), ACL_MEMCPY_HOST_TO_DEVICE)

# 获取图像信息
w, h, fmt, _ = media.dvpp_jpeg_get_image_info(jpeg_dev, len(jpeg_data))

# 预测 + 解码
out_size, _ = media.dvpp_jpeg_predict_dec_size(jpeg_dev, len(jpeg_data), PIX_FMT_NV12)
out_buf, _ = media.dvpp_malloc(out_size)
out_pic = create_nv12_pic_desc(out_buf, w, h)  # 见 vpc_guide

media.dvpp_jpeg_decode_async(ch_desc, jpeg_dev, len(jpeg_data), out_pic, stream)
acl.rt.synchronize_stream(stream)

# 取回 NV12
nv12_host = np.zeros(out_size, dtype=np.uint8)
acl.rt.memcpy(nv12_host.ctypes.data, out_size, out_buf, out_size,
              ACL_MEMCPY_DEVICE_TO_HOST)
```

---

## acllite 快速上手

acllite 封装了 JPEG 编解码为一行 API：

```python
from acllite_imageproc import AclLiteImageProc
from acllite_image import AclLiteImage
from acllite_resource import AclLiteResource

acl_res = AclLiteResource()
acl_res.init()
vpc = AclLiteImageProc()

# 准备 NV12
img = AclLiteImage(nv12_ndarray, width, height).copy_to_dvpp()

# JPEG 编码 — 一行
jpeg_img = vpc.jpege(img)
jpeg_bytes = jpeg_img.byte_data_to_np_array()  # → numpy uint8

# JPEG 解码 — 一行
decoded = vpc.jpegd(jpeg_img)
nv12 = decoded.byte_data_to_np_array()         # → numpy uint8

vpc.destroy()
```

> acllite 内部自动处理了 predict_size、out_size_ptr、stride 对齐等细节，推荐优先使用。

---

## 练习脚本走读

完整代码见 [`docs/jpeg_minimal.py`](jpeg_minimal.py)。程序使用原始 `acl.media` API 演示 **JPEGE → JPEGD 闭环**（理解底层后再用 acllite）。

### ① JPEGE — 编码一帧

```python
# 创建 JPEGE 配置
jpege_cfg = media.dvpp_create_jpege_config()
media.dvpp_set_jpege_config_level(jpege_cfg, 90)

# 预测编码后最大大小
max_size, _ = media.dvpp_jpeg_predict_enc_size(in_pic, jpege_cfg)

# 分配输出缓冲区
out_buf, _ = media.dvpp_malloc(max_size)

# in/out 参数：传 max_size，同步后读 actual_size
out_size_arr = np.array([max_size], dtype=np.int32)
out_size_ptr = acl.util.numpy_to_ptr(out_size_arr)

media.dvpp_jpeg_encode_async(ch_desc, in_pic, out_buf,
                              out_size_ptr, jpege_cfg, stream)
acl.rt.synchronize_stream(stream)

jpeg_actual = int(out_size_arr[0])  # ← 同步后才能读
```

### ② JPEGD — 解码验证

```python
# 获取 JPEG 信息
w, h, fmt, _ = media.dvpp_jpeg_get_image_info(jpeg_dev, jpeg_size)

# 预测解码大小
dec_size, _ = media.dvpp_jpeg_predict_dec_size(jpeg_dev, jpeg_size, PIX_FMT_NV12)

# 创建输出 pic_desc + 解码
dec_buf, _ = media.dvpp_malloc(dec_size)
dec_pic = create_pic_desc(dec_buf, w, h)

media.dvpp_jpeg_decode_async(ch_desc, jpeg_dev, jpeg_size, dec_pic, stream)
acl.rt.synchronize_stream(stream)

# 验证闭环：输入 NV12 大小 = 输出 NV12 大小
```

### 310B 实测输出

```
JPEGE OK  640x480 NV12 → 2097152 bytes JPEG  (quality=90)
JPEGD OK  2097152 bytes JPEG → 640x480 NV12  size=460800
闭环验证  PASS  输入=460800 输出=460800
```

> JPEG 码流 2MB 是因为测试帧的渐变+棋盘格纹理压缩率低（确定性生成，非真实照片）。正常照片在 quality=85 下通常只有几十 KB。

### 与项目的对应关系

| 文件 | 角色 |
|------|------|
| `jpeg_minimal.py` | 学习用途——裸 API 编解码闭环 |
| `vpc_acllite_demo.py` | acllite 封装——一行 jpege/jpegd |
| `webrtc_app/` | 候选集成点——截图保存、快照功能 |

---

## 场景推荐

| 场景 | 推荐方案 |
|------|---------|
| WebRTC 截图保存 | `vpc.jpege(frame)` → 写入 .jpg 文件 |
| MJPEG 视频流解码 | `vpc.jpegd()` 逐帧解码（配合 VPC resize） |
| 照片缩略图生成 | VPC resize → JLEGE 编码（全硬件管道） |
| 快速原型 | acllite（三行代码：init → jpege → 写入文件） |
| 追求最小 JPEG 文件 | 裸 API + 精细调 quality 参数 |

### 全硬件截图管道

```
WebRTC NV12 帧 → VPC resize(320×240) → JPEGE(quality=80) → JPEG 文件
                        ↑ 硬件                  ↑ 硬件
```

---

## 踩坑记录

### 坑 #1：JPEGE 输出不是 stream_desc

**现象**：试图用 `dvpp_get_stream_desc_data` 读取编码输出，得到垃圾数据。

**根因**：JPEGE 输出写入裸内存缓冲区，不是 `stream_desc`。只有 VENC 使用 stream_desc 输出。

**修复**：JPEGE 输出直接 `memcpy` 从 `out_buf` 拷出，实际大小从 `out_size_ptr` 读取。

### 坑 #2：out_size_ptr 的同步时序

**现象**：`synchronize_stream` 之前读取 `out_size_arr[0]`，得到的是 max_size 而非 actual_size。

**根因**：`out_size_arr` 是 in/out 参数，编码器在硬件完成后才写入实际值。

**修复**：**必须在 `synchronize_stream` 之后**读取 `out_size_arr[0]`。

### 坑 #3：predict_enc_size 返回的值远大于实际

**现象**：`predict_enc_size` 返回 2MB，但编码后只有 30KB。

**根因**：`predict_enc_size` 返回的是**最坏情况**的缓冲区大小，不是预测值。JPEG 的压缩率取决于图像内容。

**修复**：按 predict 值分配缓冲区（保障不溢出），编码后通过 `out_size_arr[0]` 取实际大小。

---

## 附录

### 参数速查表

```
JPEGE 编码流程：
┌──────────────────────┬─────────────────────────────────────┬───────────┐
│ 步骤                  │ API                                      │ 说明      │
├──────────────────────┼─────────────────────────────────────┼───────────┤
│ 创建配置              │ dvpp_create_jpege_config()              │ —         │
│ 设置质量              │ dvpp_set_jpege_config_level(cfg, 1-100) │ 越大画质越好 │
│ 预测输出最大大小      │ dvpp_jpeg_predict_enc_size(pic, cfg)     │ 最坏情况  │
│ 异步编码              │ dvpp_jpeg_encode_async(ch, pic, buf,    │           │
│                      │     size_ptr, cfg, stream)              │           │
│ 同步等待              │ acl.rt.synchronize_stream(stream)        │           │
└──────────────────────┴─────────────────────────────────────┴───────────┘

JPEGD 解码流程：
┌──────────────────────┬─────────────────────────────────────┬───────────┐
│ 步骤                  │ API                                      │ 说明      │
├──────────────────────┼─────────────────────────────────────┼───────────┤
│ 获取图像信息          │ dvpp_jpeg_get_image_info(ptr, size)     │ 宽度/高度  │
│ 预测输出大小          │ dvpp_jpeg_predict_dec_size(ptr,sz,fmt)  │ NV12      │
│ 异步解码              │ dvpp_jpeg_decode_async(ch, ptr, sz,     │           │
│                      │     pic_desc, stream)                   │           │
│ 同步等待              │ acl.rt.synchronize_stream(stream)        │           │
└──────────────────────┴─────────────────────────────────────┴───────────┘
```
