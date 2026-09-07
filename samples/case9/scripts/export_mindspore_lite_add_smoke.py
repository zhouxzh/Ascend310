"""Export a tiny MindIR used to separate Lite graph issues from model issues.

This script is intended for the board's existing MindSpore environment.  It
uses CPU only for export; the generated MindIR can then be tested with the
isolated MindSpore Lite environment.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import mindspore as ms
from mindspore import Tensor, nn


class AddNet(nn.Cell):
    def construct(self, left: Tensor, right: Tensor) -> Tensor:
        return left + right


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="output path without .mindir")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ms.set_context(mode=ms.GRAPH_MODE, device_target="CPU")
    ms.export(
        AddNet(),
        Tensor(np.ones((1, 4), dtype=np.float32)),
        Tensor(np.full((1, 4), 2, dtype=np.float32)),
        file_name=str(args.output),
        file_format="MINDIR",
    )
    model_path = args.output.with_suffix(".mindir")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    print(f"mindspore={ms.__version__}")
    print(f"mindir={model_path}")
    print(f"bytes={model_path.stat().st_size}")
    print(f"sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
