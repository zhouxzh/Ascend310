from __future__ import annotations

import argparse

import numpy as np

from chapter8_utils import (
    DEFAULT_BASE_MODEL,
    DEFAULT_CALIB_LIST,
    DEFAULT_FP16_MODEL,
    DEFAULT_PERF_COMPARE,
    StageRecorder,
    deterministic_rgb_frame,
    load_rgb_frame,
    make_summary_row,
    model_record,
    preprocess_resnet_rgb,
    print_stage_table,
    read_calibration_list,
    require_models,
    resolve_chapter_path,
    summarize_stages,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare performance of ResNet18 OM models with the same input path.")
    parser.add_argument(
        "--models",
        nargs="+",
        default=[str(DEFAULT_BASE_MODEL), str(DEFAULT_FP16_MODEL)],
        help="OM models to compare. The first model is the baseline.",
    )
    parser.add_argument("--labels", nargs="*", default=[], help="Optional labels for models.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup runs per model.")
    parser.add_argument("--runs", type=int, default=100, help="Measured runs per model.")
    parser.add_argument("--height", type=int, default=64, help="Synthetic frame height when no list is available.")
    parser.add_argument("--width", type=int, default=64, help="Synthetic frame width when no list is available.")
    parser.add_argument("--calib-list", default=str(DEFAULT_CALIB_LIST), help="Calibration list path.")
    parser.add_argument("--calib-root", default="", help="Calibration root directory. Defaults to list parent.")
    parser.add_argument("--output", default=str(DEFAULT_PERF_COMPARE), help="Output JSON report path.")
    return parser.parse_args()


def load_frame(index: int, paths: list | None, *, height: int, width: int) -> np.ndarray:
    if paths:
        return load_rgb_frame(paths[index % len(paths)])
    return deterministic_rgb_frame(index, height, width)


def measure_model(
    model_path,
    *,
    label: str,
    paths: list | None,
    warmup: int,
    runs: int,
    height: int,
    width: int,
) -> dict:
    from acl_resnet_runner import ReuseResNetRunner

    recorder = StageRecorder()
    runner = None
    try:
        runner = ReuseResNetRunner(model_path)
        for index in range(warmup):
            frame = load_frame(index, paths, height=height, width=width)
            runner.infer(preprocess_resnet_rgb(frame))

        top1_values: list[int] = []
        for index in range(runs):
            frame = load_frame(index + warmup, paths, height=height, width=width)
            with recorder.time("preprocess"):
                input_tensor = preprocess_resnet_rgb(frame)

            with recorder.time("inference_total"):
                outputs, timings = runner.infer(input_tensor)
            recorder.extend(timings)

            with recorder.time("postprocess"):
                top1_values.append(int(np.argmax(outputs[0])))

            total_ms = recorder.samples["preprocess"][-1]
            total_ms += recorder.samples["inference_total"][-1]
            total_ms += recorder.samples["postprocess"][-1]
            recorder.add("end_to_end", total_ms)
    finally:
        if runner is not None:
            runner.release()

    metrics = summarize_stages(recorder.samples)
    return {
        "label": label,
        "model": model_record(model_path),
        "metrics": metrics,
        "end_to_end_samples": recorder.samples["end_to_end"],
        "top1_preview": top1_values[:10],
    }


def main() -> int:
    args = parse_args()
    if args.runs <= 0:
        raise ValueError("--runs must be positive")
    if args.warmup < 0:
        raise ValueError("--warmup must be non-negative")
    if args.labels and len(args.labels) != len(args.models):
        raise ValueError("--labels length must match --models length")

    model_paths = [resolve_chapter_path(model) for model in args.models]
    require_models(model_paths)
    labels = args.labels or [path.stem for path in model_paths]

    calib_list = resolve_chapter_path(args.calib_list)
    calib_root = resolve_chapter_path(args.calib_root) if args.calib_root else calib_list.parent
    paths = None
    input_source = "deterministic_generated"
    if calib_list.exists():
        paths = read_calibration_list(calib_list, root=calib_root)
        input_source = str(calib_list)

    from acl_resnet_runner import AclSession

    variants = []
    with AclSession(args.device):
        for label, model_path in zip(labels, model_paths):
            variants.append(
                measure_model(
                    model_path,
                    label=label,
                    paths=paths,
                    warmup=args.warmup,
                    runs=args.runs,
                    height=args.height,
                    width=args.width,
                )
            )

    baseline_mean = float(variants[0]["metrics"]["end_to_end"]["mean_ms"])
    summary_rows = []
    for index, variant in enumerate(variants):
        mean_ms = float(variant["metrics"]["end_to_end"]["mean_ms"])
        speedup = None if index == 0 or mean_ms == 0.0 else baseline_mean / mean_ms
        summary_rows.append(
            make_summary_row(
                "ResNet18 精度模式性能对比",
                variant["label"],
                variant["end_to_end_samples"],
                speedup=speedup,
                note="基线" if index == 0 else "相对第一个模型",
            )
        )

    report = {
        "case": "02_perf_compare",
        "device": args.device,
        "warmup": args.warmup,
        "runs": args.runs,
        "input_source": input_source,
        "models": [variant["model"] for variant in variants],
        "variants": {variant["label"]: variant["metrics"] for variant in variants},
        "top1_preview": {variant["label"]: variant["top1_preview"] for variant in variants},
        "summary_rows": summary_rows,
    }
    output_path = write_report(resolve_chapter_path(args.output), report)

    for variant in variants:
        print(f"\n[{variant['label']}]")
        print_stage_table(variant["metrics"])
    print(f"\nreport saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
