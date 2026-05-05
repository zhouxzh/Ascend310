# CANN VPC 硬件图像处理完整指南

> **前置阅读**：建议先阅读 [DVPP 基础教程](dvpp_guide.md)（ACL 初始化、通道模型、NV12 格式、H.264 编解码理论）。
> VPC 与 VENC/VDEC 共享 ACL 初始化和 NV12 格式，但**通道模型不同**——VPC 用 Stream 同步而非回调线程。

## 目录

1. [VPC 简介](#vpc-简介)
2. [VPC 与 VENC/VDEC 的关键区别](#vpc-与-vencdec-的关键区别)
3. [VPC API 详解](#vpc-api-详解)
4. [acllite 快速上手](#acllite-快速上手)
5. [练习脚本走读](#练习脚本走读)
6. [性能基准](#性能基准)
7. [踩坑记录](#踩坑记录)
8. [场景推荐](#场景推荐)
9. [附录](#附录)

---

## VPC 简介

**VPC**（Video Pre-Processing Core）是 DVPP 中的硬件图像处理模块。它提供三大功能：

| 功能 | 说明 | 典型场景 |
|------|------|---------|
| **Resize** | 图像缩放（多种插值算法） | 1080p → 720p 下采样 |
| **Crop** | 按指定区域裁剪 | 从画面中提取 ROI 区域 |
| **CSC** | 色彩空间转换 | YUYV→NV12、NV12→RGB 等 |

VPC 在 DVPP 管道中的典型位置：

```
USB Camera YUYV ──→ [VPC CSC] ──→ NV12 ──→ [VENC] ──→ H.264 ──→ WebRTC
                        ↑                  ↑
                   硬件色彩转换          硬件编码
                   (零CPU占用)          (零CPU占用)
```

### 为什么用 VPC 而不是 OpenCV

在 Orange Pi AI Pro 上，CPU 做 `cv2.cvtColor(YUYV→BGR)` + `cv2.resize(1080p→720p)` + `bgr_to_nv12()` 会消耗 ARM Cortex-A55 的宝贵算力。VPC 完全在硬件中完成这些操作，CPU 只负责下发任务和等待完成。

### 硬件能力规格（Ascend 310B4）

| 约束项 | 值 |
|--------|-----|
| 输入分辨率范围 | 10×6 ~ 8192×8192 |
| 输出分辨率范围 | 10×6 ~ 4096×8192 |
| 宽度对齐 | 2（VPC 自动向下对齐） |
| 高度对齐 | 2 |
| 支持的输入格式 | NV12、NV21、YUV400、YUV422、YUV444 |
| 支持的输出格式 | NV12、NV21、YUV400、YUV422、YUV444 |

---

## VPC 与 VENC/VDEC 的关键区别

VPC 使用的是 **通用 DVPP 通道模型**，与 VENC/VDEC 的专用通道有本质差异：

| 维度 | VENC / VDEC | VPC |
|------|------------|-----|
| 通道创建 | `venc_create_channel()` / `vdec_create_channel()` | `dvpp_create_channel()`（无需设置 mode） |
| 异步机制 | **回调线程**（需 `process_report` 轮询） | **Stream 同步**（`synchronize_stream` 等待） |
| 输入类型 | VENC: pic_desc / VDEC: stream_desc | pic_desc（始终是图像） |
| 输出类型 | VENC: stream_desc / VDEC: pic_desc | pic_desc（始终是图像） |
| 回调函数 | 必须设置 | **不需要** |
| 回调线程 | 必须启动 | **不需要** |
| 复杂度 | 高（多线程协调） | 低（同步等待即可） |

> **310B 特别注意**：`dvpp_vpc_convert_color_async`（CSC 色彩空间转换）在 310B 上**返回 ACL_ERROR_INVALID_PARAM**，不可用。`himpi.vpc_convert_color` 需要预创建 himpi 通道（Python 接口受限）。310B 上的 YUYV→NV12 转换目前只能走 CPU 路径。

### 通道模型对比

```
VENC/VDEC 专用通道:                VPC 通用通道:

主线程: send_frame → Queue.get   主线程: vpc_xxx_async → synchronize_stream
             ↑                                  ↑
回调线程: process_report            (无需回调线程)
          → callback
          → Queue.put
```

VPC 的 Stream 模型更简单——下发异步任务后直接阻塞等待完成，不需要管理额外的线程和队列。

---

## VPC API 详解

### 通用 DVPP 通道创建

VPC 使用通用的 `dvpp_create_channel()`，与 VENC/VDEC 的专用 API 不同：

```text
// 创建 DVPP 通道描述符
dvpp_channel_desc = 创建通道描述符()
设置通道模式(dvpp_channel_desc, VPC模式)

// 创建通道（返回 0 = 成功）
创建通道(dvpp_channel_desc)

// 同时创建 Stream 用于同步等待
stream = 创建Stream()
```

具体 Python API：
```python
import acl

# 创建通用 DVPP 通道（无需设置 mode——310B 不支持 DVPP_CHANNEL_MODE 常量）
dvpp_channel_desc = acl.media.dvpp_create_channel_desc()
ret = acl.media.dvpp_create_channel(dvpp_channel_desc)

# 创建 Stream 用于同步
stream, ret = acl.rt.create_stream()
```

### Resize — 缩放

将输入图片缩放到输出尺寸：

```text
ret = dvpp_vpc_resize_async(通道描述符, 输入pic_desc, 输出pic_desc, resize配置, stream)
同步等待(stream)  # 等待硬件完成
```

Python API：
```python
# 创建 resize 配置
resize_config = acl.media.dvpp_create_resize_config()
# 可选：设置插值算法
# acl.media.dvpp_set_resize_config_interpolation(resize_config, 0)  # 0=bilinear

ret = acl.media.dvpp_vpc_resize_async(
    dvpp_channel_desc,    # DVPP 通道描述符
    input_pic_desc,       # 输入图片描述符（pic_desc）
    output_pic_desc,      # 输出图片描述符（pic_desc）
    resize_config,        # 缩放配置
    stream                # Stream 对象
)
acl.rt.synchronize_stream(stream)  # 等待完成
```

**关键约束**：
- 输入/输出 pic_desc 的宽高必须对齐到 2（VPC 自动处理）
- resize 不会改变像素格式——输入 NV12 则输出也是 NV12
- 如需同时改变格式和尺寸，需串联 CSC + Resize

### Crop — 裁剪

从输入图片中裁剪指定区域：

```text
crop_area = 创建ROI配置(左偏移, 右偏移, 上偏移, 下偏移)
ret = dvpp_vpc_crop_async(通道描述符, 输入pic_desc, 输出pic_desc, crop_area, stream)
```

Python API：
```python
# 创建 ROI 配置：从 (100, 100) 开始裁剪 224×224
# dvpp_create_roi_config 接受 4 个位置参数: (left, right, top, bottom)
crop_area = acl.media.dvpp_create_roi_config(
    100,    # 左边界偏移
    323,    # 右边界偏移 (左+宽-1)
    100,    # 上边界偏移
    323,    # 下边界偏移 (上+高-1)
)

ret = acl.media.dvpp_vpc_crop_async(
    dvpp_channel_desc, input_pic_desc, output_pic_desc, crop_area, stream)
acl.rt.synchronize_stream(stream)
```

### Crop + Resize — 最常用的组合

裁剪后缩放到目标尺寸，一次调用完成两个操作：

```python
# 从 1080p 画面中裁剪中心区域，缩放到 720×480
crop_area = acl.media.dvpp_create_roi_config()
acl.media.dvpp_set_roi_config(crop_area, left, right, top, bottom)

resize_config = acl.media.dvpp_create_resize_config()

ret = acl.media.dvpp_vpc_crop_resize_async(
    dvpp_channel_desc,
    input_pic_desc,       # 1920×1080
    output_pic_desc,      # 720×480
    crop_area,
    resize_config,
    stream
)
acl.rt.synchronize_stream(stream)
```

这是 VPC 最高效的使用方式——crop + resize 在一个硬件调用中完成，避免中间缓冲区。

### CSC — 色彩空间转换（310B 不可用）

**结论**：310B (CANN 8.3.RC1) 上，`dvpp_vpc_convert_color_async` 返回 `ACL_ERROR_INVALID_PARAM`（100000），`himpi.vpc_convert_color` 需要 himpi 通道预配置（Python 接口不支持）。CSC 在 310B 上**目前不可用**。

**替代方案**：YUYV→NV12 转换使用 CPU（`cv2.cvtColor + bgr_to_nv12`），resize 可卸载到 VPC：

```
USB Camera YUYV → CPU bgr_to_nv12() → NV12 → VPC resize → NV12(720p) → VENC
                                          ↑                    ↑
                                     CPU 做色彩转换        VPC 硬件缩放
```

如果未来 CANN 版本在 310B 上开放 CSC 支持，可通过 `dvpp_vpc_convert_color_async` 使用与 resize 完全相同的通道+Stream 模式调用。

---

## acllite 快速上手

CANN 自带了 Python 封装库 **acllite**（`/usr/local/Ascend/thirdpart/aarch64/acllite/`），对 DVPP 的 resize、crop、JPEG 编解码做了高层封装。大部分场景下比裸调 `acl.media` 更推荐。

### 导入和环境

```python
import numpy as np
from acllite_imageproc import AclLiteImageProc
from acllite_image import AclLiteImage
from acllite_resource import AclLiteResource

# 初始化 ACL（替代手工四步咒语）
acl_res = AclLiteResource()
acl_res.init()

# 创建 VPC + JPEG 处理器
vpc = AclLiteImageProc()
```

### Resize

```python
# 准备 NV12 输入（numpy ndarray）
y = np.full((480, 640), 128, dtype=np.uint8)
uv = np.full((240, 640), 128, dtype=np.uint8)
nv12 = np.vstack([y, uv])

# AclLiteImage 封装 → 拷贝到 DVPP 内存
img = AclLiteImage(nv12, 640, 480).copy_to_dvpp()

# 硬件缩放（自动 stride 对齐 + Stream 同步）
resized = vpc.resize(img, 320, 240)

# 取回主机 numpy
result = resized.byte_data_to_np_array()  # shape=(115200,) uint8
```

### Crop and Paste

```python
# 从 640×480 裁剪到 224×224
cropped = vpc.crop_and_paste(img, 640, 480, 224, 224)
data = cropped.byte_data_to_np_array()
```

### JPEG 编码

```python
# NV12 → JPEG（硬件编码，310B 支持）
jpeg_img = vpc.jpege(resized)
jpeg_bytes = jpeg_img.byte_data_to_np_array()  # JPEG 码流
```

### JPEG 解码

```python
# JPEG → NV12（硬件解码）
decoded = vpc.jpegd(jpeg_img)
# decoded.width, decoded.height, decoded.size
nv12_data = decoded.byte_data_to_np_array()
```

### 清理

```python
vpc.destroy()
# AclLiteResource 在 __del__ 中自动释放，无需显式调用
```

### 310B 实测输出

在 Orange Pi AI Pro (Ascend 310B4, CANN 8.3.RC1) 上运行上述代码的输出：

```
1. Resize 640x480→320x240: OK   size=115200
2. CropPaste→224x224:     OK   size=75264
3. JPEG Encode:           OK   jpeg_size=1842 bytes
4. JPEG Decode:           OK   320x240  size=138240
```

> JPEG 解码后 NV12 的 stride 对齐可能导致输出 size 略大于 `W×H×3/2`，属于正常现象。

### AclLiteImage 使用模式

acllite 的 `AclLiteImage` 支持三种输入方式，覆盖不同数据来源：

```python
# 方式 1: 从 numpy ndarray（内存中的原始像素）
nv12 = np.vstack([y_plane, uv_plane])
img = AclLiteImage(nv12, width, height)
img_dvpp = img.copy_to_dvpp()  # 拷贝到 DVPP 内存后才能用 VPC

# 方式 2: 从文件（支持 jpg/png/yuv）
img = AclLiteImage("input.jpg")       # JPEG 文件
img = AclLiteImage("input.yuv", 640, 480)  # YUV 文件需提供宽高

# 方式 3: 从 DVPP 设备内存指针（VPC/VDEC 输出结果）
ret, img = vdec.read()  # img 已是 AclLiteImage，memory_type=MEMORY_DVPP

# 读取结果
data = img.byte_data_to_np_array()  # → numpy uint8 一维数组
```

### acllite vs 裸调 API 对比

| | acllite | 裸 `acl.media` |
|---|---|---|
| ACL 初始化 | `AclLiteResource().init()` 一行 | 四步咒语 |
| stride 对齐 | 自动 | 手动 `((w+15)//16)*16` |
| DVPP 内存 | `copy_to_dvpp()` 一行 | `dvpp_malloc` + `memcpy` |
| Stream 同步 | 内部自动 | 手动 `synchronize_stream` |
| pic_desc 管理 | 内部自动 | 手动 create/set/destroy |
| 适用场景 | resize/crop/JPEG | 需要精细控制（VENC/VDEC） |

> **注意**：acllite 只封装了 VPC 和 JPEG，**不包含 VENC**。VENC 仍需使用 `acl.media.venc_*` 原始 API（参考 [venc_guide.md](venc_guide.md)）。VDEC 有 `DvppVdec` 封装（参考 [vdec_guide.md](vdec_guide.md)）。

---

## 练习脚本走读

完整代码见 [`docs/vpc_minimal.py`](vpc_minimal.py)。程序使用原始 `acl.media` API 演示两个核心操作
（理解底层 API 后再用 acllite 可事半功倍）：

### ① Resize — 硬件缩放

```python
# 创建 640×480 测试 NV12 帧
src_nv12 = make_test_nv12(640, 480)

# 计算 stride 对齐的内存大小
sw = ((640 + 15) // 16) * 16    # width stride 对齐到 16
sh = ((480 + 1) // 2) * 2      # height stride 对齐到 2
in_size = sw * sh * 3 // 2

# 目标 320×240
out_sw = ((320 + 15) // 16) * 16
out_sh = ((240 + 1) // 2) * 2
out_size = out_sw * out_sh * 3 // 2

# 分配设备内存 + 拷贝输入数据
in_buf, _ = media.dvpp_malloc(in_size)
out_buf, _ = media.dvpp_malloc(out_size)
acl.rt.memcpy(in_buf, src_nv12.nbytes, src_nv12.ctypes.data,
              src_nv12.nbytes, ACL_MEMCPY_HOST_TO_DEVICE)

# 构造输入/输出 pic_desc（含 height_stride）
in_pic = create_nv12_pic_desc(in_buf, 640, 480, sw, sh)
out_pic = create_nv12_pic_desc(out_buf, 320, 240, out_sw, out_sh)

# 执行缩放 + 同步等待
resize_cfg = media.dvpp_create_resize_config()
media.dvpp_vpc_resize_async(ch_desc, in_pic, out_pic, resize_cfg, stream)
acl.rt.synchronize_stream(stream)

# 拷回主机内存
host_buf = np.zeros(out_size, dtype=np.uint8)
acl.rt.memcpy(host_buf.ctypes.data, out_size, out_buf, out_size,
              ACL_MEMCPY_DEVICE_TO_HOST)
```

### ② Crop + Resize — 裁剪后缩放

```python
# 从 640×480 中裁剪中心 320×240，再缩放到 640×480
crop_area = acl.media.dvpp_create_roi_config()
acl.media.dvpp_set_roi_config(crop_area,
    left=160, right=480, top=120, bottom=360)    # 中心 320×240

resize_cfg = acl.media.dvpp_create_resize_config()
acl.media.dvpp_vpc_crop_resize_async(
    ch_desc, in_pic_desc, out_pic_desc, crop_area, resize_cfg, stream)
acl.rt.synchronize_stream(stream)
```

### ③ CSC — 310B 不可用

310B 上 CSC 需走 CPU。详见 [CSC 限制](#csc--色彩空间转换310b-不可用)。

### 清理

```python
# 释放 VPC 资源
acl.media.dvpp_destroy_resize_config(resize_cfg)
acl.media.dvpp_destroy_roi_config(crop_area)
acl.media.dvpp_destroy_pic_desc(in_pic)
acl.media.dvpp_destroy_pic_desc(out_pic)
acl.media.dvpp_free(in_buf)
acl.media.dvpp_free(out_buf)
acl.media.dvpp_destroy_channel(ch_desc)
acl.media.dvpp_destroy_channel_desc(ch_desc)
acl.rt.destroy_stream(stream)
```

### 与项目的对应关系

| 文件 | 角色 |
|------|------|
| `acllite_imageproc.py` | **推荐封装**——CANN 自带，resize/crop/JPEG 一键完成 |
| `vpc_minimal.py` | 学习用途——裸调 `acl.media` API，理解底层机制 |
| `bench_vpc.py` | 基准测试——VPC resize vs CPU cv2.resize 性能对比 |
| `webrtc_app/ascend_source.py` | 候选集成点——用 acllite resize 替代 CPU resize（CSC 仍走 CPU） |

---

## 性能基准

以下数据使用 [`docs/bench_vpc.py`](bench_vpc.py) 在 Orange Pi AI Pro（Ascend 310B4, CANN 8.3.RC1）上实测。

**测试条件**：NV12 → ½ 缩放（各分辨率缩小一半），60 帧，确定性测试帧，固定种子 42。

```
═══ VPC Resize: NV12 → ½ 缩放 ═══

Resolution        VPC_fps  CPU_fps Speedup  VPC_ms  CPU_ms Winner
─────────────────────────────────────────────────────────────────
640x480          760.6   1521.2  0.50x    1.3ms    0.7ms    CPU
1280x720          679.4    572.5  1.19x    1.5ms    1.7ms    VPC
1920x1080         272.9    290.2  0.94x    3.7ms    3.4ms    CPU
2560x1440         227.9    143.6  1.59x    4.4ms    7.0ms    VPC
3840x2160         109.8     53.0  2.07x    9.1ms   18.9ms    VPC
```

### 结果解读

VPC Resize 与 VENC 不同——**不是所有分辨率都碾压 CPU**，而是类似 VDEC 存在性能拐点：

| 分辨率 | 像素数 | VPC fps | CPU fps | 加速比 | 推荐 |
|--------|--------|---------|---------|--------|------|
| 640×480 | 0.3M | **761** | **1521** | **0.50x** | CPU |
| 1280×720 | 0.9M | **679** | **573** | **1.19x** | VPC |
| 1920×1080 | 2.1M | **273** | **290** | **0.94x** | CPU |
| 2560×1440 | 3.7M | **228** | **144** | **1.59x** | **VPC** |
| 3840×2160 | 8.3M | **110** | **53** | **2.07x** | **VPC** |

**拐点约在 2K（2560×1440）**。低分辨率下 CPU 反而更快——VPC 的固定调度开销（dvpp_malloc + memcpy + Stream 同步）在小帧上无法被硬件加速摊薄。

#### VPC vs VENC vs VDEC 性能模式对比

| 模块 | 低分辨率（≤1080p） | 高分辨率（≥2K） | 瓶颈 |
|------|-------------------|----------------|------|
| **VENC** | 碾压 CPU（5×~10×） | 碾压 CPU（8×~10×） | 纯硬件编码计算 |
| **VDEC** | 落后 CPU（~59ms 固定开销） | 领先 CPU（1.9×~2.2×） | Python 回调调度 |
| **VPC** | 与 CPU 互有胜负（0.5×~1.2×） | 领先 CPU（1.6×~2.1×） | Stream 同步开销 |

> VPC 的固定开销比 VDEC 小得多（Stream 同步比回调线程轻量），因此拐点更低（2K vs VDEC 的 2K 相同，但 1080p 差距很小）。

### CSC 性能（310B 不支持，仅供参考）

310B 上 CSC（YUYV→NV12）必须走 CPU：`cv2.cvtColor(YUYV→BGR) + bgr_to_nv12()`。VPC 只能卸载 resize，CPU 仅做色彩转换。

---

## 踩坑记录

### 坑 #1：310B 不支持 dvpp_vpc_convert_color_async

**现象**：`dvpp_vpc_convert_color_async` 返回 `100000`（ACL_ERROR_INVALID_PARAM）。

**根因**：该接口仅支持 310P 及以上型号。310B（Atlas 200I A2）的 CANN 8.3.RC1 版本不支持。

**修复**：无 VPC 硬件替代方案。YUYV→NV12 使用 CPU：`cv2.cvtColor(YUYV→BGR) + bgr_to_nv12()`。VPC 仅卸载 resize 部分。

### 坑 #2：pic_desc 必须设置 height_stride

**现象**：`dvpp_vpc_resize_async` 返回 `100000`（ACL_ERROR_INVALID_PARAM）。

**根因**：`dvpp_set_pic_desc_height_stride` 未设置，且 `dvpp_set_pic_desc_size` 使用了未对齐的 `W*H*3//2`。VPC 要求高度 stride 对齐到 2，宽度 stride 对齐到 16，缓冲区大小按 stride 计算。

**修复**：
```python
sw = ((w + 15) // 16) * 16   # width stride
sh = ((h + 1) // 2) * 2     # height stride
size = sw * sh * 3 // 2      # 按 stride 算总大小
media.dvpp_set_pic_desc_width_stride(pic, sw)
media.dvpp_set_pic_desc_height_stride(pic, sh)
media.dvpp_set_pic_desc_size(pic, size)
```

### 坑 #3：dvpp_create_roi_config 不接受 keyword 参数

**现象**：`dvpp_create_roi_config()` + `dvpp_set_roi_config()` 不存在或报错。

**根因**：`dvpp_set_roi_config` 在 CANN 8.3.RC1 的 Python API 中不接受 keyword 参数。正确做法是在 `dvpp_create_roi_config()` 中直接传入 4 个位置参数。

**修复**：
```python
# ✅ 正确
crop_area = acl.media.dvpp_create_roi_config(left, right, top, bottom)

# ❌ 错误
crop_area = acl.media.dvpp_create_roi_config()
acl.media.dvpp_set_roi_config(crop_area, left, right, top, bottom)
```

### 坑 #4：himpi 通道不可从 Python 创建

**现象**：`himpi.vpc_create_chn()` 无论传什么参数都报 "args parse failed"。

**根因**：himpi 接口是 C 扩展的直接映射，需要 C 结构体类型的参数，Python 侧不支持创建这些结构体。`vpc_convert_color` 虽然语法上可以调用，但缺少预配置的 himpi 通道，返回硬件错误 `0xa0078003`。

**当前结论**：310B 上的 VPC CSC 不可用。等待 CANN 后续版本在 310B 上开放 `dvpp_vpc_convert_color_async` 支持。

---

## 场景推荐

### 场景决策树

```
需要对图像做预处理？
├── 仅缩放（NV12 in → NV12 out）
│   └── → dvpp_vpc_resize_async （简单，Stream同步）
├── 仅裁剪
│   └── → dvpp_vpc_crop_async
├── 裁剪 + 缩放
│   └── → dvpp_vpc_crop_resize_async （推荐，一次调用）
├── 仅色彩转换（YUYV → NV12）
│   └── → 310B 不支持，使用 CPU cv2.cvtColor + bgr_to_nv12
├── 色彩转换 + 缩放（摄像头场景）
│   ├── ① CPU bgr_to_nv12（YUYV→NV12）
│   └── ② VPC dvpp_vpc_resize_async（1080p→720p）
└── 小型图像、非实时 → CPU (cv2) 足够
```

### 典型场景

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| USB 摄像头 → WebRTC | CPU CSC + VPC resize | CSC 走 CPU，resize 卸载到 VPC |
| 视频文件预处理 | dvpp_vpc_crop_resize_async | 单次调用完成裁剪+缩放 |
| AI 推理前处理 | VPC resize + AIPP CSC | Resize→AI Core 直接推理 |
| 多路视频并发（>4 路） | **VPC 必须** | CPU 做多路 resize 会占满所有核 |

---

## 附录

### 常用调试命令

```bash
# VPC 驱动状态
lsmod | grep vpc

# VPC 内核日志
dmesg | grep -i vpc | tail -10

# 运行示例脚本
python docs/vpc_minimal.py

# 运行基准测试
python docs/bench_vpc.py
```

### 参数速查表

```
VPC 通道创建：
┌──────────────────────┬─────────────────────────────────────┬───────────┐
│ 参数                  │ API                                      │ 值       │
├──────────────────────┼─────────────────────────────────────┼───────────┤
│ 通道描述符            │ dvpp_create_channel_desc()               │ —        │
│ 创建通道              │ dvpp_create_channel(ch_desc)              │ ret=0    │
│ 创建通道              │ dvpp_create_channel()                    │ ret=0    │
│ 创建 Stream           │ acl.rt.create_stream()                   │ ret=0    │
└──────────────────────┴─────────────────────────────────────┴───────────┘

VPC 操作 API：
┌──────────────────────┬─────────────────────────────────────┬───────────┐
│ 操作                  │ API                                      │ 310B     │
├──────────────────────┼─────────────────────────────────────┼───────────┤
│ 缩放                  │ dvpp_vpc_resize_async                   │ ✅       │
│ 裁剪                  │ dvpp_vpc_crop_async                     │ ✅       │
│ 裁剪+缩放             │ dvpp_vpc_crop_resize_async              │ ✅       │
│ 色域转换              │ dvpp_vpc_convert_color_async            │ ❌       │
└──────────────────────┴─────────────────────────────────────┴───────────┘

像素格式（dvpp_set_pic_desc_format）：
  1  = NV12 (YUV420SP)
  7  = YUYV (YUV422 interleaved)
  12 = RGB888
  13 = BGR888
```
