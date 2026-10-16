"""第5章样例 4：PyNative 与静态图。

MindSpore 的 `ms.jit` 需要真实源码文件（在 `python -c` 下会报
"cannot compile temporary source code"），所以本样例以文件形式提供，并用
`--mode` 分别启动一次：

    python 04_graph_mode.py --mode pynative
    python 04_graph_mode.py --mode graph
"""

from __future__ import annotations

import argparse

from chapter5_utils import BLOCKED, FAIL, PASS, emit_result, ensure_output_dir, print_section, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the same computation in PyNative and Graph mode.")
    parser.add_argument("--mode", choices=("pynative", "graph"), default="pynative", help="Execution mode.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--output", default="04_graph_mode.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    import mindspore as ms
    from mindspore import Tensor, ops

    graph = args.mode == "graph"
    ms.set_context(mode=ms.GRAPH_MODE if graph else ms.PYNATIVE_MODE, device_target="Ascend", device_id=args.device_id)

    @ms.jit
    def compiled_square_sum(x):
        return ops.reduce_sum(ops.mul(x, x))

    def plain_square_sum(x):
        return ops.reduce_sum(ops.mul(x, x))

    data = Tensor([2.0, 3.0], ms.float32)
    print_section(f"{args.mode} mode")
    result = {"mode": args.mode, "device_id": args.device_id}
    status = PASS
    try:
        compiled_value = float(compiled_square_sum(data).asnumpy())
        plain_value = float(plain_square_sum(data).asnumpy())
        print(f"jit value={compiled_value} plain value={plain_value} (expected 13.0)")
        result.update({"jit_value": compiled_value, "plain_value": plain_value, "expected": 13.0})
        if abs(compiled_value - 13.0) > 1e-3 or abs(plain_value - 13.0) > 1e-3:
            status = FAIL
    except Exception as exc:  # graph compilation failures are recorded, not hidden
        status = BLOCKED
        result.update({"error": f"{type(exc).__name__}: {exc}"[:400]})
        print(f"graph execution blocked: {result['error']}")

    result["status"] = status
    report_path = write_json(ensure_output_dir() / args.output, result)

    emit_result(
        "04_graph_mode",
        status,
        {
            "report": str(report_path),
            "mode": args.mode,
            "jit_value": result.get("jit_value", ""),
        },
        note=str(result.get("error", "")),
    )
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
