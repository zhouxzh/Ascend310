#!/usr/bin/env python3
"""Minimal ACL device/context probe; run only on an Ascend board."""

from __future__ import annotations

import json
import sys
import time


def emit(event: str, **fields: object) -> None:
    record = {"event": event, "time": time.time(), **fields}
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)


def main() -> int:
    try:
        import acl
    except Exception as exc:  # pragma: no cover - board-only
        emit("error", stage="import", exception=type(exc).__name__, message=str(exc))
        return 10

    context = None
    try:
        version = acl.get_run_path() if hasattr(acl, "get_run_path") else ""
        emit("import", module=str(getattr(acl, "__file__", "")), run_path=str(version))
        rc = acl.init()
        emit("init", rc=rc)
        if rc != 0:
            return 20
        rc = acl.rt.set_device(0)
        emit("set_device", rc=rc, device_id=0)
        if rc != 0:
            return 21
        context, rc = acl.rt.create_context(0)
        emit("create_context", rc=rc, context=str(context))
        if rc != 0:
            return 22
        return 0
    except BaseException as exc:  # pragma: no cover - board-only
        emit("error", stage="acl", exception=type(exc).__name__, message=str(exc))
        return 30
    finally:
        if context is not None:
            try:
                emit("destroy_context", rc=acl.rt.destroy_context(context))
            except BaseException as exc:
                emit("cleanup_error", stage="destroy_context", message=str(exc))
        try:
            emit("reset_device", rc=acl.rt.reset_device(0))
        except BaseException as exc:
            emit("cleanup_error", stage="reset_device", message=str(exc))
        try:
            emit("finalize", rc=acl.finalize())
        except BaseException as exc:
            emit("cleanup_error", stage="finalize", message=str(exc))


if __name__ == "__main__":
    sys.exit(main())
