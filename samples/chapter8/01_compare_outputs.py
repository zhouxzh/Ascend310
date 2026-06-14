from __future__ import annotations

import argparse

import numpy as np

from chapter8_utils import (
    DEFAULT_BASE_MODEL,
    DEFAULT_CALIB_LIST,
    DEFAULT_OUTPUT_COMPARE,
    DEFAULT_FP16_MODEL,
    StageRecorder,
    compare_logits,
    load_rgb_frame,
    model_record,
    preprocess_resnet_rgb,
    read_calibration_list,
    require_models,
    resolve_chapter_path,
    summarize_output_diffs,
    summarize_stages,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare ResNet18 outputs between two OM models.")
    parser.add_argument("--base-model", default=str(DEFAULT_BASE_MODEL), help="Reference OM model path.")
    parser.add_argument("--candidate-model", default=str(DEFAULT_FP16_MODEL), help="Candidate OM model path.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup runs for each model.")
    parser.add_argument("--samples", type=int, default=0, help="Max samples to compare (0 = all).")
    parser.add_argument("--topk", type=int, default=5, help="Top-k size for classification comparison.")
    parser.add_argument("--calib-list", default=str(DEFAULT_CALIB_LIST), help="Calibration list path.")
    parser.add_argument("--calib-root", default="", help="Calibration root directory. Defaults to list parent.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_COMPARE), help="Output JSON report path.")
    return parser.parse_args()


def compare_om_outputs(args: argparse.Namespace) -> dict:
    from acl_resnet_runner import AclSession, ReuseResNetRunner

    base_model = resolve_chapter_path(args.base_model)
    candidate_model = resolve_chapter_path(args.candidate_model)
    calib_list = resolve_chapter_path(args.calib_list)
    calib_root = resolve_chapter_path(args.calib_root) if args.calib_root else calib_list.parent
    require_models([base_model, candidate_model])
    paths = read_calibration_list(calib_list, root=calib_root, limit=args.samples or None)

    base_recorder = StageRecorder()
    candidate_recorder = StageRecorder()
    items: list[dict[str, object]] = []

    with AclSession(args.device):
        base_runner = None
        candidate_runner = None
        try:
            base_runner = ReuseResNetRunner(base_model)
            candidate_runner = ReuseResNetRunner(candidate_model)
            for index in range(args.warmup):
                frame = load_rgb_frame(paths[index % len(paths)])
                input_tensor = preprocess_resnet_rgb(frame)
                base_runner.infer(input_tensor)
                candidate_runner.infer(input_tensor)

            for index, frame_path in enumerate(paths):
                frame = load_rgb_frame(frame_path)
                with base_recorder.time("preprocess"):
                    input_tensor = preprocess_resnet_rgb(frame)

                with base_recorder.time("inference_total"):
                    base_outputs, base_timings = base_runner.infer(input_tensor)
                base_recorder.extend(base_timings)

                with candidate_recorder.time("inference_total"):
                    candidate_outputs, candidate_timings = candidate_runner.infer(input_tensor)
                candidate_recorder.extend(candidate_timings)

                item = compare_logits(base_outputs[0], candidate_outputs[0], topk=args.topk)
                item["sample_index"] = index
                item["path"] = str(frame_path)
                items.append(item)
        finally:
            if base_runner is not None:
                base_runner.release()
            if candidate_runner is not None:
                candidate_runner.release()

    return {
        "case": "01_compare_outputs",
        "mode": "om_inference",
        "device": args.device,
        "base_model": model_record(base_model),
        "candidate_model": model_record(candidate_model),
        "calib_list": str(calib_list),
        "calib_root": str(calib_root),
        "requested_samples": args.samples,
        "compared_samples": len(items),
        "topk": args.topk,
        "summary": summarize_output_diffs(items),
        "base_metrics": summarize_stages(base_recorder.samples),
        "candidate_metrics": summarize_stages(candidate_recorder.samples),
        "samples": items,
    }


def main() -> int:
    args = parse_args()
    if args.samples < 0:
        raise ValueError("--samples must not be negative")
    if args.topk <= 0:
        raise ValueError("--topk must be positive")
    if args.warmup < 0:
        raise ValueError("--warmup must be non-negative")
    report = compare_om_outputs(args)
    output_path = write_report(resolve_chapter_path(args.output), report)

    summary = report["summary"]
    print(f"compared samples: {summary['count']}")
    print(f"top1 match rate:  {float(summary['top1_match_rate']) * 100.0:.2f}%")
    print(f"mean top{args.topk} overlap: {summary['mean_topk_overlap']}")
    print(f"max abs diff:     {summary['max_abs_diff']}")
    print(f"mean abs diff:    {summary['mean_abs_diff']}")
    print(f"report saved:     {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
