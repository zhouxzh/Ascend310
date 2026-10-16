"""第5章样例 2：用 Cell 与 ops 构建网络。

本板实测结论：`nn.Dense` 的 fp32 路径在 310B4 上并不可靠（单层前向可能通过，
多层组合前向会被 `EZ1001` 拒绝），而显式 fp16 参数 + `ops.matmul` 的网络可以
稳定运行。因此本样例用 fp16 网络给出可用写法，并把三种“会失败的写法”放到
独立子进程里记录真实返回码与报错。

- `--skip-probes`：跳过三个边界探测，只跑可用的 fp16 网络。
"""

from __future__ import annotations

import argparse

from chapter5_utils import (
    BLOCKED,
    FAIL,
    PASS,
    emit_result,
    ensure_output_dir,
    first_ascend_message,
    parse_last_result,
    print_section,
    run_isolated,
    tail_lines,
    write_json,
)

DENSE_FP32_PROBE = """
import json
import mindspore as ms
from mindspore import Tensor, nn

ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id={device_id})
try:
    net = nn.Dense(4, 2)
    value = net(Tensor([[1.0, 2.0, 3.0, 4.0]], ms.float32)).asnumpy()
    print("RESULT " + json.dumps({{"status": "pass", "shape": list(value.shape)}}))
except Exception as exc:
    print("RESULT " + json.dumps({{"status": "fail", "error": (type(exc).__name__ + ": " + str(exc))[:300]}}))
"""

MLP_FP32_PROBE = """
import json
import mindspore as ms
from mindspore import Tensor, nn

ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id={device_id})
try:
    net = nn.SequentialCell([nn.Dense(4, 8), nn.ReLU(), nn.Dense(8, 1)])
    value = net(Tensor([[1.0, 2.0, 3.0, 4.0]], ms.float32)).asnumpy()
    print("RESULT " + json.dumps({{"status": "pass", "shape": list(value.shape)}}))
except Exception as exc:
    print("RESULT " + json.dumps({{"status": "fail", "error": (type(exc).__name__ + ": " + str(exc))[:300]}}))
"""

FP16_CAST_PROBE = """
import json
import mindspore as ms
from mindspore import Tensor, nn

ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id={device_id})
net = nn.Dense(4, 2).to_float(ms.float16)
value = net(Tensor([[1.0, 2.0, 3.0, 4.0]], ms.float16)).asnumpy()
print("RESULT " + json.dumps({{"status": "pass", "shape": list(value.shape)}}))
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and run a small network on Ascend 310B.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--skip-probes", action="store_true", help="Skip the fp32 and to_float boundary probes.")
    parser.add_argument("--output", default="02_cell_network.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def run_probe(name: str, template: str, device_id: int, timeout: int = 120) -> dict[str, object]:
    child = run_isolated(template.format(device_id=device_id), timeout=timeout)
    payload = parse_last_result(child["stdout"] or "")
    combined = (child["stderr"] or "") + (child["stdout"] or "")
    if child["returncode"] == 0 and payload and payload.get("status") == PASS:
        return {"name": name, "status": PASS, "shape": payload.get("shape")}
    if payload and payload.get("status") == "fail":
        # 子进程给出了明确失败结论：这是 observed-fail，不是前置条件缺失。
        return {
            "name": name,
            "status": FAIL,
            "returncode": child["returncode"],
            "error": payload.get("error", ""),
            "ascend_message": first_ascend_message(combined),
        }
    return {
        "name": name,
        "status": BLOCKED,
        "returncode": child["returncode"],
        "timeout": child.get("timeout", False),
        "error": (payload or {}).get("error") or "",
        "ascend_message": first_ascend_message(combined),
        "message": tail_lines(combined, 4),
    }


def main() -> int:
    args = parse_args()

    import numpy as np
    import mindspore as ms
    from mindspore import Tensor, nn

    from chapter5_models import Fp16Linear

    # 边界探测必须在本进程建立 Ascend 上下文之前完成：父进程占用设备时再启动
    # 子进程做设备计算，实测会互相阻塞并一直不返回。
    probes: dict[str, object] = {"skipped": True}
    if not args.skip_probes:
        print_section("boundary probes (before this process touches the device)")
        probes = {
            "dense_fp32": run_probe("dense_fp32", DENSE_FP32_PROBE, args.device_id),
            "mlp_fp32": run_probe("mlp_fp32", MLP_FP32_PROBE, args.device_id),
            "to_float_fp16": run_probe("to_float_fp16", FP16_CAST_PROBE, args.device_id),
        }
        for name, probe in probes.items():
            detail = probe.get("ascend_message") or probe.get("error") or ""
            print(f"{name}: {probe['status'].upper()} rc={probe.get('returncode')} {str(detail)[:120]}")

    print_section("context")
    ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id=args.device_id)
    print(f"mindspore {ms.__version__} <- {ms.__file__}")

    print_section("fp16 network (working path)")
    net = nn.SequentialCell(
        [
            Fp16Linear(in_dim=4, out_dim=8, seed=1),
            nn.ReLU(),
            Fp16Linear(in_dim=8, out_dim=1, seed=2),
        ]
    )
    data = Tensor(np.ones((1, 4), np.float16), ms.float16)
    output = net(data)
    forward_value = output.asnumpy().tolist()
    forward_ok = list(output.shape) == [1, 1]
    print(f"output shape={list(output.shape)} value={forward_value}")

    status = PASS if forward_ok else BLOCKED
    report = {
        "device_id": args.device_id,
        "mindspore": {"version": ms.__version__, "file": ms.__file__},
        "fp16_forward": {"shape": list(output.shape), "value": forward_value},
        "probes": probes,
    }
    report_path = write_json(ensure_output_dir() / args.output, report)

    metrics: dict[str, object] = {"report": str(report_path), "forward_shape": list(output.shape)}
    if not probes.get("skipped"):
        metrics["dense_fp32"] = probes["dense_fp32"]["status"]
        metrics["mlp_fp32"] = probes["mlp_fp32"]["status"]
        metrics["to_float_fp16"] = probes["to_float_fp16"]["status"]

    emit_result("02_cell_network", status, metrics)
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
