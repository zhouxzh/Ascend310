from __future__ import annotations

import argparse
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from perf_utils import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_DIR,
    deterministic_rgb_frame,
    make_summary_row,
    preprocess_resnet_rgb,
    summarize_ms,
    write_report,
)


SENTINEL = object()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare serial processing with a queue pipeline.")
    parser.add_argument("--simulate", action="store_true", help="Use sleep-based stages that run on any machine.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Path to resnet18_tiny_imagenet.om.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id when not using --simulate.")
    parser.add_argument("--frames", type=int, default=200, help="Frames to process.")
    parser.add_argument("--queue-size", type=int, default=8, help="Queue capacity between stages.")
    parser.add_argument("--pre-ms", type=float, default=5.0, help="Simulated preprocess latency.")
    parser.add_argument("--infer-ms", type=float, default=8.0, help="Simulated inference latency.")
    parser.add_argument("--post-ms", type=float, default=2.0, help="Simulated postprocess latency.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR / "pipeline_queue_demo.json"),
        help="Output metrics JSON path.",
    )
    return parser.parse_args()


def sleep_stage(ms: float) -> Callable[[object], object]:
    def run(item: object) -> object:
        time.sleep(ms / 1000.0)
        return item

    return run


def run_serial(frames: list[object], pre_fn, infer_fn, post_fn) -> dict:
    latency_samples = []
    start = time.perf_counter()
    for frame in frames:
        item_start = time.perf_counter()
        pre_out = pre_fn(frame)
        infer_out = infer_fn(pre_out)
        post_fn(infer_out)
        latency_samples.append((time.perf_counter() - item_start) * 1000.0)
    wall_ms = (time.perf_counter() - start) * 1000.0
    fps = len(frames) / (wall_ms / 1000.0) if wall_ms > 0 else 0.0
    return {"wall_ms": wall_ms, "fps": fps, "latency_samples": latency_samples}


def run_pipeline(frames: list[object], pre_fn, infer_fn, post_fn, queue_size: int) -> dict:
    q_pre: queue.Queue = queue.Queue(maxsize=queue_size)
    q_infer: queue.Queue = queue.Queue(maxsize=queue_size)
    q_post: queue.Queue = queue.Queue(maxsize=queue_size)
    latency_samples: list[float] = []
    errors: list[BaseException] = []

    def producer() -> None:
        try:
            for index, frame in enumerate(frames):
                q_pre.put((index, time.perf_counter(), frame))
        except BaseException as exc:
            errors.append(exc)
        finally:
            q_pre.put(SENTINEL)

    def preprocess_worker() -> None:
        try:
            while True:
                item = q_pre.get()
                if item is SENTINEL:
                    q_infer.put(SENTINEL)
                    return
                index, start_ts, frame = item
                q_infer.put((index, start_ts, pre_fn(frame)))
        except BaseException as exc:
            errors.append(exc)
            q_infer.put(SENTINEL)

    def infer_worker() -> None:
        try:
            while True:
                item = q_infer.get()
                if item is SENTINEL:
                    q_post.put(SENTINEL)
                    return
                index, start_ts, pre_out = item
                q_post.put((index, start_ts, infer_fn(pre_out)))
        except BaseException as exc:
            errors.append(exc)
            q_post.put(SENTINEL)

    def post_worker() -> None:
        try:
            while True:
                item = q_post.get()
                if item is SENTINEL:
                    return
                _, start_ts, infer_out = item
                post_fn(infer_out)
                latency_samples.append((time.perf_counter() - start_ts) * 1000.0)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=producer, name="producer"),
        threading.Thread(target=preprocess_worker, name="preprocess"),
        threading.Thread(target=infer_worker, name="infer"),
        threading.Thread(target=post_worker, name="postprocess"),
    ]
    start = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_ms = (time.perf_counter() - start) * 1000.0
    if errors:
        raise RuntimeError(f"pipeline failed: {errors[0]!r}")
    fps = len(frames) / (wall_ms / 1000.0) if wall_ms > 0 else 0.0
    return {"wall_ms": wall_ms, "fps": fps, "latency_samples": latency_samples}


def run_pipeline_acl(frames: list[np.ndarray], args: argparse.Namespace) -> dict:
    from acl_resnet_runner import AclSession, ReuseResNetRunner, import_acl

    q_pre: queue.Queue = queue.Queue(maxsize=args.queue_size)
    q_infer: queue.Queue = queue.Queue(maxsize=args.queue_size)
    q_post: queue.Queue = queue.Queue(maxsize=args.queue_size)
    latency_samples: list[float] = []
    errors: list[BaseException] = []

    session = AclSession(args.device)
    session.__enter__()
    acl_module = import_acl()
    context = session.context

    def producer() -> None:
        try:
            for index, frame in enumerate(frames):
                q_pre.put((index, time.perf_counter(), frame))
        except BaseException as exc:
            errors.append(exc)
        finally:
            q_pre.put(SENTINEL)

    def preprocess_worker() -> None:
        try:
            while True:
                item = q_pre.get()
                if item is SENTINEL:
                    q_infer.put(SENTINEL)
                    return
                index, start_ts, frame = item
                q_infer.put((index, start_ts, preprocess_resnet_rgb(frame)))
        except BaseException as exc:
            errors.append(exc)
            q_infer.put(SENTINEL)

    def infer_worker() -> None:
        runner = None
        try:
            acl_module.rt.set_context(context)
            runner = ReuseResNetRunner(Path(args.model))
            while True:
                item = q_infer.get()
                if item is SENTINEL:
                    q_post.put(SENTINEL)
                    return
                index, start_ts, input_tensor = item
                outputs, _ = runner.infer(input_tensor)
                q_post.put((index, start_ts, outputs))
        except BaseException as exc:
            errors.append(exc)
            q_post.put(SENTINEL)
        finally:
            if runner is not None:
                runner.release()

    def post_worker() -> None:
        try:
            while True:
                item = q_post.get()
                if item is SENTINEL:
                    return
                _, start_ts, outputs = item
                int(np.argmax(outputs[0]))
                latency_samples.append((time.perf_counter() - start_ts) * 1000.0)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=producer, name="producer"),
        threading.Thread(target=preprocess_worker, name="preprocess"),
        threading.Thread(target=infer_worker, name="infer"),
        threading.Thread(target=post_worker, name="postprocess"),
    ]

    start = time.perf_counter()
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        session.__exit__(None, None, None)
    wall_ms = (time.perf_counter() - start) * 1000.0
    if errors:
        raise RuntimeError(f"ACL pipeline failed: {errors[0]!r}")
    fps = len(frames) / (wall_ms / 1000.0) if wall_ms > 0 else 0.0
    return {"wall_ms": wall_ms, "fps": fps, "latency_samples": latency_samples}


def build_simulated_functions(args: argparse.Namespace):
    return sleep_stage(args.pre_ms), sleep_stage(args.infer_ms), sleep_stage(args.post_ms)


def build_acl_functions(args: argparse.Namespace):
    from acl_resnet_runner import AclSession, ReuseResNetRunner

    session = AclSession(args.device)
    session.__enter__()
    runner = ReuseResNetRunner(Path(args.model))

    def pre_fn(frame: np.ndarray) -> np.ndarray:
        return preprocess_resnet_rgb(frame)

    def infer_fn(input_tensor: np.ndarray):
        outputs, _ = runner.infer(input_tensor)
        return outputs

    def post_fn(outputs) -> int:
        return int(np.argmax(outputs[0]))

    def close() -> None:
        runner.release()
        session.__exit__(None, None, None)

    return pre_fn, infer_fn, post_fn, close


def main() -> int:
    args = parse_args()
    frames = list(range(args.frames)) if args.simulate else [
        deterministic_rgb_frame(index, 64, 64) for index in range(args.frames)
    ]

    close = None
    if args.simulate:
        pre_fn, infer_fn, post_fn = build_simulated_functions(args)
        mode = "simulate"
    else:
        pre_fn, infer_fn, post_fn, close = build_acl_functions(args)
        mode = "pyacl"

    try:
        serial = run_serial(frames, pre_fn, infer_fn, post_fn)
        if args.simulate:
            pipeline = run_pipeline(frames, pre_fn, infer_fn, post_fn, args.queue_size)
        else:
            pipeline = run_pipeline_acl(frames, args)
    finally:
        if close is not None:
            close()

    speedup = serial["wall_ms"] / pipeline["wall_ms"] if pipeline["wall_ms"] else 0.0
    serial_latency = summarize_ms(serial["latency_samples"])
    pipeline_latency = summarize_ms(pipeline["latency_samples"])
    report = {
        "case": "05_pipeline_queue_demo",
        "mode": mode,
        "frames": args.frames,
        "queue_size": args.queue_size,
        "serial": {
            "wall_ms": round(serial["wall_ms"], 4),
            "fps": round(serial["fps"], 4),
            "latency": serial_latency,
        },
        "pipeline": {
            "wall_ms": round(pipeline["wall_ms"], 4),
            "fps": round(pipeline["fps"], 4),
            "latency": pipeline_latency,
        },
        "summary_rows": [
            {
                "case": "Queue Pipeline",
                "variant": f"{mode}_serial",
                "mean_ms": round(serial["wall_ms"] / args.frames, 4),
                "p95_ms": serial_latency["p95_ms"],
                "fps": round(serial["fps"], 4),
                "runs": args.frames,
                "speedup": "",
                "note": "Pre -> Infer -> Post 串行",
            },
            {
                "case": "Queue Pipeline",
                "variant": f"{mode}_queue_pipeline",
                "mean_ms": round(pipeline["wall_ms"] / args.frames, 4),
                "p95_ms": pipeline_latency["p95_ms"],
                "fps": round(pipeline["fps"], 4),
                "runs": args.frames,
                "speedup": round(speedup, 3),
                "note": "三段线程通过 Queue 解耦",
            },
        ],
    }
    output_path = write_report(args.output, report)
    print(f"mode: {mode}")
    print(f"serial:   {serial['fps']:.2f} FPS, wall={serial['wall_ms']:.2f} ms")
    print(f"pipeline: {pipeline['fps']:.2f} FPS, wall={pipeline['wall_ms']:.2f} ms")
    print(f"speedup:  {speedup:.3f}x")
    print(f"metrics saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
