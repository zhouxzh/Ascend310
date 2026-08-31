"""Local image archive for manual palmprint testing."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

import cv2
import numpy as np


class CaptureStore:
    """Persist original/ROI images and a JSON metadata index."""

    INDEX_NAME = "index.json"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._set_private_mode(self.root)
        self._lock = threading.RLock()
        self._transaction_depth = threading.local()

    @property
    def index_path(self) -> Path:
        return self.root / self.INDEX_NAME

    @staticmethod
    def _set_private_mode(path: Path) -> None:
        try:
            mode = stat.S_IRUSR | stat.S_IWUSR
            if path.is_dir():
                mode |= stat.S_IXUSR
            os.chmod(path, mode)
        except OSError:
            pass

    @contextmanager
    def _transaction_lock(self) -> Iterator[None]:
        depth = getattr(self._transaction_depth, "value", 0)
        if depth:
            self._transaction_depth.value = depth + 1
            try:
                yield
            finally:
                self._transaction_depth.value = depth
            return
        handle = (self.root / ".captures.lock").open("a+b")
        locked = False
        self._transaction_depth.value = 1
        try:
            if os.name != "nt":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            yield
        finally:
            self._transaction_depth.value = 0
            if locked:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): CaptureStore._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [CaptureStore._jsonable(item) for item in value]
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    @staticmethod
    def _encode_image(image: Any, extension: str, quality: int | None = None) -> bytes:
        if image is None:
            raise ValueError("image is required")
        array = np.asarray(image)
        if array.size == 0:
            raise ValueError("image is empty")
        if array.dtype != np.uint8:
            array = np.clip(array, 0, 255).astype(np.uint8)
        if array.ndim == 2:
            encoded_input = array
        elif array.ndim == 3 and array.shape[2] >= 3:
            encoded_input = cv2.cvtColor(
                np.ascontiguousarray(array[:, :, :3]), cv2.COLOR_RGB2BGR
            )
        else:
            raise ValueError("image must be grayscale or RGB")
        params = []
        if extension.lower() in {".jpg", ".jpeg"} and quality is not None:
            params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
        ok, encoded = cv2.imencode(extension, encoded_input, params)
        if not ok:
            raise ValueError(f"unable to encode {extension} image")
        return encoded.tobytes()

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            CaptureStore._set_private_mode(temporary)
            os.replace(temporary, path)
            CaptureStore._set_private_mode(path)
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except (OSError, ValueError):
                # Windows does not support opening directories for fsync.
                pass
        finally:
            temporary.unlink(missing_ok=True)

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.is_file():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"capture index is unreadable: {self.index_path}") from exc
        if not isinstance(payload, list):
            raise RuntimeError("capture index must contain a list")
        return [item for item in payload if isinstance(item, dict) and item.get("capture_id")]

    def _write_index(self, records: list[dict[str, Any]]) -> None:
        self._atomic_write(
            self.index_path,
            json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def _record_dir(self, capture_id: str) -> Path:
        if not capture_id or Path(capture_id).name != capture_id:
            raise ValueError("invalid capture_id")
        path = (self.root / capture_id).resolve()
        path.relative_to(self.root)
        return path

    def _record_paths(self, capture_id: str) -> dict[str, Path]:
        directory = self._record_dir(capture_id)
        return {
            "directory": directory,
            "original": directory / "original.jpg",
            "roi": directory / "roi.png",
            "metadata": directory / "metadata.json",
        }

    def save(
        self,
        *,
        original: Any,
        roi: Any | None,
        metadata: dict[str, Any],
        capture_id: str | None = None,
    ) -> dict[str, Any]:
        capture_id = capture_id or uuid4().hex
        paths = self._record_paths(capture_id)
        original_payload = self._encode_image(original, ".jpg", 92)
        roi_payload = self._encode_image(roi, ".png") if roi is not None else None
        record = {
            "capture_id": capture_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "recognition",
            "source": "upload",
            "model_id": None,
            "backend": "npu",
            "precision": "mixed_fp16",
            "device": None,
            "requested_resolution": None,
            "actual_resolution": None,
            "original_path": f"captures/{capture_id}/original.jpg",
            "roi_path": f"captures/{capture_id}/roi.png" if roi_payload is not None else None,
            "roi_ok": roi is not None,
            "quality": {},
            "accepted": False,
            "score": 0.0,
            "status": "saved",
            "timing": {},
            "user_id": None,
            "user_name": None,
            "palm_side": None,
            "template_deleted": False,
        }
        record.update(self._jsonable(metadata))
        record["capture_id"] = capture_id
        record["original_path"] = f"captures/{capture_id}/original.jpg"
        record["roi_path"] = f"captures/{capture_id}/roi.png" if roi_payload is not None else None
        try:
            with self._lock:
                with self._transaction_lock():
                    paths["directory"].mkdir(parents=True, exist_ok=False)
                    self._set_private_mode(paths["directory"])
                    self._atomic_write(paths["original"], original_payload)
                    if roi_payload is not None:
                        self._atomic_write(paths["roi"], roi_payload)
                    self._atomic_write(
                        paths["metadata"],
                        json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                    records = self._read_index()
                    records.append(record)
                    self._write_index(records)
        except Exception:
            shutil.rmtree(paths["directory"], ignore_errors=True)
            raise
        return record

    def update_metadata(self, capture_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            with self._transaction_lock():
                records = self._read_index()
                for record in records:
                    if record.get("capture_id") == capture_id:
                        record.update(self._jsonable(updates))
                        paths = self._record_paths(capture_id)
                        self._atomic_write(
                            paths["metadata"],
                            json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
                        )
                        self._write_index(records)
                        return record
        raise KeyError(f"capture not found: {capture_id}")

    def list(
        self,
        *,
        model_id: str | None = None,
        purpose: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            records = self._read_index()
        filtered = [
            record
            for record in records
            if (model_id is None or record.get("model_id") == model_id)
            and (purpose is None or record.get("purpose") == purpose)
        ]
        return list(reversed(filtered[-limit:]))

    def get(self, capture_id: str) -> dict[str, Any]:
        for record in self.list(limit=1_000_000):
            if record.get("capture_id") == capture_id:
                return record
        raise KeyError(f"capture not found: {capture_id}")

    def path_for(self, capture_id: str, kind: str) -> Path:
        record = self.get(capture_id)
        relative = record.get(f"{kind}_path")
        if not relative:
            raise FileNotFoundError(f"capture has no {kind}: {capture_id}")
        path = (self.root.parent / relative).resolve()
        path.relative_to(self.root)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return path

    def mark_template_deleted(self, *, model_id: str, user_id: str) -> int:
        changed = 0
        with self._lock:
            with self._transaction_lock():
                records = self._read_index()
                for record in records:
                    if record.get("model_id") == model_id and record.get("user_id") == user_id:
                        record["template_deleted"] = True
                        record["status"] = "template_deleted"
                        paths = self._record_paths(str(record["capture_id"]))
                        self._atomic_write(
                            paths["metadata"],
                            json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
                        )
                        changed += 1
                if changed:
                    self._write_index(records)
        return changed

    def cleanup(self, capture_id: str) -> bool:
        with self._lock:
            with self._transaction_lock():
                records = self._read_index()
                remaining = [item for item in records if item.get("capture_id") != capture_id]
                if len(remaining) == len(records):
                    return False
                shutil.rmtree(self._record_dir(capture_id))
                self._write_index(remaining)
                return True
