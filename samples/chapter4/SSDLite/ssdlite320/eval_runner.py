from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from .coco_eval import (
    add_coco_ground_truth_item,
    build_coco_predictions,
    new_coco_ground_truth_dict,
    run_coco_evaluation,
    write_coco_ground_truth,
)
from .config import DEFAULT_IMAGE_SIZE, DEFAULT_VAL_DATA_FILES
from .dataset_hf import extract_category_names, extract_ground_truth, iter_dataset, load_coco_val
from .default_boxes import dboxes320_coco
from .postprocess import decode_batch, pick_locs_confs
from .preprocess import preprocess_image
from .reports import write_eval_report
from .visualize import visualize_sample


def build_default_boxes(args):
    return dboxes320_coco(min_ratio=args.dbox_min_ratio, max_ratio=args.dbox_max_ratio)


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


def evaluate_dataset(args, infer_fn, dboxes, val_dataset) -> dict[str, float | str]:
    run_timestamp = get_run_timestamp(args)
    result_path = resolve_result_path(args)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    predictions = []
    image_ids = []
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
        add_coco_ground_truth_item(coco_gt, category_ids, item, sample_index, args.img_size)
        predictions.extend(build_coco_predictions(detections, image_id, args.img_size, args.img_size))

    with result_path.open("w", encoding="utf-8") as file:
        json.dump(predictions, file)

    total_time = time.time() - start_time
    count = len(image_ids)
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
        report_path = write_eval_report(args, metrics, timestamp=run_timestamp)
        print(f"Saved evaluation report: {report_path}")
        return metrics

    gt_file = write_coco_ground_truth(coco_gt, category_ids, args.gt_file)
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
    report_path = write_eval_report(args, metrics, timestamp=run_timestamp)
    print(f"Saved evaluation report: {report_path}")
    return metrics


def run_evaluation(args, infer_fn) -> dict[str, float | str]:
    dboxes = build_default_boxes(args)

    if args.num_visualizations > 0:
        viz_dataset = load_coco_val(args.dataset_name, args.cache_dir, streaming=args.streaming, data_files=args.data_files)
        run_visualizations(args, infer_fn, dboxes, viz_dataset)

    if args.skip_eval:
        return {}

    val_dataset = load_coco_val(args.dataset_name, args.cache_dir, streaming=args.streaming, data_files=args.data_files)
    return evaluate_dataset(args, infer_fn, dboxes, val_dataset)


def add_common_eval_args(parser) -> None:
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
