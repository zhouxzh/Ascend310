"""VPC 硬件图像处理最简示例 — Resize / Crop+Resize.

在昇腾 310B 上运行：
    export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
    export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
    python samples/chapter5/vpc/vpc_minimal.py

预期输出：
    VPC Resize     OK  640x480 → 320x240  大小=115200 bytes
    VPC CropResize OK  640x480 → 640x480 (中心裁剪后放大)  大小=460800 bytes

注意：310B 不支持 dvpp_vpc_convert_color_async（CSC 色彩空间转换）。
    YUYV→NV12 转换需用 CPU（cv2.cvtColor + bgr_to_nv12），或等待 himpi 通道方案验证。
"""

from __future__ import annotations

import numpy as np
import acl
from acl import media

# ---- 常量 ----------------------------------------------------------------
ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
PIX_FMT_NV12 = 1            # PIXEL_FORMAT_YUV_SEMIPLANAR_420

W, H = 640, 480


# ---- 辅助函数 -------------------------------------------------------------

def check(ret: int, msg: str = "") -> None:
    if ret != ACL_SUCCESS:
        raise RuntimeError(f"{msg} 失败: {ret} (0x{ret & 0xFFFFFFFF:x})")


def make_test_nv12(w: int, h: int) -> np.ndarray:
    """生成一帧确定性 NV12 测试图像（Y 渐变 + UV=128 灰色）。"""
    y = np.zeros((h, w), dtype=np.uint8)
    y[:, :] = ((np.arange(w) / w * 255).astype(np.uint8))[None, :]
    y[:, :] = (y.astype(np.int16) +
               ((np.arange(h)[:, None] / h * 127).astype(np.int16))
               ).clip(0, 255).astype(np.uint8)
    uv = np.full((h // 2, w), 128, dtype=np.uint8)
    return np.vstack([y, uv])


def create_nv12_pic_desc(buf_ptr: int, w: int, h: int,
                        stride_w: int | None = None,
                        stride_h: int | None = None) -> int:
    """创建 NV12 pic_desc。"""
    if stride_w is None:
        stride_w = ((w + 15) // 16) * 16  # 对齐到 16
    if stride_h is None:
        stride_h = ((h + 1) // 2) * 2    # 对齐到 2

    size = stride_w * stride_h * 3 // 2    # 按 stride 算总大小
    pic = media.dvpp_create_pic_desc()
    media.dvpp_set_pic_desc_data(pic, buf_ptr)
    media.dvpp_set_pic_desc_size(pic, size)
    media.dvpp_set_pic_desc_format(pic, PIX_FMT_NV12)
    media.dvpp_set_pic_desc_width(pic, w)
    media.dvpp_set_pic_desc_height(pic, h)
    media.dvpp_set_pic_desc_width_stride(pic, stride_w)
    media.dvpp_set_pic_desc_height_stride(pic, stride_h)
    return pic


def device_to_host(dev_ptr: int, size: int) -> np.ndarray:
    """将设备内存拷贝到主机 numpy 数组。"""
    host = np.zeros(size, dtype=np.uint8)
    acl.rt.memcpy(host.ctypes.data, size, dev_ptr, size,
                  ACL_MEMCPY_DEVICE_TO_HOST)
    return host


# ---- 主流程 ---------------------------------------------------------------

def main() -> int:
    # ① ACL 初始化（四步咒语）
    check(acl.init(), "acl.init")
    check(acl.rt.set_device(0), "set_device")
    ctx, ret = acl.rt.create_context(0)
    check(ret, "create_context")
    check(acl.rt.set_context(ctx), "set_context")

    # ② 创建 VPC 通用通道 + Stream
    ch_desc = media.dvpp_create_channel_desc()
    check(media.dvpp_create_channel(ch_desc), "dvpp_create_channel")
    stream, ret = acl.rt.create_stream()
    check(ret, "create_stream")

    try:
        # ================================================================
        # 演示 1：Resize — 640×480 → 320×240
        # ================================================================
        src = make_test_nv12(W, H)
        in_size = W * H * 3 // 2
        out_w, out_h = 320, 240
        out_size = out_w * out_h * 3 // 2

        in_buf, ret = media.dvpp_malloc(in_size)
        check(ret, "dvpp_malloc in")
        out_buf, ret = media.dvpp_malloc(out_size)
        check(ret, "dvpp_malloc out")

        acl.rt.memcpy(in_buf, in_size, src.ctypes.data, in_size,
                      ACL_MEMCPY_HOST_TO_DEVICE)

        in_pic = create_nv12_pic_desc(in_buf, W, H)
        out_pic = create_nv12_pic_desc(out_buf, out_w, out_h)

        resize_cfg = media.dvpp_create_resize_config()
        ret = media.dvpp_vpc_resize_async(ch_desc, in_pic, out_pic,
                                           resize_cfg, stream)
        check(ret, "vpc_resize_async")
        acl.rt.synchronize_stream(stream)                   # ★ 同步等待

        result = device_to_host(out_buf, out_size)
        print(f"VPC Resize    OK  {W}x{H} → {out_w}x{out_h}  "
              f"大小={result.nbytes} bytes")

        # 清理本轮资源
        media.dvpp_destroy_resize_config(resize_cfg)
        media.dvpp_destroy_pic_desc(in_pic)
        media.dvpp_destroy_pic_desc(out_pic)
        media.dvpp_free(in_buf)
        media.dvpp_free(out_buf)

        # ================================================================
        # 演示 2：Crop + Resize — 裁中心 320×240 再放大到 640×480
        # ================================================================
        src2 = make_test_nv12(W, H)
        in2_buf, ret = media.dvpp_malloc(in_size)
        check(ret, "dvpp_malloc in2")
        out2_buf, ret = media.dvpp_malloc(in_size)
        check(ret, "dvpp_malloc out2")

        acl.rt.memcpy(in2_buf, in_size, src2.ctypes.data, in_size,
                      ACL_MEMCPY_HOST_TO_DEVICE)

        in2_pic = create_nv12_pic_desc(in2_buf, W, H)
        out2_pic = create_nv12_pic_desc(out2_buf, W, H)

        # 裁剪中心 320×240
        crop_left, crop_right = 160, 479
        crop_top, crop_bottom = 120, 359
        crop_area = media.dvpp_create_roi_config(
            crop_left, crop_right, crop_top, crop_bottom)

        resize_cfg2 = media.dvpp_create_resize_config()
        ret = media.dvpp_vpc_crop_resize_async(ch_desc, in2_pic, out2_pic,
                                                crop_area, resize_cfg2, stream)
        check(ret, "vpc_crop_resize_async")
        acl.rt.synchronize_stream(stream)

        result2 = device_to_host(out2_buf, in_size)
        print(f"VPC CropResize OK  {W}x{H} → {W}x{H} "
              f"(中心{crop_right - crop_left}x{crop_bottom - crop_top}裁剪后放大)  "
              f"大小={result2.nbytes} bytes")

        media.dvpp_destroy_roi_config(crop_area)
        media.dvpp_destroy_resize_config(resize_cfg2)
        media.dvpp_destroy_pic_desc(in2_pic)
        media.dvpp_destroy_pic_desc(out2_pic)
        media.dvpp_free(in2_buf)
        media.dvpp_free(out2_buf)

    finally:
        # ③ 清理
        media.dvpp_destroy_channel(ch_desc)
        media.dvpp_destroy_channel_desc(ch_desc)
        acl.rt.destroy_stream(stream)
        acl.rt.destroy_context(ctx)
        acl.rt.reset_device(0)
        acl.finalize()

    print("\n全部通过 — VPC 已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
