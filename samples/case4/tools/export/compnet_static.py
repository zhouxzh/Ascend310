"""Static inference-only CompNet used for ONNX and Ascend export."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import torch
from torch import nn
import torch.nn.functional as F


def gabor_bank(
    kernel_size: int,
    channels: int,
    sigma: torch.Tensor,
    gamma: torch.Tensor,
    theta: torch.Tensor,
    frequency: torch.Tensor,
    psi: torch.Tensor,
) -> torch.Tensor:
    radius = kernel_size // 2
    axis = torch.arange(-radius, radius + 1, dtype=torch.float32, device=sigma.device)
    y, x = torch.meshgrid(axis, axis, indexing="ij")
    x = x.reshape(1, 1, kernel_size, kernel_size).repeat(channels, 1, 1, 1)
    y = y.reshape(1, 1, kernel_size, kernel_size).repeat(channels, 1, 1, 1)
    angles = theta.reshape(-1, 1, 1, 1)
    x_theta = x * torch.cos(angles) + y * torch.sin(angles)
    y_theta = -x * torch.sin(angles) + y * torch.cos(angles)
    bank = -torch.exp(
        -0.5
        * ((gamma.reshape(-1, 1, 1, 1) * x_theta) ** 2 + y_theta**2)
        / (8.0 * sigma.reshape(-1, 1, 1, 1) ** 2)
    ) * torch.cos(
        2.0 * math.pi * frequency.reshape(-1, 1, 1, 1) * x_theta
        + psi.reshape(-1, 1, 1, 1)
    )
    return bank - bank.mean(dim=(2, 3), keepdim=True)


class StaticCompetitiveBlock(nn.Module):
    def __init__(self, kernel_size: int, init_ratio: float) -> None:
        super().__init__()
        self.gabor = nn.Conv2d(1, 9, kernel_size, stride=3, bias=False)
        self.a = nn.Parameter(torch.ones(1))
        self.b = nn.Parameter(torch.zeros(1))
        self.conv1 = nn.Conv2d(9, 32, 5)
        self.maxpool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(32, 12, 1)
        self._set_default_gabor(kernel_size, init_ratio)

    def _set_default_gabor(self, kernel_size: int, ratio: float) -> None:
        sigma = torch.tensor([9.2 * ratio])
        gamma = torch.tensor([2.0])
        theta = torch.arange(9, dtype=torch.float32) * math.pi / 9.0
        frequency = torch.tensor([0.057 / ratio])
        psi = torch.tensor([0.0])
        with torch.no_grad():
            self.gabor.weight.copy_(
                gabor_bank(kernel_size, 9, sigma, gamma, theta, frequency, psi)
            )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        value = self.gabor(value)
        value = F.softmax((value - self.b) * self.a, dim=1)
        return self.conv2(self.maxpool(self.conv1(value)))


class StaticCompNet(nn.Module):
    """CompNet without Dropout or ArcMargin classification head."""

    def __init__(self) -> None:
        super().__init__()
        self.cb1 = StaticCompetitiveBlock(35, 1.0)
        self.cb2 = StaticCompetitiveBlock(17, 0.5)
        self.cb3 = StaticCompetitiveBlock(7, 0.25)
        self.fc = nn.Linear(9708, 512)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        features = [block(value).flatten(1) for block in (self.cb1, self.cb2, self.cb3)]
        return F.normalize(self.fc(torch.cat(features, dim=1)), p=2, dim=1)


def _state_dict(payload: Any) -> dict[str, torch.Tensor]:
    if isinstance(payload, nn.Module):
        state = payload.state_dict()
    elif isinstance(payload, dict):
        state = payload.get("state_dict") or payload.get("model_state_dict") or payload
    else:
        raise ValueError("Unsupported CompNet checkpoint payload")
    result: dict[str, torch.Tensor] = {}
    for key, value in state.items():
        if torch.is_tensor(value):
            result[str(key).removeprefix("module.")] = value.detach().cpu()
    return result


def load_dynamic_checkpoint(model: StaticCompNet, checkpoint: Path) -> None:
    """Freeze the learned dynamic Gabor parameters from an upstream checkpoint."""

    # CompNet checkpoints are expected to be tensor-only state dictionaries.
    # ``weights_only`` avoids executing arbitrary pickle globals when an
    # exporter is pointed at a downloaded file.  Do not silently fall back to
    # unrestricted pickle loading: an older Torch runtime should fail with an
    # actionable message instead of weakening this boundary.
    try:
        payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    except TypeError as exc:
        raise RuntimeError(
            "CompNet export requires a PyTorch version supporting "
            "torch.load(..., weights_only=True)"
        ) from exc
    state = _state_dict(payload)
    missing: list[str] = []
    with torch.no_grad():
        for block_name in ("cb1", "cb2", "cb3"):
            target = getattr(model, block_name)
            prefix = f"{block_name}.gabor_conv2d"
            values = {}
            for name in ("sigma", "gamma", "theta", "f", "psi"):
                key = f"{prefix}.{name}"
                if key not in state:
                    missing.append(key)
                else:
                    values[name] = state[key]
            if values:
                target.gabor.weight.copy_(
                    gabor_bank(
                        target.gabor.kernel_size[0],
                        target.gabor.out_channels,
                        values["sigma"],
                        values["gamma"],
                        values["theta"],
                        values["f"],
                        values["psi"],
                    )
                )
            for name in ("a", "b"):
                key = f"{block_name}.{name}"
                if key in state:
                    getattr(target, name).copy_(state[key])
                else:
                    missing.append(key)
            for layer in ("conv1", "conv2"):
                for suffix in ("weight", "bias"):
                    key = f"{block_name}.{layer}.{suffix}"
                    if key in state:
                        getattr(getattr(target, layer), suffix).copy_(state[key])
                    else:
                        missing.append(key)
        for suffix in ("weight", "bias"):
            key = f"fc.{suffix}"
            if key in state:
                getattr(model.fc, suffix).copy_(state[key])
            else:
                missing.append(key)
    if missing:
        raise ValueError("Checkpoint lacks CompNet inference tensors: " + ", ".join(missing))


def build_static_compnet(checkpoint: Path | None, seed: int) -> tuple[StaticCompNet, bool]:
    torch.manual_seed(seed)
    model = StaticCompNet().eval()
    official = bool(checkpoint and checkpoint.is_file())
    if official:
        load_dynamic_checkpoint(model, checkpoint)
    return model.eval(), official
