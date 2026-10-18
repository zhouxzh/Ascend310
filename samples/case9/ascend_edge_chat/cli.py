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
from .backends.model_profiles import load_profiles, resolve_safe_path
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
    worker.add_argument(
        "--profile",
        default=os.environ.get("ASCEND_EDGE_CHAT_PROFILE", RELEASE_PROFILE_ID),
        help="validated profile id; use the tutorial registry for classroom experiments",
    )
    worker.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_MODEL_CONFIG,
        help="profile registry (default: immutable release registry)",
    )

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
            return _worker(args.host, args.port, args.profile, args.registry)
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


def _verify_tutorial_cache(root: Path, profile: object) -> None:
    """Verify the minimum local files before loading a non-release profile.

    Tutorial artifacts intentionally carry metadata-only hashes in the runtime
    registry.  The acquisition/reproducibility records retain the real hashes;
    this preflight only prevents a missing or partial cache from becoming an
    implicit network download.
    """

    cache_dir = resolve_safe_path(profile.cache_dir, root, allow_missing=True)
    if not cache_dir.is_dir() or cache_dir.is_symlink():
        raise ArtifactError("tutorial model cache is missing: %s" % cache_dir)
    required = ["config.json"]
    if not any((cache_dir / name).is_file() for name in ("tokenizer.json", "tokenizer.model", "sentencepiece.model")):
        required.append("tokenizer.json or tokenizer.model")
    if not any(
        item.is_file()
        and not item.name.startswith(".")
        and item.name.lower().endswith((".safetensors", ".bin", ".ckpt"))
        for item in cache_dir.iterdir()
    ):
        required.append("model.safetensors or checkpoint")
    missing = [name for name in required if (name == "config.json" and not (cache_dir / name).is_file())]
    if len(required) > 1 and not any((cache_dir / name).is_file() for name in ("tokenizer.json", "tokenizer.model", "sentencepiece.model")):
        missing.append("tokenizer")
    if len(required) > 1 and not any(
        item.is_file() and item.name.lower().endswith((".safetensors", ".bin", ".ckpt"))
        for item in cache_dir.iterdir()
    ):
        missing.append("weights")
    if missing:
        raise ArtifactError("tutorial model cache is incomplete (%s): %s" % (profile.id, ", ".join(dict.fromkeys(missing))))


def _worker(host: str, port: int, profile_id: str, registry_path: Path) -> int:
    from .backends import mindspore_worker

    root = data_root()
    registry_path = registry_path.expanduser().resolve()
    if registry_path == DEFAULT_MODEL_CONFIG.resolve() and profile_id == RELEASE_PROFILE_ID:
        verify_artifacts(root)
    else:
        try:
            profile = load_profiles(registry_path).get(profile_id)
        except Exception as exc:
            raise ArtifactError("could not load tutorial profile: %s" % exc) from exc
        if profile.is_conditional:
            raise ArtifactError("conditional profile cannot be started: %s" % profile_id)
        _verify_tutorial_cache(root, profile)
    os.environ["ASCEND_EDGE_CHAT_MODEL_PROFILES"] = str(registry_path)
    os.environ["ASCEND_EDGE_CHAT_MODEL_ROOT"] = str(root)
    os.environ["ASCEND_EDGE_CHAT_PROFILE"] = profile_id
    os.environ["ASCEND_EDGE_CHAT_LAUNCHER_VERIFIED"] = "1"
    os.environ.setdefault("ASCEND_EDGE_CHAT_PROCESS_WATCHDOG", "1")
    os.environ.setdefault("ASCEND_EDGE_CHAT_WORKER_MAIN", "1")
    os.environ.setdefault("ASCEND_EDGE_CHAT_ALLOW_PREVIEW", "1")
    timeout = os.environ.get("ASCEND_EDGE_CHAT_GENERATION_TIMEOUT", "300")
    return mindspore_worker._main(
        [
            "--profile",
            profile_id,
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
