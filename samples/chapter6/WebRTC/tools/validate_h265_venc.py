"""Validate Ascend CANN VENC H.265 elementary stream output."""

from pathlib import Path

import numpy as np

from webrtc_app.cann_encoder import CannVenc, ENTYPE_H265_MAIN
from webrtc_app.hevc import (
    HEVC_NAL_IDR_N_LP,
    HEVC_NAL_IDR_W_RADL,
    HEVC_NAL_PPS,
    HEVC_NAL_SPS,
    HEVC_NAL_VPS,
    hevc_nal_type,
    split_annexb,
)


def make_nv12_frame(width: int, height: int, index: int) -> np.ndarray:
    y = np.empty((height, width), dtype=np.uint8)
    x_gradient = np.linspace(16, 235, width, dtype=np.uint8)
    y[:] = (x_gradient.astype(np.uint16) + index * 3) % 220 + 16

    uv = np.empty((height // 2, width), dtype=np.uint8)
    uv[:, 0::2] = 96 + (index % 32)
    uv[:, 1::2] = 144 - (index % 32)
    return np.vstack([y, uv])


def main() -> None:
    width = 640
    height = 480
    fps = 30
    output_path = Path("output/test.h265")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    venc = CannVenc(width=width, height=height, fps=fps, entype=ENTYPE_H265_MAIN)
    try:
        stream = bytearray()
        for index in range(10):
            frame = make_nv12_frame(width, height, index)
            stream += venc.encode(frame, force_keyframe=(index == 0))
    finally:
        venc.destroy()

    output_path.write_bytes(stream)
    nals = list(split_annexb(bytes(stream)))
    nal_types = [hevc_nal_type(nal) for nal in nals]
    wanted = {
        HEVC_NAL_VPS,
        HEVC_NAL_SPS,
        HEVC_NAL_PPS,
        HEVC_NAL_IDR_W_RADL,
        HEVC_NAL_IDR_N_LP,
    }
    found = sorted(set(nal_types) & wanted)

    print(f"wrote {output_path} bytes={len(stream)} nals={len(nals)}")
    print(f"nal_types={nal_types}")
    print(f"key_markers={found}")
    if not stream:
        raise SystemExit("H265 VENC produced an empty stream")
    if not found:
        raise SystemExit("H265 stream did not contain VPS/SPS/PPS/IDR markers")


if __name__ == "__main__":
    main()
