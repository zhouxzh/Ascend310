"""Test the broader palmprint model families without changing the serving registry.

The board smoke in this module exercises torchvision architectures with random
weights.  It is an NPU graph/runtime test only: the models are not palmprint
fine-tuned and the result must not be reported as recognition accuracy.
Models whose papers do not ship a reproducible local checkpoint are recorded as
asset-blocked instead of being replaced by a generic backbone.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any, Callable


ARCHITECTURE_SPECS: tuple[dict[str, Any], ...] = (
    {"id": "resnet18_imagenet_backbone", "family": "ResNet", "task": "backbone", "constructor": "resnet18"},
    {"id": "resnet50_imagenet_backbone", "family": "ResNet", "task": "backbone", "constructor": "resnet50"},
    {"id": "vgg16_imagenet_backbone", "family": "VGG", "task": "backbone", "constructor": "vgg16"},
    {"id": "densenet121_imagenet_backbone", "family": "DenseNet", "task": "backbone", "constructor": "densenet121"},
    {"id": "mobilenet_v2_imagenet_backbone", "family": "MobileNetV2", "task": "backbone", "constructor": "mobilenet_v2"},
    {"id": "efficientnet_v2_s_imagenet_backbone", "family": "EfficientNetV2-S", "task": "backbone", "constructor": "efficientnet_v2_s"},
    {"id": "vit_b_16_imagenet_backbone", "family": "ViT-B/16", "task": "backbone", "constructor": "vit_b_16"},
    {"id": "swin_t_imagenet_backbone", "family": "Swin-T", "task": "backbone", "constructor": "swin_t"},
)


SPECIALIZED_SPECS: tuple[dict[str, Any], ...] = (
    {"id": "roi3net", "family": "ROI3Net", "task": "roi", "reason": "no verified checkpoint or adapter in this release"},
    {"id": "palm_id", "family": "Palm-ID", "task": "hybrid_embedding", "reason": "paper-level multimodel pipeline; no reproducible local checkpoint"},
    {"id": "pvcode_net", "family": "PVCodeNet", "task": "hybrid_embedding", "reason": "no verified checkpoint, ONNX contract, or adapter"},
    {"id": "msphnet", "family": "MSPHNet", "task": "hybrid_embedding", "reason": "no verified checkpoint, ONNX contract, or adapter"},
    {"id": "meal", "family": "MEAL", "task": "ensemble_distillation", "reason": "method/paper is not a released palmprint checkpoint"},
    {"id": "three_d_ia_resnet", "family": "3D IA-ResNet", "task": "classifier", "reason": "reported as left/right offline classification; no released checkpoint"},
    {"id": "eca_mnet", "family": "ECA-MNet", "task": "classifier", "reason": "reported as classification; no verified checkpoint or export"},
    {"id": "palmalnet", "family": "PalmALNet", "task": "embedding", "reason": "requires CROI/alignment contract; no verified checkpoint in this release"},
    {"id": "palm_gan", "family": "Palm-GAN", "task": "generator", "reason": "generation/data augmentation, not an identity embedding model"},
    {"id": "pd_gan", "family": "PD-GAN", "task": "generator", "reason": "no verified palmprint checkpoint and not a serving recognizer"},
    {"id": "diff_palm", "family": "Diff-Palm", "task": "diffusion_generator", "reason": "generation model; authors state code/checkpoints are released separately"},
    {"id": "genpalm", "family": "GenPalm", "task": "diffusion_generator", "reason": "generation model, not a serving identity encoder"},
    {"id": "palmnet_research", "family": "PalmNet", "task": "embedding", "reason": "no reproducible checkpoint/export in this release"},
    {"id": "nas_palmprint", "family": "NAS palmprint CNN", "task": "training_method", "reason": "NAS search result is not a fixed deployable model"},
    {"id": "self_supervised_efficientnet", "family": "self-supervised EfficientNet", "task": "training_method", "reason": "training recipe without a verified palmprint checkpoint"},
)


def _finite_output(value: Any) -> bool:
    import torch

    if isinstance(value, (tuple, list)):
        return all(_finite_output(item) for item in value)
    if isinstance(value, dict):
        return all(_finite_output(item) for item in value.values())
    if not torch.is_tensor(value):
        return True
    return bool(torch.isfinite(value).all().item())


def _shape(value: Any) -> Any:
    import torch

    if isinstance(value, (tuple, list)):
        return [_shape(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _shape(item) for key, item in value.items()}
    if torch.is_tensor(value):
        return list(value.shape)
    return str(type(value).__name__)


def run_board_smoke(
    *, warmup: int = 5, loops: int = 20, candidate_id: str | None = None
) -> dict[str, Any]:
    """Run generic architecture smoke on an Ascend board.

    ``weights=None`` is intentional.  It avoids an implicit download and makes
    the limitation explicit in the generated evidence.
    """

    import torch_npu  # noqa: F401 - registers the NPU backend
    import torch
    import torchvision.models as models

    if not torch.npu.is_available():
        raise RuntimeError("torch.npu is not available")
    torch.manual_seed(20260911)
    device = torch.device("npu:0")
    records: list[dict[str, Any]] = []
    selected = [
        spec for spec in ARCHITECTURE_SPECS
        if candidate_id is None or spec["id"] == candidate_id
    ]
    if not selected:
        raise ValueError(f"unknown architecture candidate: {candidate_id}")
    for spec in selected:
        started = time.time()
        record: dict[str, Any] = {
            "candidate_id": spec["id"],
            "family": spec["family"],
            "task": spec["task"],
            "backend": "npu",
            "weights": "none_random_initialization",
            "input_dtype": "float16",
            "input_shape": [1, 3, 224, 224],
            "palmprint_adapter": "required: grayscale/channel and 224x224 contract differ from serving 128x128 ROI",
            "warmup_requested": warmup,
            "loops_requested": loops,
        }
        model = None
        try:
            constructor: Callable[..., Any] = getattr(models, spec["constructor"])
            # 310B4's supported path for these graph operators is FP16. This
            # remains an architecture smoke; weights are random and are never
            # presented as palmprint recognition weights.
            model = constructor(weights=None).eval().to(device).half()
            value = torch.randn(1, 3, 224, 224, dtype=torch.float16, device=device)
            with torch.no_grad():
                for _ in range(max(0, int(warmup))):
                    model(value)
                torch.npu.synchronize()
                timings: list[float] = []
                output = None
                for _ in range(max(1, int(loops))):
                    tick = time.perf_counter()
                    output = model(value)
                    torch.npu.synchronize()
                    timings.append((time.perf_counter() - tick) * 1000.0)
            timings.sort()
            p50 = timings[len(timings) // 2]
            p95 = timings[min(len(timings) - 1, math.ceil(len(timings) * 0.95) - 1)]
            finite = _finite_output(output)
            record.update({
                "status": "passed" if finite else "npu_output_nonfinite",
                "output_shape": _shape(output),
                "finite": finite,
                "p50_ms": round(p50, 4),
                "p95_ms": round(p95, 4),
                "elapsed_seconds": round(time.time() - started, 4),
                "qualification": (
                    "NPU architecture/runtime smoke only; no palmprint weights or recognition metric"
                    if finite
                    else "NPU output was non-finite; no palmprint conclusion"
                ),
            })
        except BaseException as exc:  # noqa: BLE001 - candidate-specific evidence
            record.update({
                "status": "npu_smoke_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "qualification": "architecture smoke failed; no palmprint conclusion",
            })
        finally:
            del model
            try:
                torch.npu.empty_cache()
            except BaseException:
                pass
        records.append(record)
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hardware_scope": "Ascend 310B4 / 8T",
        "torch_backend": "torchvision, weights=None",
        "records": records,
        "specialized_models": [
            {
                **spec,
                "status": "blocked_asset_or_contract",
                "backend": "not_run",
                "qualification": "No verified local checkpoint/contract; see reason",
            }
            for spec in SPECIALIZED_SPECS
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("board-smoke", "inventory"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--loops", type=int, default=20)
    parser.add_argument("--candidate")
    args = parser.parse_args(argv)
    if args.command == "inventory":
        payload = {
            "architecture_models": list(ARCHITECTURE_SPECS),
            "specialized_models": list(SPECIALIZED_SPECS),
        }
    else:
        payload = run_board_smoke(
            warmup=args.warmup, loops=args.loops, candidate_id=args.candidate
        )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if args.command == "inventory" or all(item.get("status") == "passed" for item in payload["records"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
