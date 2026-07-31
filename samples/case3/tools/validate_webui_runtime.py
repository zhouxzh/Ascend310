#!/usr/bin/env python3
"""Run board-side checks for the unified FastAPI runtime contract."""

from __future__ import annotations

from midi_ddsp_webui import app as web_app


def main() -> int:
    if not web_app.is_ascend_board():
        raise SystemExit("This check must run on an Ascend 310B board")

    paths = {route.path for route in web_app.app.routes}
    required = {
        "/api/v1/status",
        "/api/v1/catalog",
        "/api/v1/realtime/catalog",
        "/api/v1/realtime/status",
        "/api/v1/realtime/start",
        "/api/v1/realtime/stop",
        "/api/v1/realtime/events",
    }
    missing = sorted(required - paths)
    removed = sorted(
        path
        for path in paths
        if path.startswith(("/api/v1/ddsp-vst/", "/api/v1/piano-ddsp/"))
    )
    if missing or removed:
        raise SystemExit(f"route contract failed: missing={missing}, removed={removed}")

    status = web_app.get_status()
    catalog = web_app.get_catalog()
    realtime = web_app.get_realtime_catalog()
    if "realtime" not in status or "ddsp_vst" in status or "piano_ddsp" in status:
        raise SystemExit("status contract failed")
    patches = realtime.get("patches", [])
    if not isinstance(patches, list) or not patches:
        raise SystemExit("realtime catalog has no available patches")
    if not catalog.get("ddsp_vst_models"):
        raise SystemExit("DDSP-VST catalog has no OM models")
    print(
        f"[OK] status owner={status.get('active_owner')!r}; "
        f"catalog models={len(catalog['ddsp_vst_models'])}; patches={len(patches)}"
    )
    print("[OK] unified WebUI route and catalog contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
