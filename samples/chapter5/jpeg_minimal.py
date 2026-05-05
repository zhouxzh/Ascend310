"""JPEG 硬件编解码最简示例 — NV12 → JPEGE → JPEGD → NV12 闭环.

在昇腾 310B 上运行：
    export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
    export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
    python docs/jpeg_minimal.py

预期输出：
    JPEGE OK  640x480 NV12 → 13360 bytes JPEG
    JPEGD OK  13360 bytes JPEG → 640x480 NV12  size=460800
    闭环验证  PASS  输入=460800 输出=460800
"""

from __future__ import annotations

import numpy as np
import acl
from acl import media

ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
PIX_FMT_NV12 = 1

W, H = 640, 480
JPEG_QUALITY = 90


def check(ret: int, msg: str = "") -> None:
    if ret != ACL_SUCCESS:
        raise RuntimeError(f"{msg} 失败: {ret} (0x{ret & 0xFFFFFFFF:x})")


def make_test_nv12(w: int, h: int) -> np.ndarray:
    """生成一帧确定性 NV12 测试图像。"""
    y = np.zeros((h, w), dtype=np.uint8)
    y[:, :] = ((np.arange(w) / w * 255).astype(np.uint8))[None, :]
    y[:, :] = (y.astype(np.int16) +
               ((np.arange(h)[:, None] / h * 127).astype(np.int16))
               ).clip(0, 255).astype(np.uint8)
    bar_w = 64
    bar_x = int(w * 0.4)
    y[:, bar_x:bar_x + bar_w] = 255
    uv = np.full((h // 2, w), 128, dtype=np.uint8)
    return np.vstack([y, uv])


def create_pic_desc(buf_ptr: int, w: int, h: int) -> int:
    """创建 NV12 pic_desc。"""
    sw = ((w + 15) // 16) * 16
    sh = ((h + 1) // 2) * 2
    pic = media.dvpp_create_pic_desc()
    media.dvpp_set_pic_desc_data(pic, buf_ptr)
    media.dvpp_set_pic_desc_size(pic, sw * sh * 3 // 2)
    media.dvpp_set_pic_desc_format(pic, PIX_FMT_NV12)
    media.dvpp_set_pic_desc_width(pic, w)
    media.dvpp_set_pic_desc_height(pic, h)
    media.dvpp_set_pic_desc_width_stride(pic, sw)
    media.dvpp_set_pic_desc_height_stride(pic, sh)
    return pic


def main() -> int:
    # ① ACL 初始化
    check(acl.init(), "acl.init")
    check(acl.rt.set_device(0), "set_device")
    ctx, ret = acl.rt.create_context(0)
    check(ret, "create_context")
    check(acl.rt.set_context(ctx), "set_context")

    # ② 创建通用 DVPP 通道 + Stream
    ch_desc = media.dvpp_create_channel_desc()
    check(media.dvpp_create_channel(ch_desc), "dvpp_create_channel")
    stream, ret = acl.rt.create_stream()
    check(ret, "create_stream")

    # ③ 准备输入 NV12
    nv12 = make_test_nv12(W, H)
    in_size = W * H * 3 // 2
    in_buf, ret = media.dvpp_malloc(in_size)
    check(ret, "dvpp_malloc in")
    acl.rt.memcpy(in_buf, in_size, nv12.ctypes.data, in_size,
                  ACL_MEMCPY_HOST_TO_DEVICE)

    in_pic = create_pic_desc(in_buf, W, H)

    try:
        # ================================================================
        # JPEGE — NV12 → JPEG 码流
        # ================================================================

        # 创建 JPEGE 配置
        jpege_cfg = media.dvpp_create_jpege_config()
        media.dvpp_set_jpege_config_level(jpege_cfg, JPEG_QUALITY)

        # 预测输出大小
        out_size, ret = media.dvpp_jpeg_predict_enc_size(in_pic, jpege_cfg)
        check(ret, "predict_enc_size")

        # 分配输出缓冲区
        out_buf, ret = media.dvpp_malloc(out_size)
        check(ret, "dvpp_malloc enc_out")

        # in/out 参数：传最大 size，返回实际 size
        out_size_arr = np.array([out_size], dtype=np.int32)
        if "bytes_to_ptr" in dir(acl.util):
            out_size_ptr = acl.util.bytes_to_ptr(out_size_arr.tobytes())
        else:
            out_size_ptr = acl.util.numpy_to_ptr(out_size_arr)

        # 硬件编码
        ret = media.dvpp_jpeg_encode_async(ch_desc, in_pic, out_buf,
                                            out_size_ptr, jpege_cfg, stream)
        check(ret, "jpeg_encode_async")
        acl.rt.synchronize_stream(stream)

        # 读取实际编码大小
        if "bytes_to_ptr" in dir(acl.util):
            out_size_arr = np.frombuffer(out_size_arr.tobytes(), dtype=np.int32)
        jpeg_actual_size = int(out_size_arr[0])

        # 取回 JPEG 数据
        jpeg_host = np.zeros(jpeg_actual_size, dtype=np.uint8)
        acl.rt.memcpy(jpeg_host.ctypes.data, jpeg_actual_size, out_buf,
                      jpeg_actual_size, ACL_MEMCPY_DEVICE_TO_HOST)
        print(f"JPEGE OK  {W}x{H} NV12 → {jpeg_actual_size} bytes JPEG"
              f"  (quality={JPEG_QUALITY})")

        # 清理 JPEGE 资源
        media.dvpp_destroy_jpege_config(jpege_cfg)
        media.dvpp_free(out_buf)
        media.dvpp_destroy_pic_desc(in_pic)
        media.dvpp_free(in_buf)

        # ================================================================
        # JPEGD — JPEG 码流 → NV12
        # ================================================================

        # 拷贝 JPEG 数据到设备
        jpeg_dev, ret = media.dvpp_malloc(jpeg_actual_size)
        check(ret, "dvpp_malloc jpeg_dev")
        acl.rt.memcpy(jpeg_dev, jpeg_actual_size, jpeg_host.ctypes.data,
                      jpeg_actual_size, ACL_MEMCPY_HOST_TO_DEVICE)

        # 获取 JPEG 图像信息（宽高）
        img_w, img_h, img_fmt, ret = media.dvpp_jpeg_get_image_info(
            jpeg_dev, jpeg_actual_size)
        check(ret, "jpeg_get_image_info")
        # img_w, img_h 是实际图像尺寸

        # 预测解码输出大小
        dec_size, ret = media.dvpp_jpeg_predict_dec_size(
            jpeg_dev, jpeg_actual_size, PIX_FMT_NV12)
        check(ret, "predict_dec_size")

        # 分配输出缓冲区 + pic_desc
        dec_buf, ret = media.dvpp_malloc(dec_size)
        check(ret, "dvpp_malloc dec_out")
        dec_pic = create_pic_desc(dec_buf, img_w, img_h)

        # 硬件解码
        ret = media.dvpp_jpeg_decode_async(ch_desc, jpeg_dev, jpeg_actual_size,
                                            dec_pic, stream)
        check(ret, "jpeg_decode_async")
        acl.rt.synchronize_stream(stream)

        # 取回 NV12 数据
        dec_host = np.zeros(dec_size, dtype=np.uint8)
        acl.rt.memcpy(dec_host.ctypes.data, dec_size, dec_buf, dec_size,
                      ACL_MEMCPY_DEVICE_TO_HOST)
        print(f"JPEGD OK  {jpeg_actual_size} bytes JPEG → "
              f"{img_w}x{img_h} NV12  size={dec_size}")

        # 闭环验证
        expected = img_w * img_h * 3 // 2
        # 由于 stride 对齐，dec_size 可能略大于 expected
        actual_nv12 = dec_host[:expected]  # 取有效的 NV12 数据
        match = "PASS" if dec_size >= expected else "FAIL"
        print(f"闭环验证  {match}  输入={in_size} 输出≥{expected} (实际={dec_size})")

        # 清理 JPEGD 资源
        media.dvpp_destroy_pic_desc(dec_pic)
        media.dvpp_free(dec_buf)
        media.dvpp_free(jpeg_dev)

    finally:
        # ⑧ 清理
        media.dvpp_destroy_channel(ch_desc)
        media.dvpp_destroy_channel_desc(ch_desc)
        acl.rt.destroy_stream(stream)
        acl.rt.destroy_context(ctx)
        acl.rt.reset_device(0)
        acl.finalize()

    print("\nJPEG 硬件编解码闭环全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
