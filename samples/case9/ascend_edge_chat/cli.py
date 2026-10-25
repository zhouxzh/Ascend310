"""Stable command-line interface for Ascend Edge Chat."""

from __future__ import annotations

import argparse
from contextlib import suppress
import json
import os
from pathlib import Path
import signal
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
    verify_tutorial_profile_artifacts,
)
from .backends.model_profiles import load_profiles, resolve_safe_path
from .artifacts.static_release_store import (
    DEFAULT_HF_MIRROR,
    DEFAULT_STATIC_RELEASE_LOCK,
    StaticReleaseError,
    download_static_release_artifacts,
    import_static_release_artifacts,
    load_static_release_lock,
    static_release_root,
    verify_static_release_artifacts,
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

    voice = commands.add_parser("voice", help="start the loopback board-owned voice agent")
    voice.add_argument("--host", default=None, help="loopback host; defaults to ASCEND_EDGE_CHAT_VOICE_HOST")
    voice.add_argument("--port", default=None, type=int, help="loopback port; defaults to ASCEND_EDGE_CHAT_VOICE_PORT")
    voice.add_argument("--check-config", action="store_true")

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

    acl = commands.add_parser("acl", help="manage the public Ascend310B4 static-KV release")
    acl_commands = acl.add_subparsers(dest="acl_command", required=True)
    acl_verify = acl_commands.add_parser("verify", help="verify the public B4 artifact lock")
    acl_verify.add_argument("--data-dir", type=Path)
    acl_verify.add_argument("--lock", type=Path, default=DEFAULT_STATIC_RELEASE_LOCK)
    acl_download = acl_commands.add_parser("download", help="download only locked public HF artifacts")
    acl_download.add_argument("--data-dir", type=Path)
    acl_download.add_argument("--lock", type=Path, default=DEFAULT_STATIC_RELEASE_LOCK)
    acl_download.add_argument("--mirror", default=os.environ.get("HF_ENDPOINT", DEFAULT_HF_MIRROR))
    acl_import = acl_commands.add_parser("import", help="import a labeled offline artifact bundle")
    acl_import.add_argument("--data-dir", type=Path)
    acl_import.add_argument("--lock", type=Path, default=DEFAULT_STATIC_RELEASE_LOCK)
    acl_import.add_argument("--source", required=True, type=Path)
    acl_import.add_argument("--require-manifest", action="store_true")

    acl_worker = commands.add_parser("acl-worker", help="start the loopback Ascend310B4 ACL/OM service")
    acl_worker.add_argument("--data-dir", type=Path)
    acl_worker.add_argument("--lock", type=Path, default=DEFAULT_STATIC_RELEASE_LOCK)
    acl_worker.add_argument("--host", default="127.0.0.1")
    acl_worker.add_argument("--port", default=8084, type=int)
    acl_worker.add_argument("--max-tokens", default=80, type=int)
    acl_worker.add_argument("--execution-timeout", default=240.0, type=float)
    acl_worker.add_argument("--device-id", default=0, type=int)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "worker":
            return _worker(args.host, args.port, args.profile, args.registry)
        if args.command == "gateway":
            return _gateway(args.host, args.port, args.check_config)
        if args.command == "ui":
            return _ui(args.host, args.port)
        if args.command == "voice":
            return _voice(args.host, args.port, args.check_config)
        if args.command in {"acl", "acl-worker"}:
            selected_data_dir = args.data_dir or os.environ.get("ASCEND_EDGE_CHAT_DATA_DIR")
            if not selected_data_dir:
                raise StaticReleaseError("--data-dir or ASCEND_EDGE_CHAT_DATA_DIR is required")
            acl_root = static_release_root(selected_data_dir)
            if args.command == "acl-worker":
                try:
                    return _acl_worker(
                        acl_root,
                        args.lock,
                        args.host,
                        args.port,
                        args.max_tokens,
                        args.execution_timeout,
                        args.device_id,
                    )
                except RuntimeError as exc:
                    raise StaticReleaseError("static KV worker failed: %s" % exc) from exc
            if args.acl_command == "verify":
                print(json.dumps(verify_static_release_artifacts(acl_root, lock_path=args.lock), ensure_ascii=False, indent=2))
                return 0
            if args.acl_command == "download":
                print(
                    json.dumps(
                        download_static_release_artifacts(
                            acl_root, mirror=args.mirror, lock_path=args.lock
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            print(
                json.dumps(
                    import_static_release_artifacts(
                        acl_root,
                        args.source,
                        lock_path=args.lock,
                        require_manifest=args.require_manifest,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        root = data_root(str(args.data_dir) if getattr(args, "data_dir", None) else None)
        if args.command == "verify":
            print(json.dumps(report(verify_artifacts(root)), ensure_ascii=False, indent=2))
            return 0
        if args.model_command == "download":
            print(json.dumps(report(download_artifacts(root, mirror=args.mirror)), ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(report(import_artifacts(root, args.source)), ensure_ascii=False, indent=2))
        return 0
    except (ArtifactError, StaticReleaseError) as exc:
        parser.error(str(exc))
        return 2


def _static_release_paths(root: Path, lock_path: Path) -> tuple[dict[str, Path], dict[str, str]]:
    verified = verify_static_release_artifacts(root, lock_path=lock_path)
    release = load_static_release_lock(lock_path)
    paths = {item.name: root / item.destination for item in release.artifacts}
    return paths, verified["runtime_locks"]


def _acl_worker(
    root: Path,
    lock_path: Path,
    host: str,
    port: int,
    max_tokens: int,
    execution_timeout: float,
    device_id: int,
) -> int:
    from .static_kv.launch import start_release_service

    paths, runtime_locks = _static_release_paths(root, lock_path)
    server, service = start_release_service(
        host=host,
        port=port,
        om=paths["b4-om"],
        tokenizer=paths["tokenizer"],
        tokenizer_config=paths["tokenizer-config"],
        controller_contract=paths["controller-contract"],
        runtime_contract=paths["b4-descriptor-contract"],
        om_lock=Path(runtime_locks["runtime_om_lock"]),
        tokenizer_lock=Path(runtime_locks["tokenizer_lock"]),
        max_tokens=max_tokens,
        execution_timeout_seconds=execution_timeout,
        device_id=device_id,
    )

    def stop_on_signal(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous_term = signal.signal(signal.SIGTERM, stop_on_signal)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        server.server_close()
        with suppress(Exception):
            service.runtime.close()
    return 0


def _verify_tutorial_cache(root: Path, profile: object) -> None:
    """Verify a teaching profile with the same immutable contract as release.

    A selectable lesson profile is executable code plus model data.  Its local
    cache must therefore satisfy the declared byte and digest lock before a
    worker starts; availability is never inferred from filenames alone.
    """

    try:
        verify_tutorial_profile_artifacts(root, profile)
    except ArtifactError as exc:
        raise ArtifactError("tutorial model cache is unavailable (%s)" % profile.id) from exc


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


def _voice(host: str | None, port: int | None, check_config: bool) -> int:
    """Start the optional loopback board-owned voice agent lazily."""

    from .voice import agent

    arguments: list[str] = []
    if host is not None:
        arguments.extend(["--host", host])
    if port is not None:
        arguments.extend(["--port", str(port)])
    if check_config:
        arguments.append("--check-config")
    return agent.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
