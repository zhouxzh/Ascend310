"""Stable command-line interface for Ascend Edge Chat."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .artifacts.model_store import (
    ArtifactError,
    DEFAULT_MODEL_CONFIG,
    RELEASE_PROFILE_ID,
    data_root,
    download_artifacts,
    import_artifacts,
    report,
    verify_artifacts,
    verify_release_lock,
)
from .runtime.config import load_release_environment
from .version import PROJECT_NAME, __version__


def main(argv: Sequence[str] | None = None) -> int:
    load_release_environment()
    parser = argparse.ArgumentParser(prog="ascend-edge-chat", description=PROJECT_NAME)
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    worker = commands.add_parser("worker", help="start the loopback MindSpore worker")
    worker.add_argument("--host", default="127.0.0.1")
    worker.add_argument("--port", default=8090, type=int)

    gateway = commands.add_parser("gateway", help="start the authenticated loopback gateway")
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--port", default=7861, type=int)
    gateway.add_argument("--check-config", action="store_true")

    ui = commands.add_parser("ui", help="start the trusted-LAN browser UI")
    ui.add_argument("--host", default=os.environ.get("ASCEND_EDGE_CHAT_UI_HOST", "0.0.0.0"))
    ui.add_argument("--port", default=int(os.environ.get("ASCEND_EDGE_CHAT_UI_PORT", "7865")), type=int)

    verify = commands.add_parser("verify", help="validate the model lock and local artifacts")
    verify.add_argument("--data-dir", type=Path)

    model = commands.add_parser("model", help="manage verified model artifacts")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    download = model_commands.add_parser("download", help="download artifacts through an HTTPS mirror")
    download.add_argument("--data-dir", type=Path)
    download.add_argument("--mirror", default=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
    offline = model_commands.add_parser("import", help="import artifacts from an offline directory")
    offline.add_argument("--data-dir", type=Path)
    offline.add_argument("--source", required=True, type=Path)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "worker":
            return _worker(args.host, args.port)
        if args.command == "gateway":
            return _gateway(args.host, args.port, args.check_config)
        if args.command == "ui":
            return _ui(args.host, args.port)
        root = data_root(str(args.data_dir) if getattr(args, "data_dir", None) else None)
        if args.command == "verify":
            print(json.dumps(report(verify_artifacts(root)), ensure_ascii=False, indent=2))
            return 0
        if args.model_command == "download":
            print(json.dumps(report(download_artifacts(root, mirror=args.mirror)), ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(report(import_artifacts(root, args.source)), ensure_ascii=False, indent=2))
        return 0
    except ArtifactError as exc:
        parser.error(str(exc))
        return 2


def _worker(host: str, port: int) -> int:
    from .backends import mindspore_worker

    root = data_root()
    verify_artifacts(root)
    os.environ["ASCEND_EDGE_CHAT_MODEL_PROFILES"] = str(DEFAULT_MODEL_CONFIG)
    os.environ["ASCEND_EDGE_CHAT_MODEL_ROOT"] = str(root)
    os.environ["ASCEND_EDGE_CHAT_LAUNCHER_VERIFIED"] = "1"
    os.environ.setdefault("ASCEND_EDGE_CHAT_PROCESS_WATCHDOG", "1")
    os.environ.setdefault("ASCEND_EDGE_CHAT_WORKER_MAIN", "1")
    os.environ.setdefault("ASCEND_EDGE_CHAT_ALLOW_PREVIEW", "1")
    timeout = os.environ.get("ASCEND_EDGE_CHAT_GENERATION_TIMEOUT", "300")
    return mindspore_worker._main(
        [
            "--profile",
            RELEASE_PROFILE_ID,
            "--host",
            host,
            "--port",
            str(port),
            "--generation-timeout",
            timeout,
        ]
    )


def _gateway(host: str, port: int, check_config: bool) -> int:
    from .api import gateway

    arguments = ["--host", host, "--port", str(port)]
    if check_config:
        arguments.append("--check-config")
    gateway.main(arguments)
    return 0


def _ui(host: str, port: int) -> int:
    from .ui import web

    web.main(["--host", host, "--port", str(port)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
