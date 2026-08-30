#!/usr/bin/env python3
"""Launch the loopback-only Qwen2.5 1024-token StaticCache ACL service."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from qwen25_kv_acl_runtime import (
    HARD_MAX_GENERATION_TOKENS,
    Qwen25AclRuntime,
    RuntimeUnavailable,
    detect_soc_version,
    verify_artifact_locks,
)
from qwen25_kv_acl_service import Qwen25StaticKvService, make_server


LOGGER = logging.getLogger("case9.qwen25_static_kv_launcher")
FORBIDDEN_PACKAGES = ("torch", "torch_npu", "torchaudio", "transformers", "onnxruntime", "mindspore", "mindtorch", "vllm", "mindie")


def _default_root() -> Path:
    return Path(os.environ.get("QWEN25_ROOT", "~/case9-qwen25-kv1024")).expanduser()


def parse_args() -> argparse.Namespace:
    root = _default_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("QWEN25_KV_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QWEN25_KV_PORT", "8084")))
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--om", type=Path, default=None)
    parser.add_argument("--tokenizer", type=Path, default=None)
    parser.add_argument("--tokenizer-config", type=Path, default=None)
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--lock", type=Path, default=None, help="OM provenance lock (required by default)")
    parser.add_argument("--tokenizer-lock", type=Path, default=None, help="tokenizer provenance lock (required by default)")
    parser.add_argument("--soc-version", default=os.environ.get("CASE9_QWEN25_KV_EXPECTED_SOC", ""), help="expected board SoC, e.g. Ascend310B4")
    parser.add_argument(
        "--allow-unlocked-artifacts",
        action="store_true",
        help="explicitly disable artifact lock admission (development only)",
    )
    parser.add_argument(
        "--compatibility-experiment",
        action="store_true",
        help="allow a locked OM from another SoC only for an explicitly marked experiment",
    )
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("QWEN25_KV_MAX_TOKENS", "80")))
    parser.add_argument("--smoke", action="store_true", help="execute one local completion and exit")
    parser.add_argument("--prompt", default="你好", help="prompt used by --smoke")
    return parser.parse_args()


def _check_board_environment() -> str:
    if sys.version_info[:2] != (3, 9):
        raise SystemExit(f"Qwen2.5 StaticCache service requires Python 3.9, got {sys.version}")
    expected_prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if expected_prefix:
        expected = (Path(expected_prefix) / "bin" / "python").resolve()
        actual = Path(sys.executable).resolve()
        if Path(sys.prefix).resolve() != Path(expected_prefix).resolve() or actual.parent != expected.parent:
            raise SystemExit(f"service interpreter is outside CONDA_PREFIX: {actual}, {sys.prefix} != {expected}")
    allow_dirty_base = (
        os.environ.get("CASE9_QWEN25_KV_ALLOW_DIRTY_BASE") == "1"
        and os.environ.get("CONDA_DEFAULT_ENV") == "base"
        and Path(sys.prefix).resolve() == Path(os.environ.get("CONDA_PREFIX", sys.prefix)).resolve()
    )
    for name in FORBIDDEN_PACKAGES:
        try:
            present = importlib.util.find_spec(name) is not None
        except (ImportError, AttributeError, ValueError) as exc:
            raise SystemExit(f"cannot inspect board package {name}: {type(exc).__name__}") from exc
        if present and not allow_dirty_base:
            raise SystemExit(f"forbidden board package is importable: {name}")
        if present and allow_dirty_base:
            print(f"WARNING: dirty-base test override; package remains importable: {name}", file=sys.stderr)
    try:
        import acl  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise SystemExit("PyACL module 'acl' is unavailable; source CANN first") from exc
    actual_soc = detect_soc_version()
    if not actual_soc:
        raise SystemExit("cannot determine board SoC from npu-smi; refusing to load an OM")
    return actual_soc


def main() -> int:
    args = parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("Qwen2.5 StaticCache service is loopback-only")
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    if not 1 <= args.max_tokens <= HARD_MAX_GENERATION_TOKENS:
        raise SystemExit(f"max_tokens must be between 1 and {HARD_MAX_GENERATION_TOKENS}")
    if args.compatibility_experiment and args.allow_unlocked_artifacts:
        raise SystemExit("compatibility experiments require artifact locks")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    actual_soc = _check_board_environment()
    if args.soc_version and args.soc_version != actual_soc:
        raise SystemExit(f"requested SoC {args.soc_version} does not match board {actual_soc}")
    root = args.root.expanduser()
    om = (args.om or root / "artifacts" / "qwen25-static-kv-1024-v2.om").expanduser()
    tokenizer = (args.tokenizer or root / "artifacts" / "tokenizer.json").expanduser()
    tokenizer_config = (args.tokenizer_config or root / "artifacts" / "tokenizer_config.json").expanduser()
    contract = (args.contract or root / "contracts" / "qwen25-static-kv-1024-v2-om-contract.json").expanduser()
    lock = (args.lock or Path(str(om) + ".lock.json")).expanduser()
    tokenizer_lock = (args.tokenizer_lock or Path(str(tokenizer) + ".lock.json")).expanduser()
    require_locks = not args.allow_unlocked_artifacts
    try:
        artifact_status = verify_artifact_locks(
            om,
            tokenizer,
            contract,
            lock_path=lock,
            tokenizer_lock_path=tokenizer_lock,
            expected_soc_version=actual_soc,
            require_locks=require_locks,
            allow_cross_soc=args.compatibility_experiment,
        )
    except RuntimeUnavailable as exc:
        raise SystemExit(f"artifact admission failed: {exc}") from exc
    LOGGER.info(
        "artifact locks verified=%s om=%s tokenizer=%s board_soc=%s compatibility_experiment=%s",
        artifact_status.get("verified"),
        om,
        tokenizer,
        actual_soc,
        artifact_status.get("compatibility_experiment", False),
    )
    runtime = Qwen25AclRuntime(
        om,
        tokenizer,
        contract_path=contract,
        tokenizer_config_path=tokenizer_config,
        lock_path=lock,
        tokenizer_lock_path=tokenizer_lock,
        expected_soc_version=actual_soc,
        require_artifact_locks=require_locks,
        allow_cross_soc=args.compatibility_experiment,
        artifact_status=artifact_status,
        max_tokens=args.max_tokens,
    )
    server = None
    try:
        runtime.start()
        if args.smoke:
            result = runtime.complete([{"role": "user", "content": args.prompt}], args.max_tokens)
            print(json.dumps({"status": "passed", "model": runtime.model_id, "result": result.text, "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "finish_reason": result.finish_reason}, ensure_ascii=False, sort_keys=True))
            return 0
        server = make_server(args.host, args.port, Qwen25StaticKvService(runtime))
        LOGGER.info("listening host=%s port=%d model=%s sequence=%d cache_layout=%s", args.host, args.port, runtime.model_id, runtime.status()["sequence_length"], runtime.status()["cache_layout"])
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        runtime.close()
        LOGGER.info("StaticCache service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
