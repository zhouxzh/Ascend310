"""
Standalone GhostNet 1.0x implementation.

GhostNet replaces standard convolutions with Ghost modules: the primary
convolution produces a subset of output channels, and a cheap depthwise
operation generates the remaining "ghost" feature maps.

Reference: Han et al., "GhostNet: More Features from Cheap Operations", CVPR 2020.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

class GhostModule(nn.Module):
    """Ghost feature module.

    Splits output channels: primary path via regular conv, cheap path via
    depthwise conv on the primary output.  The two are concatenated.
    """

    def __init__(self, in_ch, out_ch, kernel_size=3, stride=1, ratio=2,
                 padding=None):
        super().__init__()
        primary_out = out_ch // ratio
        cheap_out = out_ch - primary_out

        if padding is None:
            padding = kernel_size // 2

        self.primary = nn.Sequential(
            nn.Conv2d(in_ch, primary_out, kernel_size, stride, padding,
                      bias=False),
            nn.BatchNorm2d(primary_out),
            nn.ReLU6(inplace=True),
        )

        self.cheap = nn.Sequential(
            nn.Conv2d(primary_out, cheap_out, kernel_size, 1, padding,
                      groups=primary_out, bias=False),
            nn.BatchNorm2d(cheap_out),
        )

    def forward(self, x):
        p = self.primary(x)
        c = self.cheap(p)
        return torch.cat([p, c], dim=1)


class SELayer(nn.Module):
    """Squeeze-and-Excitation with hard-sigmoid gating.

    Uses a manual hard-sigmoid (relu6 / 6) so that ONNX export works
    cleanly with opset 11.
    """

    def __init__(self, in_ch, reduction=4):
        super().__init__()
        hidden = max(in_ch // reduction, 4)
        self.conv1 = nn.Conv2d(in_ch, hidden, 1)
        self.conv2 = nn.Conv2d(hidden, in_ch, 1)

    def forward(self, x):
        s = F.adaptive_avg_pool2d(x, 1)
        s = F.relu6(self.conv1(s)) / 6.0  # hard-sigmoid
        s = self.conv2(s)
        return x * s


class GhostBottleneck(nn.Module):
    """Ghost bottleneck block.

    Stride = 1 (residual):
        GhostModule(in→hidden) → GhostModule(hidden→out) → [SE] → [+residual]

    Stride = 2 (downsample):
        GhostModule(in→hidden, stride=2) → DWConv(hidden→hidden) →
        GhostModule(hidden→out) → [SE] + shortcut(DWConv+1x1)
    """

    def __init__(self, in_ch, hidden_ch, out_ch, kernel_size, stride, se=0):
        super().__init__()
        self.stride = stride
        self.use_residual = (stride == 1 and in_ch == out_ch)

        # -- main path --
        self.ghost1 = GhostModule(in_ch, hidden_ch, kernel_size, stride)

        if stride == 2:
            self.dw_conv = nn.Sequential(
                nn.Conv2d(hidden_ch, hidden_ch, kernel_size, 1,
                          kernel_size // 2, groups=hidden_ch, bias=False),
                nn.BatchNorm2d(hidden_ch),
            )
        else:
            self.dw_conv = nn.Identity()

        self.ghost2 = GhostModule(hidden_ch, out_ch, kernel_size, stride=1)
        self.ghost2.cheap.add_module("relu6", nn.ReLU6(inplace=True))

        self.se = SELayer(out_ch) if se else nn.Identity()

        # -- shortcut (stride = 2 only) --
        if stride == 2:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_ch, in_ch, kernel_size, stride,
                          kernel_size // 2, groups=in_ch, bias=False),
                nn.BatchNorm2d(in_ch),
                nn.Conv2d(in_ch, out_ch, 1, bias=False),
                nn.BatchNorm2d(out_ch),
            )
        else:
            self.shortcut = None

    def forward(self, x):
        identity = x
        out = self.ghost1(x)
        out = self.dw_conv(out)
        out = self.ghost2(out)
        out = self.se(out)

        if self.shortcut is not None:
            identity = self.shortcut(x)
            return out + identity

        if self.use_residual:
            out = out + identity
        return out


# ---------------------------------------------------------------------------
# Network definition
# ---------------------------------------------------------------------------

# GhostNet 1.0x bottleneck configuration:
#   [out_ch, hidden_ch, kernel_size, stride, use_se]
GHOSTNET_CFG = [
    [16,   16,   3, 1, 0],
    [24,   48,   3, 2, 0],
    [24,   72,   3, 1, 0],
    [40,   72,   5, 2, 1],
    [40,  120,   5, 1, 1],
    [80,  240,   3, 2, 0],
    [80,  200,   3, 1, 0],
    [80,  184,   3, 1, 0],
    [80,  184,   3, 1, 0],
    [112, 480,   3, 1, 1],
    [112, 672,   3, 1, 1],
    [160, 672,   5, 2, 1],
    [160, 960,   5, 1, 0],
    [160, 960,   5, 1, 1],
    [160, 960,   5, 1, 0],
    [160, 960,   5, 1, 1],
]


class GhostNet(nn.Module):
    """GhostNet 1.0x backbone.

    Args:
        num_classes: if None, outputs a 1280-dim embedding (feature
                     extractor mode).  If set, adds a Linear classifier.
    """

    def __init__(self, num_classes=None):
        super().__init__()

        # stem
        self.stem = nn.Sequential(
            nn.Conv2d(3, 16, 3, 2, 1, bias=False),
            nn.BatchNorm2d(16),
            nn.ReLU6(inplace=True),
        )

        # bottleneck stack
        in_ch = 16
        layers = []
        for out_ch, hidden_ch, k, s, se in GHOSTNET_CFG:
            layers.append(
                GhostBottleneck(in_ch, hidden_ch, out_ch, k, s, se)
            )
            in_ch = out_ch
        self.bottlenecks = nn.Sequential(*layers)

        # head
        self.head_conv = nn.Sequential(
            nn.Conv2d(in_ch, 960, 1, 1, bias=False),
            nn.BatchNorm2d(960),
            nn.ReLU6(inplace=True),
        )
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.embed_conv = nn.Conv2d(960, 1280, 1, 1)

        self.classifier = None
        if num_classes is not None:
            self.classifier = nn.Linear(1280, num_classes)

        self._initialize_weights()

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out",
                                        nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                nn.init.zeros_(m.bias)

    def forward(self, x):
        x = self.stem(x)
        x = self.bottlenecks(x)
        x = self.head_conv(x)
        x = self.pool(x)
        x = self.embed_conv(x)
        x = x.flatten(1)  # (N, 1280)

        if self.classifier is not None:
            x = self.classifier(x)
        return x


def ghostnet_1x(num_classes=None):
    """Factory: GhostNet 1.0x.

    Returns a 1280-dim feature extractor when num_classes is None,
    otherwise a classifier with the given number of classes.
    """
    return GhostNet(num_classes=num_classes)
