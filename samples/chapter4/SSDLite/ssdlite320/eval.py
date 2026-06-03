from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from tqdm import tqdm

from .config import DEFAULT_IMAGE_SIZE, DEFAULT_VAL_DATA_FILES
from .data import (
    IMAGENET_MEAN,
    IMAGENET_STD,
    extract_category_names,
    extract_ground_truth,
    iter_dataset,
    load_coco_val,
    preprocess_image,
)
from .postprocess import Detections, dboxes320_coco, decode_batch, pick_locs_confs

REPORT_COLUMNS = [
    "timestamp",
    "backbone",
    "model_path",
    "provider",
    "img_size",
    "dbox_min_ratio",
    "dbox_max_ratio",
    "mAP",
    "AP50",
    "AP75",
    "mAP_small",
    "mAP_medium",
    "mAP_large",
    "fps_total",
    "fps_inference",
    "result_file",
]


def new_coco_ground_truth_dict() -> tuple[dict[str, Any], set[int]]:
    return {"images": [], "annotations": [], "categories": []}, set()


def add_coco_ground_truth_item(
    coco_gt: dict[str, Any],
    category_ids: set[int],
    item: dict[str, Any],
    index: int,
    image_size: int = DEFAULT_IMAGE_SIZE,
) -> None:
    image_id = int(item.get("image_id", index))
    image_width, image_height = item["image"].size
    coco_gt["images"].append({"id": image_id, "width": image_size, "height": image_size})

    objects = item.get("objects", {})
    for bbox, category_id in zip(objects.get("bbox", []), objects.get("category", [])):
        xmin, ymin, xmax, ymax = bbox
        bx = xmin * image_size / image_width
        by = ymin * image_size / image_height
        bw = (xmax - xmin) * image_size / image_width
        bh = (ymax - ymin) * image_size / image_height
        mapped_category_id = int(category_id) + 1
        coco_gt["annotations"].append(
            {
                "id": len(coco_gt["annotations"]),
                "image_id": image_id,
                "category_id": mapped_category_id,
                "bbox": [bx, by, bw, bh],
                "area": bw * bh,
                "iscrowd": 0,
            }
        )
        category_ids.add(mapped_category_id)


def write_coco_ground_truth(coco_gt: dict[str, Any], category_ids: set[int], gt_file: str) -> str:
    coco_gt["categories"] = [{"id": category_id, "name": str(category_id)} for category_id in sorted(category_ids)]
    gt_path = Path(gt_file)
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    with gt_path.open("w", encoding="utf-8") as file:
        json.dump(coco_gt, file)
    return str(gt_path)


def build_coco_predictions(
    detections: Detections,
    image_id: int,
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    if detections.boxes.size == 0:
        return []

    scaled_boxes = detections.boxes.astype(np.float32, copy=True)
    scaled_boxes[:, [0, 2]] *= image_width
    scaled_boxes[:, [1, 3]] *= image_height
    scaled_boxes[:, 2] -= scaled_boxes[:, 0]
    scaled_boxes[:, 3] -= scaled_boxes[:, 1]

    return [
        {
            "image_id": int(image_id),
            "category_id": int(label),
            "bbox": [round(float(x), 3) for x in box],
            "score": round(float(score), 5),
        }
        for box, label, score in zip(scaled_boxes.tolist(), detections.labels.tolist(), detections.scores.tolist())
    ]


def run_coco_evaluation(gt_file: str, result_file: str, image_ids: list[int]) -> dict[str, float]:
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    coco_gt = COCO(gt_file)
    coco_dt = coco_gt.loadRes(result_file)
    coco_eval = COCOeval(coco_gt, coco_dt, "bbox")
    coco_eval.params.imgIds = sorted(image_ids)
    coco_eval.evaluate()
    coco_eval.accumulate()
    coco_eval.summarize()
    return {
        "mAP": float(coco_eval.stats[0]),
        "mAP_50": float(coco_eval.stats[1]),
        "mAP_75": float(coco_eval.stats[2]),
        "mAP_small": float(coco_eval.stats[3]),
        "mAP_medium": float(coco_eval.stats[4]),
        "mAP_large": float(coco_eval.stats[5]),
    }


def visualize_sample(
    image_chw: np.ndarray,
    gt_boxes: np.ndarray,
    gt_labels: np.ndarray,
    detections: Detections,
    category_names: Sequence[str] | None,
    save_path: str,
    score_threshold: float = 0.4,
) -> None:
    import matplotlib.patches as patches
    import matplotlib.pyplot as plt

    image = image_chw.copy()
    image = image * IMAGENET_STD + IMAGENET_MEAN
    image = np.clip(image.transpose(1, 2, 0), 0.0, 1.0)
    image_height, image_width = image.shape[:2]

    fig, ax = plt.subplots(1, figsize=(10, 10))
    ax.imshow(image)

    for box, label in zip(gt_boxes, gt_labels):
        xmin, ymin, xmax, ymax = box
        width, height = xmax - xmin, ymax - ymin
        ax.add_patch(patches.Rectangle((xmin, ymin), width, height, linewidth=2, edgecolor="lime", facecolor="none"))
        label_value = int(label)
        label_name = category_names[label_value] if category_names and label_value < len(category_names) else str(label_value)
        ax.text(xmin, ymin, f"GT: {label_name}", color="lime", fontsize=9, backgroundcolor="black", alpha=0.6)

    if detections.boxes.size > 0:
        pred_boxes = detections.boxes.copy()
        pred_boxes[:, [0, 2]] *= image_width
        pred_boxes[:, [1, 3]] *= image_height
        for box, label, score in zip(pred_boxes, detections.labels, detections.scores):
            if score < score_threshold:
                continue
            xmin, ymin, xmax, ymax = box
            width, height = xmax - xmin, ymax - ymin
            ax.add_patch(patches.Rectangle((xmin, ymin), width, height, linewidth=2, edgecolor="red", facecolor="none"))
            label_value = int(label)
            label_name = category_names[label_value] if category_names and label_value < len(category_names) else str(label_value)
            ax.text(xmin, ymax, f"Pred: {label_name} {score:.2f}", color="white", fontsize=9, backgroundcolor="red", alpha=0.7)

    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    plt.axis("off")
    plt.savefig(save_path, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def get_run_timestamp(args) -> str:
    if not getattr(args, "run_timestamp", None):
        args.run_timestamp = datetime.now().isoformat(timespec="seconds")
    return args.run_timestamp


def get_run_id(args) -> str:
    if not getattr(args, "run_id", None):
        get_run_timestamp(args)
        compact_timestamp = args.run_timestamp.replace("-", "").replace(":", "").replace("T", "_")
        args.run_id = f"{args.backend}_eval_{compact_timestamp}"
    return args.run_id


def resolve_result_path(args) -> Path:
    if args.result_file:
        return Path(args.result_file)
    run_id = get_run_id(args)
    return Path(args.output_dir) / "val_results" / run_id / f"ssd320_{args.backbone}_{args.backend}_predictions.json"


def resolve_report_path(args) -> Path:
    if args.report_file:
        return Path(args.report_file)
    return Path(args.report_dir) / f"{get_run_id(args)}_{args.backbone}.csv"


def configure_all_run(args) -> str:
    timestamp = get_run_timestamp(args)
    compact_timestamp = timestamp.replace("-", "").replace(":", "").replace("T", "_")
    args.run_id = f"{args.backend}_all_eval_{compact_timestamp}"
    if not args.report_file:
        args.report_file = str(Path(args.report_dir) / f"{args.run_id}.csv")
    return args.report_file


def write_eval_report(args, metrics: dict[str, Any], timestamp: str, append: bool = False) -> str:
    report_path = resolve_report_path(args)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not append or not report_path.exists() or report_path.stat().st_size == 0

    row = {
        "timestamp": timestamp,
        "backbone": args.backbone,
        "model_path": str(args.model_path),
        "provider": args.backend,
        "img_size": args.img_size,
        "dbox_min_ratio": args.dbox_min_ratio,
        "dbox_max_ratio": args.dbox_max_ratio,
        "mAP": metrics.get("mAP", 0.0),
        "AP50": metrics.get("mAP_50", 0.0),
        "AP75": metrics.get("mAP_75", 0.0),
        "mAP_small": metrics.get("mAP_small", 0.0),
        "mAP_medium": metrics.get("mAP_medium", 0.0),
        "mAP_large": metrics.get("mAP_large", 0.0),
        "fps_total": metrics.get("fps_total", 0.0),
        "fps_inference": metrics.get("fps_inference", 0.0),
        "result_file": metrics.get("result_file", ""),
    }

    mode = "a" if append else "w"
    with report_path.open(mode, newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return str(report_path)


def build_default_boxes(args):
    return dboxes320_coco(min_ratio=args.dbox_min_ratio, max_ratio=args.dbox_max_ratio)


def run_visualizations(args, infer_fn, dboxes, val_dataset) -> None:
    if args.num_visualizations <= 0:
        return

    category_names = extract_category_names(val_dataset)
    save_dir = Path(args.output_dir) / "viz_results" / get_run_id(args) / args.backbone
    for index, item in enumerate(iter_dataset(val_dataset, args.num_visualizations)):
        image_batch, image_chw = preprocess_image(item["image"], args.img_size)
        locs, confs = pick_locs_confs(infer_fn(image_batch))
        detections = decode_batch(
            locs,
            confs,
            dboxes,
            iou_threshold=args.decode_iou_threshold,
            max_output=args.max_output,
            score_threshold=args.score_threshold,
        )[0]
        gt_boxes, gt_labels = extract_ground_truth(item, args.img_size)
        visualize_sample(
            image_chw,
            gt_boxes,
            gt_labels,
            detections,
            category_names,
            str(save_dir / f"vis_{index}.jpg"),
        )


def evaluate_dataset(
    args,
    infer_fn,
    dboxes,
    val_dataset,
    write_gt: bool = True,
    append_report: bool = False,
) -> dict[str, float | str]:
    run_timestamp = get_run_timestamp(args)
    result_path = resolve_result_path(args)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    predictions = []
    image_ids = []
    coco_gt = None
    category_ids = set()
    if write_gt:
        coco_gt, category_ids = new_coco_ground_truth_dict()
    start_time = time.time()
    inference_time = 0.0

    for sample_index, item in enumerate(tqdm(iter_dataset(val_dataset, args.max_samples), desc=f"Evaluating {args.backend}")):
        step_start = time.time()
        image_batch, _ = preprocess_image(item["image"], args.img_size)
        locs, confs = pick_locs_confs(infer_fn(image_batch))
        detections = decode_batch(
            locs,
            confs,
            dboxes,
            iou_threshold=args.decode_iou_threshold,
            max_output=args.max_output,
            score_threshold=args.score_threshold,
        )[0]
        inference_time += time.time() - step_start

        image_id = int(item["image_id"])
        image_ids.append(image_id)
        if write_gt and coco_gt is not None:
            add_coco_ground_truth_item(coco_gt, category_ids, item, sample_index, args.img_size)
        predictions.extend(build_coco_predictions(detections, image_id, args.img_size, args.img_size))

    with result_path.open("w", encoding="utf-8") as file:
        json.dump(predictions, file)

    total_time = time.time() - start_time
    count = len(image_ids)
    gt_file = args.gt_file
    if write_gt and coco_gt is not None:
        gt_file = write_coco_ground_truth(coco_gt, category_ids, args.gt_file)
    elif not Path(gt_file).exists():
        raise FileNotFoundError(f"COCO ground-truth file does not exist: {gt_file}")

    if not predictions:
        print("No predictions were produced; skip COCO mAP calculation.")
        metrics = {
            "mAP": 0.0,
            "mAP_50": 0.0,
            "mAP_75": 0.0,
            "mAP_small": 0.0,
            "mAP_medium": 0.0,
            "mAP_large": 0.0,
            "fps_total": count / total_time if total_time > 0 else 0.0,
            "fps_inference": count / inference_time if inference_time > 0 else 0.0,
            "result_file": str(result_path),
        }
        report_path = write_eval_report(args, metrics, timestamp=run_timestamp, append=append_report)
        print(f"Saved evaluation report: {report_path}")
        return metrics

    metrics = run_coco_evaluation(gt_file, str(result_path), image_ids)
    metrics.update(
        {
            "fps_total": count / total_time if total_time > 0 else 0.0,
            "fps_inference": count / inference_time if inference_time > 0 else 0.0,
            "result_file": str(result_path),
        }
    )
    print(
        f"mAP={metrics['mAP']:.4f}, AP50={metrics['mAP_50']:.4f}, "
        f"AP75={metrics['mAP_75']:.4f}, fps_total={metrics['fps_total']:.2f}, "
        f"fps_inference={metrics['fps_inference']:.2f}"
    )
    report_path = write_eval_report(args, metrics, timestamp=run_timestamp, append=append_report)
    print(f"Saved evaluation report: {report_path}")
    return metrics


def run_evaluation(
    args,
    infer_fn,
    write_gt: bool = True,
    append_report: bool = False,
) -> dict[str, float | str]:
    dboxes = build_default_boxes(args)

    if args.num_visualizations > 0:
        viz_dataset = load_coco_val(args.dataset_name, args.cache_dir, streaming=args.streaming, data_files=args.data_files)
        run_visualizations(args, infer_fn, dboxes, viz_dataset)

    if args.skip_eval:
        return {}

    val_dataset = load_coco_val(args.dataset_name, args.cache_dir, streaming=args.streaming, data_files=args.data_files)
    return evaluate_dataset(args, infer_fn, dboxes, val_dataset, write_gt=write_gt, append_report=append_report)


def add_common_eval_args(parser) -> None:
    parser.add_argument("--all", action="store_true", help="Evaluate every matching model file for this backend.")
    parser.add_argument("--backbone", default="mobilenetv4_conv_small", help="Backbone name used in ssd320_{backbone} model files.")
    parser.add_argument("--model", default=None, help="Explicit ONNX/OM model path.")
    parser.add_argument("--dataset-name", default="detection-datasets/coco", help="Hugging Face validation dataset name.")
    parser.add_argument("--cache-dir", default="./data/hf_cache", help="Dataset cache directory.")
    parser.add_argument(
        "--data-files",
        default=DEFAULT_VAL_DATA_FILES,
        help="Validation parquet pattern inside the Hugging Face dataset. Use '' to let datasets resolve all files.",
    )
    parser.add_argument("--streaming", action=argparse.BooleanOptionalAction, default=False, help="Stream the validation split instead of preparing the local dataset cache. Default is cached mode.")
    parser.add_argument("--max-samples", type=int, default=0, help="Limit the number of validation samples. 0 means all available samples.")
    parser.add_argument("--img-size", type=int, default=DEFAULT_IMAGE_SIZE, help="Inference image size.")
    parser.add_argument("--num-visualizations", type=int, default=0, help="Number of validation images to visualize.")
    parser.add_argument("--skip-eval", action="store_true", help="Only run visualization samples; skip full COCO evaluation.")
    parser.add_argument("--score-threshold", type=float, default=0.05, help="Class score threshold before NMS.")
    parser.add_argument("--decode-iou-threshold", type=float, default=0.5, help="IoU threshold used during decode NMS.")
    parser.add_argument("--max-output", type=int, default=200, help="Maximum predictions kept per image.")
    parser.add_argument("--dbox-min-ratio", type=float, default=0.1, help="Minimum default-box ratio used for decoding.")
    parser.add_argument("--dbox-max-ratio", type=float, default=0.9, help="Maximum default-box ratio used for decoding.")
    parser.add_argument("--gt-file", default="data/coco_gt.json", help="COCO ground-truth cache path.")
    parser.add_argument("--result-file", default=None, help="Path to save COCO-format predictions.")
    parser.add_argument("--report-dir", default="reports", help="Directory used for timestamped CSV evaluation reports.")
    parser.add_argument("--report-file", default=None, help="Explicit CSV report path. If omitted, a new timestamped CSV is created for each run.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for visualization and evaluation outputs.")
