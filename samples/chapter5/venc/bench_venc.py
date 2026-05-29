"""VENC 硬件 vs CPU 软件编码性能对比 (H.264 / H.265).

分辨率扫描 480p → 4K, GOP=30 (I/P 混合), 确定性测试帧。

    python samples/chapter5/venc/bench_venc.py
"""

from __future__ import annotations

import ctypes
import fractions
import gc
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
    (2560, 1440),      # 2K — VENC 预期稳定
    (3840, 2160),      # 4K — VENC 甜区
]

TEST_FRAMES = 90            # 恰好 3 个 GOP（GOP=30 × 3）
TEST_GOP = 30                # 1 个 I + 29 个 P，模拟真实视频流
RANDOM_SEED = 42             # 固定种子 → 结果可复现

WARMUP_FRAMES = 3
FPS = 30

# ---- ACL 常量 ----------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

PIX_FMT_NV12 = 1
ENTYPE_H264_BASE = 1
ENTYPE_H265_MAIN = 0

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
# 测试帧生成（确定性 YUV420/NV12，可复现）
# ===================================================================

def make_test_yuv420_frame(i: int, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """生成一帧确定性 YUV420 测试图像，返回 Y、U、V 三个平面。

    与 VDEC 基准测试使用相同的确定性内容策略，保证可复现。
    """
    y = np.zeros((h, w), dtype=np.uint8)
    # 水平渐变
    y[:, :] = ((np.arange(w) / w * 255 + i * 4) % 255).astype(np.uint8)[None, :]
    # 垂直渐变叠加
    y[:, :] = (y.astype(np.int16) +
               ((np.arange(h)[:, None] / h * 127 + i * 3) % 128).astype(np.int16)
               ).clip(0, 255).astype(np.uint8)
    # 移动白条
    bar_x = int((np.sin(i / 15.0) + 1) * 0.5 * (w - 64))
    y[:, bar_x:bar_x + 64] = 255
    # 角落棋盘格
    xx, yy = np.meshgrid(np.arange(32), np.arange(32))
    chess = ((xx // 8 + yy // 8) % 2 == 0)
    y[:32, :32][chess] = 255

    u = np.full((h // 2, w // 2), 128, dtype=np.uint8)
    v = np.full((h // 2, w // 2), 128, dtype=np.uint8)
    return y, u, v


def yuv420_to_nv12(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """YUV420 planar → NV12 semi-planar."""
    h, w = y.shape
    uv = np.empty((h // 2, w), dtype=np.uint8)
    uv[:, 0::2] = u
    uv[:, 1::2] = v
    return np.vstack([y, uv])


def yuv420_to_i420_ndarray(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """YUV420 planar → PyAV expects I420 ndarray shape (H*3/2, W)."""
    h, w = y.shape
    i420 = np.empty((h * 3 // 2, w), dtype=np.uint8)
    i420[:h, :] = y
    i420[h:h + h // 4, :] = u.reshape(h // 4, w)
    i420[h + h // 4:h + h // 2, :] = v.reshape(h // 4, w)
    return i420


# ===================================================================
# CannVenc —— 通道复用版
# ===================================================================

class CannVenc:
    """同步 VENC 编码器。创建一次通道，连续编码多帧。"""

    def __init__(self, width: int, height: int, bitrate: int = 2000,
                 entype: int = ENTYPE_H264_BASE):
        _ensure_acl()
        self.width = width
        self.height = height
        self.bitrate = bitrate
        self.entype = entype
        self._ctx = acl.rt.get_context(0)[1]
        self._running = True

        self._cb_queue: queue.Queue = queue.Queue(maxsize=64)

        align = 16
        self._stride = ((width + align - 1) // align) * align
        self._out_buf_size = width * height * 3 // 2

        self._create_channel()

    # -- 回调线程 ----------------------------------------------------------------

    def _callback_thread(self, _args):
        acl.rt.set_context(self._ctx)
        while self._running:
            acl.rt.process_report(300)

    def _venc_callback(self, input_pic_desc, output_stream_desc, _user_data):
        try:
            size = media.dvpp_get_stream_desc_size(output_stream_desc)
            if size > 0:
                ptr = media.dvpp_get_stream_desc_data(output_stream_desc)
                host_buf, ret = acl.rt.malloc_host(size)
                if ret == ACL_SUCCESS:
                    acl.rt.memcpy(host_buf, size, ptr, size,
                                  ACL_MEMCPY_DEVICE_TO_HOST)
                    self._cb_queue.put(ctypes.string_at(host_buf, size))
                    acl.rt.free_host(host_buf)
                else:
                    self._cb_queue.put(None)
            else:
                self._cb_queue.put(None)
        except Exception:
            try:
                self._cb_queue.put(None)
            except Exception:
                pass
        finally:
            media.dvpp_destroy_pic_desc(input_pic_desc)

    # -- 通道创建 ----------------------------------------------------------------

    def _create_channel(self):
        self._ch_desc = media.venc_create_channel_desc()

        tid, ret = acl.util.start_thread(self._callback_thread, [])
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"启动回调线程失败: {ret}")

        media.venc_set_channel_desc_thread_id(self._ch_desc, tid)
        media.venc_set_channel_desc_callback(self._ch_desc,
                                              self._venc_callback)
        media.venc_set_channel_desc_entype(self._ch_desc, self.entype)
        media.venc_set_channel_desc_pic_format(self._ch_desc, PIX_FMT_NV12)
        media.venc_set_channel_desc_pic_width(self._ch_desc, self.width)
        media.venc_set_channel_desc_pic_height(self._ch_desc, self.height)
        media.venc_set_channel_desc_key_frame_interval(
            self._ch_desc, max(FPS, 1))
        media.venc_set_channel_desc_src_rate(self._ch_desc, max(FPS, 1))
        media.venc_set_channel_desc_max_bit_rate(self._ch_desc, self.bitrate)
        media.venc_set_channel_desc_rc_mode(self._ch_desc, 2)  # CBR

        ret = media.venc_create_channel(self._ch_desc)
        if ret != ACL_SUCCESS:
            raise RuntimeError(
                f"venc_create_channel 失败: {ret} (0x{ret:x})。"
                f"请运行 'dmesg | grep -i venc | tail -5' 查看详细错误。"
            )
        self._frame_cfg = media.venc_create_frame_config()

    # -- 编码一帧 ----------------------------------------------------------------

    def encode(self, nv12: np.ndarray, force_keyframe: bool = False) -> bytes:
        h, w, stride = self.height, self.width, self._stride

        # NV12 宽度补齐到 stride（16 对齐）
        padded = np.zeros(stride * h * 3 // 2, dtype=np.uint8)
        padded = padded.reshape(-1, stride)
        src = nv12.reshape(-1, w)
        for r in range(h):
            padded[r, :w] = src[r, :w]
        for r in range(h // 2):
            padded[h + r, :w] = src[h + r, :w]
        padded = padded.ravel()

        in_buf, ret = media.dvpp_malloc(padded.nbytes)
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"dvpp_malloc 输入失败: {ret}")
        acl.rt.memcpy(in_buf, padded.nbytes,
                      padded.ctypes.data, padded.nbytes,
                      ACL_MEMCPY_HOST_TO_DEVICE)

        pic = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(pic, in_buf)
        media.dvpp_set_pic_desc_size(pic, padded.nbytes)
        media.dvpp_set_pic_desc_format(pic, PIX_FMT_NV12)
        media.dvpp_set_pic_desc_width(pic, w)
        media.dvpp_set_pic_desc_height(pic, h)
        media.dvpp_set_pic_desc_width_stride(pic, stride)

        out_buf, ret = media.dvpp_malloc(self._out_buf_size)
        if ret != ACL_SUCCESS:
            media.dvpp_free(in_buf)
            media.dvpp_destroy_pic_desc(pic)
            raise RuntimeError(f"dvpp_malloc 输出失败: {ret}")

        sd = media.dvpp_create_stream_desc()
        media.dvpp_set_stream_desc_data(sd, out_buf)
        media.dvpp_set_stream_desc_size(sd, self._out_buf_size)

        if force_keyframe:
            media.venc_set_frame_config_force_i_frame(self._frame_cfg, True)

        while not self._cb_queue.empty():
            try:
                self._cb_queue.get_nowait()
            except queue.Empty:
                break

        ret = media.venc_send_frame(self._ch_desc, pic, sd,
                                     self._frame_cfg, None)
        if ret != ACL_SUCCESS:
            media.dvpp_free(in_buf)
            media.dvpp_free(out_buf)
            media.dvpp_destroy_pic_desc(pic)
            media.dvpp_destroy_stream_desc(sd)
            raise RuntimeError(f"venc_send_frame 失败: {ret}")

        try:
            encoded = self._cb_queue.get(timeout=5.0)
        except queue.Empty:
            encoded = None

        media.dvpp_free(in_buf)
        media.dvpp_free(out_buf)
        media.dvpp_destroy_stream_desc(sd)

        if force_keyframe:
            media.venc_set_frame_config_force_i_frame(self._frame_cfg, False)

        return encoded or b""

    def destroy(self):
        if self._ch_desc is not None:
            media.venc_destroy_channel(self._ch_desc)
            self._ch_desc = None
        self._running = False
        if self._frame_cfg is not None:
            media.venc_destroy_frame_config(self._frame_cfg)
            self._frame_cfg = None


# ===================================================================
# CPU 编码
# ===================================================================

def _encoder_options(codec_name: str, level: str) -> dict[str, str]:
    options = {"level": level, "tune": "zerolatency"}
    if codec_name == "libx265":
        options["x265-params"] = "log-level=error"
    return options


def bench_cpu_encode(w: int, h: int, codec_name: str, bitrate_bps: int,
                     thread_count: int
                     ) -> tuple[float, float, float]:
    """CPU 软件编码。返回 (elapsed, fps, ms_per_frame)。"""
    import av

    level = "31" if w * h <= 1280 * 720 else "40"

    codec = av.CodecContext.create(codec_name, "w")
    if hasattr(codec, "thread_count"):
        codec.thread_count = thread_count
    codec.width = w
    codec.height = h
    codec.pix_fmt = "yuv420p"
    codec.bit_rate = bitrate_bps
    codec.framerate = fractions.Fraction(FPS, 1)
    codec.time_base = fractions.Fraction(1, FPS)
    codec.options = _encoder_options(codec_name, level)
    if codec_name == "libx264":
        codec.profile = "Baseline"

    for i in range(WARMUP_FRAMES):
        y, u, v = make_test_yuv420_frame(i, w, h)
        i420 = yuv420_to_i420_ndarray(y, u, v)
        frame = av.VideoFrame.from_ndarray(i420, format="yuv420p")
        for _ in codec.encode(frame):
            pass

    t0 = time.perf_counter()
    for i in range(TEST_FRAMES):
        y, u, v = make_test_yuv420_frame(i, w, h)
        i420 = yuv420_to_i420_ndarray(y, u, v)
        frame = av.VideoFrame.from_ndarray(i420, format="yuv420p")
        for _ in codec.encode(frame):
            pass
    elapsed = time.perf_counter() - t0

    fps = TEST_FRAMES / elapsed
    ms = elapsed / TEST_FRAMES * 1000
    return elapsed, fps, ms


# ===================================================================
# 主流程
# ===================================================================

def _benchmark_codec(title: str, cpu_codec_name: str, entype: int,
                     bitrate_scale: float = 1.0) -> None:
    print(f"═══ {title} 分辨率扫描（90 帧，GOP=30）═══")
    print()
    widths = [10, 12, 12, 12]
    aligns = ["<", ">", ">", ">"]
    _print_row(
        ["分辨率", "VENC帧率", "CPU多线程", "CPU单线程"],
        widths,
        aligns,
    )
    print("─" * (sum(widths) + 2 * (len(widths) - 1)))

    for w, h in RESOLUTIONS:
        venc = None
        try:
            bitrate_kbps = max(2000, int(w * h * FPS * 0.1 / 1000 * bitrate_scale))
            bitrate_bps = bitrate_kbps * 1000

            venc = CannVenc(w, h, bitrate=bitrate_kbps, entype=entype)
            for i in range(WARMUP_FRAMES):
                y, u, v = make_test_yuv420_frame(i, w, h)
                nv12 = yuv420_to_nv12(y, u, v)
                venc.encode(nv12, force_keyframe=(i == 0))
            t0 = time.perf_counter()
            for i in range(TEST_FRAMES):
                y, u, v = make_test_yuv420_frame(i, w, h)
                nv12 = yuv420_to_nv12(y, u, v)
                venc.encode(nv12, force_keyframe=(i % TEST_GOP == 0))
            venc_t = time.perf_counter() - t0
            venc_fps = TEST_FRAMES / venc_t

            _, cpu_mt_fps, _ = bench_cpu_encode(
                w, h, cpu_codec_name, bitrate_bps, thread_count=0
            )
            _, cpu_st_fps, _ = bench_cpu_encode(
                w, h, cpu_codec_name, bitrate_bps, thread_count=1
            )

            _print_row(
                [
                    f"{w}x{h}",
                    f"{venc_fps:.1f}",
                    f"{cpu_mt_fps:.1f}",
                    f"{cpu_st_fps:.1f}",
                ],
                widths,
                aligns,
            )
        except Exception as e:
            _print_row([f"{w}x{h}", f"SKIP ({e})"], [10, 80], ["<", "<"])
        finally:
            if venc is not None:
                venc.destroy()
            gc.collect()
    print()


def main() -> int:
    np.random.seed(RANDOM_SEED)
    _ensure_acl()

    _benchmark_codec("1. H.264", "libx264", ENTYPE_H264_BASE, bitrate_scale=1.0)
    print()
    _benchmark_codec("2. H.265", "libx265", ENTYPE_H265_MAIN, bitrate_scale=0.7)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
