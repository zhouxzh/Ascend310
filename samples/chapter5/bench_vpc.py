"""VPC 硬件 vs CPU 图像缩放性能对比.

分辨率扫描 480p → 4K, NV12 → ½ 缩放, 确定性测试帧。

    python docs/bench_vpc.py
"""

from __future__ import annotations

import time
import numpy as np
import acl
from acl import media
import cv2

# ===================================================================
# 测试参数
# ===================================================================

RESOLUTIONS = [
    (640, 480),
    (1280, 720),
    (1920, 1080),
    (2560, 1440),
    (3840, 2160),
]

TEST_FRAMES = 60
WARMUP_FRAMES = 3
RANDOM_SEED = 42

ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
PIX_FMT_NV12 = 1

_ACL_CTX = None
_ACL_READY = False


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


def stride_size(w: int, h: int) -> tuple[int, int, int]:
    """返回对齐后的 stride 和缓冲区大小。"""
    sw = ((w + 15) // 16) * 16
    sh = ((h + 1) // 2) * 2
    return sw, sh, sw * sh * 3 // 2


# ===================================================================
# 测试帧生成
# ===================================================================

def make_test_nv12_frames(n: int, w: int, h: int) -> list[np.ndarray]:
    """生成 n 帧确定性 NV12。"""
    frames = []
    for i in range(n):
        y = np.zeros((h, w), dtype=np.uint8)
        y[:, :] = ((np.arange(w) / w * 255 + i * 4) % 255).astype(np.uint8)
        y[:, :] = (y.astype(np.int16) +
                   ((np.arange(h)[:, None] / h * 127 + i * 3) % 128
                    ).astype(np.int16)).clip(0, 255).astype(np.uint8)
        bar_x = int((np.sin(i / 15.0) + 1) * 0.5 * (w - 64))
        y[:, bar_x:bar_x + 64] = 255
        xx, yy = np.meshgrid(np.arange(32), np.arange(32))
        chess = ((xx // 8 + yy // 8) % 2 == 0)
        y[:32, :32][chess] = 255
        uv = np.full((h // 2, w), 128, dtype=np.uint8)
        frames.append(np.vstack([y, uv]))
    return frames


# ===================================================================
# VPC Resize 封装
# ===================================================================

class VpcResizer:
    """VPC 硬件缩放器。"""

    def __init__(self):
        _ensure_acl()
        self._ch_desc = media.dvpp_create_channel_desc()
        ret = media.dvpp_create_channel(self._ch_desc)
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"dvpp_create_channel = {ret}")
        self._stream, ret = acl.rt.create_stream()
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"create_stream = {ret}")
        self._resize_cfg = media.dvpp_create_resize_config()

    def resize(self, nv12: np.ndarray, w: int, h: int,
               out_w: int, out_h: int) -> bytes:
        """VPC 硬件缩放一帧。"""
        in_sw, in_sh, in_size = stride_size(w, h)
        out_sw, out_sh, out_size = stride_size(out_w, out_h)

        in_buf, _ = media.dvpp_malloc(in_size)
        out_buf, _ = media.dvpp_malloc(out_size)

        # 拷贝原始数据（无 padding）
        acl.rt.memcpy(in_buf, nv12.nbytes, nv12.ctypes.data, nv12.nbytes,
                      ACL_MEMCPY_HOST_TO_DEVICE)

        in_pic = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(in_pic, in_buf)
        media.dvpp_set_pic_desc_size(in_pic, in_size)
        media.dvpp_set_pic_desc_format(in_pic, PIX_FMT_NV12)
        media.dvpp_set_pic_desc_width(in_pic, w)
        media.dvpp_set_pic_desc_height(in_pic, h)
        media.dvpp_set_pic_desc_width_stride(in_pic, in_sw)
        media.dvpp_set_pic_desc_height_stride(in_pic, in_sh)

        out_pic = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(out_pic, out_buf)
        media.dvpp_set_pic_desc_size(out_pic, out_size)
        media.dvpp_set_pic_desc_format(out_pic, PIX_FMT_NV12)
        media.dvpp_set_pic_desc_width(out_pic, out_w)
        media.dvpp_set_pic_desc_height(out_pic, out_h)
        media.dvpp_set_pic_desc_width_stride(out_pic, out_sw)
        media.dvpp_set_pic_desc_height_stride(out_pic, out_sh)

        media.dvpp_vpc_resize_async(self._ch_desc, in_pic, out_pic,
                                     self._resize_cfg, self._stream)
        acl.rt.synchronize_stream(self._stream)

        host = np.zeros(out_size, dtype=np.uint8)
        acl.rt.memcpy(host.ctypes.data, out_size, out_buf, out_size,
                      ACL_MEMCPY_DEVICE_TO_HOST)

        media.dvpp_destroy_pic_desc(in_pic)
        media.dvpp_destroy_pic_desc(out_pic)
        media.dvpp_free(in_buf)
        media.dvpp_free(out_buf)
        return bytes(host)

    def destroy(self):
        media.dvpp_destroy_resize_config(self._resize_cfg)
        media.dvpp_destroy_channel(self._ch_desc)
        media.dvpp_destroy_channel_desc(self._ch_desc)
        acl.rt.destroy_stream(self._stream)


# ===================================================================
# CPU Resize 封装（对比基准）
# ===================================================================

def cpu_resize(nv12: np.ndarray, w: int, h: int,
               out_w: int, out_h: int) -> np.ndarray:
    """CPU 缩放 NV12（Y 和 UV 分别缩放）。"""
    y = nv12[:h, :w].copy()
    uv = nv12[h:, :w].copy()
    y_out = cv2.resize(y, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    uv_out = cv2.resize(uv, (out_w, out_h // 2), interpolation=cv2.INTER_LINEAR)
    return np.vstack([y_out, uv_out])


# ===================================================================
# 主流程
# ===================================================================

def main() -> int:
    np.random.seed(RANDOM_SEED)
    _ensure_acl()
    sep = "─" * 85

    print("═══ VPC Resize: NV12 → ½ 缩放 ═══")
    print()
    print(f"{'Resolution':<16} {'VPC_fps':>8} {'CPU_fps':>8} "
          f"{'Speedup':>7} {'VPC_ms':>7} {'CPU_ms':>7} {'Winner':>6}")
    print(sep)

    for w, h in RESOLUTIONS:
        out_w, out_h = w // 2, h // 2
        nv12_frames = make_test_nv12_frames(TEST_FRAMES + WARMUP_FRAMES, w, h)

        vpc = VpcResizer()
        try:
            # 预热
            for i in range(WARMUP_FRAMES):
                vpc.resize(nv12_frames[i], w, h, out_w, out_h)
            # 正式测试
            t0 = time.perf_counter()
            for i in range(TEST_FRAMES):
                vpc.resize(nv12_frames[WARMUP_FRAMES + i], w, h, out_w, out_h)
            vpc_t = time.perf_counter() - t0
        finally:
            vpc.destroy()

        # CPU 对比
        for i in range(WARMUP_FRAMES):
            cpu_resize(nv12_frames[i], w, h, out_w, out_h)
        t0 = time.perf_counter()
        for i in range(TEST_FRAMES):
            cpu_resize(nv12_frames[WARMUP_FRAMES + i], w, h, out_w, out_h)
        cpu_t = time.perf_counter() - t0

        vpc_fps = TEST_FRAMES / vpc_t
        cpu_fps = TEST_FRAMES / cpu_t
        speedup = vpc_fps / cpu_fps
        winner = "VPC" if speedup > 1.0 else "CPU"

        print(f"{w}x{h:<9} {vpc_fps:>8.1f} {cpu_fps:>8.1f} "
              f"{speedup:>5.2f}x {vpc_t/TEST_FRAMES*1000:>6.1f}ms "
              f"{cpu_t/TEST_FRAMES*1000:>6.1f}ms {winner:>6}")

    print()
    print("结果解读")
    print("  VPC 硬件缩放预期在各分辨率下领先 CPU（参考 VENC 的 5×~10× 加速比）。")
    print("  310B 不支持纯 CSC 色彩转换，YUYV→NV12 仍需 CPU 完成。")
    print("  但 resize 可以卸载到 VPC，CPU 只做颜色转换，大幅降低 CPU 占用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
