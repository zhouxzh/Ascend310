#!/usr/bin/env python3
"""Make implicit ONNX Transpose permutations explicit for ATC."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CASE_ROOT = Path(__file__).resolve().parents[2]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from ascend_edge_chat.onnx_graph_surgery import (  # noqa: E402
    OnnxGraphSurgeryError,
    normalize_transpose_perms,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize implicit ONNX Transpose permutations for ATC."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = normalize_transpose_perms(args.input, args.output, args.report)
    print(
        json.dumps(
            {
                "normalized_transposes": report["normalized_transposes"],
                "source_bytes": report["source"]["bytes"],
                "output_bytes": report["output"]["bytes"],
                "output_sha256": report["output"]["sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OnnxGraphSurgeryError as exc:
        print("Transpose normalization failed: %s" % exc, file=sys.stderr)
        raise SystemExit(2) from exc
