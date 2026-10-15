#!/usr/bin/env python3
"""Run a supervised B4-to-B1 compatibility experiment for two historical OM files.

This launcher deliberately does not replace ``serve_qwen25_acl.py``.  It is
the only supported entry point for the two historical Ascend310B4-built,
2048-token Qwen2.5 artifacts when they are tested on an Ascend310B1/20T
board.  It does not convert an ONNX graph or run ATC.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
from pathlib import Path
import sys
from typing import Optional, Sequence


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from qwen25_kv_acl_runtime import detect_soc_version
from qwen25_legacy_compat_service import (
    ArtifactPaths,
    CompatibilityError,
    SOURCE_SOC,
    TARGET_SOC,
    WorkerConfig,
    profile_for,
    supervise_worker,
    validate_experiment_request,
    verify_pinned_artifacts,
)


LOGGER = logging.getLogger("case9.qwen25_legacy_compat_launcher")
FORBIDDEN_PACKAGES = (
    "torch",
    "torch_npu",
    "torchaudio",
    "transformers",
    "onnxruntime",
    "mindspore",
    "mindtorch",
    "vllm",
    "mindie",
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        required=True,
        choices=("qwen25-full2048-b4-compat", "qwen25-lastlogits2048-b4-compat"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18102)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--tokenizer-config", type=Path, required=True)
    parser.add_argument("--pin-manifest", type=Path, required=True)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--source-soc", required=True)
    parser.add_argument("--board-soc", required=True)
    parser.add_argument("--compatibility-experiment", action="store_true")
    parser.add_argument("--allow-dirty-base", action="store_true")
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--acl-execution-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=300.0)
    return parser.parse_args(argv)


def _check_board_environment(*, allow_dirty_base: bool) -> str:
    if sys.version_info[:2] != (3, 9):
        raise CompatibilityError(f"historical 2048 OM compatibility runner requires Python 3.9, got {sys.version}")
    if os.environ.get("CONDA_DEFAULT_ENV") != "base":
        raise CompatibilityError("historical 2048 OM compatibility runner is restricted to the documented base environment")
    conda_prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if not conda_prefix:
        raise CompatibilityError("CONDA_PREFIX is empty after base activation")
    expected = (Path(conda_prefix) / "bin" / "python").resolve()
    actual = Path(sys.executable).resolve()
    if Path(sys.prefix).resolve() != Path(conda_prefix).resolve() or actual.parent != expected.parent:
        raise CompatibilityError(f"service interpreter is outside CONDA_PREFIX: {actual}")
    present = []
    for package in FORBIDDEN_PACKAGES:
        try:
            if importlib.util.find_spec(package) is not None:
                present.append(package)
        except (ImportError, AttributeError, ValueError) as exc:
            raise CompatibilityError(f"cannot inspect board package {package}: {type(exc).__name__}") from exc
    if present and not allow_dirty_base:
        raise CompatibilityError(
            "dirty shared base contains importable packages; pass --allow-dirty-base to record the experiment explicitly: "
            + ", ".join(present)
        )
    if not allow_dirty_base:
        raise CompatibilityError("--allow-dirty-base is required for this 20T shared-base experiment")
    for package in present:
        LOGGER.warning("dirty-base compatibility experiment; package is present but never imported: %s", package)
    observed_soc = detect_soc_version()
    if not observed_soc:
        raise CompatibilityError("cannot determine board SoC from npu-smi")
    return observed_soc


def validate_args(args: argparse.Namespace, observed_soc: str) -> ArtifactPaths:
    if args.host != "127.0.0.1":
        raise CompatibilityError("historical compatibility service is loopback-only")
    if not 1 <= int(args.port) <= 65535:
        raise CompatibilityError("port must be between 1 and 65535")
    if int(args.device_id) < 0:
        raise CompatibilityError("device-id must be non-negative")
    if min(float(args.startup_timeout_seconds), float(args.acl_execution_timeout_seconds), float(args.request_timeout_seconds)) <= 0:
        raise CompatibilityError("all watchdog timeouts must be positive")
    profile = profile_for(args.profile)
    validate_experiment_request(
        profile,
        compatibility_experiment=bool(args.compatibility_experiment),
        source_soc=str(args.source_soc),
        board_soc=str(args.board_soc),
        observed_board_soc=observed_soc,
        allow_dirty_base=bool(args.allow_dirty_base),
    )
    return ArtifactPaths(
        om=args.om.expanduser(),
        contract=args.contract.expanduser(),
        tokenizer=args.tokenizer.expanduser(),
        tokenizer_config=args.tokenizer_config.expanduser(),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        observed_soc = _check_board_environment(allow_dirty_base=bool(args.allow_dirty_base))
        paths = validate_args(args, observed_soc)
        profile = profile_for(args.profile)
        binding = verify_pinned_artifacts(profile, paths, args.pin_manifest.expanduser())
        # Keep artifact identity verification ahead of any ACL import.  The
        # supervisor must remain ACL-free: the spawned worker owns device
        # initialization and can therefore be terminated safely by its parent.
        # ``find_spec`` verifies that the CANN environment exposes PyACL
        # without loading its shared libraries into the watchdog process.
        if importlib.util.find_spec("acl") is None:
            raise CompatibilityError("PyACL module 'acl' is unavailable; source the CANN environment first")
    except CompatibilityError as exc:
        raise SystemExit(f"historical OM compatibility admission failed: {exc}") from exc
    LOGGER.warning(
        "starting explicit cross-SoC experiment profile=%s source_soc=%s board_soc=%s om_sha256=%s",
        binding.profile.key,
        SOURCE_SOC,
        TARGET_SOC,
        binding.artifacts["om"].sha256,
    )
    config = WorkerConfig(
        profile=profile.key,
        host=args.host,
        port=int(args.port),
        artifact_paths={name: str(path.resolve()) for name, path in paths.items().items()},
        pin_manifest=str(args.pin_manifest.expanduser().resolve()),
        device_id=int(args.device_id),
        source_soc=str(args.source_soc),
        board_soc=str(args.board_soc),
        observed_board_soc=observed_soc,
        compatibility_experiment=bool(args.compatibility_experiment),
        allow_dirty_base=bool(args.allow_dirty_base),
    )
    return supervise_worker(
        config,
        startup_timeout_seconds=float(args.startup_timeout_seconds),
        execution_timeout_seconds=float(args.acl_execution_timeout_seconds),
        request_timeout_seconds=float(args.request_timeout_seconds),
    )


if __name__ == "__main__":
    raise SystemExit(main())
