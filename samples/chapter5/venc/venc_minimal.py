"""最简 CANN VENC 编码程序 — 直接用原始 ACL API，不封装。

同一帧 NV12 输入分别编码为 H.264 Baseline 和 H.265 Main，方便对照
两个 codec 在 DVPP VENC 中的 API 差异：主要只有 entype 不同。

关键知识点：
  • VENC 通道是异步的 — 通过回调线程收集编码输出
  • entype=1 是 H.264 Baseline，entype=0 是 H.265 Main
  • max_bit_rate 单位是 kbps，不是 bps（2000 = 2 Mbps）
  • key_frame_interval（GOP）必须在 [1, 65536] 范围内
  • 输入必须是 NV12 格式，宽度按 16 对齐（stride）

    python samples/chapter5/venc/venc_minimal.py
"""

import ctypes
import queue

import acl
import numpy as np
from acl import media


# ---- 常量 --------------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

PIX_FMT_NV12 = 1
ENTYPE_H265_MAIN = 0
ENTYPE_H264_BASE = 1

W, H, FPS = 640, 480, 30
CODECS = [
    ("H.264 Baseline", ENTYPE_H264_BASE, 2_000),
    ("H.265 Main", ENTYPE_H265_MAIN, 2_000),
]


def make_test_nv12(w: int, h: int) -> np.ndarray:
    """生成一帧确定性 NV12 测试图像（Y 渐变 + UV=128 灰色）。"""
    if w % 2 != 0 or h % 2 != 0:
        raise ValueError(f"NV12 requires even width/height, got {w}x{h}")

    y = np.zeros((h, w), dtype=np.uint8)
    y[:, :] = (np.arange(w) / w * 255).astype(np.uint8)[None, :]
    y = (y.astype(np.int16) +
         (np.arange(h)[:, None] / h * 80).astype(np.int16)).clip(0, 255).astype(np.uint8)

    xx, yy = np.meshgrid(np.arange(32), np.arange(32))
    chess = ((xx // 8 + yy // 8) % 2 == 0)
    y[:32, :32][chess] = 255

    uv = np.full((h // 2, w), 128, dtype=np.uint8)
    return np.vstack([y, uv])


def pad_nv12(nv12: np.ndarray, w: int, h: int, stride: int) -> np.ndarray:
    """将紧凑 NV12 补齐为 VENC 要求的 stride 宽度。"""
    padded = np.zeros(stride * h * 3 // 2, dtype=np.uint8).reshape(-1, stride)
    src = nv12.reshape(-1, w)
    for r in range(h):
        padded[r, :w] = src[r, :w]
    for r in range(h // 2):
        padded[h + r, :w] = src[h + r, :w]
    return padded.ravel()


# ---- ACL 初始化（参见 check_cann.py）-------------------------------------------
acl.init()
acl.rt.set_device(0)
ctx, _ = acl.rt.create_context(0)
acl.rt.set_context(ctx)


# ---- 回调线程 ------------------------------------------------------------------
cb_queue: queue.Queue = queue.Queue(maxsize=8)
running = [True]


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
            cb_queue.put(None)
    except Exception:
        cb_queue.put(None)
    finally:
        media.dvpp_destroy_pic_desc(input_pic_desc)


def callback_thread(_args):
    """回调处理线程 — 必须重新绑定 ACL 上下文。"""
    acl.rt.set_context(ctx)
    while running[0]:
        acl.rt.process_report(300)


def create_venc_channel(codec_name: str, entype: int, bitrate_kbps: int):
    """创建一个 VENC 通道和帧配置。"""
    desc = media.venc_create_channel_desc()
    media.venc_set_channel_desc_thread_id(desc, tid)
    media.venc_set_channel_desc_callback(desc, venc_callback)
    media.venc_set_channel_desc_entype(desc, entype)
    media.venc_set_channel_desc_pic_format(desc, PIX_FMT_NV12)
    media.venc_set_channel_desc_pic_width(desc, W)
    media.venc_set_channel_desc_pic_height(desc, H)
    media.venc_set_channel_desc_key_frame_interval(desc, FPS)
    media.venc_set_channel_desc_src_rate(desc, FPS)
    media.venc_set_channel_desc_max_bit_rate(desc, bitrate_kbps)
    media.venc_set_channel_desc_rc_mode(desc, 2)  # CBR

    ret = media.venc_create_channel(desc)
    if ret != ACL_SUCCESS:
        import os
        os.system("dmesg | grep -i venc | tail -5")
        raise RuntimeError(f"{codec_name} venc_create_channel 失败: {ret} (0x{ret:x})")

    frame_cfg = media.venc_create_frame_config()
    print(f"VENC {codec_name} 通道已创建  {W}x{H}@{FPS}fps")
    return desc, frame_cfg


def encode_one_frame(codec_name: str, entype: int, bitrate_kbps: int) -> bytes:
    """创建通道，编码同一帧 NV12，返回码流 bytes。"""
    desc = frame_cfg = None
    in_buf = out_buf = None
    pic_desc = stream_desc = None
    send_ok = False

    try:
        desc, frame_cfg = create_venc_channel(codec_name, entype, bitrate_kbps)

        stride = ((W + 15) // 16) * 16
        nv12 = make_test_nv12(W, H)
        padded = pad_nv12(nv12, W, H, stride)

        in_buf, ret = media.dvpp_malloc(padded.nbytes)
        assert ret == ACL_SUCCESS, f"dvpp_malloc 输入失败: {ret}"
        acl.rt.memcpy(in_buf, padded.nbytes, padded.ctypes.data, padded.nbytes,
                      ACL_MEMCPY_HOST_TO_DEVICE)

        pic_desc = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(pic_desc, in_buf)
        media.dvpp_set_pic_desc_size(pic_desc, padded.nbytes)
        media.dvpp_set_pic_desc_format(pic_desc, PIX_FMT_NV12)
        media.dvpp_set_pic_desc_width(pic_desc, W)
        media.dvpp_set_pic_desc_height(pic_desc, H)
        media.dvpp_set_pic_desc_width_stride(pic_desc, stride)

        out_size = W * H * 3 // 2
        out_buf, ret = media.dvpp_malloc(out_size)
        assert ret == ACL_SUCCESS, f"dvpp_malloc 输出失败: {ret}"
        stream_desc = media.dvpp_create_stream_desc()
        media.dvpp_set_stream_desc_data(stream_desc, out_buf)
        media.dvpp_set_stream_desc_size(stream_desc, out_size)

        media.venc_set_frame_config_force_i_frame(frame_cfg, True)

        while not cb_queue.empty():
            cb_queue.get_nowait()

        ret = media.venc_send_frame(desc, pic_desc, stream_desc, frame_cfg, None)
        assert ret == ACL_SUCCESS, f"{codec_name} venc_send_frame 失败: {ret}"
        send_ok = True

        encoded = cb_queue.get(timeout=5.0)
        encoded = encoded or b""
        print(f"{codec_name} 已编码关键帧: {len(encoded):,} 字节")
        return encoded
    finally:
        if pic_desc is not None and not send_ok:
            media.dvpp_destroy_pic_desc(pic_desc)
        if stream_desc is not None:
            media.dvpp_destroy_stream_desc(stream_desc)
        if in_buf is not None:
            media.dvpp_free(in_buf)
        if out_buf is not None:
            media.dvpp_free(out_buf)
        if desc is not None:
            media.venc_destroy_channel(desc)
        if frame_cfg is not None:
            media.venc_destroy_frame_config(frame_cfg)
        if desc is not None:
            media.venc_destroy_channel_desc(desc)


# ---- 逐 codec 编码 -------------------------------------------------------------
tid, ret = acl.util.start_thread(callback_thread, [])
assert ret == ACL_SUCCESS, f"启动回调线程失败: {ret}"

try:
    for codec_name, entype, bitrate_kbps in CODECS:
        encode_one_frame(codec_name, entype, bitrate_kbps)
finally:
    running[0] = False
    acl.util.stop_thread(tid)

print("资源已释放。")
