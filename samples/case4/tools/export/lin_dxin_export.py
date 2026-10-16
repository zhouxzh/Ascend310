"""Export Lin-Dxin's two-input ResNet18 feature comparator to ONNX.

The upstream checkpoint is a real two-input network and is intentionally kept
outside the workbench's single-input embedding adapter. Run this on a host
with PyTorch; the Ascend board only receives the resulting ONNX and OM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes: int, planes: int, stride: int = 1) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, 3, stride, 1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(planes, planes, 3, 1, 1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = (
            nn.Sequential(
                nn.Conv2d(inplanes, planes, 1, stride, bias=False),
                nn.BatchNorm2d(planes),
            )
            if stride != 1 or inplanes != planes
            else None
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        residual = value
        value = self.relu(self.bn1(self.conv1(value)))
        value = self.bn2(self.conv2(value))
        if self.downsample is not None:
            residual = self.downsample(residual)
        return self.relu(value + residual)


def _layer(inplanes: int, planes: int, blocks: int, stride: int) -> tuple[nn.Sequential, int]:
    layers = [BasicBlock(inplanes, planes, stride)]
    for _ in range(1, blocks):
        layers.append(BasicBlock(planes, planes))
    return nn.Sequential(*layers), planes


class TwoInputResNet18(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Conv2d(3, 64, 7, 2, 3, bias=False)
        blocks: list[nn.Module] = [nn.BatchNorm2d(64), nn.ReLU(inplace=True), nn.MaxPool2d(3, 2, 1)]
        channels = 64
        for planes, stride in ((64, 1), (128, 2), (256, 2), (512, 2)):
            layer, channels = _layer(channels, planes, 2, stride)
            blocks.append(layer)
        blocks.append(nn.AdaptiveAvgPool2d((1, 1)))
        self.resnet_layer = nn.Sequential(*blocks)

    def forward(self, input1: torch.Tensor, input2: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        value1 = torch.squeeze(self.resnet_layer(self.conv(input1)))
        value2 = torch.squeeze(self.resnet_layer(self.conv(input2)))
        return value1, value2


def export(checkpoint: Path, output: Path, opset: int) -> dict[str, Any]:
    payload = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    if not isinstance(payload, dict):
        raise ValueError("checkpoint is not a state dictionary")
    model = TwoInputResNet18()
    result = model.load_state_dict(payload, strict=True)
    if result.missing_keys or result.unexpected_keys:
        raise ValueError(f"checkpoint mismatch: {result}")
    model.eval()
    value1 = torch.zeros(1, 3, 224, 224)
    value2 = torch.zeros(1, 3, 224, 224)
    with torch.no_grad():
        outputs = model(value1, value2)
    if [list(item.shape) for item in outputs] != [[512], [512]]:
        raise ValueError(f"unexpected output shapes: {[list(item.shape) for item in outputs]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        (value1, value2),
        str(output),
        input_names=["input1", "input2"],
        output_names=["embedding1", "embedding2"],
        opset_version=opset,
        do_constant_folding=True,
        dynamic_axes=None,
        dynamo=False,
    )
    return {
        "model_id": "lin_dxin_resnet18_pair",
        "status": "onnx_exported",
        "input_shapes": [[1, 3, 224, 224], [1, 3, 224, 224]],
        "output_shapes": [[512], [512]],
        "inputs": ["input1", "input2"],
        "outputs": ["embedding1", "embedding2"],
        "onnx": str(output),
        "bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=13)
    args = parser.parse_args()
    print(json.dumps(export(args.checkpoint, args.output, args.opset), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
