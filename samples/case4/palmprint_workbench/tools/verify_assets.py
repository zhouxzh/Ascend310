"""Manual runtime asset verification entry point.

The board verifier lives under :mod:`tools.board` and is imported explicitly;
there is no path mutation or shell wrapper.  Verification never installs,
downloads, converts models, or builds EDCC.
"""

from __future__ import annotations

def main(argv: list[str] | None = None) -> int:
    """Run the explicit board-side verifier with the current arguments."""

    from tools.board.verify_runtime_assets import main as verify_main

    return int(verify_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
