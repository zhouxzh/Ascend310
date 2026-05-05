"""acllite VPC 最简示例 — 一行代码完成 resize / crop / JPEG 编解码。

CANN 自带的 acllite 封装了 DVPP 通道管理、stride 对齐、Stream 同步等细节。
大部分场景下比裸调 acl.media 更推荐。

在昇腾 310B 上运行：
    export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
    export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/thirdpart/aarch64/acllite:$PYTHONPATH"
    python samples/chapter5/vpc/vpc_acllite_demo.py

预期输出：
    Resize        OK  640x480 → 320x240   size=115200
    CropPaste     OK  640x480 → 224x224   size=75264
    JPEG Encode   OK  320x240  →  1842 bytes
    JPEG Decode   OK   1842 bytes → 320x240  size=138240
"""

from __future__ import annotations

import numpy as np
from acllite_imageproc import AclLiteImageProc
from acllite_image import AclLiteImage
from acllite_resource import AclLiteResource

W, H = 640, 480


def make_test_nv12(w: int, h: int) -> np.ndarray:
    """生成一帧确定性 NV12 测试图像。"""
    y = np.zeros((h, w), dtype=np.uint8)
    y[:, :] = ((np.arange(w) / w * 255).astype(np.uint8))[None, :]
    y[:, :] = (y.astype(np.int16) +
               ((np.arange(h)[:, None] / h * 127).astype(np.int16))
               ).clip(0, 255).astype(np.uint8)
    uv = np.full((h // 2, w), 128, dtype=np.uint8)
    return np.vstack([y, uv])


def main() -> int:
    # ① 初始化 ACL（一行替代四步咒语）
    acl_res = AclLiteResource()
    acl_res.init()
    print("ACL init OK")

    # ② 创建 VPC + JPEG 处理器（一行）
    vpc = AclLiteImageProc()
    print("VPC  create OK")

    # 准备输入
    nv12 = make_test_nv12(W, H)
    img = AclLiteImage(nv12, W, H).copy_to_dvpp()

    # ③ Resize
    resized = vpc.resize(img, 320, 240)
    assert resized is not None, "Resize 失败"
    print(f"Resize        OK  {W}x{H} → {resized.width}x{resized.height}"
          f"   size={resized.size}")

    # ④ Crop and Paste
    cropped = vpc.crop_and_paste(img, W, H, 224, 224)
    assert cropped is not None, "Crop 失败"
    print(f"CropPaste     OK  {W}x{H} → {cropped.width}x{cropped.height}"
          f"   size={cropped.size}")

    # ⑤ JPEG 编码 (NV12 → JPEG)
    jpeg_img = vpc.jpege(resized)
    assert jpeg_img is not None, "JPEG 编码失败"
    jpeg_data = jpeg_img.byte_data_to_np_array()
    print(f"JPEG Encode   OK  {resized.width}x{resized.height}  → "
          f" {len(jpeg_data)} bytes")

    # ⑥ JPEG 解码 (JPEG → NV12)
    decoded = vpc.jpegd(jpeg_img)
    assert decoded is not None, "JPEG 解码失败"
    print(f"JPEG Decode   OK   {len(jpeg_data)} bytes → "
          f"{decoded.width}x{decoded.height}  size={decoded.size}")

    # ⑦ 清理
    vpc.destroy()
    # AclLiteResource 在 __del__ 中自动释放，无需显式调用
    print("\nacllite VPC 全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
