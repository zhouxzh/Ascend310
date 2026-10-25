#!/usr/bin/env python3
"""Screen locked public Qwen quantized ONNX graphs without loading weights."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CASE_ROOT = Path(__file__).resolve().parents[2]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from ascend_edge_chat.quantized_onnx import (  # noqa: E402
    DEFAULT_CANDIDATES_PATH,
    QuantizedCandidateError,
    load_candidate_lock,
    screen_candidates,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect fixed-revision public quantized Qwen ONNX candidates."
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="Candidate lock JSON.",
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        required=True,
        help="Board directory containing <candidate-id>/<file> artifacts.",
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="Candidate ID to inspect. Repeat to select multiple; default is all.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report atomically.")
    parser.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="Return 1 when at least one candidate is blocked.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lock = load_candidate_lock(args.lock)
    report = screen_candidates(
        args.artifacts_root,
        lock,
        candidate_ids=args.candidate or None,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.output)
    else:
        sys.stdout.write(encoded)
    if args.fail_on_blocked and any(
        item["state"].startswith("blocked") for item in report["candidates"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QuantizedCandidateError as exc:
        print("quantized candidate screening failed: %s" % exc, file=sys.stderr)
        raise SystemExit(2) from exc
