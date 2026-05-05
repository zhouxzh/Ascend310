"""最简 CANN VDEC 解码程序 — 用原始 ACL API 解码一帧 H.264。

与 VENC 不同，VDEC 的输入是 H.264 码流描述符，输出是 NV12 图片描述符。

关键知识点：
  • VDEC 通道必须显式设置 channel_id（VENC 不需要）
  • 回调中先检查 pic_desc 的 ret_code，非 0 表示解码失败
  • 输入必须是完整的 H.264 NAL 单元，不能是裸 Annex-B 随机字节
  • 输出是 NV12 格式，与 VENC 输入格式相同

    python docs/vdec_minimal.py
"""

import ctypes
import queue
import threading
import fractions
import numpy as np
import acl
from acl import media

# ---- 常量 --------------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
ENTYPE_H264_BASE = 1        # H.264 Baseline
PIX_FMT_NV12 = 1            # YUV420SP
W, H = 640, 480

# ---- ACL 初始化 ----------------------------------------------------------------
acl.init()
acl.rt.set_device(0)
ctx, _ = acl.rt.create_context(0)
acl.rt.set_context(ctx)


# ---- 回调线程 ------------------------------------------------------------------
cb_queue: queue.Queue = queue.Queue(maxsize=8)


def vdec_callback(input_stream_desc, output_pic_desc, _user_data):
    """解码完成时由 CANN 调用（在回调线程中执行）。

    VDEC 的回调参数顺序与 VENC 相反：第一个是输入（码流），第二个是输出（图片）。
    """
    try:
        ret_code = media.dvpp_get_pic_desc_ret_code(output_pic_desc)
        if ret_code == 0:
            pic_data = media.dvpp_get_pic_desc_data(output_pic_desc)
            pic_size = media.dvpp_get_pic_desc_size(output_pic_desc)
            host_buf, ret = acl.rt.malloc_host(pic_size)
            if ret == ACL_SUCCESS:
                acl.rt.memcpy(host_buf, pic_size, pic_data, pic_size,
                              ACL_MEMCPY_DEVICE_TO_HOST)
                cb_queue.put(ctypes.string_at(host_buf, pic_size))
                acl.rt.free_host(host_buf)
            else:
                cb_queue.put(None)
        else:
            print(f"  解码失败，ret_code={ret_code}")
            cb_queue.put(None)
    except Exception as exc:
        print(f"  回调异常: {exc}")
        cb_queue.put(None)
    finally:
        # 回调负责销毁输入流描述符和输出图片描述符
        media.dvpp_destroy_stream_desc(input_stream_desc)
        media.dvpp_destroy_pic_desc(output_pic_desc)


def callback_thread(_args):
    acl.rt.set_context(ctx)
    while running[0]:
        acl.rt.process_report(300)


# ---- 生成测试 H.264 码流（用 libx264 软件编码一帧）------------------------------
import av

codec = av.CodecContext.create("libx264", "w")
codec.width = W
codec.height = H
codec.pix_fmt = "yuv420p"
codec.bit_rate = 2_000_000
codec.framerate = fractions.Fraction(30, 1)
codec.time_base = fractions.Fraction(1, 30)
codec.options = {"level": "31", "tune": "zerolatency"}
codec.profile = "Baseline"

# 生成随机 BGR 帧并编码为 H.264
bgr = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
rgb_frame = av.VideoFrame.from_ndarray(bgr[..., ::-1], format="rgb24")
h264_data = bytearray()
for pkt in codec.encode(rgb_frame):
    h264_data += bytes(pkt)
for pkt in codec.encode(None):          # 排空编码器缓冲区
    h264_data += bytes(pkt)
h264_bytes = bytes(h264_data)
print(f"测试 H.264 码流: {len(h264_bytes):,} 字节  ({W}x{H})")

# 用 numpy 包装以便 memcpy
h264_arr = np.frombuffer(h264_bytes, dtype=np.uint8)


# ---- 创建 VDEC 通道 ------------------------------------------------------------
running = [True]
tid, ret = acl.util.start_thread(callback_thread, [])
assert ret == ACL_SUCCESS, f"启动回调线程失败: {ret}"

desc = media.vdec_create_channel_desc()
media.vdec_set_channel_desc_channel_id(desc, 0)        # VDEC 必须显式设置 channel_id
media.vdec_set_channel_desc_thread_id(desc, tid)
media.vdec_set_channel_desc_callback(desc, vdec_callback)
media.vdec_set_channel_desc_entype(desc, ENTYPE_H264_BASE)
media.vdec_set_channel_desc_out_pic_format(desc, PIX_FMT_NV12)
media.vdec_set_channel_desc_out_pic_width(desc, W)
media.vdec_set_channel_desc_out_pic_height(desc, H)

ret = media.vdec_create_channel(desc)
if ret != ACL_SUCCESS:
    import os
    os.system("dmesg | grep -i vdec | tail -5")
    raise RuntimeError(f"vdec_create_channel 失败: {ret} (0x{ret:x})")

frame_cfg = media.vdec_create_frame_config()
print(f"VDEC 通道已创建  channel_id=0  {W}x{H}")


# ---- 解码一帧 ------------------------------------------------------------------
# DVPP 输入 — 分配设备内存并拷贝 H.264 码流
in_buf, ret = media.dvpp_malloc(len(h264_arr))
assert ret == ACL_SUCCESS, f"dvpp_malloc 输入失败: {ret}"
acl.rt.memcpy(in_buf, len(h264_arr), h264_arr.ctypes.data, len(h264_arr),
              ACL_MEMCPY_HOST_TO_DEVICE)

stream_desc = media.dvpp_create_stream_desc()
media.dvpp_set_stream_desc_data(stream_desc, in_buf)
media.dvpp_set_stream_desc_size(stream_desc, len(h264_arr))

# DVPP 输出 — 分配设备内存存放解码后 NV12
out_size = W * H * 3 // 2
out_buf, ret = media.dvpp_malloc(out_size)
assert ret == ACL_SUCCESS, f"dvpp_malloc 输出失败: {ret}"

pic_desc = media.dvpp_create_pic_desc()
media.dvpp_set_pic_desc_data(pic_desc, out_buf)
media.dvpp_set_pic_desc_size(pic_desc, out_size)
media.dvpp_set_pic_desc_format(pic_desc, PIX_FMT_NV12)

ret = media.vdec_send_frame(desc, stream_desc, pic_desc, frame_cfg, None)
assert ret == ACL_SUCCESS, f"vdec_send_frame 失败: {ret}"

decoded = cb_queue.get(timeout=5.0)
if decoded:
    nv12_frame = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, W)
    print(f"已解码 NV12: {nv12_frame.shape}  {len(decoded):,} 字节")
    print(f"  Y 平面: {H}×{W}  UV 平面: {H//2}×{W}")
else:
    print("解码未产生输出")


# ---- 清理资源 ------------------------------------------------------------------
media.vdec_destroy_channel(desc)
media.vdec_destroy_channel_desc(desc)
media.vdec_destroy_frame_config(frame_cfg)
running[0] = False
media.dvpp_free(in_buf)
media.dvpp_free(out_buf)
print("资源已释放。")
