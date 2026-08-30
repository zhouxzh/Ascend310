#!/usr/bin/env python3
"""Run the loopback-only Qwen2.5 static ACL service on an Ascend board."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
from pathlib import Path
import sys

# The launcher lives below the deployment root.  Add that root explicitly so
# imports work whether this file is invoked as ``python scripts/...`` or as a
# module from a service manager.
_DEPLOY_ROOT = Path(__file__).resolve().parents[1]
if str(_DEPLOY_ROOT) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_ROOT))

from qwen25_acl_runtime import Qwen25AclRuntime
from qwen25_acl_service import Qwen25AclService, make_server


LOGGER = logging.getLogger("case9.qwen25.launcher")
_FORBIDDEN_BOARD_PACKAGES = (
    "torch", "torch_npu", "torchaudio", "transformers", "onnxruntime",
    "mindspore", "mindtorch", "vllm", "mindie",
)


def _default_root() -> Path:
    return Path(os.environ.get("QWEN25_ROOT", "~/case9-qwen25")).expanduser()


def parse_args() -> argparse.Namespace:
    root = _default_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("QWEN25_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QWEN25_PORT", "8082")))
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--om", type=Path, default=None)
    parser.add_argument("--tokenizer", type=Path, default=None)
    parser.add_argument("--tokenizer-config", type=Path, default=None)
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("QWEN25_MAX_TOKENS", "8")))
    return parser.parse_args()


def _check_board_environment() -> None:
    for name in _FORBIDDEN_BOARD_PACKAGES:
        try:
            present = importlib.util.find_spec(name) is not None
        except (ImportError, AttributeError, ValueError) as exc:
            raise SystemExit(f"cannot inspect board package {name}: {type(exc).__name__}") from exc
        if present:
            raise SystemExit(f"forbidden board package is importable: {name}")
    try:
        import acl  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise SystemExit("PyACL module 'acl' is unavailable; source CANN first") from exc


def main() -> int:
    args = parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("Qwen2.5 ACL service is loopback-only")
    _check_board_environment()
    root = args.root.expanduser()
    om = (args.om or root / "artifacts" / "qwen25-static-2048.om").expanduser()
    tokenizer = (args.tokenizer or root / "artifacts" / "tokenizer.json").expanduser()
    tokenizer_config = (
        args.tokenizer_config or root / "artifacts" / "tokenizer_config.json"
    ).expanduser()
    contract = (args.contract or root / "contracts" / "qwen25-static-contract.json").expanduser()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = Qwen25AclRuntime(
        om,
        tokenizer,
        contract_path=contract,
        tokenizer_config_path=tokenizer_config,
        max_tokens=args.max_tokens,
    )
    server = None
    try:
        runtime.start()
        server = make_server(args.host, args.port, Qwen25AclService(runtime))
        LOGGER.info(
            "listening host=%s port=%d model=%s execution_mode=%s precision=%s sequence_length=%d",
            args.host,
            args.port,
            runtime.model_id,
            runtime.contract.execution_mode,
            runtime.contract.logits_dtype,
            runtime.contract.static_sequence_length,
        )
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        runtime.close()
        LOGGER.info("service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
