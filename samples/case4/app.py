#!/usr/bin/env python3
"""Thin process entry point for the NPU-only palmprint workbench.

The implementation lives in :mod:`palmprint_workbench.api`; this file exists
for operators who prefer ``python app.py`` and for ASGI servers configured as
``app:app``.  Offline CPU/EDCC tools are deliberately outside this boundary.
"""

from __future__ import annotations

import argparse
from typing import Any

from palmprint_workbench.config import SERVER_HOST, SERVER_PORT


__all__ = ["app", "create_api_app", "main"]


def __getattr__(name: str) -> Any:
    """Lazily expose only the ASGI application.

    Keeping the import lazy avoids constructing the global workbench when a
    CLI help command or a static source check imports this module.  Attribute
    names other than ``app`` are deliberately rejected; callers that need an
    offline callback must import the offline service module explicitly.
    """

    if name == "app":
        from palmprint_workbench.api.server import app as service_app

        return service_app
    raise AttributeError(name)


def create_api_app() -> Any:
    """Create an isolated FastAPI instance for tests or ASGI tooling."""

    from palmprint_workbench.api.server import create_app

    return create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=SERVER_HOST)
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    args = parser.parse_args()
    from palmprint_workbench.api.server import serve

    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
