"""VDEC 硬件 vs CPU 软件解码性能对比 (H.264 / H.265).

分辨率扫描 480p → 4K, GOP=30 (I/P 混合), 确定性测试帧。

    python samples/chapter5/vdec/bench_vdec.py
"""

from __future__ import annotations

import ctypes
import fractions
import queue
import time
import unicodedata

import numpy as np

import acl
from acl import media

# ===================================================================
# 测试参数
# ===================================================================

RESOLUTIONS = [
    (640, 480),
    (1280, 720),
    (1920, 1080),
    (2560, 1440),      # 2K — VDEC 拐点附近
    (3840, 2160),      # 4K — VDEC 预期甜区
]

TEST_FRAMES = 90            # 恰好 3 个 GOP（GOP=30 × 3）
TEST_GOP = 30                # 1 个 I + 29 个 P，模拟真实视频流
RANDOM_SEED = 42             # 固定种子 → 结果可复现

WARMUP_FRAMES = 3
FPS = 30

# 编码参数（H.264 Baseline / H.265 Main）
#   tune=zerolatency  GOP=30（I/P 混合）
#   bit_rate = max(2M, w*h*fps*0.1) 按像素数缩放
#   level ≤720p→3.1, 1080p+→4.0

# ---- ACL 常量 ----------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

ENTYPE_H264_BASE = 1
ENTYPE_H265_MAIN = 0
PIX_FMT_NV12 = 1

_ACL_CTX = None
_ACL_READY = False


def _display_width(text: object) -> int:
    """Return terminal display width, treating CJK wide chars as two columns."""
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return width


def _pad(text: object, width: int, align: str = ">") -> str:
    text = str(text)
    spaces = max(0, width - _display_width(text))
    if align == "<":
        return text + " " * spaces
    return " " * spaces + text


def _print_row(values: list[object], widths: list[int], aligns: list[str]) -> None:
    print("  ".join(_pad(value, width, align)
                    for value, width, align in zip(values, widths, aligns)))


def _ensure_acl():
    global _ACL_CTX, _ACL_READY
    if _ACL_READY:
        return
    for func, args in [(acl.init, ()), (acl.rt.set_device, (0,))]:
        ret = func(*args)
        if ret != 0:
            raise RuntimeError(f"{func.__name__} = {ret}")
    ctx, ret = acl.rt.create_context(0)
    if ret != 0:
        raise RuntimeError(f"create_context = {ret}")
    ret = acl.rt.set_context(ctx)
    if ret != 0:
        raise RuntimeError(f"set_context = {ret}")
    _ACL_CTX = ctx
    _ACL_READY = True


# ===================================================================
# 测试帧生成（确定性内容，可复现）
# ===================================================================

def make_test_frames(n: int, w: int, h: int) -> list[np.ndarray]:
    """生成 n 帧确定性 BGR 图像——标准测试内容，可复现。

    不是随机噪声！使用渐变 + 移动条 + 棋盘格，模拟真实视频的
    空间和时间相关性。随机噪声没有任何相关性，压缩率异常低，
    不是标准的编码测试方法。
    """
    frames = []
    for i in range(n):
        bgr = np.zeros((h, w, 3), dtype=np.uint8)
        # 水平渐变 (R 通道)
        bgr[..., 2] = ((np.arange(w) / w * 255 + i * 4) % 255).astype(np.uint8)
        # 垂直渐变 (G 通道)
        bgr[..., 1] = ((np.arange(h)[:, None] / h * 255 + i * 3) % 255).astype(np.uint8)
        # 移动白条
        bar_x = int((np.sin(i / 15.0) + 1) * 0.5 * (w - 64))
        bgr[:, bar_x:bar_x + 64, :] = 255
        # 角落棋盘格（固定）
        xx, yy = np.meshgrid(np.arange(32), np.arange(32))
        chess = ((xx // 8 + yy // 8) % 2 == 0)
        bgr[:32, :32][chess] = [255, 0, 0]
        frames.append(bgr)
    return frames


# ===================================================================
# 编码（同一组原始帧 → H.264 和 H.265 分别编码）
# ===================================================================

def _encoder_options(codec_name: str, level: str) -> dict[str, str]:
    options = {"level": level, "tune": "zerolatency"}
    if codec_name == "libx265":
        options["x265-params"] = "log-level=error"
    return options


def encode_frames(frames: list[np.ndarray], codec_name: str, gop: int
                  ) -> tuple[list[bytes], int, int]:
    """把一组 BGR 帧编码为 H.264 或 H.265 码流。

    codec_name = "libx264" 或 "libx265"
    返回 (streams, i_avg_bytes, p_avg_bytes)
    """
    import av

    h, w = frames[0].shape[:2]
    level = "31" if w * h <= 1280 * 720 else "40"

    codec = av.CodecContext.create(codec_name, "w")
    codec.width = w
    codec.height = h
    codec.pix_fmt = "yuv420p"
    codec.bit_rate = max(2_000_000, int(w * h * FPS * 0.1))
    codec.framerate = fractions.Fraction(FPS, 1)
    codec.time_base = fractions.Fraction(1, FPS)
    codec.options = _encoder_options(codec_name, level)
    # H.265 默认 Main profile, H.264 = Baseline
    if codec_name == "libx264":
        codec.profile = "Baseline"

    streams, i_sizes, p_sizes = [], [], []

    for i, bgr in enumerate(frames):
        frame = av.VideoFrame.from_ndarray(bgr[..., ::-1], format="rgb24")
        frame.pict_type = (av.video.frame.PictureType.I if i % gop == 0
                           else av.video.frame.PictureType.P)
        data = bytearray()
        for pkt in codec.encode(frame):
            data += bytes(pkt)
        if data:
            stream = bytes(data)
            streams.append(stream)
            (i_sizes if (i % gop == 0) else p_sizes).append(len(stream))

    tail = bytearray()
    for pkt in codec.encode(None):
        tail += bytes(pkt)
    if tail:
        if streams:
            streams[-1] += bytes(tail)
        else:
            streams.append(bytes(tail))

    i_avg = sum(i_sizes) // len(i_sizes) if i_sizes else 0
    p_avg = sum(p_sizes) // len(p_sizes) if p_sizes else 0
    return streams, i_avg, p_avg


# ===================================================================
# CannVdec —— 通道复用版
# ===================================================================

class CannVdec:
    """同步 VDEC 解码器。创建一次通道，连续解码多帧。"""

    def __init__(self, width: int, height: int, entype: int = ENTYPE_H264_BASE):
        _ensure_acl()
        self.width = width
        self.height = height
        self._entype = entype
        self._running = True
        self._cb_queue: queue.Queue = queue.Queue(maxsize=16)
        self._create_channel()

    def _callback_thread(self, _args):
        acl.rt.set_context(_ACL_CTX)
        while self._running:
            acl.rt.process_report(300)

    def _vdec_callback(self, in_stream, out_pic, _user_data):
        pic_data = None
        try:
            rc = media.dvpp_get_pic_desc_ret_code(out_pic)
            sz = media.dvpp_get_pic_desc_size(out_pic)
            pic_data = media.dvpp_get_pic_desc_data(out_pic)
            if rc == 0 and sz > 0:
                host, _ = acl.rt.malloc_host(sz)
                acl.rt.memcpy(host, sz, pic_data, sz, ACL_MEMCPY_DEVICE_TO_HOST)
                self._cb_queue.put(ctypes.string_at(host, sz))
                acl.rt.free_host(host)
            else:
                self._cb_queue.put(None)
        except Exception:
            self._cb_queue.put(None)
        finally:
            if pic_data is not None:
                media.dvpp_free(pic_data)
            media.dvpp_destroy_stream_desc(in_stream)
            media.dvpp_destroy_pic_desc(out_pic)

    def _create_channel(self):
        tid, _ = acl.util.start_thread(self._callback_thread, [])
        self._tid = tid
        desc = media.vdec_create_channel_desc()
        media.vdec_set_channel_desc_channel_id(desc, 0)
        media.vdec_set_channel_desc_thread_id(desc, tid)
        media.vdec_set_channel_desc_callback(desc, self._vdec_callback)
        media.vdec_set_channel_desc_entype(desc, self._entype)
        media.vdec_set_channel_desc_out_pic_format(desc, PIX_FMT_NV12)
        media.vdec_set_channel_desc_out_pic_width(desc, self.width)
        media.vdec_set_channel_desc_out_pic_height(desc, self.height)
        ret = media.vdec_create_channel(desc)
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"vdec_create_channel: {ret}")
        self._ch_desc = desc
        self._frame_cfg = media.vdec_create_frame_config()

    def decode(self, stream_data: bytes) -> np.ndarray | None:
        arr = np.frombuffer(stream_data, dtype=np.uint8)
        w = self.width
        in_buf, _ = media.dvpp_malloc(len(arr))
        acl.rt.memcpy(in_buf, len(arr), arr.ctypes.data,
                      len(arr), ACL_MEMCPY_HOST_TO_DEVICE)
        sd = media.dvpp_create_stream_desc()
        media.dvpp_set_stream_desc_data(sd, in_buf)
        media.dvpp_set_stream_desc_size(sd, len(arr))
        out_sz = w * self.height * 3 // 2
        out_buf, _ = media.dvpp_malloc(out_sz)
        pd = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(pd, out_buf)
        media.dvpp_set_pic_desc_size(pd, out_sz)
        media.dvpp_set_pic_desc_format(pd, PIX_FMT_NV12)
        media.vdec_send_frame(self._ch_desc, sd, pd, self._frame_cfg, None)
        try:
            decoded = self._cb_queue.get(timeout=5.0)
        except queue.Empty:
            decoded = None
        media.dvpp_free(in_buf)
        if decoded is not None:
            return np.frombuffer(decoded, dtype=np.uint8).reshape(-1, w)
        return None

    def destroy(self):
        if self._ch_desc is not None:
            media.vdec_destroy_channel(self._ch_desc)
        self._running = False
        if hasattr(self, '_tid'):
            acl.util.stop_thread(self._tid)
        if self._frame_cfg is not None:
            media.vdec_destroy_frame_config(self._frame_cfg)
        media.vdec_destroy_channel_desc(self._ch_desc)
        self._ch_desc = None


# ===================================================================
# CPU 解码
# ===================================================================

def bench_cpu_warm(streams: list[bytes], total_frames: int,
                   codec_name: str = "h264", thread_count: int = 0
                   ) -> tuple[float, float, float]:
    """CPU decode. thread_count=0 → auto (all cores), =1 → single thread."""
    import av

    streams = [stream for stream in streams if stream]
    total_frames = min(total_frames, len(streams))
    if total_frames <= 0:
        raise RuntimeError("encoder produced no non-empty packets")

    def _new_decoder():
        codec = av.CodecContext.create(codec_name, "r")
        if hasattr(codec, 'thread_count'):
            codec.thread_count = thread_count
        return codec

    warmup_frames = min(WARMUP_FRAMES, total_frames)
    warmup_codec = _new_decoder()
    for i in range(warmup_frames):
        for _ in warmup_codec.decode(av.Packet(streams[i])):
            pass

    codec = _new_decoder()
    t0 = time.perf_counter()
    for i in range(total_frames):
        for _ in codec.decode(av.Packet(streams[i])):
            pass
    elapsed = time.perf_counter() - t0
    fps = total_frames / elapsed
    ms = elapsed / total_frames * 1000
    return elapsed, fps, ms


# ===================================================================
# 主流程
# ===================================================================

def _benchmark_codec(
    title: str,
    encoder_name: str,
    decoder_name: str,
    entype: int,
) -> None:
    print(f"═══ {title} 分辨率扫描（90 帧，GOP=30）═══")
    print()
    widths = [10, 12, 12, 12]
    aligns = ["<", ">", ">", ">"]
    _print_row(
        ["分辨率", "VDEC帧率", "CPU多线程", "CPU单线程"],
        widths,
        aligns,
    )
    print("─" * (sum(widths) + 2 * (len(widths) - 1)))

    for w, h in RESOLUTIONS:
        vdec = None
        try:
            frames = make_test_frames(TEST_FRAMES, w, h)
            streams, _, _ = encode_frames(frames, encoder_name, gop=TEST_GOP)
            streams = [stream for stream in streams if stream]
            total_frames = min(TEST_FRAMES, len(streams))
            if total_frames <= 0:
                raise RuntimeError("encoder produced no non-empty packets")
            warmup_frames = min(WARMUP_FRAMES, total_frames)

            vdec = CannVdec(w, h, entype=entype)
            for i in range(warmup_frames):
                vdec.decode(streams[i])
            t0 = time.perf_counter()
            for i in range(total_frames):
                vdec.decode(streams[i])
            vdec_t = time.perf_counter() - t0
            vdec_fps = total_frames / vdec_t

            _, cpu_mt_fps, cpu_mt_ms = bench_cpu_warm(
                streams, total_frames, decoder_name, thread_count=0
            )
            _, cpu_st_fps, cpu_st_ms = bench_cpu_warm(
                streams, total_frames, decoder_name, thread_count=1
            )

            _print_row(
                [
                    f"{w}x{h}",
                    f"{vdec_fps:.1f}",
                    f"{cpu_mt_fps:.1f}",
                    f"{cpu_st_fps:.1f}",
                ],
                widths,
                aligns,
            )
        except Exception as e:
            _print_row([f"{w}x{h}", f"SKIP ({e})"], [10, 80], ["<", "<"])
        finally:
            if vdec is not None:
                vdec.destroy()
    print()


def main() -> int:
    np.random.seed(RANDOM_SEED)
    _ensure_acl()

    _benchmark_codec("1. H.264", "libx264", "h264", ENTYPE_H264_BASE)
    print()
    _benchmark_codec("2. H.265", "libx265", "hevc", ENTYPE_H265_MAIN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
