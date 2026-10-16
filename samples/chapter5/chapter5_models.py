"""第5章样例共享的最小模型与合成数据。

Ascend 310B 的矩阵计算路径要求 fp16（fp32/bf16 会在 cube 单元被拒绝），因此
样例统一使用显式 fp16 `Parameter` + `ops.matmul`，不用 `to_float(ms.float16)`。
"""

from __future__ import annotations

import numpy as np
import mindspore as ms
from mindspore import Tensor, nn, ops


class Fp16Linear(nn.Cell):
    """最小可训练线性层：fp16 权重与偏置，矩阵乘走 `ops.matmul`。"""

    def __init__(self, in_dim: int = 4, out_dim: int = 1, seed: int = 0) -> None:
        super().__init__()
        rng = np.random.default_rng(seed)
        weight = rng.normal(scale=0.1, size=(in_dim, out_dim)).astype(np.float16)
        self.weight = ms.Parameter(Tensor(weight, ms.float16), name="weight")
        self.bias = ms.Parameter(Tensor(np.zeros((out_dim,), np.float16), ms.float16), name="bias")

    def construct(self, x):
        return ops.matmul(x, self.weight) + self.bias


def make_linear_dataset(samples: int = 32, seed: int = 0) -> tuple[Tensor, Tensor]:
    """生成可复现的 y = x @ w + b 合成数据（fp16）。"""

    rng = np.random.default_rng(seed)
    x = rng.normal(size=(samples, 4)).astype(np.float16)
    true_weight = np.array([[2.0], [-1.0], [0.5], [0.25]], dtype=np.float16)
    y = (x @ true_weight + np.float16(0.5)).astype(np.float16)
    return Tensor(x, ms.float16), Tensor(y, ms.float16)
