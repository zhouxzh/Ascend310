from __future__ import annotations

import argparse
import time
from typing import Any

import numpy as np

from perf_utils import (
    DEFAULT_OUTPUT_DIR,
    deterministic_nv12_frame,
    make_summary_row,
    parse_resolution,
    print_stage_table,
    summarize_stages,
    write_report,
)


ACL_SUCCESS = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
PIX_FMT_NV12 = 1

acl: Any | None = None
media: Any | None = None
cv2: Any | None = None


def import_acl_media():
    global acl, media
    if acl is None or media is None:
        import acl as acl_module
        from acl import media as media_module

        acl = acl_module
        media = media_module
    return acl, media


def import_cv2():
    global cv2
    if cv2 is None:
        import cv2 as cv2_module

        cv2 = cv2_module
    return cv2


def check_ret(result: Any, message: str) -> None:
    ret = result[-1] if isinstance(result, tuple) else result
    if ret != ACL_SUCCESS:
        recent = ""
        if acl is not None and hasattr(acl, "get_recent_err_msg"):
            recent = f", recent_error={acl.get_recent_err_msg()}"
        raise RuntimeError(f"{message} failed, ret={ret}{recent}")


def stride_size(width: int, height: int) -> tuple[int, int, int]:
    width_stride = ((width + 15) // 16) * 16
    height_stride = ((height + 1) // 2) * 2
    return width_stride, height_stride, width_stride * height_stride * 3 // 2


class AclDvppSession:
    def __init__(self, device_id: int) -> None:
        self.acl, _ = import_acl_media()
        self.device_id = int(device_id)
        self.context = None
        self.initialized = False

    def __enter__(self) -> "AclDvppSession":
        ret = self.acl.init()
        if ret not in (0, 100002):
            check_ret(ret, "acl.init")
        self.initialized = True
        check_ret(self.acl.rt.set_device(self.device_id), "acl.rt.set_device")
        self.context, ret = self.acl.rt.create_context(self.device_id)
        check_ret(ret, "acl.rt.create_context")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            self.acl.rt.destroy_context(self.context)
            self.context = None
        if self.initialized:
            self.acl.rt.reset_device(self.device_id)
            self.acl.finalize()
            self.initialized = False


class VpcResizer:
    def __init__(self) -> None:
        self.acl, self.media = import_acl_media()
        self.channel_desc = self.media.dvpp_create_channel_desc()
        check_ret(self.media.dvpp_create_channel(self.channel_desc), "dvpp_create_channel")
        self.stream, ret = self.acl.rt.create_stream()
        check_ret(ret, "acl.rt.create_stream")
        self.resize_config = self.media.dvpp_create_resize_config()

    def resize(self, nv12: np.ndarray, width: int, height: int, out_width: int, out_height: int) -> bytes:
        in_width_stride, in_height_stride, in_size = stride_size(width, height)
        out_width_stride, out_height_stride, out_size = stride_size(out_width, out_height)

        in_buf, ret = self.media.dvpp_malloc(in_size)
        check_ret(ret, "dvpp_malloc input")
        out_buf, ret = self.media.dvpp_malloc(out_size)
        check_ret(ret, "dvpp_malloc output")

        in_pic = None
        out_pic = None
        try:
            check_ret(
                self.acl.rt.memcpy(
                    in_buf,
                    nv12.nbytes,
                    nv12.ctypes.data,
                    nv12.nbytes,
                    ACL_MEMCPY_HOST_TO_DEVICE,
                ),
                "acl.rt.memcpy host_to_device",
            )

            in_pic = self.media.dvpp_create_pic_desc()
            self.media.dvpp_set_pic_desc_data(in_pic, in_buf)
            self.media.dvpp_set_pic_desc_size(in_pic, in_size)
            self.media.dvpp_set_pic_desc_format(in_pic, PIX_FMT_NV12)
            self.media.dvpp_set_pic_desc_width(in_pic, width)
            self.media.dvpp_set_pic_desc_height(in_pic, height)
            self.media.dvpp_set_pic_desc_width_stride(in_pic, in_width_stride)
            self.media.dvpp_set_pic_desc_height_stride(in_pic, in_height_stride)

            out_pic = self.media.dvpp_create_pic_desc()
            self.media.dvpp_set_pic_desc_data(out_pic, out_buf)
            self.media.dvpp_set_pic_desc_size(out_pic, out_size)
            self.media.dvpp_set_pic_desc_format(out_pic, PIX_FMT_NV12)
            self.media.dvpp_set_pic_desc_width(out_pic, out_width)
            self.media.dvpp_set_pic_desc_height(out_pic, out_height)
            self.media.dvpp_set_pic_desc_width_stride(out_pic, out_width_stride)
            self.media.dvpp_set_pic_desc_height_stride(out_pic, out_height_stride)

            check_ret(
                self.media.dvpp_vpc_resize_async(
                    self.channel_desc,
                    in_pic,
                    out_pic,
                    self.resize_config,
                    self.stream,
                ),
                "dvpp_vpc_resize_async",
            )
            check_ret(self.acl.rt.synchronize_stream(self.stream), "acl.rt.synchronize_stream")

            host = np.empty(out_size, dtype=np.uint8)
            check_ret(
                self.acl.rt.memcpy(
                    host.ctypes.data,
                    out_size,
                    out_buf,
                    out_size,
                    ACL_MEMCPY_DEVICE_TO_HOST,
                ),
                "acl.rt.memcpy device_to_host",
            )
            return bytes(host)
        finally:
            if in_pic is not None:
                self.media.dvpp_destroy_pic_desc(in_pic)
            if out_pic is not None:
                self.media.dvpp_destroy_pic_desc(out_pic)
            self.media.dvpp_free(in_buf)
            self.media.dvpp_free(out_buf)

    def destroy(self) -> None:
        self.media.dvpp_destroy_resize_config(self.resize_config)
        self.media.dvpp_destroy_channel(self.channel_desc)
        self.media.dvpp_destroy_channel_desc(self.channel_desc)
        self.acl.rt.destroy_stream(self.stream)


def cpu_resize_nv12(nv12: np.ndarray, width: int, height: int, out_width: int, out_height: int) -> np.ndarray:
    cv2_module = import_cv2()
    y_plane = nv12[:height, :width]
    uv_plane = nv12[height:, :width]
    y_out = cv2_module.resize(y_plane, (out_width, out_height), interpolation=cv2_module.INTER_LINEAR)
    uv_out = cv2_module.resize(uv_plane, (out_width, out_height // 2), interpolation=cv2_module.INTER_LINEAR)
    return np.vstack([y_out, uv_out])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare DVPP VPC resize with CPU resize on NV12 frames.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup frames.")
    parser.add_argument("--frames", type=int, default=60, help="Measured frames.")
    parser.add_argument("--resolution", default="1920x1080", help="Input resolution, for example 1920x1080.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR / "dvpp_resize_vs_cpu.json"),
        help="Output metrics JSON path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    width, height = parse_resolution(args.resolution)
    out_width, out_height = width // 2, height // 2
    frames = [
        deterministic_nv12_frame(index, width, height) for index in range(args.warmup + args.frames)
    ]

    vpc_samples = []
    with AclDvppSession(args.device):
        vpc = VpcResizer()
        try:
            for index in range(args.warmup):
                vpc.resize(frames[index], width, height, out_width, out_height)
            for index in range(args.frames):
                start = time.perf_counter()
                vpc.resize(frames[args.warmup + index], width, height, out_width, out_height)
                vpc_samples.append((time.perf_counter() - start) * 1000.0)
        finally:
            vpc.destroy()

    cpu_samples = []
    for index in range(args.warmup):
        cpu_resize_nv12(frames[index], width, height, out_width, out_height)
    for index in range(args.frames):
        start = time.perf_counter()
        cpu_resize_nv12(frames[args.warmup + index], width, height, out_width, out_height)
        cpu_samples.append((time.perf_counter() - start) * 1000.0)

    metrics = {
        "dvpp_vpc_resize": summarize_stages({"resize": vpc_samples})["resize"],
        "cpu_cv2_resize": summarize_stages({"resize": cpu_samples})["resize"],
    }
    vpc_mean = float(metrics["dvpp_vpc_resize"]["mean_ms"])
    cpu_mean = float(metrics["cpu_cv2_resize"]["mean_ms"])
    speedup = cpu_mean / vpc_mean if vpc_mean else 0.0

    report = {
        "case": "04_dvpp_resize_vs_cpu",
        "resolution": f"{width}x{height}",
        "output_resolution": f"{out_width}x{out_height}",
        "device": args.device,
        "warmup": args.warmup,
        "frames": args.frames,
        "variants": metrics,
        "summary_rows": [
            make_summary_row(
                "DVPP VPC Resize",
                "cpu_cv2_resize",
                cpu_samples,
                note="CPU resize NV12 Y/UV",
            ),
            make_summary_row(
                "DVPP VPC Resize",
                "dvpp_vpc_resize",
                vpc_samples,
                speedup=speedup,
                note="DVPP VPC resize + H2D/D2H",
            ),
        ],
    }
    output_path = write_report(args.output, report)
    print_stage_table(metrics)
    print(f"\nspeedup(mean CPU/DVPP): {speedup:.3f}x")
    print(f"metrics saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
