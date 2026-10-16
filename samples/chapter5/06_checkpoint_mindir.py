"""第5章样例 6：保存与加载（CKPT / MindIR）。

导出 CKPT 与 MindIR 到 `outputs/`，记录字节数与 SHA-256，并把 CKPT 重新加载到
新网络后做一次数值对齐，确认“文件能保存”不等于“权重能正确恢复”。
"""

from __future__ import annotations

import argparse

from chapter5_utils import BLOCKED, PASS, emit_result, ensure_output_dir, file_record, print_section, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save, reload and export a small MindSpore model.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--ckpt-name", default="chapter5_linear.ckpt", help="CKPT file name under outputs/.")
    parser.add_argument("--mindir-name", default="chapter5_linear", help="MindIR base name under outputs/ (no suffix).")
    parser.add_argument("--output", default="06_checkpoint_mindir.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    import numpy as np
    import mindspore as ms
    from mindspore import Tensor

    from chapter5_models import Fp16Linear, make_linear_dataset

    output_dir = ensure_output_dir()
    ckpt_path = output_dir / args.ckpt_name
    mindir_base = output_dir / args.mindir_name

    print_section("context")
    ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id=args.device_id)
    print(f"mindspore {ms.__version__} <- {ms.__file__}")

    x, _ = make_linear_dataset(samples=4, seed=0)
    net = Fp16Linear(in_dim=4, out_dim=1, seed=1)
    before = net(x).asnumpy()

    print_section("checkpoint")
    ms.save_checkpoint(net, str(ckpt_path))
    ckpt_record = file_record(ckpt_path)
    print(f"ckpt bytes={ckpt_record['bytes']} sha256={ckpt_record['sha256'][:16]}...")

    reloaded = Fp16Linear(in_dim=4, out_dim=1, seed=9)
    parameters = ms.load_checkpoint(str(ckpt_path))
    ms.load_param_into_net(reloaded, parameters)
    after = reloaded(x).asnumpy()
    max_abs_diff = float(np.max(np.abs(before - after)))
    print(f"reload max abs diff: {max_abs_diff}")

    print_section("mindir")
    export_status = PASS
    export_error = ""
    try:
        ms.export(
            net,
            Tensor(np.zeros((1, 4), np.float16), ms.float16),
            file_name=str(mindir_base),
            file_format="MINDIR",
        )
    except Exception as exc:
        export_status = BLOCKED
        export_error = f"{type(exc).__name__}: {exc}"[:400]
        print(f"mindir export blocked: {export_error}")

    mindir_path = mindir_base.with_suffix(".mindir")
    mindir_record = file_record(mindir_path)
    if mindir_record["exists"]:
        print(f"mindir bytes={mindir_record['bytes']} sha256={mindir_record['sha256'][:16]}...")

    aligned = max_abs_diff <= 1e-3
    status = PASS if export_status == PASS and aligned else BLOCKED

    report = {
        "device_id": args.device_id,
        "checkpoint": ckpt_record,
        "mindir": mindir_record,
        "reload_max_abs_diff": max_abs_diff,
        "aligned": aligned,
        "export_status": export_status,
        "export_error": export_error,
    }
    report_path = write_json(output_dir / args.output, report)

    emit_result(
        "06_checkpoint_mindir",
        status,
        {
            "report": str(report_path),
            "ckpt_bytes": ckpt_record.get("bytes", 0),
            "mindir_bytes": mindir_record.get("bytes", 0),
            "reload_max_abs_diff": max_abs_diff,
        },
        note=export_error,
    )
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
