"""Export PPNet's 512-D feature path to ONNX on the host.

The board receives only the resulting ONNX/OM.  PPNet's training ``forward``
returns logits plus a pair distance; the workbench-compatible research graph
exports ``getFeatureCode`` with a single ``[1,1,128,128]`` input and ``[1,512]``
output instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn


class PPNet(nn.Module):
    def __init__(self, classes: int = 600) -> None:
        super().__init__()
        self.layer1 = nn.ModuleDict({"conv": nn.Conv2d(1, 16, 5, 1), "bn": nn.BatchNorm2d(16)})
        self.layer2 = nn.ModuleDict({"conv": nn.Conv2d(16, 32, 1, 1), "bn": nn.BatchNorm2d(32, momentum=0.001), "sigmoid": nn.Sigmoid(), "avgpool": nn.AvgPool2d(2, 2)})
        self.layer3 = nn.ModuleDict({"conv": nn.Conv2d(32, 64, 3, 1), "bn": nn.BatchNorm2d(64, momentum=0.001), "sigmoid": nn.Sigmoid(), "avgpool": nn.AvgPool2d(2, 2)})
        self.layer4 = nn.ModuleDict({"conv": nn.Conv2d(64, 64, 3, 1), "bn": nn.BatchNorm2d(64, momentum=0.001), "relu": nn.ReLU()})
        self.layer5 = nn.ModuleDict({"conv": nn.Conv2d(64, 256, 3, 1), "bn": nn.BatchNorm2d(256, momentum=0.001), "relu": nn.ReLU(), "maxpool": nn.MaxPool2d(2, 2)})
        self.fc1 = nn.Linear(43264, 512)
        self.bn1 = nn.BatchNorm1d(512, momentum=0.001)
        self.relu1 = nn.ReLU()
        self.fc2 = nn.Linear(512, 512)
        self.bn2 = nn.BatchNorm1d(512, momentum=0.001)
        self.relu2 = nn.ReLU()
        self.drop2 = nn.Dropout(0.25)
        self.fc3 = nn.Linear(512, classes)

    def get_feature_code(self, value: torch.Tensor) -> torch.Tensor:
        value = self.layer1["bn"](self.layer1["conv"](value))
        value = self.layer2["avgpool"](self.layer2["sigmoid"](self.layer2["bn"](self.layer2["conv"](value))))
        value = self.layer3["avgpool"](self.layer3["sigmoid"](self.layer3["bn"](self.layer3["conv"](value))))
        value = self.layer4["relu"](self.layer4["bn"](self.layer4["conv"](value)))
        value = self.layer5["maxpool"](self.layer5["relu"](self.layer5["bn"](self.layer5["conv"](value))))
        value = value.flatten(1)
        value = self.relu1(self.bn1(self.fc1(value)))
        return self.relu2(self.bn2(self.fc2(value)))

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return self.get_feature_code(value)


def export(checkpoint: Path, output: Path, opset: int) -> dict[str, Any]:
    payload = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    if not isinstance(payload, dict):
        raise ValueError("checkpoint is not a state dictionary")
    model = PPNet()
    result = model.load_state_dict(payload, strict=True)
    if result.missing_keys or result.unexpected_keys:
        raise ValueError(f"checkpoint mismatch: {result}")
    model.eval()
    value = torch.zeros(1, 1, 128, 128)
    with torch.no_grad():
        result_value = model(value)
    if list(result_value.shape) != [1, 512]:
        raise ValueError(f"unexpected output shape: {list(result_value.shape)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        value,
        str(output),
        input_names=["input"],
        output_names=["embedding"],
        opset_version=opset,
        do_constant_folding=True,
        dynamic_axes=None,
        dynamo=False,
    )
    return {
        "model_id": "ppnet",
        "status": "onnx_exported",
        "input_shape": [1, 1, 128, 128],
        "output_shape": [1, 512],
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
