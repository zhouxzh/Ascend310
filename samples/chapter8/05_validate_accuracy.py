from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from chapter8_utils import (
    DEFAULT_ACCURACY_COMPARE,
    DEFAULT_FP16_MODEL,
    DEFAULT_FP32_MODEL,
    DEFAULT_INT8_MODEL,
    DEFAULT_VAL_LIST,
    StageRecorder,
    load_rgb_frame,
    model_record,
    preprocess_resnet_rgb,
    require_models,
    resolve_chapter_path,
    summarize_stages,
    topk_indices,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare FP32/FP16/INT8 OM accuracy on a prepared Tiny-ImageNet validation list."
    )
    parser.add_argument("--val-list", default=str(DEFAULT_VAL_LIST), help="Validation list path.")
    parser.add_argument("--samples", type=int, default=0, help="Max validation samples to evaluate (0 = all).")

    parser.add_argument(
        "--om-models",
        nargs="+",
        default=[str(DEFAULT_FP32_MODEL), str(DEFAULT_FP16_MODEL), str(DEFAULT_INT8_MODEL)],
        help="OM models to evaluate.",
    )
    parser.add_argument("--labels", nargs="*", default=[], help="Labels for OM models.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup runs before measuring.")
    parser.add_argument("--topk", type=int, default=5, help="Top-k accuracy size.")
    parser.add_argument("--progress-interval", type=int, default=500, help="Print progress every N samples.")
    parser.add_argument("--mismatch-limit", type=int, default=50, help="Max mismatch examples saved in report.")
    parser.add_argument("--save-samples", action="store_true", help="Save per-sample predictions in JSON report.")
    parser.add_argument("--output", default=str(DEFAULT_ACCURACY_COMPARE), help="Output JSON report path.")
    return parser.parse_args()


def resolve_data_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return resolve_chapter_path(path)


def read_validation_list(
    list_path: Path,
    *,
    root: Path | None = None,
    limit: int | None = None,
) -> list[tuple[Path, int]]:
    if not list_path.exists():
        raise FileNotFoundError(
            f"Validation list not found: {list_path}. "
            "Run `python tools/download_tiny_imagenet.py val` from samples/chapter8 first."
        )

    base_dir = root if root is not None else list_path.parent
    records: list[tuple[Path, int]] = []
    with list_path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                raise ValueError(f"{list_path}:{line_no}: expected '<path> <label>', got {line!r}")
            image_path = Path(parts[0]).expanduser()
            if not image_path.is_absolute():
                image_path = base_dir / image_path
            records.append((image_path, int(parts[1])))
            if limit is not None and len(records) >= limit:
                break

    if not records:
        raise ValueError(f"Validation list is empty: {list_path}")
    return records


def prepare_validation_data(args: argparse.Namespace) -> tuple[list[tuple[Path, int]], dict[str, Any]]:
    val_list = resolve_data_path(args.val_list)
    limit = args.samples or None

    records = read_validation_list(val_list, limit=limit)
    with val_list.open("r", encoding="utf-8") as f:
        available_images = sum(1 for line in f if line.strip() and not line.startswith("#"))
    metadata: dict[str, Any] = {
        "val_list": str(val_list),
        "available_images": available_images,
        "evaluated_images": len(records),
        "evaluated_class_count": len({label for _, label in records}),
    }
    return records, metadata


def new_accuracy_state() -> dict[str, Any]:
    return {
        "count": 0,
        "top1_correct": 0,
        "topk_correct": 0,
    }


def validate_variant_labels(labels: list[str]) -> None:
    duplicates = sorted(label for label, count in Counter(labels).items() if count > 1)
    if duplicates:
        raise ValueError(f"OM model labels must be unique, duplicated: {', '.join(duplicates)}")


def add_accuracy(state: dict[str, Any], logits: np.ndarray, label: int, topk: int) -> list[int]:
    top = topk_indices(logits, topk)
    state["count"] += 1
    if top and top[0] == label:
        state["top1_correct"] += 1
    if label in top:
        state["topk_correct"] += 1
    return top


def finalize_accuracy(
    state: dict[str, Any],
    *,
    topk: int,
    effective_topk: int | None = None,
) -> dict[str, Any]:
    count = int(state["count"])
    top1_correct = int(state["top1_correct"])
    topk_correct = int(state["topk_correct"])
    top1_accuracy = top1_correct / count if count else 0.0
    topk_accuracy = topk_correct / count if count else 0.0
    result: dict[str, Any] = {
        "count": count,
        "top1_correct": top1_correct,
        "topk_correct": topk_correct,
        "top1_accuracy": round(top1_accuracy, 6),
        "topk_accuracy": round(topk_accuracy, 6),
        "topk": topk,
    }
    if effective_topk is not None:
        result["effective_topk"] = effective_topk
    if effective_topk == 5 or (effective_topk is None and topk == 5):
        result["top5_correct"] = topk_correct
        result["top5_accuracy"] = round(topk_accuracy, 6)
    return result


def infer_output_classes(runner: Any) -> int:
    if getattr(runner, "output_shapes", None):
        output_shape = runner.output_shapes[0]
        if output_shape is not None:
            classes = int(np.prod(output_shape))
            if classes > 0:
                return classes

    if getattr(runner, "output_sizes", None):
        classes = int(runner.output_sizes[0] // np.dtype(np.float32).itemsize)
        if classes > 0:
            return classes

    raise ValueError("Cannot infer output class count from OM model metadata.")


def print_accuracy_table(report: dict[str, Any], topk: int) -> None:
    print(f"validation samples: {report['dataset']['evaluated_images']}")
    topk_label = int(report.get("effective_topk", topk))
    print(f"{'variant':<8} {'top1':>9} {f'top{topk_label}':>9} {'mean_ms':>10} {'p95_ms':>10}")
    print("-" * 52)
    for label, item in report["variants"].items():
        accuracy = item["accuracy"]
        timing = item["metrics"].get("inference_total", {})
        print(
            f"{label:<8} {accuracy['top1_accuracy'] * 100.0:>8.2f}% "
            f"{accuracy['topk_accuracy'] * 100.0:>8.2f}% "
            f"{float(timing.get('mean_ms', 0.0)):>10.4f} {float(timing.get('p95_ms', 0.0)):>10.4f}"
        )


def evaluate_accuracy(args: argparse.Namespace, records: list[tuple[Path, int]], dataset_meta: dict[str, Any]) -> dict[str, Any]:
    from acl_resnet_runner import AclSession, ReuseResNetRunner

    om_paths = [resolve_chapter_path(path) for path in args.om_models]
    default_om_names = [
        DEFAULT_FP32_MODEL.name,
        DEFAULT_FP16_MODEL.name,
        DEFAULT_INT8_MODEL.name,
    ]
    if args.labels:
        labels = args.labels
    elif [path.name for path in om_paths] == default_om_names:
        labels = ["fp32", "fp16", "int8"]
    else:
        labels = [path.stem for path in om_paths]
    validate_variant_labels(labels)

    require_models(om_paths)

    om_states = {label: new_accuracy_state() for label in labels}
    preprocess_recorder = StageRecorder()
    om_recorders = {label: StageRecorder() for label in labels}
    mismatches: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    effective_topk = args.topk
    output_classes: dict[str, int] = {}

    with AclSession(args.device):
        runners: dict[str, Any] = {}
        try:
            for label, path in zip(labels, om_paths):
                runners[label] = ReuseResNetRunner(path)

            output_classes = {label: infer_output_classes(runner) for label, runner in runners.items()}
            if len(set(output_classes.values())) != 1:
                raise ValueError(f"OM output class count mismatch: {output_classes}")
            effective_topk = min(args.topk, next(iter(output_classes.values())))

            for index in range(args.warmup):
                frame_path, _ = records[index % len(records)]
                input_tensor = preprocess_resnet_rgb(load_rgb_frame(frame_path))
                for runner in runners.values():
                    runner.infer(input_tensor)

            for index, (frame_path, label_id) in enumerate(records):
                with preprocess_recorder.time("preprocess"):
                    input_tensor = preprocess_resnet_rgb(load_rgb_frame(frame_path))

                predictions: dict[str, list[int]] = {}
                wrong_variants: list[str] = []

                for model_label, runner in runners.items():
                    recorder = om_recorders[model_label]
                    with recorder.time("inference_total"):
                        outputs, timings = runner.infer(input_tensor)
                    recorder.extend(timings)

                    om_output = outputs[0]
                    om_top = add_accuracy(om_states[model_label], om_output, label_id, effective_topk)
                    predictions[model_label] = om_top

                    if not om_top or om_top[0] != label_id:
                        wrong_variants.append(model_label)

                if args.save_samples:
                    samples.append(
                        {
                            "sample_index": index,
                            "path": str(frame_path),
                            "label": int(label_id),
                            "predictions": dict(predictions),
                        }
                    )
                if wrong_variants and len(mismatches) < args.mismatch_limit:
                    mismatches.append(
                        {
                            "sample_index": index,
                            "path": str(frame_path),
                            "label": int(label_id),
                            "wrong_variants": wrong_variants,
                            "predictions": dict(predictions),
                        }
                    )

                if args.progress_interval > 0 and (index + 1) % args.progress_interval == 0:
                    print(f"processed {index + 1}/{len(records)} validation samples")
        finally:
            for runner in runners.values():
                runner.release()

    variants: dict[str, Any] = {}

    for model_label, model_path in zip(labels, om_paths):
        variants[model_label] = {
            "model": model_record(model_path),
            "accuracy": finalize_accuracy(
                om_states[model_label],
                topk=args.topk,
                effective_topk=effective_topk,
            ),
            "metrics": summarize_stages(om_recorders[model_label].samples),
        }

    report: dict[str, Any] = {
        "case": "05_validate_accuracy",
        "device": args.device,
        "topk": args.topk,
        "effective_topk": effective_topk,
        "output_classes": output_classes,
        "warmup": args.warmup,
        "dataset": dataset_meta,
        "preprocess_metrics": summarize_stages(preprocess_recorder.samples),
        "variants": variants,
        "mismatches": mismatches,
        "mismatch_limit": args.mismatch_limit,
    }
    if args.save_samples:
        report["samples"] = samples
    return report


def main() -> int:
    args = parse_args()
    if args.samples < 0:
        raise ValueError("--samples must not be negative")
    if args.topk <= 0:
        raise ValueError("--topk must be positive")
    if args.warmup < 0:
        raise ValueError("--warmup must be non-negative")
    if args.progress_interval < 0:
        raise ValueError("--progress-interval must not be negative")
    if args.mismatch_limit < 0:
        raise ValueError("--mismatch-limit must not be negative")
    if args.labels and len(args.labels) != len(args.om_models):
        raise ValueError("--labels length must match --om-models length")
    validate_variant_labels(args.labels)

    records, dataset_meta = prepare_validation_data(args)
    print(f"validation list: {dataset_meta['val_list']}")
    print(f"validation images available: {dataset_meta['available_images']}")
    print(f"validation images evaluated: {dataset_meta['evaluated_images']}")

    report = evaluate_accuracy(args, records, dataset_meta)
    output_path = write_report(resolve_chapter_path(args.output), report)
    print_accuracy_table(report, args.topk)
    print(f"report saved: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
