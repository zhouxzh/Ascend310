"""最简 CANN VDEC 解码程序 — 用原始 ACL API 解码 H.264/H.265 各一帧。

同一份 YUV420 测试帧先由 CPU 编码为 H.264 Baseline 和 H.265 Main
合法码流，再分别交给 DVPP VDEC 解码为 NV12。这样可以对照两个 codec
在 VDEC 中的 API 差异：主要只有 entype 和测试码流编码器不同。

关键知识点：
  • VDEC 通道必须显式设置 channel_id（VENC 不需要）
  • entype=1 是 H.264 Baseline，entype=0 是 H.265 Main
  • 回调中先检查 pic_desc 的 ret_code，非 0 表示解码失败
  • 输入必须是完整合法码流，不能是随机字节
  • 输出是 NV12 格式，与 VENC 输入格式相同

    python samples/chapter5/vdec/vdec_minimal.py
"""

import ctypes
import fractions
import queue

import acl
import numpy as np
from acl import media

# ---- 常量 --------------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

ENTYPE_H265_MAIN = 0
ENTYPE_H264_BASE = 1
PIX_FMT_NV12 = 1

W, H, FPS = 640, 480, 30
CODECS = [
    ("H.264 Baseline", "libx264", "31", ENTYPE_H264_BASE),
    ("H.265 Main", "libx265", "31", ENTYPE_H265_MAIN),
]


def make_test_i420(w: int, h: int) -> np.ndarray:
    """生成一帧确定性 I420/YUV420P 测试图像。"""
    if w % 2 != 0 or h % 2 != 0:
        raise ValueError(f"YUV420 requires even width/height, got {w}x{h}")

    y = np.zeros((h, w), dtype=np.uint8)
    y[:, :] = (np.arange(w) / w * 255).astype(np.uint8)[None, :]
    y = (y.astype(np.int16) +
         (np.arange(h)[:, None] / h * 80).astype(np.int16)).clip(0, 255).astype(np.uint8)

    xx, yy = np.meshgrid(np.arange(32), np.arange(32))
    chess = ((xx // 8 + yy // 8) % 2 == 0)
    y[:32, :32][chess] = 255

    u = np.full((h // 2, w // 2), 128, dtype=np.uint8)
    v = np.full((h // 2, w // 2), 128, dtype=np.uint8)

    i420 = np.empty((h * 3 // 2, w), dtype=np.uint8)
    i420[:h, :] = y
    i420[h:h + h // 4, :] = u.reshape(h // 4, w)
    i420[h + h // 4:h + h // 2, :] = v.reshape(h // 4, w)
    return i420


def encoder_options(ffmpeg_codec: str, level: str) -> dict[str, str]:
    options = {"level": level, "tune": "zerolatency"}
    if ffmpeg_codec == "libx265":
        options["x265-params"] = "log-level=error"
    return options


def make_test_stream(codec_name: str, ffmpeg_codec: str, level: str) -> bytes:
    """用 CPU 编码器生成一帧合法 H.264/H.265 Annex-B 风格测试码流。"""
    import av

    codec = av.CodecContext.create(ffmpeg_codec, "w")
    codec.width = W
    codec.height = H
    codec.pix_fmt = "yuv420p"
    codec.bit_rate = 2_000_000
    codec.framerate = fractions.Fraction(FPS, 1)
    codec.time_base = fractions.Fraction(1, FPS)
    codec.options = encoder_options(ffmpeg_codec, level)
    if ffmpeg_codec == "libx264":
        codec.profile = "Baseline"

    frame = av.VideoFrame.from_ndarray(make_test_i420(W, H), format="yuv420p")
    data = bytearray()
    for pkt in codec.encode(frame):
        data += bytes(pkt)
    for pkt in codec.encode(None):
        data += bytes(pkt)

    stream = bytes(data)
    print(f"测试 {codec_name} 码流: {len(stream):,} 字节  ({W}x{H})")
    return stream


# ---- ACL 初始化 ----------------------------------------------------------------
acl.init()
acl.rt.set_device(0)
ctx, _ = acl.rt.create_context(0)
acl.rt.set_context(ctx)


# ---- 回调线程 ------------------------------------------------------------------
cb_queue: queue.Queue = queue.Queue(maxsize=8)
running = [True]


def vdec_callback(input_stream_desc, output_pic_desc, _user_data):
    """解码完成时由 CANN 调用（在回调线程中执行）。

    VDEC 的回调参数顺序与 VENC 相反：第一个是输入（码流），第二个是输出（图片）。
    """
    pic_data = None
    try:
        ret_code = media.dvpp_get_pic_desc_ret_code(output_pic_desc)
        pic_data = media.dvpp_get_pic_desc_data(output_pic_desc)
        pic_size = media.dvpp_get_pic_desc_size(output_pic_desc)
        if ret_code == 0 and pic_size > 0:
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
        if pic_data is not None:
            media.dvpp_free(pic_data)
        media.dvpp_destroy_stream_desc(input_stream_desc)
        media.dvpp_destroy_pic_desc(output_pic_desc)


def callback_thread(_args):
    acl.rt.set_context(ctx)
    while running[0]:
        acl.rt.process_report(300)


def create_vdec_channel(codec_name: str, entype: int, channel_id: int):
    """创建一个 VDEC 通道和帧配置。"""
    desc = media.vdec_create_channel_desc()
    media.vdec_set_channel_desc_channel_id(desc, channel_id)
    media.vdec_set_channel_desc_thread_id(desc, tid)
    media.vdec_set_channel_desc_callback(desc, vdec_callback)
    media.vdec_set_channel_desc_entype(desc, entype)
    media.vdec_set_channel_desc_out_pic_format(desc, PIX_FMT_NV12)
    media.vdec_set_channel_desc_out_pic_width(desc, W)
    media.vdec_set_channel_desc_out_pic_height(desc, H)

    ret = media.vdec_create_channel(desc)
    if ret != ACL_SUCCESS:
        import os
        os.system("dmesg | grep -i vdec | tail -5")
        raise RuntimeError(f"{codec_name} vdec_create_channel 失败: {ret} (0x{ret:x})")

    frame_cfg = media.vdec_create_frame_config()
    print(f"VDEC {codec_name} 通道已创建  channel_id={channel_id}  {W}x{H}")
    return desc, frame_cfg


def decode_one_stream(codec_name: str, stream: bytes, entype: int, channel_id: int) -> bytes:
    """创建通道，解码一帧 H.264/H.265 码流，返回 NV12 bytes。"""
    desc = frame_cfg = None
    in_buf = out_buf = None
    stream_desc = pic_desc = None
    send_ok = False

    try:
        desc, frame_cfg = create_vdec_channel(codec_name, entype, channel_id)

        arr = np.frombuffer(stream, dtype=np.uint8)
        in_buf, ret = media.dvpp_malloc(len(arr))
        assert ret == ACL_SUCCESS, f"dvpp_malloc 输入失败: {ret}"
        acl.rt.memcpy(in_buf, len(arr), arr.ctypes.data, len(arr),
                      ACL_MEMCPY_HOST_TO_DEVICE)

        stream_desc = media.dvpp_create_stream_desc()
        media.dvpp_set_stream_desc_data(stream_desc, in_buf)
        media.dvpp_set_stream_desc_size(stream_desc, len(arr))

        out_size = W * H * 3 // 2
        out_buf, ret = media.dvpp_malloc(out_size)
        assert ret == ACL_SUCCESS, f"dvpp_malloc 输出失败: {ret}"

        pic_desc = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(pic_desc, out_buf)
        media.dvpp_set_pic_desc_size(pic_desc, out_size)
        media.dvpp_set_pic_desc_format(pic_desc, PIX_FMT_NV12)

        while not cb_queue.empty():
            cb_queue.get_nowait()

        ret = media.vdec_send_frame(desc, stream_desc, pic_desc, frame_cfg, None)
        assert ret == ACL_SUCCESS, f"{codec_name} vdec_send_frame 失败: {ret}"
        send_ok = True

        decoded = cb_queue.get(timeout=5.0)
        decoded = decoded or b""
        if decoded:
            nv12_frame = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, W)
            print(f"{codec_name} 已解码 NV12: {nv12_frame.shape}  {len(decoded):,} 字节")
            print(f"  Y 平面: {H}×{W}  UV 平面: {H//2}×{W}")
        else:
            print(f"{codec_name} 解码未产生输出")
        return decoded
    finally:
        if stream_desc is not None and not send_ok:
            media.dvpp_destroy_stream_desc(stream_desc)
        if pic_desc is not None and not send_ok:
            media.dvpp_destroy_pic_desc(pic_desc)
        if in_buf is not None:
            media.dvpp_free(in_buf)
        if out_buf is not None and not send_ok:
            media.dvpp_free(out_buf)
        if desc is not None:
            media.vdec_destroy_channel(desc)
        if frame_cfg is not None:
            media.vdec_destroy_frame_config(frame_cfg)
        if desc is not None:
            media.vdec_destroy_channel_desc(desc)


# ---- 逐 codec 解码 -------------------------------------------------------------
tid, ret = acl.util.start_thread(callback_thread, [])
assert ret == ACL_SUCCESS, f"启动回调线程失败: {ret}"

try:
    for codec_name, ffmpeg_codec, level, entype in CODECS:
        stream = make_test_stream(codec_name, ffmpeg_codec, level)
        decode_one_stream(codec_name, stream, entype, channel_id=0)
finally:
    running[0] = False
    acl.util.stop_thread(tid)

print("资源已释放。")
