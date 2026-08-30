"""Torch-free, descriptor-driven StaticCache runtime for Qwen2.5 ACL/OM.

This module is separate from :mod:`qwen25_acl_runtime`, which serves the
verified full-context candidate.  The StaticCache graph accepts one token and
48 split key/value tensors.  Tensor order, shape and byte size always come
from the inspected contract; no cache layout is guessed from a filename.

The native backend keeps one ACL dataset and device buffer per request.  When
the binding exposes device-to-device copies, token cache outputs are written
directly into resident cache input buffers.  A host-copy path remains for
test doubles and older ACL bindings.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import Any, Dict, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

from qwen25_kv_acl_contract import ContractError, Qwen25Contract, TensorContract


LOGGER = logging.getLogger("case9.qwen25_static_kv_acl")
MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
VOCABULARY_SIZE = 151936
DEFAULT_MAX_GENERATION_TOKENS = 80
HARD_MAX_GENERATION_TOKENS = 80
# The 310B4 graph needs roughly one serialized NPU execution per generated
# token.  Stop at the first complete sentence once a modest minimum has been
# reached, so the default 80-token allowance is a hard safety ceiling rather
# than a reason to generate several slow, repetitive sentences.
SENTENCE_STOP_MIN_TOKENS = 16
# A token step on the 310B4 static graph is deliberately serialized.  The
# deadline must cover the fixed prompt prefill plus the admitted generation
# budget; the service still rejects a request once this deadline is exceeded.
MAX_EXECUTION_TIMEOUT_SECONDS = 240.0
# CANN's aclrtMemcpyKind C ABI defines DEVICE_TO_DEVICE as the fourth enum
# value (0-based). Some CANN 8.0 Python bindings omit the symbolic constant
# even though ``acl.rt.memcpy`` accepts the ABI integer. The value is used
# only after the runtime has loaded ACL and any failure stays on the verified
# host-cache path.
_ACL_MEMCPY_DEVICE_TO_DEVICE = 3
# Compatibility names used by earlier board launchers. They intentionally
# retain the current 80-token admission limit.
QWEN25_DEFAULT_MAX_GENERATION_TOKENS = DEFAULT_MAX_GENERATION_TOKENS
QWEN25_HARD_MAX_GENERATION_TOKENS = HARD_MAX_GENERATION_TOKENS
QWEN25_MAX_EXECUTION_TIMEOUT_SECONDS = MAX_EXECUTION_TIMEOUT_SECONDS


def _sha256_file(path: Union[str, Path]) -> str:
    """Hash a model artifact without loading it into the Python heap."""

    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_soc_version() -> Optional[str]:
    """Return the SoC reported by ``npu-smi`` when available.

    The launcher uses this as an admission check.  An unset value is kept
    distinct from a guessed value so a missing board diagnostic fails closed.
    """

    for variable in ("CASE9_ACTUAL_SOC_VERSION", "ASCEND_SOC_VERSION"):
        value = os.environ.get(variable, "").strip()
        if value:
            match = re.fullmatch(r"(?:Ascend\s*)?310B([14])", value, re.IGNORECASE)
            return f"Ascend310B{match.group(1)}" if match else value
    try:
        result = subprocess.run(
            ["npu-smi", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stdout = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else (result.stdout or "")
    stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
    # ``npu-smi 25.x`` commonly prints the chip column as bare ``310B4``
    # (without the ``Ascend`` product prefix), while older releases include
    # ``Ascend310B4``.  Accept both spellings but keep the admitted B1/B4
    # suffix strict so a different device cannot pass by accident.
    match = re.search(r"(?:Ascend\s*)?310B([14])\b", stdout + "\n" + stderr, re.IGNORECASE)
    return f"Ascend310B{match.group(1)}" if match else None


def _lock_value(raw: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def _verify_locked_file(path: Path, raw: Mapping[str, Any], label: str) -> Dict[str, Any]:
    # Accept the small lock shapes emitted by both the board provisioning
    # scripts and the reproducibility bundle (some wrap file metadata under
    # ``artifact`` or ``file``).
    if not any(name in raw for name in ("bytes", "size", "expected_bytes")):
        for wrapper in ("artifact", "file", "metadata"):
            candidate = raw.get(wrapper)
            if isinstance(candidate, Mapping):
                raw = candidate
                break
    if not path.is_file():
        raise RuntimeUnavailable(f"{label} does not exist: {path}")
    expected_bytes = _lock_value(raw, "bytes", "size", "expected_bytes")
    expected_sha = _lock_value(raw, "sha256", "sha256sum", "digest")
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0:
        raise RuntimeUnavailable(f"{label} lock has no positive byte size")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise RuntimeUnavailable(f"{label} lock has no valid SHA-256")
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise RuntimeUnavailable(f"{label} byte size mismatch: {actual_bytes} != {expected_bytes}")
    actual_sha = _sha256_file(path)
    if actual_sha.lower() != expected_sha.lower():
        raise RuntimeUnavailable(f"{label} SHA-256 mismatch")
    return {"path": str(path), "bytes": actual_bytes, "sha256": actual_sha}


def verify_artifact_locks(
    om_path: Union[str, Path],
    tokenizer_path: Union[str, Path],
    contract_path: Optional[Union[str, Path]],
    *,
    lock_path: Optional[Union[str, Path]] = None,
    tokenizer_lock_path: Optional[Union[str, Path]] = None,
    expected_soc_version: Optional[str] = None,
    require_locks: bool = True,
    allow_cross_soc: bool = False,
) -> Dict[str, Any]:
    """Verify OM, tokenizer and contract provenance before ACL initialization.

    A lock is a small JSON object containing ``bytes`` and ``sha256``.  The OM
    lock additionally contains ``soc_version``.  ``contract_path`` always
    means the inspected ACL/OM descriptor contract used at runtime:

    * Legacy B4 locks use ``contract_sha256`` for that runtime contract.
    * Newer locks may retain ``contract_sha256`` (or
      ``controller_contract_sha256``) for the controller/export contract and
      must carry ``runtime_contract_sha256`` for the runtime descriptor.

    This distinction prevents a valid ONNX/export contract from being passed
    off as proof that the ATC-produced descriptor contract is the one the ACL
    service will actually bind.  A tokenizer lock may be a sibling
    ``tokenizer.json.lock.json`` or an embedded
    ``tokenizer``/``tokenizer_artifact`` object in the OM lock.
    """

    om = Path(om_path).expanduser()
    tokenizer = Path(tokenizer_path).expanduser()
    contract = Path(contract_path).expanduser() if contract_path is not None else None
    om_lock = Path(lock_path).expanduser() if lock_path is not None else Path(str(om) + ".lock.json")
    tokenizer_lock = (
        Path(tokenizer_lock_path).expanduser()
        if tokenizer_lock_path is not None
        else Path(str(tokenizer) + ".lock.json")
    )
    status: Dict[str, Any] = {
        "verified": False,
        "required": bool(require_locks),
        "lock_path": str(om_lock),
        "tokenizer_lock_path": str(tokenizer_lock),
    }
    if not om_lock.is_file():
        if require_locks:
            raise RuntimeUnavailable(f"OM lock file is required: {om_lock}")
        if om.is_file():
            status["om"] = {"path": str(om), "bytes": om.stat().st_size, "sha256": _sha256_file(om)}
        if contract is not None and contract.is_file():
            status["contract"] = {"path": str(contract), "bytes": contract.stat().st_size, "sha256": _sha256_file(contract)}
        if tokenizer.is_file():
            status["tokenizer"] = {"path": str(tokenizer), "bytes": tokenizer.stat().st_size, "sha256": _sha256_file(tokenizer)}
        status["warning"] = "artifact lock files were not present; provenance is unverified"
        return status
    try:
        raw = json.loads(om_lock.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeUnavailable(f"OM lock is not valid JSON: {om_lock}") from exc
    if not isinstance(raw, Mapping):
        raise RuntimeUnavailable("OM lock root must be an object")
    status["om"] = _verify_locked_file(om, raw, "OM")
    lock_model = _lock_value(raw, "model_id", "model")
    if isinstance(lock_model, Mapping):
        lock_model = lock_model.get("model_id")
    if lock_model is not None and str(lock_model) != MODEL_ID:
        raise RuntimeUnavailable(f"OM lock model_id is not {MODEL_ID}")
    status["model_id"] = str(lock_model) if lock_model is not None else MODEL_ID
    lock_soc = _lock_value(raw, "soc_version", "target_soc", "soc")
    if require_locks and not isinstance(lock_soc, str):
        raise RuntimeUnavailable("OM lock is missing soc_version")
    if expected_soc_version and lock_soc and str(lock_soc) != str(expected_soc_version):
        if not allow_cross_soc:
            raise RuntimeUnavailable(f"OM target SoC mismatch: lock={lock_soc} board={expected_soc_version}")
        # The lock remains an assertion about the OM's original target.  Do
        # not rewrite it to make a cross-SoC load look native; this explicit
        # marker exists solely for the separately reported compatibility gate.
        status["compatibility_experiment"] = True
        status["board_soc_version"] = str(expected_soc_version)
    else:
        status["compatibility_experiment"] = False
        if expected_soc_version:
            status["board_soc_version"] = str(expected_soc_version)
    status["soc_version"] = lock_soc
    if require_locks and contract is None:
        raise RuntimeUnavailable("contract lock verification requires a contract path")
    if contract is not None:
        if not contract.is_file():
            raise RuntimeUnavailable(f"contract does not exist: {contract}")
        # ``runtime_contract_sha256`` was added after the original B4 lock.
        # When it exists, the old ``contract_sha256`` can deliberately refer
        # to the controller/export contract, so never compare it with the
        # runtime descriptor.  Without the new field, retain the legacy B4
        # behavior where ``contract_sha256`` is the runtime descriptor hash.
        runtime_contract_sha = _lock_value(raw, "runtime_contract_sha256", "runtime_contract_sha256sum")
        legacy_contract_sha = _lock_value(raw, "contract_sha256", "contract_sha256sum")
        controller_contract_sha = _lock_value(
            raw,
            "controller_contract_sha256",
            "export_contract_sha256",
        )
        for field_name, digest in (
            ("runtime_contract_sha256", runtime_contract_sha),
            ("contract_sha256", legacy_contract_sha),
            ("controller_contract_sha256", controller_contract_sha),
        ):
            if digest is not None and (
                not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
            ):
                raise RuntimeUnavailable(f"OM lock {field_name} is not a valid SHA-256")
        if (
            isinstance(legacy_contract_sha, str)
            and isinstance(controller_contract_sha, str)
            and legacy_contract_sha.lower() != controller_contract_sha.lower()
        ):
            raise RuntimeUnavailable("OM lock controller contract SHA-256 fields disagree")
        contract_sha = runtime_contract_sha if runtime_contract_sha is not None else legacy_contract_sha
        if require_locks and not isinstance(contract_sha, str):
            raise RuntimeUnavailable("OM lock is missing runtime_contract_sha256 (or legacy contract_sha256)")
        actual_contract_sha = _sha256_file(contract)
        if contract_sha and actual_contract_sha.lower() != str(contract_sha).lower():
            raise RuntimeUnavailable("runtime descriptor contract SHA-256 mismatch")
        status["contract"] = {
            "path": str(contract),
            "bytes": contract.stat().st_size,
            "sha256": actual_contract_sha,
            "lock_field": "runtime_contract_sha256" if runtime_contract_sha is not None else "contract_sha256",
        }
        status["runtime_contract_sha256"] = actual_contract_sha
        # Preserve the export/controller proof in health output without
        # confusing it with the descriptor that ACL binds at runtime.
        if runtime_contract_sha is not None:
            status["controller_contract_sha256"] = (
                str(controller_contract_sha or legacy_contract_sha)
                if controller_contract_sha is not None or legacy_contract_sha is not None
                else None
            )
    embedded_tokenizer = _lock_value(raw, "tokenizer", "tokenizer_artifact", "tokenizer_lock")
    if tokenizer_lock.is_file():
        try:
            tokenizer_raw = json.loads(tokenizer_lock.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeUnavailable(f"tokenizer lock is not valid JSON: {tokenizer_lock}") from exc
        if not isinstance(tokenizer_raw, Mapping):
            raise RuntimeUnavailable("tokenizer lock root must be an object")
        tokenizer_meta = _verify_locked_file(tokenizer, tokenizer_raw, "tokenizer")
    elif isinstance(embedded_tokenizer, Mapping):
        tokenizer_meta = _verify_locked_file(tokenizer, embedded_tokenizer, "tokenizer")
    elif require_locks:
        raise RuntimeUnavailable(f"tokenizer lock file is required: {tokenizer_lock}")
    else:
        tokenizer_meta = {"path": str(tokenizer), "bytes": tokenizer.stat().st_size, "sha256": _sha256_file(tokenizer)}
    status["tokenizer"] = tokenizer_meta
    status["verified"] = True
    return status


class Qwen25RuntimeError(RuntimeError):
    """Base class for sanitized runtime failures."""


class RuntimeUnavailable(Qwen25RuntimeError):
    pass


class RuntimeBusy(Qwen25RuntimeError):
    pass


class RuntimeRequestError(Qwen25RuntimeError):
    pass


class RuntimeExecutionTimeout(Qwen25RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeTensorDescriptor:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    byte_size: Optional[int] = None


TensorDescriptor = RuntimeTensorDescriptor


@dataclass(frozen=True)
class RuntimeDescriptor:
    inputs: Tuple[RuntimeTensorDescriptor, ...]
    outputs: Tuple[RuntimeTensorDescriptor, ...]


@dataclass(frozen=True)
class GenerationResult:
    """An accumulated generation snapshot."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str = "stop"
    done: bool = True

    def __iter__(self):
        """Allow legacy callers to unpack the four public fields."""
        yield self.text
        yield self.prompt_tokens
        yield self.completion_tokens
        yield self.finish_reason


@dataclass(frozen=True)
class BackendStep:
    outputs: Any
    cache_device_updated: bool = False


class Qwen25Backend(Protocol):
    def open(self, model_path: Path) -> RuntimeDescriptor:
        ...

    def run(self, inputs: Mapping[str, Any]) -> Any:
        ...

    def close(self) -> None:
        ...


@dataclass
class _CacheState:
    values: Dict[str, Any]
    device_resident: bool = False


@dataclass
class _NativeRequest:
    input_dataset: Any
    output_dataset: Any
    input_records: Dict[str, Tuple[int, Any]]
    output_records: Dict[str, Tuple[int, Any]]
    output_arrays: Dict[str, Any]
    cache_input_pointers: Dict[str, int]
    allocations: List[Tuple[int, Any]]
    device_cache_valid: bool = True
    # Once a token cache has been committed with device-to-device copies, the
    # host mirror is intentionally no longer authoritative.  A later D2D
    # failure must fail closed instead of falling back to a stale cache.
    device_cache_used: bool = False


class _RequestWatchdog:
    """Mark a blocked ACL request unhealthy after its hard deadline.

    PyACL's synchronous ``execute`` cannot be interrupted safely from a
    Python worker thread.  The watchdog therefore cancels the request,
    invokes an optional backend abort hook, and poisons the backend.  This is
    deliberately fail-closed: once the deadline fires, health becomes false
    and the process must be restarted instead of accepting a possibly stale
    cache.  The thread is daemonized so it cannot keep a clean shutdown alive.
    """

    def __init__(self, runtime: "Qwen25AclRuntime", deadline: float) -> None:
        self.runtime = runtime
        self.deadline = float(deadline)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="case9-qwen25-acl-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.2)

    def _run(self) -> None:
        remaining = max(0.0, self.deadline - time.monotonic())
        if self._stop.wait(remaining):
            return
        runtime = self.runtime
        runtime._watchdog_triggered = True
        runtime._runtime_error = "ACL request exceeded the hard execution deadline"
        runtime._cancel.set()
        backend = runtime._backend
        setattr(backend, "poisoned", True)
        abort = getattr(backend, "abort", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                LOGGER.exception("ACL backend abort hook failed after watchdog deadline")
        LOGGER.error("ACL request watchdog expired; service is unhealthy and requires restart")


class Qwen25AclRuntime:
    """One StaticCache model, one request, and one ACL execution at a time."""

    def __init__(
        self,
        om_path: Union[str, Path],
        tokenizer_path: Union[str, Path],
        *,
        contract_path: Optional[Union[str, Path]] = None,
        contract: Optional[Any] = None,
        backend: Optional[Qwen25Backend] = None,
        tokenizer: Optional[Any] = None,
        tokenizer_config_path: Optional[Union[str, Path]] = None,
        device_id: int = 0,
        max_tokens: int = DEFAULT_MAX_GENERATION_TOKENS,
        execution_timeout_seconds: float = MAX_EXECUTION_TIMEOUT_SECONDS,
        lock_path: Optional[Union[str, Path]] = None,
        tokenizer_lock_path: Optional[Union[str, Path]] = None,
        expected_soc_version: Optional[str] = None,
        require_artifact_locks: bool = True,
        allow_cross_soc: bool = False,
        artifact_status: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.om_path = Path(om_path).expanduser()
        self.tokenizer_path = Path(tokenizer_path).expanduser()
        if not self.om_path.is_file():
            raise RuntimeUnavailable(f"OM file does not exist: {self.om_path}")
        if not self.tokenizer_path.is_file():
            raise RuntimeUnavailable(f"tokenizer file does not exist: {self.tokenizer_path}")
        if contract_path is not None and contract is not None:
            raise RuntimeUnavailable("provide contract or contract_path, not both")
        if contract is None and contract_path is not None:
            try:
                contract = Qwen25Contract.load(contract_path)
            except Exception as exc:
                raise RuntimeUnavailable(str(exc)) from exc
        if contract is not None:
            try:
                contract.validate_static_expectations()
            except Exception as exc:
                raise RuntimeUnavailable(str(exc)) from exc
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= HARD_MAX_GENERATION_TOKENS:
            raise RuntimeUnavailable(f"max_tokens must be between 1 and {HARD_MAX_GENERATION_TOKENS}")
        if not isinstance(execution_timeout_seconds, (int, float)) or execution_timeout_seconds <= 0:
            raise RuntimeUnavailable("execution_timeout_seconds must be positive")
        self.contract = contract
        self.device_id = int(device_id)
        self.max_tokens = int(max_tokens)
        self.execution_timeout_seconds = min(float(execution_timeout_seconds), MAX_EXECUTION_TIMEOUT_SECONDS)
        self._backend: Any = backend or NativeQwen25Backend(device_id=self.device_id)
        self._tokenizer = tokenizer
        self.tokenizer_config_path = Path(tokenizer_config_path).expanduser() if tokenizer_config_path else None
        self._contract_path = Path(contract_path).expanduser() if contract_path is not None else None
        self._lock_path = Path(lock_path).expanduser() if lock_path is not None else Path(str(self.om_path) + ".lock.json")
        self._tokenizer_lock_path = (
            Path(tokenizer_lock_path).expanduser()
            if tokenizer_lock_path is not None
            else Path(str(self.tokenizer_path) + ".lock.json")
        )
        self.expected_soc_version = expected_soc_version.strip() if isinstance(expected_soc_version, str) and expected_soc_version.strip() else None
        self.require_artifact_locks = bool(require_artifact_locks)
        self.allow_cross_soc = bool(allow_cross_soc)
        self._descriptor: Optional[RuntimeDescriptor] = None
        self._started = False
        self._closed = False
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._last_stop_reason = "stop"
        self._active_request: Any = None
        self._watchdog_triggered = False
        self._runtime_error: Optional[str] = None
        self._artifact_status: Dict[str, Any] = dict(artifact_status or {"verified": False, "required": False})
        self._watchdog: Optional[_RequestWatchdog] = None

    @property
    def model_id(self) -> str:
        value = getattr(self.contract, "model_id", None)
        return str(value) if value == MODEL_ID else MODEL_ID

    @property
    def descriptor(self) -> Optional[RuntimeDescriptor]:
        return self._descriptor

    @property
    def started(self) -> bool:
        return (
            self._started
            and not self._closed
            and not self._watchdog_triggered
            and not bool(getattr(self._backend, "poisoned", False))
            and not bool(getattr(self._backend, "cleanup_failed", False))
        )

    @property
    def last_stop_reason(self) -> str:
        return self._last_stop_reason

    def start(self) -> None:
        if self.started:
            return
        if self._closed:
            raise RuntimeUnavailable("Qwen2.5 StaticCache runtime has been closed")
        self._cancel.clear()
        self._watchdog_triggered = False
        self._runtime_error = None
        try:
            # Hash and lock checks happen before touching ACL.  This avoids
            # loading a wrong-SoC or partially transferred OM into the NPU.
            if (self.require_artifact_locks or self._lock_path.is_file()) and not self._artifact_status.get("verified"):
                self._artifact_status = verify_artifact_locks(
                    self.om_path,
                    self.tokenizer_path,
                    self._contract_path,
                    lock_path=self._lock_path,
                    tokenizer_lock_path=self._tokenizer_lock_path,
                    expected_soc_version=self.expected_soc_version,
                    require_locks=self.require_artifact_locks,
                    allow_cross_soc=self.allow_cross_soc,
                )
            descriptor = self._backend.open(self.om_path)
            if not isinstance(descriptor, RuntimeDescriptor):
                raise RuntimeUnavailable("ACL backend returned no descriptor")
            if self.contract is None:
                self.contract = Qwen25Contract.from_descriptor(descriptor.inputs, descriptor.outputs)
            self.contract.validate_static_expectations()
            if getattr(self.contract, "model_id", MODEL_ID) != MODEL_ID:
                raise RuntimeUnavailable("OM contract is not the 1024-token StaticCache model")
            self.contract.validate_descriptor(descriptor.inputs, descriptor.outputs)
            configure = getattr(self._backend, "configure_contract", None)
            if callable(configure):
                configure(self.contract)
            if self._tokenizer is None:
                from qwen25_kv_tokenizer import Qwen25Tokenizer

                self._tokenizer = Qwen25Tokenizer(self.tokenizer_path, self.tokenizer_config_path)
            self._validate_tokenizer(self._tokenizer, self.contract)
            self._descriptor = descriptor
            self._started = True
        except RuntimeUnavailable:
            self._runtime_error = "runtime initialization failed"
            self._backend.close()
            raise
        except Exception as exc:
            self._runtime_error = f"runtime initialization failed: {type(exc).__name__}"
            self._backend.close()
            raise RuntimeUnavailable(f"Qwen2.5 StaticCache initialization failed: {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        self._closed = True
        self._started = False
        self._cancel.set()
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None
        if self._active_request is not None:
            try:
                self._end_backend_request(self._active_request)
            except Exception:
                self._runtime_error = "request cleanup failed; restart required"
                LOGGER.exception("StaticCache request cleanup failed during runtime close")
                setattr(self._backend, "poisoned", True)
            finally:
                self._active_request = None
        self._descriptor = None
        self._backend.close()

    def cancel(self) -> None:
        self._cancel.set()

    def status(self) -> Dict[str, Any]:
        contract = self.contract
        cache_inputs = tuple(getattr(contract, "cache_inputs", ()) or ())
        artifact = self._artifact_status
        om_artifact = artifact.get("om", {}) if isinstance(artifact, Mapping) else {}
        tokenizer_artifact = artifact.get("tokenizer", {}) if isinstance(artifact, Mapping) else {}
        contract_artifact = artifact.get("contract", {}) if isinstance(artifact, Mapping) else {}
        return {
            "ready": self.started,
            "model": self.model_id,
            "backend": "acl_om",
            "execution_mode": str(getattr(contract, "execution_mode", "static_kv_token_fp32")) if contract else "static_kv_token_fp32",
            "cache_layout": getattr(contract, "cache_layout", "split") if contract else "split",
            "cache_tensor_count": len(cache_inputs),
            "cache_dtype": "float32",
            "cache_shape": list(getattr(contract, "cache_shape", (1, 2, 1024, 64))),
            "mask_length": int(getattr(contract, "mask_length", 1024)),
            "sequence_length": int(getattr(contract, "static_sequence_length", 1024)),
            "max_tokens": int(self.max_tokens),
            "sentence_stop_min_tokens": SENTENCE_STOP_MIN_TOKENS,
            "descriptor_validated": self._descriptor is not None,
            "request_buffer_reuse": bool(getattr(self._backend, "supports_request_reuse", False)),
            "device_cache_update": bool(getattr(self._backend, "supports_device_cache_update", False)),
            "restart_required": bool(getattr(self._backend, "poisoned", False) or getattr(self._backend, "cleanup_failed", False)),
            "cleanup_failed": bool(getattr(self._backend, "cleanup_failed", False)),
            "runtime_error": self._runtime_error,
            "watchdog_triggered": bool(self._watchdog_triggered),
            "artifact_lock_verified": bool(self._artifact_status.get("verified", False)),
            "artifact_verified": bool(self._artifact_status.get("verified", False)),
            "healthy": self.started,
            "artifact_lock": self._artifact_status,
            "om_bytes": om_artifact.get("bytes"),
            "om_sha256": om_artifact.get("sha256"),
            "contract_sha256": contract_artifact.get("sha256"),
            "runtime_contract_sha256": self._artifact_status.get("runtime_contract_sha256")
            or contract_artifact.get("sha256"),
            "controller_contract_sha256": self._artifact_status.get("controller_contract_sha256"),
            "tokenizer_bytes": tokenizer_artifact.get("bytes"),
            "tokenizer_sha256": tokenizer_artifact.get("sha256"),
            "target_soc": artifact.get("soc_version") if isinstance(artifact, Mapping) else None,
            "board_soc": artifact.get("board_soc_version") if isinstance(artifact, Mapping) else None,
            "compatibility_experiment": bool(
                artifact.get("compatibility_experiment", False)
                if isinstance(artifact, Mapping)
                else False
            ),
        }

    def validate_prompt_budget(self, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> int:
        if not self.started:
            raise RuntimeUnavailable("Qwen2.5 StaticCache runtime is not ready")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= self.max_tokens:
            raise RuntimeRequestError(f"max_tokens must be between 1 and {self.max_tokens}")
        prompt_ids = self._encode_messages(messages)
        limit = int(getattr(self.contract, "static_sequence_length", 1024))
        if len(prompt_ids) + max_tokens > limit:
            raise RuntimeRequestError(f"prompt plus max_tokens exceeds the fixed {limit}-token context")
        return len(prompt_ids)

    def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: Optional[int] = None) -> GenerationResult:
        final = GenerationResult("", 0, 0, "stop", True)
        for item in self.stream(messages, max_tokens):
            final = item
        return final

    def stream(self, messages: Sequence[Mapping[str, Any]], max_tokens: Optional[int] = None) -> Iterator[GenerationResult]:
        if not self.started:
            raise RuntimeUnavailable("Qwen2.5 StaticCache runtime is not ready")
        limit = self.max_tokens if max_tokens is None else max_tokens
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.max_tokens:
            raise RuntimeRequestError(f"max_tokens must be between 1 and {self.max_tokens}")
        if not self._lock.acquire(blocking=False):
            raise RuntimeBusy("another Qwen2.5 NPU operation is already running")
        self._cancel.clear()
        request_handle = None
        try:
            prompt_ids = self._encode_messages(messages)
            sequence_length = int(getattr(self.contract, "static_sequence_length", 1024))
            if len(prompt_ids) + limit > sequence_length:
                raise RuntimeRequestError(f"prompt plus max_tokens exceeds the fixed {sequence_length}-token context")
            cache = self._new_cache()
            request_handle = self._begin_backend_request(cache)
            self._active_request = request_handle
            deadline = time.monotonic() + self.execution_timeout_seconds
            watchdog = _RequestWatchdog(self, deadline)
            self._watchdog = watchdog
            watchdog.start()
            real_length = 0
            next_id: Optional[int] = None
            for token_id in prompt_ids:
                self._check_cancelled()
                _, cache, logits = self._run_step(token_id, real_length, cache, deadline, request_handle)
                real_length += 1
                next_id = _greedy_next_id(logits, self._vocabulary_size())
            if next_id is None:
                raise RuntimeUnavailable("Qwen2.5 produced no logits for the prompt")

            generated: List[int] = []
            text = ""
            eos_id = int(getattr(self.contract, "eos_token_id", 151645))
            # Some 310B4 exports do not emit Qwen's im_end token promptly.
            # Stop at the first complete sentence after the minimum rather
            # than waiting until the 80-token ceiling; every extra token is a
            # serialized NPU execution and otherwise looks like a stall.
            sentence_stop_min = SENTENCE_STOP_MIN_TOKENS
            for _ in range(limit):
                self._check_cancelled()
                token_id = int(next_id)
                generated.append(token_id)
                text = self._decode(generated)
                at_eos = token_id == eos_id
                at_sentence_boundary = (
                    len(generated) >= sentence_stop_min
                    and _ends_with_sentence_boundary(text)
                )
                at_limit = len(generated) >= limit or real_length >= sequence_length
                if at_eos or at_sentence_boundary or at_limit:
                    reason = "stop" if (at_eos or at_sentence_boundary) else "length"
                    self._last_stop_reason = reason
                    yield GenerationResult(text, len(prompt_ids), len(generated), reason, True)
                    break
                _, cache, logits = self._run_step(token_id, real_length, cache, deadline, request_handle)
                real_length += 1
                next_id = _greedy_next_id(logits, self._vocabulary_size())
                yield GenerationResult(text, len(prompt_ids), len(generated), "in_progress", False)
        finally:
            try:
                if self._watchdog is not None:
                    self._watchdog.stop()
                    self._watchdog = None
                if request_handle is not None:
                    self._end_backend_request(request_handle)
            finally:
                # Never leave the admission lock held when ACL cleanup
                # reports an error; the backend is already fail-closed.
                self._active_request = None
                self._lock.release()

    def _encode_messages(self, messages: Sequence[Mapping[str, Any]]) -> List[int]:
        if self._tokenizer is None or self.contract is None:
            raise RuntimeUnavailable("Qwen2.5 tokenizer/contract is not initialized")
        try:
            values = [int(value) for value in self._tokenizer.encode_messages(messages)]
        except Exception as exc:
            raise RuntimeRequestError(f"Qwen2.5 tokenization failed: {exc}") from exc
        vocab = self._vocabulary_size()
        if not values or any(value < 0 or value >= vocab for value in values):
            raise RuntimeRequestError("prompt contains no valid Qwen2.5 token IDs")
        return values

    def _decode(self, token_ids: Sequence[int]) -> str:
        try:
            return str(self._tokenizer.decode(token_ids))
        except Exception as exc:
            raise RuntimeRequestError("Qwen2.5 tokenizer decode failed") from exc

    def _vocabulary_size(self) -> int:
        return int(getattr(self.contract, "vocabulary_size", VOCABULARY_SIZE))

    def _check_cancelled(self) -> None:
        if self._watchdog_triggered:
            raise RuntimeExecutionTimeout(self._runtime_error or "ACL execution exceeded request deadline")
        if self._cancel.is_set():
            raise RuntimeRequestError("Qwen2.5 generation was cancelled")

    def _new_cache(self) -> _CacheState:
        if self.contract is None:
            raise RuntimeUnavailable("Qwen2.5 contract is not initialized")
        np = _numpy()
        inputs = tuple(getattr(self.contract, "cache_inputs", ()) or ())
        if len(inputs) != 48 or getattr(self.contract, "cache_layout", "split") != "split":
            raise RuntimeUnavailable("StaticCache contract must expose 48 split cache inputs")
        values: Dict[str, Any] = {}
        for item in sorted(inputs, key=lambda value: int(value.cache_index)):
            if item.dtype != "float32" or len(item.shape) != 4:
                raise RuntimeUnavailable(f"cache input {item.name} is not a split FP32 tensor")
            values[item.name] = np.zeros(tuple(item.shape), dtype=np.float32)
        return _CacheState(values)

    def _begin_backend_request(self, cache: _CacheState) -> Any:
        begin = getattr(self._backend, "begin_request", None)
        return begin(cache.values) if callable(begin) else None

    def _end_backend_request(self, handle: Any) -> None:
        end = getattr(self._backend, "end_request", None)
        if callable(end):
            try:
                end(handle)
            except Exception:
                setattr(self._backend, "poisoned", True)
                LOGGER.exception("StaticCache request cleanup failed")

    def _run_step(self, token_id: int, real_length: int, cache: _CacheState, deadline: float, request_handle: Any) -> Tuple[Any, _CacheState, Any]:
        if self.contract is None or self._descriptor is None:
            raise RuntimeUnavailable("Qwen2.5 StaticCache runtime is not initialized")
        sequence_length = int(getattr(self.contract, "static_sequence_length", 1024))
        mask_length = int(getattr(self.contract, "mask_length", sequence_length))
        if real_length < 0 or real_length >= sequence_length or real_length + 1 > mask_length:
            raise RuntimeRequestError("Qwen2.5 StaticCache position is out of range")
        np = _numpy()
        base: Dict[str, Any] = {
            "input_ids": np.asarray([[int(token_id)]], dtype=np.int64),
            "attention_mask": _attention_mask(mask_length, real_length, np),
            "position_ids": np.asarray([[real_length]], dtype=np.int64),
        }
        run_request = getattr(self._backend, "run_request_step", None)
        try:
            if time.monotonic() >= deadline:
                raise RuntimeExecutionTimeout("ACL execution exceeded request deadline")
            if request_handle is not None and callable(run_request):
                with _execution_deadline(max(0.001, deadline - time.monotonic())):
                    result = run_request(request_handle, base, real_length, cache.values)
                if not isinstance(result, BackendStep):
                    result = BackendStep(result, bool(getattr(self._backend, "supports_device_cache_update", False)))
            else:
                arrays = dict(base)
                arrays.update(cache.values)
                with _execution_deadline(max(0.001, deadline - time.monotonic())):
                    result = BackendStep(self._backend.run(self._prepare_inputs(arrays)), False)
            # SIGALRM is unavailable in ThreadingHTTPServer worker threads;
            # enforce the same deadline after a synchronous ACL call returns.
            if time.monotonic() >= deadline:
                raise RuntimeExecutionTimeout("ACL execution exceeded request deadline")
        except RuntimeExecutionTimeout:
            setattr(self._backend, "poisoned", True)
            self._runtime_error = "ACL execution exceeded request deadline"
            raise
        except RuntimeUnavailable:
            # A failure after an ACL request has been opened may leave device
            # state or dataset buffers uncertain.  Fail closed and require a
            # clean service restart rather than accepting a partially updated
            # KV cache on the next request.
            setattr(self._backend, "poisoned", True)
            self._runtime_error = "ACL execution failed; restart required"
            raise
        except RuntimeRequestError:
            raise
        except Exception as exc:
            setattr(self._backend, "poisoned", True)
            self._runtime_error = f"ACL execution failed: {type(exc).__name__}"
            raise RuntimeUnavailable(f"Qwen2.5 ACL execution failed: {type(exc).__name__}") from exc
        outputs = _normalize_outputs(
            result.outputs,
            self._descriptor,
            allow_missing_cache=bool(result.cache_device_updated),
        )
        logits_item = getattr(self.contract, "logits_output", None)
        if logits_item is None:
            raise RuntimeUnavailable("contract does not identify a logits output")
        logits_index = int(getattr(self.contract, "logits_output_index"))
        logits = _select_logits(outputs[logits_index], logits_item, real_length, self._vocabulary_size())
        if result.cache_device_updated:
            # The native backend deliberately returns only the logits after a
            # successful D2D cache commit.  The device buffers are the source
            # of truth for subsequent steps; retaining the old host arrays
            # avoids a 48-tensor D2H transfer on every token.
            updated_cache = _CacheState(dict(cache.values), True)
        else:
            updated_cache = self._update_cache(outputs, cache, real_length)
        return result.outputs, updated_cache, logits

    def _prepare_inputs(self, arrays: Mapping[str, Any]) -> Dict[str, Any]:
        if self._descriptor is None:
            raise RuntimeUnavailable("ACL descriptor is not initialized")
        np = _numpy()
        prepared: Dict[str, Any] = {}
        for item in self._descriptor.inputs:
            if item.name not in arrays:
                raise RuntimeUnavailable(f"Qwen2.5 input {item.name} is missing")
            value = np.ascontiguousarray(arrays[item.name], dtype=_numpy_dtype(item.dtype, np))
            if tuple(value.shape) != tuple(item.shape):
                raise RuntimeUnavailable(f"Qwen2.5 input {item.name} shape mismatch")
            if item.byte_size is not None and int(value.nbytes) != int(item.byte_size):
                raise RuntimeUnavailable(f"Qwen2.5 input {item.name} byte size mismatch")
            prepared[item.name] = value
        return prepared

    def _update_cache(self, outputs: Sequence[Any], old: _CacheState, real_length: int) -> _CacheState:
        if self.contract is None or self._descriptor is None:
            raise RuntimeUnavailable("Qwen2.5 contract/descriptor is not initialized")
        np = _numpy()
        by_name = {item.name: index for index, item in enumerate(self._descriptor.outputs)}
        input_by_index = {int(item.cache_index): item for item in self.contract.cache_inputs}
        values = dict(old.values)
        for out_item in self.contract.cache_outputs:
            if out_item.name not in by_name or out_item.cache_index is None:
                raise RuntimeUnavailable("cache output metadata is incomplete")
            value = np.asarray(outputs[by_name[out_item.name]])
            if value.dtype != np.dtype("float32") or tuple(value.shape) != tuple(out_item.shape):
                raise RuntimeUnavailable(f"cache output {out_item.name} violates its descriptor")
            in_item = input_by_index.get(int(out_item.cache_index))
            if in_item is None:
                raise RuntimeUnavailable("cache output has no matching input")
            current = np.asarray(values[in_item.name])
            if out_item.cache_update == "full" or tuple(value.shape) == tuple(current.shape):
                values[in_item.name] = np.ascontiguousarray(value.copy(), dtype=np.float32)
            elif out_item.cache_update == "token" or _is_token_cache_shape(current.shape, value.shape):
                values[in_item.name] = _write_token_cache(current, value, real_length)
            else:
                raise RuntimeUnavailable(f"cache output {out_item.name} has no supported update mode")
        return _CacheState(values, False)

    @staticmethod
    def _validate_tokenizer(tokenizer: Any, contract: Any) -> None:
        vocab = int(getattr(tokenizer, "vocab_size"))
        expected_vocab = int(getattr(contract, "vocabulary_size", VOCABULARY_SIZE))
        # Qwen2.5 reserves model output rows that are not materialized in
        # tokenizer.json (151,665 tokenizer entries versus 151,936 logits
        # rows).  A smaller tokenizer vocabulary is valid; IDs above it are
        # rejected by the decoder if the model ever emits a reserved row.
        if vocab <= 0 or vocab > expected_vocab:
            raise RuntimeUnavailable(
                f"tokenizer vocabulary {vocab} exceeds/does not fit model vocabulary {expected_vocab}"
            )
        for name in ("eos_token_id", "pad_token_id"):
            value = getattr(tokenizer, name, None)
            expected = getattr(contract, name, None)
            if not isinstance(value, int) or isinstance(value, bool) or int(value) != int(expected):
                raise RuntimeUnavailable(f"tokenizer {name} does not match contract")
        expected_bos = getattr(contract, "bos_token_id", None)
        actual_bos = getattr(tokenizer, "bos_token_id", None)
        if expected_bos is None:
            if actual_bos is not None:
                raise RuntimeUnavailable("tokenizer bos_token_id does not match null contract")
        elif (
            not isinstance(actual_bos, int)
            or isinstance(actual_bos, bool)
            or int(actual_bos) != int(expected_bos)
        ):
            raise RuntimeUnavailable("tokenizer bos_token_id does not match contract")


def _numpy() -> Any:
    try:
        return importlib.import_module("numpy")
    except ImportError as exc:
        raise RuntimeUnavailable("numpy is required by the StaticCache runtime") from exc


def _numpy_dtype(dtype: str, np: Any) -> Any:
    mapping = {"int64": np.int64, "float32": np.float32, "float16": np.float16}
    try:
        return mapping[str(dtype).lower()]
    except KeyError as exc:
        raise RuntimeUnavailable(f"unsupported ACL tensor dtype {dtype}") from exc


def _attention_mask(length: int, real_length: int, np: Any = None) -> Any:
    np = np or _numpy()
    if length <= 0 or real_length < 0 or real_length + 1 > length:
        raise RuntimeRequestError("attention mask position exceeds fixed StaticCache length")
    mask = np.zeros((1, int(length)), dtype=np.int64)
    mask[0, : real_length + 1] = 1
    return np.ascontiguousarray(mask)


def _ends_with_sentence_boundary(text: str) -> bool:
    """Return true when decoded text ends at a user-visible sentence break."""

    stripped = text.rstrip()
    return bool(stripped) and stripped.endswith(
        ("。", "！", "？", "；", ".", "!", "?", ";", "\n")
    )


def _normalize_outputs(
    raw: Any,
    descriptor: RuntimeDescriptor,
    *,
    allow_missing_cache: bool = False,
) -> List[Any]:
    if isinstance(raw, Mapping):
        result: List[Any] = []
        for item in descriptor.outputs:
            if item.name not in raw:
                if allow_missing_cache and not _is_logits_descriptor(item):
                    result.append(None)
                    continue
                raise RuntimeUnavailable(f"ACL output {item.name} is missing")
            result.append(raw[item.name])
        return result
    if isinstance(raw, (list, tuple)):
        if len(raw) != len(descriptor.outputs):
            raise RuntimeUnavailable("ACL output count differs from descriptor")
        return list(raw)
    raise RuntimeUnavailable("ACL backend returned invalid outputs")


def _select_logits(value: Any, descriptor: Any, real_length: int, vocabulary_size: int = VOCABULARY_SIZE) -> Any:
    np = _numpy()
    array = np.asarray(value)
    expected_shape = tuple(getattr(descriptor, "shape", ()))
    if array.dtype != np.dtype("float32") or tuple(array.shape) != expected_shape:
        raise RuntimeUnavailable("logits violate the exact StaticCache descriptor")
    if array.ndim != 3 or array.shape[0] != 1 or array.shape[-1] != vocabulary_size:
        raise RuntimeUnavailable("logits shape is not [1,1,vocabulary]")
    row_index = 0 if array.shape[1] == 1 else min(real_length, array.shape[1] - 1)
    row = np.ascontiguousarray(array[0, row_index, :], dtype=np.float32)
    if not bool(np.isfinite(row).all()):
        raise RuntimeUnavailable("logits contain non-finite values")
    return row


def _is_logits_descriptor(item: Any) -> bool:
    """Identify the sole admitted logits descriptor without a name heuristic."""

    shape = tuple(getattr(item, "shape", ()))
    return (
        str(getattr(item, "dtype", "")).lower() == "float32"
        and len(shape) == 3
        and int(shape[0]) == 1
        and int(shape[-1]) == VOCABULARY_SIZE
    )


def _greedy_next_id(row: Any, vocabulary_size: int = VOCABULARY_SIZE) -> int:
    np = _numpy()
    values = np.asarray(row)
    if values.ndim != 1 or values.shape[0] != vocabulary_size or not bool(np.isfinite(values).all()):
        raise RuntimeUnavailable("logits vocabulary dimension or values are invalid")
    return int(np.argmax(values))


def _is_token_cache_shape(full_shape: Sequence[int], token_shape: Sequence[int]) -> bool:
    if len(full_shape) != len(token_shape) or len(full_shape) != 4:
        return False
    differing = [i for i, (full, token) in enumerate(zip(full_shape, token_shape)) if int(full) != int(token)]
    return len(differing) == 1 and int(token_shape[differing[0]]) == 1 and int(full_shape[differing[0]]) > 1


def _write_token_cache(current: Any, token: Any, real_length: int) -> Any:
    """Insert [B,1,H,D] or [B,H,1,D] token output into a split cache."""
    np = _numpy()
    full = np.asarray(current)
    value = np.asarray(token)
    if full.ndim != value.ndim or full.ndim != 4:
        raise RuntimeUnavailable("split cache tensors must be rank four")
    # Compare shapes after removing one candidate sequence dimension from the
    # full cache and one singleton dimension from the token output.  This
    # handles both [B,S,H,D] and the StaticCache [B,H,S,D] layout.
    matches: List[Tuple[int, int]] = []
    for seq_candidate, full_dim in enumerate(full.shape):
        if seq_candidate == 0 or full_dim <= 1:
            continue
        for token_candidate, token_dim in enumerate(value.shape):
            if token_candidate == 0 or token_dim != 1:
                continue
            if tuple(full.shape[:seq_candidate] + full.shape[seq_candidate + 1 :]) == tuple(value.shape[:token_candidate] + value.shape[token_candidate + 1 :]):
                matches.append((seq_candidate, token_candidate))
    if len(matches) != 1:
        raise RuntimeUnavailable("cannot identify token cache sequence axis")
    seq_axis, token_axis = matches[0]
    if real_length < 0 or real_length >= full.shape[seq_axis]:
        raise RuntimeRequestError("token cache position exceeds static sequence")
    moved = np.moveaxis(value, token_axis, seq_axis)
    expected = list(full.shape)
    expected[seq_axis] = 1
    if tuple(moved.shape) != tuple(expected):
        raise RuntimeUnavailable("token cache dimensions do not match split cache")
    updated = np.array(full, copy=True, dtype=np.float32)
    index = [slice(None)] * full.ndim
    index[seq_axis] = slice(real_length, real_length + 1)
    updated[tuple(index)] = moved
    return np.ascontiguousarray(updated, dtype=np.float32)


@contextmanager
def _execution_deadline(seconds: float) -> Iterator[None]:
    if seconds <= 0 or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)

    def handler(_signum: int, _frame: Any) -> None:
        raise RuntimeExecutionTimeout("ACL execution exceeded request deadline")

    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class NativeQwen25Backend:
    """Synchronous PyACL backend with request-level buffer reuse."""

    supports_request_reuse = True
    supports_device_cache_update = False

    def __init__(self, device_id: int = 0) -> None:
        self.device_id = int(device_id)
        self.acl: Any = None
        self.context: Any = None
        self.stream: Any = None
        self.model_id: Any = None
        self.desc: Any = None
        self._opened = False
        self._inputs: Tuple[RuntimeTensorDescriptor, ...] = ()
        self._outputs: Tuple[RuntimeTensorDescriptor, ...] = ()
        self._active: Optional[_NativeRequest] = None
        self._cache_input_names: Tuple[str, ...] = ()
        self._cache_output_names: Tuple[str, ...] = ()
        self._logits_output_name: Optional[str] = None
        self._d2d_disabled = False
        self.poisoned = False
        self.cleanup_failed = False

    def open(self, model_path: Path) -> RuntimeDescriptor:
        if self._opened or self.poisoned:
            raise RuntimeUnavailable("ACL backend is already open or unhealthy")
        try:
            self.acl = importlib.import_module("acl")
            # Report the path as active only after a real cache copy succeeds.
            # CANN 8.0 may omit the Python symbol while still accepting its
            # documented C ABI enum value, so capability is checked lazily in
            # _device_cache_d2d() rather than inferred here.
            self.supports_device_cache_update = False
            _acl_check(self.acl.init(), "acl.init")
            _acl_check(self.acl.rt.set_device(self.device_id), "acl.rt.set_device")
            self.context, ret = _split_acl(self.acl.rt.create_context(self.device_id))
            _acl_check(ret, "acl.rt.create_context")
            self.stream, ret = _split_acl(self.acl.rt.create_stream())
            _acl_check(ret, "acl.rt.create_stream")
            self.model_id, ret = _split_acl(self.acl.mdl.load_from_file(str(model_path)))
            _acl_check(ret, "acl.mdl.load_from_file")
            self.desc = self.acl.mdl.create_desc()
            if self.desc is None:
                raise RuntimeUnavailable("acl.mdl.create_desc returned no descriptor")
            _acl_check(self.acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            self._inputs = tuple(self._read_descriptors(True))
            self._outputs = tuple(self._read_descriptors(False))
            self._opened = True
            return RuntimeDescriptor(self._inputs, self._outputs)
        except RuntimeUnavailable:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise RuntimeUnavailable(f"ACL descriptor initialization failed: {type(exc).__name__}") from exc

    def configure_contract(self, contract: Any) -> None:
        """Bind cache indices to descriptor names after strict validation."""
        self._cache_input_names = tuple(item.name for item in sorted(contract.cache_inputs, key=lambda item: int(item.cache_index)))
        self._cache_output_names = tuple(item.name for item in sorted(contract.cache_outputs, key=lambda item: int(item.cache_index)))
        self._logits_output_name = str(contract.logits_output.name)

    def _read_descriptors(self, is_input: bool) -> List[RuntimeTensorDescriptor]:
        kind = "input" if is_input else "output"
        count_fn = getattr(self.acl.mdl, f"get_num_{kind}s", None)
        size_fn = getattr(self.acl.mdl, f"get_{kind}_size_by_index", None)
        name_fn = getattr(self.acl.mdl, f"get_{kind}_name_by_index", None)
        dims_fn = getattr(self.acl.mdl, f"get_{kind}_dims", None)
        dtype_fn = getattr(self.acl.mdl, f"get_{kind}_data_type", None)
        if not all(callable(fn) for fn in (count_fn, size_fn, dims_fn, dtype_fn)):
            raise RuntimeUnavailable(f"ACL binding cannot read {kind} descriptors")
        count = int(_first_acl(count_fn(self.desc)))
        result: List[RuntimeTensorDescriptor] = []
        for index in range(count):
            name = _first_acl(name_fn(self.desc, index)) if callable(name_fn) else f"{kind}_{index}"
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            result.append(RuntimeTensorDescriptor(str(name), _acl_dtype(self.acl, int(_first_acl(dtype_fn(self.desc, index)))), _parse_dims(dims_fn(self.desc, index)), int(_first_acl(size_fn(self.desc, index)))))
        return result

    def begin_request(self, cache_values: Mapping[str, Any]) -> _NativeRequest:
        if not self._opened or self.acl is None or self._active is not None:
            raise RuntimeUnavailable("ACL backend is not ready for a request")
        # HTTP requests are handled by worker threads, while the model/context
        # is opened by the launcher thread.  Bind the ACL context before any
        # dataset, device-buffer, or memcpy call; setting it only immediately
        # before execute() makes worker-thread allocations fail on 310B4.
        if self.context is not None:
            _acl_check(self.acl.rt.set_context(self.context), "acl.rt.set_context")
        input_dataset = self.acl.mdl.create_dataset()
        output_dataset = self.acl.mdl.create_dataset()
        if input_dataset is None or output_dataset is None:
            if input_dataset is not None:
                _safe_call(getattr(self.acl.mdl, "destroy_dataset", None), input_dataset, label="acl.mdl.destroy_dataset")
            if output_dataset is not None:
                _safe_call(getattr(self.acl.mdl, "destroy_dataset", None), output_dataset, label="acl.mdl.destroy_dataset")
            raise RuntimeUnavailable("ACL request dataset creation failed")
        input_records: Dict[str, Tuple[int, Any]] = {}
        output_records: Dict[str, Tuple[int, Any]] = {}
        output_arrays: Dict[str, Any] = {}
        cache_input_pointers: Dict[str, int] = {}
        allocations: List[Tuple[int, Any]] = []
        np = _numpy()
        try:
            for item in self._inputs:
                pointer, data = self._add_buffer(input_dataset, _tensor_bytes(item))
                allocations.append((pointer, data))
                input_records[item.name] = (pointer, data)
                if item.name in self._cache_input_names or (
                    not self._cache_input_names and item.name not in {"input_ids", "attention_mask", "position_ids"}
                ):
                    if item.name not in cache_values:
                        raise RuntimeUnavailable(f"cache input {item.name} is missing")
                    value = np.ascontiguousarray(cache_values[item.name], dtype=_numpy_dtype(item.dtype, np))
                    if tuple(value.shape) != item.shape or int(value.nbytes) != _tensor_bytes(item):
                        raise RuntimeUnavailable(f"cache input {item.name} shape/size mismatch")
                    self._copy_h2d(pointer, value)
                    cache_input_pointers[item.name] = pointer
            for item in self._outputs:
                pointer, data = self._add_buffer(output_dataset, _tensor_bytes(item))
                allocations.append((pointer, data))
                output_records[item.name] = (pointer, data)
                output_arrays[item.name] = np.empty(item.shape, dtype=_numpy_dtype(item.dtype, np))
            request = _NativeRequest(input_dataset, output_dataset, input_records, output_records, output_arrays, cache_input_pointers, allocations)
            self._active = request
            return request
        except Exception:
            self._cleanup_request(_NativeRequest(input_dataset, output_dataset, input_records, output_records, output_arrays, cache_input_pointers, allocations))
            raise

    def run_request_step(self, request: _NativeRequest, base_inputs: Mapping[str, Any], real_length: int, cache_values: Optional[Mapping[str, Any]] = None) -> BackendStep:
        if request is not self._active or self.acl is None or self.model_id is None:
            raise RuntimeUnavailable("ACL request is not active")
        np = _numpy()
        for item in self._inputs[:3]:
            value = base_inputs.get(item.name)
            if value is None:
                raise RuntimeUnavailable(f"missing base input {item.name}")
            array = np.ascontiguousarray(value, dtype=_numpy_dtype(item.dtype, np))
            if tuple(array.shape) != item.shape or int(array.nbytes) != _tensor_bytes(item):
                raise RuntimeUnavailable(f"invalid base input buffer {item.name}")
            self._copy_h2d(request.input_records[item.name][0], array)
        # If D2D is unavailable, the runtime's host cache is authoritative and
        # must be copied back before each subsequent execution.  The normal
        # path performs this only after the first D2D attempt fails.
        if not request.device_cache_valid and cache_values is not None:
            for item in self._inputs[3:]:
                value = cache_values.get(item.name)
                if value is None:
                    raise RuntimeUnavailable(f"missing host cache input {item.name}")
                array = np.ascontiguousarray(value, dtype=_numpy_dtype(item.dtype, np))
                self._copy_h2d(request.input_records[item.name][0], array)
        if self.context is not None:
            _acl_check(self.acl.rt.set_context(self.context), "acl.rt.set_context")
        execute = getattr(self.acl.mdl, "execute", None)
        if not callable(execute):
            raise RuntimeUnavailable("CANN binding lacks synchronous acl.mdl.execute")
        _acl_check(execute(self.model_id, request.input_dataset, request.output_dataset), "acl.mdl.execute")
        logits_item = self._logits_descriptor()
        if logits_item is None:
            raise RuntimeUnavailable("ACL descriptor does not identify a logits output")
        logits = request.output_arrays[logits_item.name]
        self._copy_d2h(logits, request.output_records[logits_item.name][0], int(logits.nbytes))
        outputs: Dict[str, Any] = {logits_item.name: logits}
        cache_ok = self._device_cache_d2d(request, real_length)
        if cache_ok:
            request.device_cache_used = True
            request.device_cache_valid = True
            return BackendStep(outputs, True)
        if request.device_cache_used:
            # Host buffers deliberately stop tracking the cache after a
            # successful D2D path.  Falling back here would restore stale
            # data and silently corrupt generation, so the service must be
            # restarted before accepting another request.
            self.poisoned = True
            raise RuntimeUnavailable("device cache update failed after resident-cache execution")
        for item in self._outputs:
            if item.name == logits_item.name:
                continue
            array = request.output_arrays[item.name]
            self._copy_d2h(array, request.output_records[item.name][0], int(array.nbytes))
            outputs[item.name] = array
        request.device_cache_valid = bool(cache_ok)
        return BackendStep(outputs, False)

    def end_request(self, request: _NativeRequest) -> None:
        if request is not self._active:
            return
        ok = self._cleanup_request(request)
        self._active = None
        if not ok:
            self.cleanup_failed = True
            self.poisoned = True
            raise RuntimeUnavailable("ACL request buffer cleanup failed; restart required")

    def run(self, inputs: Mapping[str, Any]) -> Any:
        cache = {item.name: inputs[item.name] for item in self._inputs[3:] if item.name in inputs}
        request = self.begin_request(cache)
        try:
            base = {name: inputs[name] for name in ("input_ids", "attention_mask", "position_ids")}
            return self.run_request_step(request, base, 0).outputs
        finally:
            self.end_request(request)

    def _device_cache_d2d(self, request: _NativeRequest, real_length: int) -> bool:
        if self.acl is None or self._d2d_disabled:
            return False
        d2d = self._device_to_device_memcpy_kind()
        memcpy = getattr(self.acl.rt, "memcpy", None)
        if d2d is None or not callable(memcpy) or len(self._inputs) < 51 or len(self._outputs) < 49:
            self._d2d_disabled = True
            return False
        cache_inputs = tuple(self._inputs[3:])
        if self._cache_input_names:
            by_name = {item.name: item for item in self._inputs}
            cache_inputs = tuple(by_name[name] for name in self._cache_input_names if name in by_name)
        # Contract validation guarantees 48 cache outputs in matching index
        # order.  Prefer configured names; shape filtering is a safe fallback
        # for direct backend tests that do not provide a contract.
        if self._cache_output_names:
            by_name = {item.name: item for item in self._outputs}
            cache_outputs = tuple(by_name[name] for name in self._cache_output_names if name in by_name)
        else:
            cache_outputs = tuple(item for item in self._outputs if not (len(item.shape) == 3 and item.shape[-1] == VOCABULARY_SIZE))
        if len(cache_inputs) != 48 or len(cache_outputs) != 48:
            return False
        try:
            for input_item, output_item in zip(cache_inputs, cache_outputs):
                dst = request.cache_input_pointers.get(input_item.name)
                source = request.output_records.get(output_item.name)
                if dst is None or source is None:
                    return False
                src = int(source[0])
                if tuple(output_item.shape) == tuple(input_item.shape):
                    _acl_check(memcpy(dst, _tensor_bytes(input_item), src, _tensor_bytes(output_item), int(d2d)), "acl.rt.memcpy device_to_device")
                    continue
                # Planned split layout is input [1,H,S,D], output [1,1,H,D].
                if len(input_item.shape) != 4 or len(output_item.shape) != 4 or output_item.shape[1] != 1:
                    return False
                heads, sequence, dim = int(input_item.shape[1]), int(input_item.shape[2]), int(input_item.shape[3])
                token_bytes = dim * 4
                for head in range(heads):
                    dst_offset = (head * sequence + real_length) * token_bytes
                    src_offset = head * token_bytes
                    _acl_check(memcpy(int(dst) + dst_offset, token_bytes, int(src) + src_offset, token_bytes, int(d2d)), "acl.rt.memcpy device_to_device")
            self.supports_device_cache_update = True
            return True
        except Exception:
            self._d2d_disabled = True
            self.supports_device_cache_update = False
            LOGGER.warning("device cache D2D update unavailable; using host cache copies", exc_info=True)
            return False

    def _device_to_device_memcpy_kind(self) -> Optional[int]:
        """Resolve the D2D enum without requiring a Python-binding symbol."""

        if self.acl is None:
            return None
        for owner in (self.acl, getattr(self.acl, "rt", None)):
            value = getattr(owner, "ACL_MEMCPY_DEVICE_TO_DEVICE", None)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        # CANN 8.0's installed acl_rt.h defines the enum sequence as H2H=0,
        # H2D=1, D2H=2, D2D=3. Do not attempt the raw ABI fallback when a
        # binding advertises incompatible H2D/D2H values.
        for owner in (self.acl, getattr(self.acl, "rt", None)):
            h2d = getattr(owner, "ACL_MEMCPY_HOST_TO_DEVICE", 1)
            d2h = getattr(owner, "ACL_MEMCPY_DEVICE_TO_HOST", 2)
            try:
                if int(h2d) != 1 or int(d2h) != 2:
                    return None
            except (TypeError, ValueError):
                return None
        return _ACL_MEMCPY_DEVICE_TO_DEVICE

    def _logits_descriptor(self) -> Optional[RuntimeTensorDescriptor]:
        if self._logits_output_name:
            for item in self._outputs:
                if item.name == self._logits_output_name:
                    return item
        candidates = [item for item in self._outputs if _is_logits_descriptor(item)]
        return candidates[0] if len(candidates) == 1 else None

    def _add_buffer(self, dataset: Any, size: int) -> Tuple[int, Any]:
        policy = getattr(self.acl, "ACL_MEM_MALLOC_HUGE_FIRST", 0)
        pointer, ret = _split_acl(self.acl.rt.malloc(int(size), policy))
        _acl_check(ret, "acl.rt.malloc")
        data = self.acl.create_data_buffer(pointer, int(size))
        if data is None:
            _safe_call(getattr(self.acl.rt, "free", None), pointer, label="acl.rt.free")
            raise RuntimeUnavailable("acl.create_data_buffer returned no buffer")
        try:
            _acl_check(self.acl.mdl.add_dataset_buffer(dataset, data), "acl.mdl.add_dataset_buffer")
        except Exception:
            _safe_call(getattr(self.acl, "destroy_data_buffer", None), data, label="acl.destroy_data_buffer")
            _safe_call(getattr(self.acl.rt, "free", None), pointer, label="acl.rt.free")
            raise
        return int(pointer), data

    def _copy_h2d(self, pointer: int, array: Any) -> None:
        _acl_check(self.acl.rt.memcpy(pointer, int(array.nbytes), _host_pointer(self.acl, array), int(array.nbytes), getattr(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1)), "acl.rt.memcpy host_to_device")

    def _copy_d2h(self, array: Any, pointer: int, size: int) -> None:
        _acl_check(self.acl.rt.memcpy(_host_pointer(self.acl, array), int(array.nbytes), pointer, int(size), getattr(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2)), "acl.rt.memcpy device_to_host")

    def _cleanup_request(self, request: _NativeRequest) -> bool:
        if self.acl is None:
            return False
        ok = True
        if self.stream is not None:
            ok = _safe_call(getattr(self.acl.rt, "synchronize_stream", None), self.stream, label="acl.rt.synchronize_stream") and ok
        for dataset in (request.input_dataset, request.output_dataset):
            ok = _safe_call(getattr(self.acl.mdl, "destroy_dataset", None), dataset, label="acl.mdl.destroy_dataset") and ok
        for _, data in request.allocations:
            ok = _safe_call(getattr(self.acl, "destroy_data_buffer", None), data, label="acl.destroy_data_buffer") and ok
        for pointer, _ in request.allocations:
            ok = _safe_call(getattr(self.acl.rt, "free", None), pointer, label="acl.rt.free") and ok
        return ok

    def close(self) -> None:
        if self.acl is None:
            return
        if self._active is not None:
            try:
                self.end_request(self._active)
            except Exception:
                self.cleanup_failed = True
        steps = (("model_id", getattr(self.acl.mdl, "unload", None)), ("desc", getattr(self.acl.mdl, "destroy_desc", None)), ("stream", getattr(self.acl.rt, "destroy_stream", None)), ("context", getattr(self.acl.rt, "destroy_context", None)))
        for name, function in steps:
            handle = getattr(self, name)
            if handle is None:
                continue
            if not _safe_call(function, handle, label=f"ACL cleanup {name}"):
                self.cleanup_failed = True
                self.poisoned = True
                return
            setattr(self, name, None)
        # Do not reset the shared Ascend device here.  Other validated case9
        # services may be using the same NPU; resetting it would invalidate
        # their contexts and turn a request cleanup into a board-wide action.
        if not _safe_call(getattr(self.acl, "finalize", None), label="acl.finalize"):
            self.cleanup_failed = True
            self.poisoned = True
            return
        self.acl = None
        self._opened = False


def _tensor_bytes(item: RuntimeTensorDescriptor) -> int:
    sizes = {"int64": 8, "float32": 4, "float16": 2}
    try:
        size = sizes[item.dtype]
    except KeyError as exc:
        raise RuntimeUnavailable(f"unsupported tensor dtype {item.dtype}") from exc
    for dimension in item.shape:
        size *= int(dimension)
    return size


def _host_pointer(acl: Any, array: Any) -> int:
    util = getattr(acl, "util", None)
    converter = getattr(util, "numpy_to_ptr", None) if util is not None else None
    return int(converter(array)) if callable(converter) else int(array.ctypes.data)


def _split_acl(value: Any) -> Tuple[Any, Any]:
    return (value[0], value[1]) if isinstance(value, tuple) and len(value) == 2 else (value, 0)


def _first_acl(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2:
        _acl_check(value[1], "ACL descriptor query")
        return value[0]
    return value


def _parse_dims(value: Any) -> Tuple[int, ...]:
    value = _first_acl(value)
    if hasattr(value, "dims"):
        value = value.dims
    if isinstance(value, Mapping):
        value = value.get("dims", value.get("shape"))
    if not isinstance(value, (list, tuple)):
        raise RuntimeUnavailable("ACL descriptor dimensions are unreadable")
    dims: List[int] = []
    for item in value:
        item = getattr(item, "dim", item)
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise RuntimeUnavailable("ACL descriptor contains a dynamic/non-positive dimension")
        dims.append(int(item))
    return tuple(dims)


def _acl_dtype(acl: Any, value: int) -> str:
    mapping = {int(getattr(acl, "ACL_FLOAT", 0)): "float32", int(getattr(acl, "ACL_FLOAT16", 1)): "float16", int(getattr(acl, "ACL_INT64", 9)): "int64"}
    if int(value) not in mapping:
        raise RuntimeUnavailable(f"unsupported ACL data type {value}")
    return mapping[int(value)]


def _acl_check(value: Any, operation: str) -> None:
    if isinstance(value, tuple) and len(value) == 2:
        value = value[1]
    if value is None:
        return
    if isinstance(value, bool):
        if not value:
            raise RuntimeUnavailable(f"{operation} failed")
    elif isinstance(value, int) and value != 0:
        raise RuntimeUnavailable(f"{operation} failed with ACL status {value}")


def _safe_call(function: Any, *args: Any, label: str, retries: int = 2) -> bool:
    if not callable(function):
        LOGGER.error("%s unavailable", label)
        return False
    for attempt in range(max(1, retries)):
        try:
            _acl_check(function(*args), label)
            return True
        except Exception:
            if attempt + 1 == max(1, retries):
                LOGGER.error("%s failed", label, exc_info=True)
    return False


__all__ = [
    "MODEL_ID", "Qwen25AclRuntime", "NativeQwen25Backend", "RuntimeDescriptor", "RuntimeTensorDescriptor", "TensorDescriptor", "GenerationResult", "BackendStep", "RuntimeUnavailable", "RuntimeBusy", "RuntimeRequestError", "RuntimeExecutionTimeout", "Qwen25RuntimeError", "DEFAULT_MAX_GENERATION_TOKENS", "HARD_MAX_GENERATION_TOKENS", "MAX_EXECUTION_TIMEOUT_SECONDS", "QWEN25_DEFAULT_MAX_GENERATION_TOKENS", "QWEN25_HARD_MAX_GENERATION_TOKENS", "QWEN25_MAX_EXECUTION_TIMEOUT_SECONDS", "detect_soc_version", "verify_artifact_locks", "_sha256_file", "_attention_mask", "_normalize_outputs", "_select_logits", "_greedy_next_id", "_write_token_cache", "_is_token_cache_shape",
]
