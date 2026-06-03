import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ssdlite320.backends import OnnxCpuRunner
from ssdlite320.config import backbone_from_model_path, list_model_paths, resolve_model_path
from ssdlite320.eval import add_common_eval_args, configure_all_run, run_evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SSDLite320 ONNX inference with CPUExecutionProvider.")
    add_common_eval_args(parser)
    args = parser.parse_args()
    if args.all and args.model:
        parser.error("--model cannot be used with --all")
    if args.all and args.result_file:
        parser.error("--result-file cannot be used with --all")
    return args


def run_one_model(args, onnx_path: Path, write_gt: bool, append_report: bool) -> dict:
    args.backbone = backbone_from_model_path(onnx_path)
    args.model_path = onnx_path
    print(f"ONNX model: {onnx_path}")

    runner = OnnxCpuRunner(str(onnx_path))
    return run_evaluation(args, runner.infer, write_gt=write_gt, append_report=append_report)


def run_all_models(args) -> int:
    onnx_paths = list_model_paths("onnx")
    if not onnx_paths:
        raise FileNotFoundError("No ONNX models found in weights/, models/, or logs/.")

    report_file = configure_all_run(args)
    Path(report_file).unlink(missing_ok=True)
    print(f"Found {len(onnx_paths)} ONNX model(s).")
    print(f"Combined report: {report_file}")

    write_gt = True
    for index, onnx_path in enumerate(onnx_paths, start=1):
        print(f"[{index}/{len(onnx_paths)}] Evaluating {onnx_path.name}")
        run_one_model(args, onnx_path, write_gt=write_gt, append_report=True)
        if not args.skip_eval:
            write_gt = False

    return 0


def main() -> int:
    args = parse_args()
    args.backend = "cpu"
    if args.all:
        return run_all_models(args)

    onnx_path = resolve_model_path(args.model, args.backbone, "onnx")
    run_one_model(args, onnx_path, write_gt=True, append_report=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
