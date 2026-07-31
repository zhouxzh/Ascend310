#!/usr/bin/env python3
"""Record or validate a stateful MIDI-DDSP ATC conversion result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "validate"))
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--expected-onnx-sha256", required=True)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--soc-version", required=True)
    parser.add_argument("--input-shape", required=True)
    parser.add_argument("--precision-mode-v2", required=True)
    return parser.parse_args()


def expected_payload(args: argparse.Namespace) -> dict[str, object]:
    for path in (args.onnx, args.om, args.log, args.summary):
        if not path.is_file():
            raise FileNotFoundError(path)
    source_hash = sha256(args.onnx)
    if source_hash != args.expected_onnx_sha256:
        raise ValueError(f"ONNX SHA256 mismatch for {args.onnx}")
    summary = args.summary.read_text(encoding="utf-8", errors="replace").splitlines()
    required_lines = {"ATC_EXIT_CODE=0", "OM_UPDATED=yes", "ERROR_LINES=none"}
    if not required_lines.issubset(summary):
        raise ValueError(f"ATC summary is not successful: {args.summary}")
    return {
        "schema": "midi-ddsp-atc-conversion/v1",
        "source_onnx": args.onnx.name,
        "source_onnx_sha256": source_hash,
        "om": args.om.name,
        "om_sha256": sha256(args.om),
        "atc_log": args.log.name,
        "atc_log_sha256": sha256(args.log),
        "atc_summary": args.summary.name,
        "atc_summary_sha256": sha256(args.summary),
        "soc_version": args.soc_version,
        "input_shape": args.input_shape,
        "precision_mode_v2": args.precision_mode_v2,
    }


def main() -> int:
    args = parse_args()
    expected = expected_payload(args)
    if args.action == "record":
        args.provenance.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.provenance.with_suffix(args.provenance.suffix + ".part")
        temporary.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, args.provenance)
    else:
        actual = json.loads(args.provenance.read_text(encoding="utf-8"))
        if actual != expected:
            raise ValueError(f"Conversion provenance mismatch: {args.provenance}")
    print(args.provenance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
