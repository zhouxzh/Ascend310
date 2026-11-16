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
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
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
            "palm_id": None,
            "person_id": None,
            "display_name": None,
            "palm_side": None,
            "roi_pipeline_id": None,
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

    def save_multi(
        self, *, original: Any, rois: Mapping[int, Any], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Archive one frame and its independently indexed palm crops."""
        encoded = {}
        for index, roi in rois.items():
            if not isinstance(index, int) or index < 1:
                raise ValueError("hand index must be a positive integer")
            encoded[index] = self._encode_image(roi, ".png")
        record = self.save(original=original, roi=None, metadata=metadata)
        capture_id = record["capture_id"]
        paths = {}
        try:
            for index, payload in encoded.items():
                filename = f"roi_{index:03d}.png"
                self._atomic_write(self._record_dir(capture_id) / filename, payload)
                paths[str(index)] = f"captures/{capture_id}/{filename}"
            return self.update_metadata(capture_id, roi_paths=paths, roi_ok=bool(paths))
        except Exception:
            self.cleanup(capture_id)
            raise

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
        directory = self._record_dir(capture_id)
        if kind not in {"original", "roi"}:
            raise ValueError("invalid capture kind")
        record = self.get(capture_id)
        relative = record.get(f"{kind}_path")
        if not relative:
            raise FileNotFoundError(f"capture has no {kind}: {capture_id}")
        filename = "original.jpg" if kind == "original" else "roi.png"
        if relative != f"captures/{capture_id}/{filename}":
            raise ValueError("capture path does not match its record")
        path = (directory / filename).resolve()
        path.relative_to(self.root)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return path

    def cleanup_many(self, capture_ids: list[str] | tuple[str, ...] | set[str]) -> int:
        """Delete a bounded set of capture records and all image bytes."""

        requested = {str(capture_id) for capture_id in capture_ids if capture_id}
        if not requested:
            return 0
        with self._lock:
            with self._transaction_lock():
                records = self._read_index()
                found = [
                    str(record["capture_id"])
                    for record in records
                    if str(record.get("capture_id")) in requested
                ]
                for capture_id in found:
                    shutil.rmtree(self._record_dir(capture_id))
                if found:
                    self._write_index(
                        [
                            record
                            for record in records
                            if str(record.get("capture_id")) not in requested
                        ]
                    )
        return len(found)

    def purge_template(
        self,
        *,
        model_id: str,
        palm_id: str,
        keep_capture_ids: set[str] | None = None,
        roi_pipeline_id: str | None = None,
    ) -> int:
        """Actually remove enrollment images belonging to one stable palm."""

        keep = keep_capture_ids or set()
        with self._lock:
            records = self._read_index()
        matching = [
            str(record["capture_id"])
            for record in records
            if record.get("model_id") == model_id
            and str(record.get("palm_id") or record.get("user_id")) == palm_id
            and str(record.get("capture_id")) not in keep
            and (roi_pipeline_id is None or record.get("roi_pipeline_id") == roi_pipeline_id)
        ]
        return self.cleanup_many(matching)

    def mark_template_deleted(self, *, model_id: str, user_id: str) -> int:
        """Compatibility alias: template deletion now removes biometric files."""

        return self.purge_template(model_id=model_id, palm_id=user_id)

    def cleanup(self, capture_id: str) -> bool:
        return bool(self.cleanup_many([capture_id]))


class DetectorDebugCaptureStore:
    """Bounded, opt-in archive of full frames used by the live detector.

    This store is intentionally separate from enrollment/recognition captures.
    It is disabled by default, creates a per-process run directory only after
    its first write, and never writes images when disabled. Each saved frame has
    an original JPEG, a box/label overlay JPEG, and a JSON sidecar so a later
    inspection can relate a left/right decision to the raw detector output.
    """

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool,
        max_frames: int,
        min_interval_seconds: float,
    ) -> None:
        if max_frames < 1:
            raise ValueError("max_frames must be positive")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self.root = Path(root).expanduser().resolve()
        self.enabled = bool(enabled)
        self.max_frames = int(max_frames)
        self.min_interval_seconds = float(min_interval_seconds)
        self._lock = threading.RLock()
        self._run_dir: Path | None = None
        self._stored_frames = 0
        self._last_saved_monotonic = 0.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "stored_frames": self._stored_frames,
                "max_frames": self.max_frames,
                "min_interval_seconds": self.min_interval_seconds,
                "run_id": self._run_dir.name if self._run_dir is not None else None,
            }

    def _ensure_run_dir(self) -> Path:
        if self._run_dir is None:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            CaptureStore._set_private_mode(self.root)
            name = datetime.now(timezone.utc).strftime("detector-debug-%Y%m%dT%H%M%SZ")
            run_dir = (self.root / f"{name}-{uuid4().hex[:8]}").resolve()
            run_dir.relative_to(self.root)
            run_dir.mkdir(mode=0o700)
            CaptureStore._set_private_mode(run_dir)
            self._run_dir = run_dir
        return self._run_dir

    @staticmethod
    def _annotate(image_rgb: Any, detections: list[dict[str, Any]]) -> np.ndarray:
        image = np.asarray(image_rgb)
        if image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("debug image must be RGB")
        rgb = np.ascontiguousarray(image[:, :, :3], dtype=np.uint8)
        canvas = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        height, width = canvas.shape[:2]
        for item in detections:
            box = item.get("bbox_xyxy") if isinstance(item, dict) else None
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                left, top, right, bottom = (int(round(float(value))) for value in box)
            except (TypeError, ValueError):
                continue
            left, right = sorted((max(0, min(left, width - 1)), max(0, min(right, width - 1))))
            top, bottom = sorted((max(0, min(top, height - 1)), max(0, min(bottom, height - 1))))
            if right <= left or bottom <= top:
                continue
            side = str(item.get("detected_palm_side", "unknown"))
            score = float(item.get("detection_score", 0.0))
            color = (255, 170, 0) if side == "left" else (255, 120, 90) if side == "right" else (180, 180, 180)
            cv2.rectangle(canvas, (left, top), (right, bottom), color, 2)
            cv2.putText(
                canvas,
                f"{side} {score:.3f}",
                (left, max(16, top - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)

    def maybe_save(
        self,
        *,
        image_rgb: Any,
        device: str,
        requested_resolution: str,
        detections: list[dict[str, Any]],
        detection_ms: float,
        detector_debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {**self.status(), "saved": False, "reason": "disabled"}
        now_monotonic = time.monotonic()
        with self._lock:
            if self._stored_frames >= self.max_frames:
                return {**self.status(), "saved": False, "reason": "frame_limit_reached"}
            if now_monotonic - self._last_saved_monotonic < self.min_interval_seconds:
                return {**self.status(), "saved": False, "reason": "interval_not_elapsed"}
            image = np.asarray(image_rgb)
            if image.ndim != 3 or image.shape[2] < 3:
                raise ValueError("debug image must be RGB")
            height, width = image.shape[:2]
            run_dir = self._ensure_run_dir()
            sequence = self._stored_frames + 1
            stem = f"frame-{sequence:04d}"
            original_path = run_dir / f"{stem}.jpg"
            overlay_path = run_dir / f"{stem}-overlay.jpg"
            metadata_path = run_dir / f"{stem}.json"
            metadata = {
                "schema_version": 1,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "device": str(device),
                "requested_resolution": str(requested_resolution),
                "actual_resolution": f"{int(width)}x{int(height)}",
                "detection_ms": float(detection_ms),
                "detections": CaptureStore._jsonable(detections),
                "detector_debug": CaptureStore._jsonable(detector_debug),
                "original_file": original_path.name,
                "overlay_file": overlay_path.name,
            }
            try:
                CaptureStore._atomic_write(original_path, CaptureStore._encode_image(image, ".jpg", 92))
                CaptureStore._atomic_write(
                    overlay_path,
                    CaptureStore._encode_image(self._annotate(image, detections), ".jpg", 92),
                )
                CaptureStore._atomic_write(
                    metadata_path,
                    json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
                )
            except Exception:
                original_path.unlink(missing_ok=True)
                overlay_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                raise
            self._stored_frames = sequence
            self._last_saved_monotonic = now_monotonic
            return {**self.status(), "saved": True, "reason": "saved", "sequence": sequence}
