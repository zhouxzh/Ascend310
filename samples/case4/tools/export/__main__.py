"""Run the board-only model export CLI as ``python -m tools.export``."""

from __future__ import annotations

from .prepare_models import main


if __name__ == "__main__":
    raise SystemExit(main())
