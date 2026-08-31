#!/usr/bin/env python3
"""Retired legacy smoke-test entry point.

The former script exercised the removed CPU/EDCC callback surface. It is kept
as a small compatibility marker outside the release package so an old local
command fails clearly instead of importing a second, unsupported service.
Use the API contract tests and the board tools documented in README.md.
"""

from __future__ import annotations


def main() -> int:
    raise SystemExit(
        "The legacy CPU/EDCC UI smoke test is retired; use the NPU-only API "
        "and tools.board diagnostics instead."
    )


if __name__ == "__main__":
    main()
