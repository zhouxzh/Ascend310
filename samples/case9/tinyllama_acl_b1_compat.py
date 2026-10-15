#!/usr/bin/env python3
"""Explicit Ascend310B4 TinyLlama OM experiment on an Ascend310B1 board.

This module is deliberately separate from ``scripts/run_tinyllama_acl_service.sh``.
That wrapper is the historical Ascend310B4 route.  A B4 OM on a B1 board is
not an admitted deployment, even when ACL can load and execute it.  This entry
point makes that distinction machine-readable, requires an explicit dirty-base
opt-in, and keeps the existing TinyLlama ACL runtime responsible for all ACL
resource ownership and teardown.

No conversion, ATC invocation, package installation, or system configuration
change is performed here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from tinyllama_acl_contract import ContractError, TinyLlamaContract
from tinyllama_acl_runtime import (
    DEFAULT_MAX_GENERATION_TOKENS,
    MAX_EXECUTION_TIMEOUT_SECONDS,
    NativeTinyLlamaBackend,
    RuntimeUnavailable,
    TinyLlamaAclRuntime,
    _contract_from_descriptor,
)
from tinyllama_acl_service import (
    CompletionRequest,
    MODEL_ID,
    RequestError,
    TinyLlamaAclHttpService,
)


LOGGER = logging.getLogger("case9.tinyllama_b1_compat")
SOURCE_SOC = "Ascend310B4"
TARGET_SOC = "Ascend310B1"
EXPERIMENT_CLASSIFICATION = "cross_soc_compatibility_experiment"
DIRTY_BASE_OPT_IN_ENV = "CASE9_TINYLLAMA_B1_COMPAT_ALLOW_DIRTY_BASE"
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
ADMITTED_TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer.model",
    "special_tokens_map.json",
    "tokenizer_config.json",
)


class CompatibilityError(RuntimeUnavailable):
    """A safety/admission error specific to the B4-to-B1 experiment."""


@dataclass(frozen=True)
class ArtifactBinding:
    """Immutable artifact fields obtained from the checked-in manifest."""

    revision: str
    om_bytes: int
    om_sha256: str
    tokenizer_zip_bytes: int
    tokenizer_zip_sha256: str
    tokenizer_files: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class CompatibilityMetadata:
    source_soc: str
    board_soc: str
    dirty_packages: Tuple[str, ...]
    om_sha256: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "compatibility_experiment": True,
            "experiment_classification": EXPERIMENT_CLASSIFICATION,
            "source_soc": self.source_soc,
            "board_soc": self.board_soc,
            "admission": "not_admitted",
            "dirty_base_opt_in": True,
            "dirty_packages_detected": list(self.dirty_packages),
        }
        if self.om_sha256 is not None:
            result["om_sha256"] = self.om_sha256
        return result


class B1CompatibilityTinyLlamaAclRuntime(TinyLlamaAclRuntime):
    """Existing TinyLlama runtime with unambiguous cross-SoC health metadata."""

    def __init__(self, *args: Any, compatibility: CompatibilityMetadata, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._compatibility = compatibility

    def status(self) -> Dict[str, Any]:
        status = super().status()
        status.update(self._compatibility.as_dict())
        return status


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise CompatibilityError(f"cannot hash artifact: {path}") from exc
    return digest.hexdigest()


def _load_manifest_binding(manifest_path: Path) -> ArtifactBinding:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = document["artifacts"]
        om = artifacts["tinyllama_acl_om"]
        tokenizer = artifacts["tinyllama_tokenizer_zip"]
        revision = str(om["revision"])
        tokenizer_revision = str(tokenizer["revision"])
        binding = ArtifactBinding(
            revision=revision,
            om_bytes=int(om["expected_bytes"]),
            om_sha256=str(om["sha256"]).lower(),
            tokenizer_zip_bytes=int(tokenizer["expected_bytes"]),
            tokenizer_zip_sha256=str(tokenizer["sha256"]).lower(),
            tokenizer_files=tokenizer["extracted_files"],
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise CompatibilityError("TinyLlama manifest lacks a valid immutable artifact binding") from exc
    if revision != tokenizer_revision or not revision:
        raise CompatibilityError("TinyLlama OM and tokenizer revisions differ")
    if len(binding.om_sha256) != 64 or len(binding.tokenizer_zip_sha256) != 64:
        raise CompatibilityError("TinyLlama manifest SHA-256 binding is invalid")
    if set(binding.tokenizer_files) != set(ADMITTED_TOKENIZER_FILES):
        raise CompatibilityError("TinyLlama manifest tokenizer file set is not admitted")
    return binding


def _verify_file(path: Path, expected_bytes: int, expected_sha256: str, label: str) -> None:
    if not path.is_file():
        raise CompatibilityError(f"{label} is missing: {path}")
    try:
        actual_bytes = path.stat().st_size
    except OSError as exc:
        raise CompatibilityError(f"cannot stat {label}: {path}") from exc
    if actual_bytes != int(expected_bytes):
        raise CompatibilityError(f"{label} byte count differs: {actual_bytes} != {expected_bytes}")
    actual_sha256 = _sha256(path)
    if actual_sha256.lower() != str(expected_sha256).lower():
        raise CompatibilityError(f"{label} SHA-256 differs from its immutable binding")


def verify_artifacts(
    *,
    manifest_path: Path,
    om_path: Path,
    tokenizer_zip_path: Path,
    tokenizer_path: Path,
    tokenizer_config_path: Path,
    contract_path: Optional[Path],
    require_contract: bool,
) -> ArtifactBinding:
    """Verify OM, archive, extracted tokenizer set, and optional contract.

    The verification intentionally happens before importing ACL.  It prevents a
    corrupt or mismatched cross-SoC artifact from reaching device creation.
    """

    binding = _load_manifest_binding(manifest_path)
    _verify_file(om_path, binding.om_bytes, binding.om_sha256, "TinyLlama OM")
    _verify_file(
        tokenizer_zip_path,
        binding.tokenizer_zip_bytes,
        binding.tokenizer_zip_sha256,
        "TinyLlama tokenizer archive",
    )
    if tokenizer_path.name != "tokenizer.json":
        raise CompatibilityError("TinyLlama runtime must load tokenizer.json")
    if tokenizer_config_path.resolve() != (tokenizer_path.parent / "tokenizer_config.json").resolve():
        raise CompatibilityError("tokenizer config must be the manifest-bound tokenizer_config.json")
    for name in ADMITTED_TOKENIZER_FILES:
        item = binding.tokenizer_files[name]
        try:
            expected_bytes = int(item["expected_bytes"])
            expected_sha256 = str(item["sha256"]).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise CompatibilityError(f"TinyLlama manifest tokenizer binding is invalid: {name}") from exc
        _verify_file(tokenizer_path.parent / name, expected_bytes, expected_sha256, f"tokenizer file {name}")
    if require_contract:
        if contract_path is None or not contract_path.is_file():
            raise CompatibilityError("a bound TinyLlama descriptor contract is required")
        try:
            contract = TinyLlamaContract.load(contract_path)
            contract.validate_static_expectations(strict_dimensions=True)
        except ContractError as exc:
            raise CompatibilityError(f"TinyLlama contract is invalid: {exc}") from exc
        if (
            contract.source_revision != binding.revision
            or contract.source_bytes != binding.om_bytes
            or (contract.source_sha256 or "").lower() != binding.om_sha256
        ):
            raise CompatibilityError("TinyLlama contract is not bound to the immutable manifest artifact")
    return binding


def _detect_soc(npu_smi: str) -> str:
    try:
        completed = subprocess.run(
            [npu_smi, "info"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CompatibilityError("cannot run npu-smi info to identify board SoC") from exc
    output = completed.stdout or ""
    if completed.returncode != 0:
        raise CompatibilityError("npu-smi info failed while identifying board SoC")
    if "310B1" in output.upper():
        return TARGET_SOC
    if "310B4" in output.upper():
        return SOURCE_SOC
    raise CompatibilityError("npu-smi output does not identify an Ascend310B1 board")


def _dirty_packages() -> Tuple[str, ...]:
    present = []
    for package in FORBIDDEN_PACKAGES:
        try:
            if importlib.util.find_spec(package) is not None:
                present.append(package)
        except (ImportError, AttributeError, ValueError) as exc:
            raise CompatibilityError(f"cannot inspect installed package {package}") from exc
    return tuple(present)


def _require_experiment_opt_in(enabled: bool) -> None:
    if not enabled:
        raise CompatibilityError("pass --compatibility-experiment for the B4-to-B1 TinyLlama route")
    if os.environ.get(DIRTY_BASE_OPT_IN_ENV) != "1":
        raise CompatibilityError(f"set {DIRTY_BASE_OPT_IN_ENV}=1 to acknowledge the dirty-base experiment")
    if os.environ.get("CONDA_DEFAULT_ENV") != "base":
        raise CompatibilityError("B4-to-B1 TinyLlama compatibility testing is restricted to the documented base environment")
    prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if not prefix or Path(prefix).resolve() != Path(sys.prefix).resolve():
        raise CompatibilityError("active Python is outside the declared conda base environment")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=True, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def inspect_and_bind_contract(
    om_path: Path,
    binding: ArtifactBinding,
    contract_path: Path,
    *,
    device_id: int = 0,
) -> Dict[str, Any]:
    """Read the live descriptor and atomically create a manifest-bound contract.

    Existing contracts are never overwritten: a second run validates its
    descriptor through the runtime instead.  This keeps historical B4 evidence
    separate from a fresh B1 experiment.
    """

    if contract_path.exists():
        raise CompatibilityError(f"refusing to overwrite existing contract: {contract_path}")
    backend = NativeTinyLlamaBackend(device_id=int(device_id))
    try:
        descriptor = backend.open(om_path)
        contract = _contract_from_descriptor(descriptor)
        contract = replace(
            contract,
            source_revision=binding.revision,
            source_bytes=binding.om_bytes,
            source_sha256=binding.om_sha256,
        )
        contract.validate_static_expectations(strict_dimensions=True)
        contract.validate_descriptor(descriptor.inputs, descriptor.outputs)
        payload = contract.as_dict()
        payload["descriptor"] = {
            "inputs": [item.__dict__ for item in descriptor.inputs],
            "outputs": [item.__dict__ for item in descriptor.outputs],
        }
        _atomic_json(contract_path, payload)
        return payload
    finally:
        backend.close()


def _compatibility_status(metadata: CompatibilityMetadata, command: str) -> Dict[str, Any]:
    result = metadata.as_dict()
    result["command"] = command
    result["model"] = MODEL_ID
    return result


def _runtime(
    args: argparse.Namespace,
    metadata: CompatibilityMetadata,
) -> B1CompatibilityTinyLlamaAclRuntime:
    return B1CompatibilityTinyLlamaAclRuntime(
        args.om,
        args.tokenizer,
        contract_path=args.contract,
        tokenizer_config_path=args.tokenizer_config,
        tokenizer_manifest_path=args.manifest,
        device_id=args.device_id,
        max_tokens=args.max_tokens,
        execution_timeout_seconds=args.execution_timeout,
        verify_artifact=True,
        compatibility=metadata,
    )


def _smoke(args: argparse.Namespace, metadata: CompatibilityMetadata) -> int:
    runtime = _runtime(args, metadata)
    service = TinyLlamaAclHttpService(runtime, auto_start=False)
    try:
        runtime.start()
        request = CompletionRequest(
            MODEL_ID,
            [{"role": "user", "content": args.prompt}],
            False,
            args.max_tokens,
        )
        result = service.complete(request)
        payload = _compatibility_status(metadata, "smoke")
        payload["result"] = result
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        # TinyLlamaAclRuntime owns native ACL handles and releases them in the
        # established dataset -> buffer -> model -> context/finalize order.
        service.close()


def _serve(args: argparse.Namespace, metadata: CompatibilityMetadata) -> int:
    if args.host != "127.0.0.1":
        raise CompatibilityError("TinyLlama B4-to-B1 compatibility service must remain loopback-only")
    runtime = _runtime(args, metadata)
    service = TinyLlamaAclHttpService(runtime, auto_start=False)
    server = None
    try:
        runtime.start()
        server = service.make_server(args.host, args.port)
        LOGGER.info(
            "starting TinyLlama B4-to-B1 compatibility service host=%s port=%d source_soc=%s board_soc=%s",
            args.host,
            args.port,
            SOURCE_SOC,
            TARGET_SOC,
        )
        server.serve_forever(poll_interval=0.5)
        return 0
    finally:
        if server is not None:
            server.server_close()
        service.close()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "smoke", "serve"))
    parser.add_argument("--compatibility-experiment", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--tokenizer-zip", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--tokenizer-config", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--npu-smi", default="npu-smi")
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--execution-timeout", type=float, default=MAX_EXECUTION_TIMEOUT_SECONDS)
    parser.add_argument("--prompt", default="Hello")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18103)
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.device_id < 0:
        raise CompatibilityError("device-id must be non-negative")
    if not 1 <= args.max_tokens <= DEFAULT_MAX_GENERATION_TOKENS:
        raise CompatibilityError(
            f"max-tokens must be between 1 and {DEFAULT_MAX_GENERATION_TOKENS} for this historical OM"
        )
    if not 0 < args.execution_timeout <= MAX_EXECUTION_TIMEOUT_SECONDS:
        raise CompatibilityError(
            f"execution-timeout must be between 0 and {MAX_EXECUTION_TIMEOUT_SECONDS} seconds"
        )
    if not 1 <= args.port <= 65535:
        raise CompatibilityError("port must be between 1 and 65535")
    if args.command == "serve" and args.host != "127.0.0.1":
        raise CompatibilityError("TinyLlama B4-to-B1 compatibility service must remain loopback-only")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        _validate_args(args)
        _require_experiment_opt_in(args.compatibility_experiment)
        board_soc = _detect_soc(args.npu_smi)
        if board_soc != TARGET_SOC:
            raise CompatibilityError(f"B4-to-B1 compatibility route requires {TARGET_SOC}, observed {board_soc}")
        dirty_packages = _dirty_packages()
        metadata = CompatibilityMetadata(SOURCE_SOC, board_soc, dirty_packages)
        binding = verify_artifacts(
            manifest_path=args.manifest,
            om_path=args.om,
            tokenizer_zip_path=args.tokenizer_zip,
            tokenizer_path=args.tokenizer,
            tokenizer_config_path=args.tokenizer_config,
            contract_path=args.contract,
            require_contract=args.command != "inspect",
        )
        metadata = replace(metadata, om_sha256=binding.om_sha256)
        if args.command == "inspect":
            payload = inspect_and_bind_contract(
                args.om,
                binding,
                args.contract,
                device_id=args.device_id,
            )
            payload["compatibility"] = _compatibility_status(metadata, "inspect")
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "smoke":
            return _smoke(args, metadata)
        return _serve(args, metadata)
    except (CompatibilityError, ContractError, RequestError, RuntimeUnavailable) as exc:
        LOGGER.error("TinyLlama B4-to-B1 compatibility experiment refused: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ADMITTED_TOKENIZER_FILES",
    "ArtifactBinding",
    "B1CompatibilityTinyLlamaAclRuntime",
    "CompatibilityError",
    "CompatibilityMetadata",
    "DIRTY_BASE_OPT_IN_ENV",
    "EXPERIMENT_CLASSIFICATION",
    "FORBIDDEN_PACKAGES",
    "SOURCE_SOC",
    "TARGET_SOC",
    "inspect_and_bind_contract",
    "main",
    "verify_artifacts",
]
