"""Export reproducible Holzweber research models for board-side ATC.

This tool deliberately keeps classifier and ROI contracts separate from the
workbench's 128x128 embedding API. It is a host-side export tool: the board
does not need PyTorch. It does not modify the production registry.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torchvision import models


def _state_dict(payload: Any) -> dict[str, torch.Tensor]:
    if isinstance(payload, nn.Module):
        payload = payload.state_dict()
    if not isinstance(payload, dict):
        raise ValueError("checkpoint is not a state dictionary")
    for key in ("state_dict", "model_state_dict", "model"):
        if isinstance(payload.get(key), dict):
            payload = payload[key]
            break
    result = {}
    for key, value in payload.items():
        if torch.is_tensor(value):
            name = str(key)
            for prefix in ("module.", "model."):
                if name.startswith(prefix):
                    name = name[len(prefix):]
            # Holzweber's ROI checkpoint names the two blocks explicitly;
            # the export wrapper keeps the same topology under shorter names.
            name = name.replace("featureExtractionCNN.", "feature.")
            name = name.replace("regressionNet.", "regression.")
            result[name] = value.detach().cpu()
    return result


def _load(model: nn.Module, checkpoint: Path) -> dict[str, Any]:
    payload = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    state = _state_dict(payload)
    result = model.load_state_dict(state, strict=False)
    missing = list(result.missing_keys)
    unexpected = list(result.unexpected_keys)
    if missing or unexpected:
        raise ValueError(
            f"checkpoint contract mismatch for {checkpoint.name}: "
            f"missing={missing[:8]}, unexpected={unexpected[:8]}"
        )
    return {"checkpoint": str(checkpoint), "keys": len(state), "missing": [], "unexpected": []}


class ROILAnet(nn.Module):
    def __init__(self, h: int = 56, w: int = 56, points: int = 18) -> None:
        super().__init__()
        vgg = models.vgg16(weights=None).features[:18]
        vgg[-1] = nn.LocalResponseNorm(1024, 1e-6, 1, 0.5)
        self.feature = vgg
        self.regression = nn.Sequential(
            nn.Linear((h // 8) * (w // 8) * 256, 512),
            nn.LeakyReLU(0.01, inplace=True),
            nn.Dropout(0.2),
            nn.Linear(512, 128),
            nn.LeakyReLU(0.01, inplace=True),
            nn.Dropout(0.1),
            nn.Linear(128, points),
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value = self.feature(value)
        return self.regression(value.flatten(1))


def build(kind: str, checkpoint: Path) -> tuple[nn.Module, list[int], int, str, dict[str, Any]]:
    if kind == "resnet18_tongji_classifier":
        model = models.resnet18(weights=None)
        model.fc = nn.Linear(model.fc.in_features, 600)
        shape, classes, name = [1, 3, 224, 224], 600, "logits"
    elif kind == "mobilenet_v2_tongji_classifier":
        model = models.mobilenet_v2(weights=None)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, 600)
        shape, classes, name = [1, 3, 224, 224], 600, "logits"
    elif kind == "roi_lanet":
        model = ROILAnet()
        shape, classes, name = [1, 3, 56, 56], 18, "theta"
    else:
        raise ValueError(f"unknown model kind: {kind}")
    info = _load(model, checkpoint)
    model.eval()
    return model, shape, classes, name, info


def export_one(kind: str, checkpoint: Path, output: Path, opset: int = 13) -> dict[str, Any]:
    model, shape, output_dim, output_name, checkpoint_info = build(kind, checkpoint)
    output.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(*shape, dtype=torch.float32)
    with torch.no_grad():
        result = model(dummy)
    if list(result.shape) != [shape[0], output_dim]:
        raise ValueError(f"unexpected output shape {list(result.shape)} for {kind}")
    # The board image does not ship the optional Python ``onnx`` package. These
    # models use only standard operators and do not need ONNX Script function
    # injection, so bypass that optional post-processing step. ATC remains the
    # authoritative parser for the exported graph on the target device.
    from torch.onnx._internal import onnx_proto_utils

    onnx_proto_utils._add_onnxscript_fn = lambda model_bytes, custom_opsets: model_bytes
    torch.onnx.export(
        model,
        dummy,
        str(output),
        input_names=["input"],
        output_names=[output_name],
        opset_version=opset,
        do_constant_folding=True,
        dynamic_axes=None,
    )
    return {
        "model_id": kind,
        "status": "onnx_exported",
        "checkpoint": checkpoint_info,
        "input_shape": shape,
        "output_shape": list(result.shape),
        "output_name": output_name,
        "onnx": str(output),
        "bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("resnet18_tongji_classifier", "mobilenet_v2_tongji_classifier", "roi_lanet"), required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=13)
    args = parser.parse_args()
    import json
    print(json.dumps(export_one(args.kind, args.checkpoint, args.output, args.opset), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
