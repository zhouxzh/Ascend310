"""Atomic per-model palm template storage and NumPy search."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import struct
import stat
import threading
import time
from contextlib import contextmanager
from typing import Any, TYPE_CHECKING
from uuid import uuid4

import numpy as np

from ..config import TEMPLATE_DIR, TOP_K
if TYPE_CHECKING:
    from ..runtime.adapters import PalmAdapter


class TemplateStore:
    _PLAIN_MAGIC = b"PWST1\x00"
    _ENCRYPTED_MAGIC = b"PWEN1\x00"
    _AAD = b"palmprint-template-v1"
    _POLICY_ERRORS = (
        "Plaintext template store is not allowed",
        "Encrypted template files are not supported by the internal-test release",
        "Template encryption requires the cryptography package",
    )

    def __init__(
        self,
        root: Path = TEMPLATE_DIR,
        *,
        key_file: Path | None = None,
        require_encryption: bool | None = None,
        defer_key_error: bool = False,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._set_private_mode(self.root)
        self.lock = threading.RLock()
        self._transaction_depth = threading.local()
        # This frozen release is for internal manual testing.  Keep template
        # bytes inspectable and local; encryption is deferred to a future
        # production release and is not activated by environment variables.
        self.key_file = None
        self.require_encryption = False
        self._key = None
        self._persistence_blocked = False

    @staticmethod
    def _set_private_mode(path: Path) -> None:
        """Keep template files readable only by the service account."""

        try:
            mode = stat.S_IRUSR | stat.S_IWUSR
            if path.is_dir():
                mode |= stat.S_IXUSR
            os.chmod(path, mode)
        except OSError:
            # Windows and some mounted filesystems do not implement POSIX modes.
            pass

    @classmethod
    def _read_key(cls, path: Path | None) -> bytes | None:
        if path is None:
            return None
        try:
            if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise PermissionError(
                    f"Template key file must not be group/world accessible: {path}"
                )
            raw = path.read_bytes().strip()
        except OSError as exc:
            raise RuntimeError(f"Unable to read template key file: {path}") from exc
        if len(raw) == 32:
            return raw
        try:
            decoded = base64.b64decode(raw, validate=True)
            if len(decoded) == 32:
                return decoded
        except (ValueError, TypeError):
            pass
        try:
            decoded = bytes.fromhex(raw.decode("ascii"))
            if len(decoded) == 32:
                return decoded
        except (UnicodeDecodeError, ValueError):
            pass
        raise ValueError("Template key must be 32 raw bytes, hex, or base64")

    @classmethod
    def _crypto(cls, key: bytes):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise RuntimeError(
                "Template encryption requires the cryptography package"
            ) from exc
        return AESGCM(key)

    def _paths(self, model_id: str) -> tuple[Path, Path]:
        return self.root / f"{model_id}.npz", self.root / f"{model_id}.json"

    def _store_path(self, model_id: str) -> Path:
        return self.root / f"{model_id}.pstore"

    def _backup_path(self, model_id: str) -> Path:
        """Return the last known-good generation for crash recovery."""

        return self.root / f"{model_id}.pstore.bak"

    def _previous_path(self, model_id: str) -> Path:
        """Return the generation before the current recovery snapshot."""

        return self.root / f"{model_id}.pstore.previous"

    def _version_path(self, model_id: str) -> Path:
        return self.root / f"{model_id}.pstore.version.json"

    def readiness(self) -> dict[str, Any]:
        """Return non-sensitive storage readiness information for health APIs."""

        with self.lock:
            files = list(self.root.iterdir()) if self.root.is_dir() else []
            legacy_files = [
                path
                for path in files
                if path.suffix.lower() in {".npz", ".json"}
                and not path.name.endswith(".pstore.version.json")
            ]
            store_files = [path for path in files if path.suffix.lower() == ".pstore"]
            encrypted_files = []
            for path in store_files:
                try:
                    if path.read_bytes().startswith(self._ENCRYPTED_MAGIC):
                        encrypted_files.append(path)
                except OSError:
                    encrypted_files.append(path)
            if self.require_encryption and self._key is None:
                reason = "missing_external_key"
                ready = False
            elif self.require_encryption and legacy_files:
                reason = "legacy_plaintext_store_present"
                ready = False
            else:
                reason = "ready"
                ready = True
            return {
                "ready": ready,
                "encryption_required": self.require_encryption,
                "key_configured": self._key is not None,
                "legacy_plaintext_present": bool(legacy_files),
                "store_count": len(store_files),
                "encrypted_store_count": len(encrypted_files),
                "reason": reason,
            }

    @classmethod
    def _pack(cls, codes: np.ndarray, metadata: list[dict[str, Any]]) -> bytes:
        encoded_codes = io.BytesIO()
        np.savez_compressed(encoded_codes, codes=np.ascontiguousarray(codes))
        encoded_metadata = json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        encoded_array = encoded_codes.getvalue()
        header = struct.pack(">QQ", len(encoded_array), len(encoded_metadata))
        return cls._PLAIN_MAGIC + header + encoded_array + encoded_metadata

    @classmethod
    def _unpack(cls, payload: bytes) -> tuple[np.ndarray, list[dict[str, Any]], bool]:
        encrypted = payload.startswith(cls._ENCRYPTED_MAGIC)
        if encrypted:
            raise ValueError("encrypted payload requires a key")
        if not payload.startswith(cls._PLAIN_MAGIC):
            raise ValueError("unknown template store format")
        header_size = len(cls._PLAIN_MAGIC) + 16
        if len(payload) < header_size:
            raise ValueError("truncated template store")
        codes_size, metadata_size = struct.unpack(
            ">QQ", payload[len(cls._PLAIN_MAGIC) : header_size]
        )
        codes_start = header_size
        metadata_start = codes_start + codes_size
        metadata_end = metadata_start + metadata_size
        if metadata_end != len(payload):
            raise ValueError("template store length mismatch")
        with np.load(io.BytesIO(payload[codes_start:metadata_start]), allow_pickle=False) as data:
            codes = np.asarray(data["codes"])
        metadata = json.loads(payload[metadata_start:metadata_end].decode("utf-8"))
        if not isinstance(metadata, list):
            raise ValueError("template metadata must be a list")
        return codes, metadata, False

    def _encode_store(self, codes: np.ndarray, metadata: list[dict[str, Any]]) -> bytes:
        plain = self._pack(codes, metadata)
        if self._key is None:
            return plain
        nonce = os.urandom(12)
        encrypted = self._crypto(self._key).encrypt(nonce, plain, self._AAD)
        return self._ENCRYPTED_MAGIC + nonce + encrypted

    def _decode_store(self, payload: bytes) -> tuple[np.ndarray, list[dict[str, Any]]]:
        if payload.startswith(self._ENCRYPTED_MAGIC):
            raise RuntimeError(
                "Encrypted template files are not supported by the internal-test release"
            )
        if self.require_encryption:
            raise RuntimeError("Plaintext template store is not allowed in release mode")
        decoded, metadata, _ = self._unpack(payload)
        return decoded, metadata

    def _load_legacy(self, model_id: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
        codes_path, metadata_path = self._paths(model_id)
        if not codes_path.is_file() and not metadata_path.is_file():
            return np.empty((0, 0), dtype=np.float32), []
        if self.require_encryption:
            raise RuntimeError(f"Plaintext template store is not allowed for {model_id}")
        if not codes_path.is_file() or not metadata_path.is_file():
            raise RuntimeError(f"Incomplete template store for {model_id}")
        with np.load(codes_path, allow_pickle=False) as payload:
            codes = np.asarray(payload["codes"])
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if codes.ndim != 2 or codes.shape[0] != len(metadata):
            raise RuntimeError(f"Template metadata mismatch for {model_id}")
        return codes, metadata

    def load(self, model_id: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
        store_path = self._store_path(model_id)
        with self.lock:
            if store_path.is_file():
                try:
                    payload = store_path.read_bytes()
                    codes, metadata = self._decode_store(payload)
                    self._validate_loaded(codes, metadata, model_id)
                except Exception as current_error:
                    if not self._recovery_allowed(current_error):
                        raise
                    with self._transaction_lock():
                        # Another process may have completed a commit while
                        # this reader was decoding the old generation.
                        try:
                            payload = store_path.read_bytes()
                            codes, metadata = self._decode_store(payload)
                            self._validate_loaded(codes, metadata, model_id)
                            return codes, metadata
                        except Exception as refreshed_error:
                            if not self._recovery_allowed(refreshed_error):
                                raise
                            recovered = self._recover(model_id, refreshed_error)
                            codes, metadata, payload, generation, source = recovered
                            previous_payload = None
                            previous_path = self._previous_path(model_id)
                            if previous_path.is_file():
                                previous_payload = previous_path.read_bytes()
                            self._atomic_write(store_path, payload)
                            self._atomic_write(self._backup_path(model_id), payload)
                            self._write_version(
                                model_id,
                                generation,
                                payload,
                                backup_payload=payload,
                                previous_payload=previous_payload,
                                recovered_from=source.name,
                            )
            else:
                with self._transaction_lock():
                    try:
                        codes, metadata, payload, generation, source = self._recover(
                            model_id, None
                        )
                        previous_payload = None
                        previous_path = self._previous_path(model_id)
                        if previous_path.is_file():
                            previous_payload = previous_path.read_bytes()
                        self._atomic_write(store_path, payload)
                        self._atomic_write(self._backup_path(model_id), payload)
                        self._write_version(
                            model_id,
                            generation,
                            payload,
                            backup_payload=payload,
                            previous_payload=previous_payload,
                            recovered_from=source.name,
                        )
                    except FileNotFoundError:
                        codes, metadata = self._load_legacy(model_id)
                        self._validate_loaded(codes, metadata, model_id)
            return codes, metadata

    def _recover(
        self, model_id: str, primary_error: Exception | None
    ) -> tuple[np.ndarray, list[dict[str, Any]], bytes, int, Path]:
        """Decode the newest valid recovery snapshot without accepting plaintext.

        ``.bak`` is refreshed only after a successful commit, while
        ``.previous`` retains one older generation for a second-level fallback.
        """

        current_generation = self._read_generation(model_id)
        version = self._read_version(model_id)
        candidates = (
            (
                self._backup_path(model_id),
                current_generation,
                version.get("backup_sha256") or version.get("sha256"),
            ),
            (
                self._previous_path(model_id),
                max(0, current_generation - 1),
                version.get("previous_sha256"),
            ),
        )
        errors: list[Exception] = []
        for path, generation, expected_sha in candidates:
            if not path.is_file():
                continue
            try:
                payload = path.read_bytes()
                if expected_sha and hashlib.sha256(payload).hexdigest() != expected_sha:
                    raise ValueError(f"Recovery snapshot hash mismatch: {path.name}")
                codes, metadata = self._decode_store(payload)
                self._validate_loaded(codes, metadata, model_id)
                return codes, metadata, payload, generation, path
            except Exception as exc:
                errors.append(exc)
        if primary_error is not None:
            if primary_error.__class__.__name__ in {"InvalidTag", "InvalidSignature"}:
                raise RuntimeError(
                    f"Refusing to overwrite or use an unauthenticated template store for {model_id}"
                ) from primary_error
            raise primary_error
        if errors:
            raise errors[-1]
        raise FileNotFoundError(self._store_path(model_id))

    @classmethod
    def _recovery_allowed(cls, error: Exception) -> bool:
        return not (
            isinstance(error, RuntimeError)
            and any(marker in str(error) for marker in cls._POLICY_ERRORS)
        )

    @staticmethod
    def _safe_to_replace_unreadable(error: Exception) -> bool:
        """Only replace files with clearly structural, not cryptographic, damage."""

        if error.__class__.__name__ in {"InvalidTag", "InvalidSignature"}:
            return False
        if isinstance(error, RuntimeError):
            return False
        return isinstance(error, (OSError, ValueError, EOFError, struct.error, KeyError))

    @staticmethod
    def _validate_loaded(
        codes: np.ndarray, metadata: list[dict[str, Any]], model_id: str
    ) -> None:
        if codes.ndim != 2 or codes.shape[0] != len(metadata):
            raise RuntimeError(f"Template metadata mismatch for {model_id}")

    def _read_version(self, model_id: str) -> dict[str, Any]:
        try:
            payload = json.loads(
                self._version_path(model_id).read_text(encoding="utf-8")
            )
            return dict(payload) if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _read_generation(self, model_id: str) -> int:
        payload = self._read_version(model_id)
        try:
            return max(0, int(payload.get("generation", 0)))
        except (TypeError, ValueError):
            return 0

    def _write_version(
        self,
        model_id: str,
        generation: int,
        payload: bytes,
        *,
        backup_payload: bytes | None = None,
        previous_payload: bytes | None = None,
        recovered_from: str | None = None,
    ) -> None:
        version = json.dumps(
            {
                "generation": generation,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "backup_sha256": hashlib.sha256(
                    backup_payload if backup_payload is not None else payload
                ).hexdigest(),
                "previous_sha256": (
                    hashlib.sha256(previous_payload).hexdigest()
                    if previous_payload is not None
                    else None
                ),
                "format": "pstore-v1",
                "recovered_from": recovered_from,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self._atomic_write(self._version_path(model_id), version)

    def _atomic_write(self, destination: Path, payload: bytes) -> None:
        """Write a private file and atomically publish its name."""

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root, suffix=".tmp", delete=False
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._set_private_mode(temp_path)
            os.replace(temp_path, destination)
            self._set_private_mode(destination)
            self._fsync_directory()
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _fsync_directory(self) -> None:
        try:
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Windows and some filesystem mounts do not expose directory fsync.
            pass

    @contextmanager
    def _transaction_lock(self):
        """Serialize read-modify-write transactions across board processes."""

        depth = int(getattr(self._transaction_depth, "value", 0))
        if depth:
            self._transaction_depth.value = depth + 1
            try:
                yield
            finally:
                self._transaction_depth.value = depth
            return
        lock_path = self.root / ".templates.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        self._set_private_mode(lock_path)
        locked = False
        try:
            self._transaction_depth.value = 1
            if os.name != "nt":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            # Windows deployments use the documented single-process service
            # mode; the owning RLock still protects all in-process writers.
            yield
        finally:
            self._transaction_depth.value = 0
            if locked:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def enroll(
        self,
        model_id: str,
        codes: list[np.ndarray],
        user_name: str,
        palm_side: str,
        user_id: str | None = None,
    ) -> str:
        if not codes:
            raise ValueError("At least one code is required")
        if self._persistence_blocked:
            raise RuntimeError(
                "Template persistence is disabled by the current release policy"
            )
        identity_id = user_id or uuid4().hex
        batch = np.stack([np.asarray(code) for code in codes], axis=0)
        with self.lock:
            with self._transaction_lock():
                existing, metadata = self.load(model_id)
                if existing.size and existing.shape[1:] != batch.shape[1:]:
                    raise ValueError("Template dimension changed; clear the old model store first")
                combined = batch if not existing.size else np.concatenate([existing, batch], axis=0)
                now = time.time()
                metadata.extend(
                    {
                        "user_id": identity_id,
                        "user_name": user_name.strip(),
                        "palm_side": palm_side,
                        "enrolled_at": now,
                    }
                    for _ in codes
                )
                self._save(model_id, combined, metadata)
        return identity_id

    def search(
        self,
        model_id: str,
        adapter: PalmAdapter,
        query: np.ndarray,
        *,
        threshold: float,
        top_k: int = TOP_K,
    ) -> dict[str, Any]:
        codes, metadata = self.load(model_id)
        if not metadata:
            return {
                "accepted": False,
                "score": 0.0,
                "matches": [],
                "reason": "template store is empty",
            }
        sample_scores = adapter.compare(query, codes)
        grouped: dict[str, dict[str, Any]] = {}
        for score, item in zip(sample_scores, metadata):
            identity = str(item["user_id"])
            record = grouped.setdefault(identity, {**item, "sample_scores": []})
            record["sample_scores"].append(float(score))
        ranked = []
        for record in grouped.values():
            values = sorted(record.pop("sample_scores"), reverse=True)[:3]
            ranked.append({**record, "score": float(np.mean(values)), "samples": len(values)})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        best = ranked[0]
        accepted = bool(best["score"] >= threshold)
        return {
            "accepted": accepted,
            "score": best["score"],
            "user_id": best["user_id"],
            "user_name": best["user_name"],
            "palm_side": best["palm_side"],
            "matches": ranked[:top_k],
            "reason": "accepted" if accepted else "below threshold",
        }

    def users(self, model_id: str) -> list[dict[str, Any]]:
        _, metadata = self.load(model_id)
        users: dict[str, dict[str, Any]] = {}
        for item in metadata:
            identity = str(item["user_id"])
            record = users.setdefault(identity, {**item, "samples": 0})
            record["samples"] += 1
        return sorted(users.values(), key=lambda item: (item["user_name"], item["palm_side"]))

    def remove(self, model_id: str, user_id: str) -> bool:
        with self.lock:
            with self._transaction_lock():
                codes, metadata = self.load(model_id)
                keep = [index for index, item in enumerate(metadata) if str(item["user_id"]) != user_id]
                if len(keep) == len(metadata):
                    return False
                width = codes.shape[1] if codes.ndim == 2 else 0
                next_codes = codes[keep] if keep else np.empty((0, width), dtype=codes.dtype)
                self._save(model_id, next_codes, [metadata[index] for index in keep])
                return True

    def _save(self, model_id: str, codes: np.ndarray, metadata: list[dict[str, Any]]) -> None:
        if self._persistence_blocked:
            raise RuntimeError(
                "Template persistence is disabled by the current release policy"
            )
        store_path = self._store_path(model_id)
        self.root.mkdir(parents=True, exist_ok=True)
        payload = self._encode_store(codes, metadata)
        # Validate before publication so a serialization or encryption error
        # can never replace a known-good generation.
        decoded_codes, decoded_metadata = self._decode_store(payload)
        self._validate_loaded(decoded_codes, decoded_metadata, model_id)
        previous = b""
        if store_path.is_file():
            previous = store_path.read_bytes()
            try:
                old_codes, old_metadata = self._decode_store(previous)
                self._validate_loaded(old_codes, old_metadata, model_id)
            except Exception as exc:
                # A corrupt current file must not become the recovery copy.
                if not self._safe_to_replace_unreadable(exc):
                    raise RuntimeError(
                        f"Refusing to overwrite an unreadable template store for {model_id}"
                    ) from exc
                previous = b""
            if previous:
                self._atomic_write(self._previous_path(model_id), previous)
        self._atomic_write(store_path, payload)
        # The backup is the newest committed snapshot.  A separate previous
        # file preserves one generation for forensic/manual rollback.
        self._atomic_write(self._backup_path(model_id), payload)
        self._write_version(
            model_id,
            self._read_generation(model_id) + 1,
            payload,
            backup_payload=payload,
            previous_payload=previous or None,
        )
