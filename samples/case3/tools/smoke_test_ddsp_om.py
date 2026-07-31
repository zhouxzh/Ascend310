#!/usr/bin/env python3
"""Load a DDSP-VST OM with PyACL and run deterministic inference steps."""

from __future__ import annotations

import argparse
import hashlib
import platform
import sys
from pathlib import Path

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pyacl_ddsp import PyAclModelRunner


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args()
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        parser.error("OM inference must run on the Ascend board")
    if args.steps <= 0:
        parser.error("--steps must be positive")

    model = args.model.resolve()
    state = np.zeros((512,), dtype=np.float32)
    with PyAclModelRunner(model, args.device_id) as runner:
        for index in range(args.steps):
            outputs = runner.infer(
                {
                    "state": state,
                    "f0_scaled": np.asarray([0.25 + 0.5 * index / args.steps], dtype=np.float32),
                    "pw_scaled": np.asarray([0.6], dtype=np.float32),
                }
            )
            state = outputs["state_out"]
    print(
        f"[OK] PyACL OM inference: steps={args.steps}, "
        f"amplitude={float(outputs['amplitude'][0]):.6f}, "
        f"state_norm={float(np.linalg.norm(state)):.6f}"
    )
    print(f"[OK] model_sha256={sha256_file(model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
