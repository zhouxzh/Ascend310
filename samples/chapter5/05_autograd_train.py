"""第5章样例 5：自动微分、优化器与训练循环。

310B4 的 cube 单元不接受 fp32/bf16 矩阵计算：`nn.Dense` 的 fp32 前向可以跑通，
但同一路径放进 `value_and_grad` 会被 `EZ1001` 拒绝。本样例给出的可训练写法是
显式 fp16 `Parameter` + `ops.matmul`，并用独立子进程复现 fp32 的失败边界。
"""

from __future__ import annotations

import argparse
import time

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

FP32_GRAD_PROBE = """
import json
import mindspore as ms
from mindspore import Tensor, nn, ops

ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id={device_id})
net = nn.Dense(4, 1)
optimizer = nn.Adam(net.trainable_params(), learning_rate=1e-2)
grad_fn = ms.value_and_grad(lambda x, y: ops.mse_loss(net(x), y), None, net.trainable_params())
x = Tensor([[1.0, 2.0, 3.0, 4.0]], ms.float32)
y = Tensor([[1.0]], ms.float32)
try:
    loss, grads = grad_fn(x, y)
    optimizer(grads)
    print("RESULT " + json.dumps({{"status": "pass", "loss": float(loss.asnumpy())}}))
except Exception as exc:
    print("RESULT " + json.dumps({{"status": "fail", "error": (type(exc).__name__ + ": " + str(exc))[:400]}}))
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a tiny fp16 linear model on Ascend.")
    parser.add_argument("--steps", type=int, default=200, help="Number of optimizer steps.")
    parser.add_argument("--lr", type=float, default=0.05, help="Adam learning rate.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--skip-fp32-probe", action="store_true", help="Skip the fp32 gradient boundary probe.")
    parser.add_argument("--log-every", type=int, default=50, help="Print loss every N steps.")
    parser.add_argument("--output", default="05_autograd_train.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def probe_fp32_gradients(device_id: int, timeout: int = 120) -> dict[str, object]:
    child = run_isolated(FP32_GRAD_PROBE.format(device_id=device_id), timeout=timeout)
    payload = parse_last_result(child["stdout"] or "")
    combined = (child["stderr"] or "") + (child["stdout"] or "")
    if child["returncode"] == 0 and payload and payload.get("status") == "pass":
        return {"status": PASS, "loss": payload.get("loss")}
    if payload and payload.get("status") == "fail":
        # 子进程给出了明确失败结论：observed-fail，而不是前置条件缺失。
        return {
            "status": FAIL,
            "returncode": child["returncode"],
            "error": payload.get("error", ""),
            "ascend_message": first_ascend_message(str(payload.get("error", "")) + combined),
        }
    if child.get("timeout"):
        return {
            "status": BLOCKED,
            "timeout": True,
            "timeout_seconds": timeout,
            "note": "fp32 Dense + value_and_grad neither returned nor failed within the probe budget",
        }
    return {
        "status": BLOCKED,
        "returncode": child["returncode"],
        "error": (payload or {}).get("error") or "",
        "ascend_message": first_ascend_message(combined),
        "message": tail_lines(combined, 4),
    }


def main() -> int:
    args = parse_args()

    import mindspore as ms
    from mindspore import nn, ops

    from chapter5_models import Fp16Linear, make_linear_dataset

    # 边界探测先于训练执行：本进程一旦建立 Ascend 上下文并开始跑算子，再启动
    # 子进程做设备计算会互相阻塞（实测超过探测预算仍不返回）。
    fp32_probe: dict[str, object] = {"skipped": True}
    if not args.skip_fp32_probe:
        print_section("fp32 gradient boundary (before training)")
        fp32_probe = probe_fp32_gradients(args.device_id)
        print(f"fp32 Dense + value_and_grad: {fp32_probe['status'].upper()} rc={fp32_probe.get('returncode')}")
        if fp32_probe.get("ascend_message"):
            print(f"ascend message: {fp32_probe['ascend_message']}")

    print_section("context")
    ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id=args.device_id)
    print(f"mindspore {ms.__version__} <- {ms.__file__}")

    x, y = make_linear_dataset(samples=32, seed=0)
    net = Fp16Linear(in_dim=4, out_dim=1, seed=1)
    optimizer = nn.Adam(net.trainable_params(), learning_rate=args.lr)
    grad_fn = ms.value_and_grad(lambda data, target: ops.mse_loss(net(data), target), None, net.trainable_params())

    print_section("training")
    losses: list[float] = []
    started = time.time()
    for step in range(args.steps):
        loss, grads = grad_fn(x, y)
        optimizer(grads)
        value = float(loss.asnumpy())
        losses.append(value)
        if args.log_every and (step % args.log_every == 0 or step == args.steps - 1):
            print(f"step {step}: loss={value:.6f}")
    elapsed = time.time() - started

    loss_first = losses[0] if losses else float("nan")
    loss_last = losses[-1] if losses else float("nan")
    decreased = bool(losses) and loss_last < loss_first
    print(f"loss {loss_first:.6f} -> {loss_last:.6f} in {elapsed:.2f}s (decreased={decreased})")

    status = PASS if decreased else BLOCKED
    report = {
        "device_id": args.device_id,
        "steps": args.steps,
        "learning_rate": args.lr,
        "loss_first": loss_first,
        "loss_last": loss_last,
        "decreased": decreased,
        "elapsed_seconds": round(elapsed, 3),
        "loss_head": [round(value, 6) for value in losses[:5]],
        "loss_tail": [round(value, 6) for value in losses[-5:]],
        "fp32_grad_probe": fp32_probe,
    }
    report_path = write_json(ensure_output_dir() / args.output, report)

    emit_result(
        "05_autograd_train",
        status,
        {
            "report": str(report_path),
            "loss_first": round(loss_first, 6),
            "loss_last": round(loss_last, 6),
            "elapsed_seconds": round(elapsed, 3),
            "fp32_grad_probe": fp32_probe.get("status", "skipped"),
        },
        note=str(fp32_probe.get("ascend_message", "")),
    )
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
