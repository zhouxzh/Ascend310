"""acllite VDEC 最简示例 — libx264 编码 → DvppVdec 硬件解码。

CANN 自带的 acllite 封装了 VDEC 的回调线程、通道创建、帧队列等细节。

在昇腾 310B 上运行：
    export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
    export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/thirdpart/aarch64/acllite:$PYTHONPATH"
    python samples/chapter5/vdec/vdec_acllite_demo.py

预期输出：
    libx264: BGR 640x480 → H.264 ~168KB OK
    VDEC init OK
    VDEC decode OK  460800 bytes = 640x480x3/2
"""

from __future__ import annotations

import fractions
import numpy as np
import av
import acl
from acllite_resource import AclLiteResource
from dvpp_vdec import DvppVdec
import constants as const

W, H = 640, 480


def main() -> int:
    # ① 初始化 ACL
    acl_res = AclLiteResource()
    acl_res.init()
    ctx = acl.rt.get_context(0)[1]
    print("ACL init OK")

    # ② 用 libx264 生成一帧 H.264 测试码流
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height = W, H
    codec.pix_fmt = "yuv420p"
    codec.bit_rate = 2_000_000
    codec.framerate = fractions.Fraction(30, 1)
    codec.time_base = fractions.Fraction(1, 30)
    codec.options = {"level": "31", "tune": "zerolatency"}
    codec.profile = "Baseline"

    bgr = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
    frame = av.VideoFrame.from_ndarray(bgr[..., ::-1], format="rgb24")

    h264_data = bytearray()
    for pkt in codec.encode(frame):
        h264_data += bytes(pkt)
    for pkt in codec.encode(None):
        h264_data += bytes(pkt)

    h264_arr = np.frombuffer(bytes(h264_data), dtype=np.uint8)
    print(f"libx264: BGR {W}x{H} → H.264 {len(h264_arr)} bytes  OK")

    # ③ 拷贝 H.264 到 DVPP 内存
    in_size = len(h264_arr)
    in_buf, ret = acl.media.dvpp_malloc(in_size)
    assert ret == 0, f"dvpp_malloc 失败: {ret}"
    acl.rt.memcpy(in_buf, in_size, h264_arr.ctypes.data, in_size,
                  const.ACL_MEMCPY_HOST_TO_DEVICE)

    # ④ 创建 DvppVdec + 解码
    vdec = DvppVdec(channel_id=0, width=W, height=H,
                    entype=const.ENTYPE_H264_BASE, ctx=ctx)
    ret = vdec.init()
    assert ret == const.SUCCESS, f"VDEC init 失败: {ret}"
    print("VDEC init OK")

    ret = vdec.process(in_buf, in_size, user_data=(0, 0))
    assert ret == const.SUCCESS, f"VDEC process 失败: {ret}"

    # ⑤ 读取解码帧
    ret, image = vdec.read()
    assert ret == const.SUCCESS and image is not None, \
        f"VDEC read 失败: ret={ret}"
    nv12 = image.byte_data_to_np_array()
    expected = W * H * 3 // 2
    assert len(nv12) == expected, \
        f"size 不匹配: {len(nv12)} != {expected}"
    print(f"VDEC decode OK  {len(nv12)} bytes = {W}x{H}x3/2")

    # ⑥ 清理
    vdec.destroy()
    acl.media.dvpp_free(in_buf)
    # AclLiteResource 在 __del__ 中自动释放，无需显式调用
    print("\nacllite VDEC 全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
