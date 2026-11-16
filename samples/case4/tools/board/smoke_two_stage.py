"""Bounded synthetic ACL smoke for the two-model manual-test pipeline."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from palmprint_workbench.config import REPORT_DIR, ROI_PIPELINE_ID
from palmprint_workbench.services.workbench import WORKBENCH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report_path = args.report.resolve()
    report_path.relative_to(REPORT_DIR.resolve())
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "roi_pipeline_id": ROI_PIPELINE_ID}
    started = time.perf_counter()
    try:
        image = np.random.default_rng(310).integers(35, 210, (480, 640, 3), dtype=np.uint8)
        boxes = WORKBENCH.detector.detect(image)
        result["detector"] = {
            "input_shape": [1, 3, 320, 320],
            "output_shape": list(WORKBENCH.detector.runner.output_shapes[0]),
            "synthetic_boxes": len(boxes),
        }
        adapter = WORKBENCH.adapter("ccnet", "npu", "mixed_fp16")
        encoded = adapter.encode(np.random.default_rng(311).integers(35, 210, (128, 128), dtype=np.uint8))
        code = np.asarray(encoded.code)
        result["ccnet"] = {"feature_shape": list(code.shape), "finite": bool(np.all(np.isfinite(code)))}
        result["both_loaded"] = WORKBENCH.detector.runner is not None and adapter is not None
        result["ok"] = bool(result["both_loaded"] and result["ccnet"]["finite"] and code.size == 2048)
    except BaseException as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            result["close"] = WORKBENCH.close()
        except BaseException as exc:
            result["ok"] = False
            result["close_error"] = f"{type(exc).__name__}: {exc}"
        result["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
