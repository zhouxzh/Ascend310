"""FastAPI command boundary for the NPU-only workbench.

Imports are intentionally lazy so importing ``palmprint_workbench.api`` does
not initialize PyACL or allocate an OM runner.  Use ``python -m
palmprint_workbench.api`` for the same service as ``python app.py``.
"""

from __future__ import annotations

import argparse
from typing import Any, Sequence

from ..config import SERVER_HOST, SERVER_PORT

__all__ = ["create_app", "main", "serve"]


def create_app() -> Any:
    """Return a freshly configured FastAPI application."""

    from .server import create_app as _create_app

    return _create_app()


def serve(host: str = SERVER_HOST, port: int = SERVER_PORT) -> None:
    """Start the production service after the caller configured CANN/conda."""

    from .server import serve as _serve

    _serve(host=host, port=int(port))


def main(argv: Sequence[str] | None = None) -> int:
    """Parse service options and run Uvicorn.

    Environment activation is deliberately outside this function.  Operators
    must source the board's conda and CANN environments manually before
    invoking it, as documented in the repository README.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=SERVER_HOST)
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    return 0
