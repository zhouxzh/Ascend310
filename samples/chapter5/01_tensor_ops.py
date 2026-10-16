"""第5章样例 1：张量、dtype 与三种算子路径的板端边界。

脚本按“逐元素 fp32 -> 矩阵乘 fp32 -> 矩阵乘 fp16 -> CPU 数值对照”的顺序探测，
每个探测都在独立子进程里执行，这样即使某个算子路径让进程消失，主流程仍能
记录退出码并给出结论。
"""

from __future__ import annotations

import argparse

from chapter5_utils import (
    BLOCKED,
    FAIL,
    PASS,
    emit_result,
    ensure_output_dir,
    parse_last_result,
    print_section,
    run_isolated,
    tail_lines,
    write_json,
)

OP_PROBE = """
import json
import mindspore as ms
from mindspore import Tensor, ops

ms.set_context(mode=ms.PYNATIVE_MODE, device_target="{device_target}"{device_arg})
a = Tensor([[1.0, 2.0], [3.0, 4.0]], ms.{dtype})
try:
    if "{op}" == "matmul":
        value = ops.matmul(a, a).asnumpy().tolist()
    else:
        value = ops.add(a, a).asnumpy().tolist()
    print("RESULT " + json.dumps({{"status": "pass", "value": value}}))
except Exception as exc:
    message = str(exc).replace("\\n", " | ")[:400]
    print("RESULT " + json.dumps({{"status": "fail", "error": type(exc).__name__ + ": " + message}}))
"""

EXPECTED = {
    "ascend_add_fp32": PASS,
    "ascend_matmul_fp32": None,
    "ascend_matmul_fp16": PASS,
    "cpu_matmul_fp32": PASS,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe MindSpore tensor and operator paths on Ascend 310B.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--output", default="01_tensor_ops.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def run_probe(name: str, device_target: str, dtype: str, op: str, device_id: int) -> dict[str, object]:
    device_arg = "" if device_target.upper() == "CPU" else f", device_id={device_id}"
    code = OP_PROBE.format(device_target=device_target, device_arg=device_arg, dtype=dtype, op=op)
    child = run_isolated(code, timeout=300)
    payload = parse_last_result(child["stdout"] or "")
    if child["returncode"] == 0 and payload and payload.get("status") == PASS:
        return {"name": name, "status": PASS, "value": payload.get("value")}
    return {
        "name": name,
        "status": BLOCKED if child["returncode"] not in (0, 1) else FAIL,
        "returncode": child["returncode"],
        "error": (payload or {}).get("error") or tail_lines(child["stderr"] or "", 4),
    }


def close_enough(left: list[list[float]], right: list[list[float]], tol: float = 1e-2) -> bool:
    if len(left) != len(right):
        return False
    return all(abs(a - b) <= tol for lrow, rrow in zip(left, right) for a, b in zip(lrow, rrow))


def main() -> int:
    args = parse_args()

    probes = {
        "ascend_add_fp32": run_probe("ascend_add_fp32", "Ascend", "float32", "add", args.device_id),
        "ascend_matmul_fp32": run_probe("ascend_matmul_fp32", "Ascend", "float32", "matmul", args.device_id),
        "ascend_matmul_fp16": run_probe("ascend_matmul_fp16", "Ascend", "float16", "matmul", args.device_id),
        "cpu_matmul_fp32": run_probe("cpu_matmul_fp32", "CPU", "float32", "matmul", args.device_id),
    }

    print_section("operator probes")
    for name, probe in probes.items():
        if probe["status"] == PASS:
            print(f"{name}: PASS value={probe.get('value')}")
        else:
            print(f"{name}: {probe['status'].upper()} rc={probe.get('returncode')} error={probe.get('error')}")

    ascend_fp16 = probes["ascend_matmul_fp16"].get("value")
    cpu_fp32 = probes["cpu_matmul_fp32"].get("value")
    numeric_match = bool(ascend_fp16 and cpu_fp32 and close_enough(ascend_fp16, cpu_fp32))
    print_section("numeric comparison")
    print(f"ascend fp16 matmul vs cpu fp32 matmul within 1e-2: {numeric_match}")

    required = ("ascend_add_fp32", "ascend_matmul_fp16")
    passed = all(probes[name]["status"] == PASS for name in required)
    fp32_matmul = probes["ascend_matmul_fp32"]
    boundary_note = (
        "ascend fp32 matmul rejected by the cube unit"
        if fp32_matmul["status"] != PASS
        else "ascend fp32 matmul accepted on this board"
    )
    status = PASS if passed else FAIL

    report = {
        "device_id": args.device_id,
        "probes": probes,
        "numeric_match": numeric_match,
        "expected": EXPECTED,
        "note": boundary_note,
    }
    report_path = write_json(ensure_output_dir() / args.output, report)

    emit_result(
        "01_tensor_ops",
        status,
        {
            "report": str(report_path),
            "ascend_add_fp32": probes["ascend_add_fp32"]["status"],
            "ascend_matmul_fp32": probes["ascend_matmul_fp32"]["status"],
            "ascend_matmul_fp16": probes["ascend_matmul_fp16"]["status"],
            "numeric_match": numeric_match,
        },
        note=boundary_note,
    )
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
