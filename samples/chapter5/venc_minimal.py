"""最简 CANN VENC 编码程序 — 直接用原始 ACL API，不封装。

每个 API 调用都显式写出，方便看清 VENC 的完整流程。

关键知识点：
  • VENC 通道是异步的 — 通过回调线程收集编码输出
  • max_bit_rate 单位是 kbps，不是 bps（2000 = 2 Mbps）
  • key_frame_interval（GOP）必须在 [1, 65536] 范围内
  • 输入必须是 NV12 格式，宽度按 16 对齐（stride）

    python docs/venc_minimal.py
"""

import ctypes
import queue
import threading
import numpy as np
import acl
from acl import media


def bgr_to_nv12(bgr: np.ndarray) -> np.ndarray:
    """BGR (H, W, 3) uint8 → NV12 (H*3/2, W) uint8

    NV12 内存布局（连续缓冲区）：
        [Y 平面: H 行 × W 列] [UV 平面: H/2 行 × W 列, U/V 交错]
    """
    import cv2
    h, w = bgr.shape[:2]
    # OpenCV 输出 I420：Y(H×W) | U(H/4×W) | V(H/4×W) — 三个平面分开
    i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
    y = i420[:h, :w]
    u_flat = i420[h: h + h // 4, :w].ravel()
    v_flat = i420[h + h // 4: h + h // 2, :w].ravel()
    # NV12: U 和 V 交替写入 UV 平面
    uv = np.empty(h // 2 * w, dtype=np.uint8)
    uv[0::2] = u_flat
    uv[1::2] = v_flat
    return np.vstack([y, uv.reshape(h // 2, w)])


# ---- 常量 --------------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
PIX_FMT_NV12 = 1
ENTYPE_H264 = 1          # H.264 Baseline
W, H, FPS = 640, 480, 30

# ---- ACL 初始化（参见 check_cann.py）-------------------------------------------
acl.init()
acl.rt.set_device(0)
ctx, _ = acl.rt.create_context(0)
acl.rt.set_context(ctx)


# ---- 回调线程 ------------------------------------------------------------------
cb_queue: queue.Queue = queue.Queue(maxsize=8)


def venc_callback(input_pic_desc, output_stream_desc, _user_data):
    """编码完成时由 CANN 调用（在回调线程中执行）。"""
    try:
        size = media.dvpp_get_stream_desc_size(output_stream_desc)
        if size > 0:
            ptr = media.dvpp_get_stream_desc_data(output_stream_desc)
            host_buf, ret = acl.rt.malloc_host(size)
            if ret == ACL_SUCCESS:
                acl.rt.memcpy(host_buf, size, ptr, size,
                              ACL_MEMCPY_DEVICE_TO_HOST)
                cb_queue.put(ctypes.string_at(host_buf, size))
                acl.rt.free_host(host_buf)
            else:
                cb_queue.put(None)
        else:
            cb_queue.put(None)   # 编码器内部缓冲，P 帧常出现
    except Exception:
        cb_queue.put(None)
    finally:
        media.dvpp_destroy_pic_desc(input_pic_desc)


def callback_thread(_args):
    """回调处理线程 — 必须重新绑定 ACL 上下文。"""
    acl.rt.set_context(ctx)
    while running[0]:
        acl.rt.process_report(300)   # 300ms 轮询间隔


# ---- 创建 VENC 通道 ------------------------------------------------------------
running = [True]
tid, ret = acl.util.start_thread(callback_thread, [])
assert ret == ACL_SUCCESS, f"启动回调线程失败: {ret}"

desc = media.venc_create_channel_desc()
media.venc_set_channel_desc_thread_id(desc, tid)
media.venc_set_channel_desc_callback(desc, venc_callback)
media.venc_set_channel_desc_entype(desc, ENTYPE_H264)
media.venc_set_channel_desc_pic_format(desc, PIX_FMT_NV12)
media.venc_set_channel_desc_pic_width(desc, W)
media.venc_set_channel_desc_pic_height(desc, H)
media.venc_set_channel_desc_key_frame_interval(desc, FPS)    # GOP，必须 ≥1
media.venc_set_channel_desc_src_rate(desc, FPS)
media.venc_set_channel_desc_max_bit_rate(desc, 2_000)        # 单位 kbps！
media.venc_set_channel_desc_rc_mode(desc, 2)                 # CBR

ret = media.venc_create_channel(desc)
if ret != ACL_SUCCESS:
    import os
    os.system("dmesg | grep -i venc | tail -5")
    raise RuntimeError(f"venc_create_channel 失败: {ret} (0x{ret:x})")

frame_cfg = media.venc_create_frame_config()
print(f"VENC 通道已创建  {W}x{H}@{FPS}fps")


# ---- 编码一帧 ------------------------------------------------------------------
align = 16
stride = ((W + align - 1) // align) * align

# 生成随机 BGR 帧 → NV12
bgr = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
nv12 = bgr_to_nv12(bgr)

# NV12 宽度补齐到 stride（16 对齐）
padded = np.zeros(stride * H * 3 // 2, dtype=np.uint8).reshape(-1, stride)
src = nv12.reshape(-1, W)
for r in range(H):
    padded[r, :W] = src[r, :W]
for r in range(H // 2):
    padded[H + r, :W] = src[H + r, :W]
padded = padded.ravel()

# DVPP 输入 — 分配设备内存并拷贝 NV12 数据
in_buf, ret = media.dvpp_malloc(padded.nbytes)
assert ret == ACL_SUCCESS
acl.rt.memcpy(in_buf, padded.nbytes, padded.ctypes.data, padded.nbytes,
              ACL_MEMCPY_HOST_TO_DEVICE)

pic_desc = media.dvpp_create_pic_desc()
media.dvpp_set_pic_desc_data(pic_desc, in_buf)
media.dvpp_set_pic_desc_size(pic_desc, padded.nbytes)
media.dvpp_set_pic_desc_format(pic_desc, PIX_FMT_NV12)
media.dvpp_set_pic_desc_width(pic_desc, W)
media.dvpp_set_pic_desc_height(pic_desc, H)
media.dvpp_set_pic_desc_width_stride(pic_desc, stride)

# DVPP 输出 — 分配设备内存存放编码后码流
out_buf, ret = media.dvpp_malloc(W * H * 3 // 2)
assert ret == ACL_SUCCESS
stream_desc = media.dvpp_create_stream_desc()
media.dvpp_set_stream_desc_data(stream_desc, out_buf)
media.dvpp_set_stream_desc_size(stream_desc, W * H * 3 // 2)

# 强制编码为关键帧
media.venc_set_frame_config_force_i_frame(frame_cfg, True)

ret = media.venc_send_frame(desc, pic_desc, stream_desc, frame_cfg, None)
assert ret == ACL_SUCCESS, f"venc_send_frame 失败: {ret}"

encoded = cb_queue.get(timeout=5.0)
print(f"已编码关键帧: {len(encoded) if encoded else 0} 字节")


# ---- 清理资源 ------------------------------------------------------------------
media.venc_destroy_channel(desc)
media.venc_destroy_channel_desc(desc)
media.venc_destroy_frame_config(frame_cfg)
running[0] = False
media.dvpp_free(in_buf)
media.dvpp_free(out_buf)
media.dvpp_destroy_stream_desc(stream_desc)
print("资源已释放。")
