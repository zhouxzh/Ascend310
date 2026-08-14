"""Generate the narrow mixed-precision keep_dtype list for TorchSig YOLO11 decode."""

from __future__ import annotations

import argparse
from pathlib import Path

import onnx


BOX_DECODE_START = 320


def select_keep_dtype_nodes(model: onnx.ModelProto) -> list[str]:
    """Select only the terminal Ultralytics DFL decode and output nodes.

    Both Detect branches and the feature-extraction backbone deliberately remain
    subject to ATC's mixed-precision policy.  The selected tail converts DFL bins
    into decoded boxes and joins them with class confidences.
    """
    selected: list[str] = []
    for index, node in enumerate(model.graph.node):
        if index >= BOX_DECODE_START and node.op_type != "Constant":
            if not node.name:
                raise ValueError("YOLO ONNX graph contains an unnamed selected node")
            selected.append(node.name)
    if "/model.23/dfl/Softmax" not in selected or "/model.23/Concat_3" not in selected:
        raise ValueError("ONNX graph does not contain the expected TorchSig YOLO decode nodes")
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model = onnx.load(args.onnx)
    onnx.checker.check_model(model)
    selected = select_keep_dtype_nodes(model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(selected) + "\n", encoding="ascii")
    print(f"wrote {args.output} with {len(selected)} operator names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
