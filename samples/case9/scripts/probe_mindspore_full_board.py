#!/usr/bin/env python3
"""Minimal full MindSpore Ascend operator probe; board-only."""

from __future__ import annotations

import json
import sys
import time

import numpy as np


def emit(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, "time": time.time(), **fields}, ensure_ascii=False), flush=True)


def main() -> int:
    try:
        import mindspore as ms
        from mindspore import Tensor, ops
    except Exception as exc:
        emit("error", stage="import", exception=type(exc).__name__, message=str(exc))
        return 10
    emit("import", version=str(ms.__version__), module=str(ms.__file__))
    try:
        ms.set_context(mode=ms.GRAPH_MODE, device_target="Ascend", device_id=0)
        emit("context", target="Ascend", device_id=0)
        left = Tensor(np.ones((1, 4), dtype=np.float32))
        right = Tensor(np.full((1, 4), 2.0, dtype=np.float32))
        output = ops.Add()(left, right)
        emit("predict_passed", shape=list(output.shape), dtype=str(output.dtype), values=output.asnumpy().tolist())
        return 0
    except BaseException as exc:
        emit("error", stage="execute", exception=type(exc).__name__, message=str(exc))
        return 20


if __name__ == "__main__":
    sys.exit(main())
