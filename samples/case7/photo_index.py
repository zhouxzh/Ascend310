"""SQLite metadata and isolated FAISS indexes for the smart album."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, UnidentifiedImageError

from config import (
    ALBUM_DB_PATH,
    HAAR_MIN_NEIGHBORS,
    HAAR_SCALE_FACTOR,
    IMPORT_DIR,
    INDEX_DIR,
    MAX_IMAGE_PIXELS,
    METADATA_PATH,
    PHOTO_ROOTS,
    SUPPORTED_IMAGE_EXTENSIONS,
    TOP_K_RESULTS,
    UPLOAD_TMP_DIR,
)
from embedding_backend import ModelManager, l2_normalize


SCHEMA_VERSION = 3


class AlbumIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    photo_id: int
    filepath: str
    filename: str
    face_count: int
    score: float
    model_id: str


@dataclass(frozen=True)
class ImportSummary:
    discovered: int = 0
    indexed: int = 0
    unchanged: int = 0
    duplicates: int = 0
    skipped: int = 0
    unavailable: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self):
        return asdict(self)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            if os.path.commonpath((str(resolved), str(root))) == str(root):
                return True
        except ValueError:
            continue
    return False


def _managed_directory(path: Path, roots: Sequence[Path], create: bool = False) -> Path:
    """Resolve an application-owned directory without following an escape.

    Upload destinations are configuration-controlled filesystem paths.  Check
    every existing component before creating the directory so a symlink under
    a release tree cannot redirect personal uploads somewhere unexpected.
    ``roots`` contains only explicitly managed roots; the caller must not pass
    a broad home or Pictures directory here.
    """

    candidate = Path(path).expanduser()
    absolute = Path(os.path.abspath(str(candidate)))
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise AlbumIndexError(f"symbolic links are not accepted in upload directory: {candidate}")
    resolved = absolute.resolve(strict=False)
    if not _inside(resolved, roots):
        raise AlbumIndexError(f"upload directory is outside managed photo roots: {resolved}")
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    # Re-check after mkdir.  This also protects callers from a pre-existing
    # symlink at the leaf on platforms where mkdir is a no-op for directories.
    if resolved.is_symlink() or not _inside(resolved.resolve(strict=False), roots):
        raise AlbumIndexError(f"upload directory escaped managed photo roots: {resolved}")
    return resolved.resolve(strict=False)


def _extract_metadata(path: Path) -> dict:
    """Read lightweight metadata without changing the original image bytes."""
    now = time.time()
    metadata = {
        "capture_time": None,
        "capture_time_source": "upload_time",
        "upload_time": now,
        "width": None,
        "height": None,
        "mime_type": None,
        "tags": "",
    }
    try:
        with Image.open(path) as image:
            metadata["width"] = int(image.width)
            metadata["height"] = int(image.height)
            metadata["mime_type"] = image.get_format_mimetype() or image.format
            exif = image.getexif()
            value = exif.get(36867) or exif.get(306)
            if value:
                text = str(value).replace(" ", "T", 1)
                metadata["capture_time"] = text
                metadata["capture_time_source"] = "exif"
    except (OSError, ValueError):
        pass
    return metadata


class _NumpyIndex:
    """Explicit test-only replacement when FAISS is unavailable locally."""

    def __init__(self, vectors: np.ndarray, ids: np.ndarray):
        self.vectors = vectors
        self.ids = ids

    def search(self, queries: np.ndarray, k: int):
        scores = queries @ self.vectors.T
        order = np.argsort(-scores, axis=1)[:, :k]
        return np.take_along_axis(scores, order, axis=1), self.ids[order]


class AlbumIndex:
    """Metadata source of truth with one vector space per admitted model."""

    def __init__(
        self,
        manager: Optional[ModelManager] = None,
        db_path: Path = Path(ALBUM_DB_PATH),
        index_dir: Path = Path(INDEX_DIR),
        import_dir: Path = Path(IMPORT_DIR),
        photo_roots: Sequence[str] = PHOTO_ROOTS,
        allow_numpy_fallback: bool = False,
        upload_tmp_dir: Path = Path(UPLOAD_TMP_DIR),
    ):
        self.manager = manager
        self.db_path = Path(db_path)
        self.index_dir = Path(index_dir)
        self.photo_roots = tuple(Path(root).expanduser().resolve() for root in photo_roots)
        if not self.photo_roots:
            raise AlbumIndexError("at least one managed photo root is required")
        # Validate the configured destination at startup, but do not create it
        # until an upload arrives.  This keeps read-only service startup free
        # of unexpected filesystem writes.
        self.import_dir = _managed_directory(Path(import_dir), self.photo_roots)
        # Staged multipart files are not photos and must never be discovered
        # when an operator indexes the managed library recursively.
        self.upload_tmp_dir = Path(upload_tmp_dir).expanduser().resolve()
        self.allow_numpy_fallback = bool(allow_numpy_fallback)
        self._lock = threading.RLock()
        self._indexes: Dict[str, object] = {}
        self._face_cascade = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._init_schema()
        self._init_face_detector()
        self._import_legacy_metadata_once()
        self._backfill_photo_metadata()

    def _init_schema(self):
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    face_count INTEGER NOT NULL DEFAULT 0,
                    available INTEGER NOT NULL DEFAULT 1,
                    capture_time TEXT,
                    capture_time_source TEXT NOT NULL DEFAULT 'upload_time',
                    upload_time REAL,
                    width INTEGER,
                    height INTEGER,
                    mime_type TEXT,
                    tags TEXT NOT NULL DEFAULT '',
                    deleted_at REAL,
                    indexed_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (photo_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS embeddings_model_idx ON embeddings(model_id);
                CREATE TABLE IF NOT EXISTS display_state (
                    device_id TEXT PRIMARY KEY,
                    photo_id INTEGER,
                    slot_key TEXT,
                    policy_revision INTEGER NOT NULL DEFAULT 1,
                    selection_revision INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS display_history (
                    device_id TEXT NOT NULL,
                    photo_id INTEGER NOT NULL,
                    shown_at REAL NOT NULL,
                    PRIMARY KEY (device_id, photo_id, shown_at)
                );
                CREATE INDEX IF NOT EXISTS display_history_device_idx ON display_history(device_id, shown_at);
                """
            )
            current = self._connection.execute(
                "SELECT value FROM schema_info WHERE key='schema_version'"
            ).fetchone()
            if current and int(current["value"]) > SCHEMA_VERSION:
                raise AlbumIndexError(f"unsupported album database schema {current['value']}")
            columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(photos)").fetchall()
            }
            migrations = {
                "capture_time": "ALTER TABLE photos ADD COLUMN capture_time TEXT",
                "capture_time_source": "ALTER TABLE photos ADD COLUMN capture_time_source TEXT NOT NULL DEFAULT 'upload_time'",
                "upload_time": "ALTER TABLE photos ADD COLUMN upload_time REAL",
                "width": "ALTER TABLE photos ADD COLUMN width INTEGER",
                "height": "ALTER TABLE photos ADD COLUMN height INTEGER",
                "mime_type": "ALTER TABLE photos ADD COLUMN mime_type TEXT",
                "tags": "ALTER TABLE photos ADD COLUMN tags TEXT NOT NULL DEFAULT ''",
                "deleted_at": "ALTER TABLE photos ADD COLUMN deleted_at REAL",
            }
            for name, statement in migrations.items():
                if name not in columns:
                    self._connection.execute(statement)
            self._connection.execute(
                "UPDATE photos SET upload_time=COALESCE(upload_time, updated_at), capture_time_source=COALESCE(capture_time_source, 'upload_time')"
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO schema_info(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _init_face_detector(self):
        try:
            import cv2
        except ImportError:
            return
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if cascade_path.is_file():
            detector = cv2.CascadeClassifier(str(cascade_path))
            if not detector.empty():
                self._face_cascade = detector

    def _import_legacy_metadata_once(self):
        marker = self._connection.execute(
            "SELECT value FROM schema_info WHERE key='legacy_metadata_imported'"
        ).fetchone()
        if marker:
            return
        legacy_path = Path(METADATA_PATH)
        imported = 0
        if legacy_path.is_file():
            try:
                values = json.loads(legacy_path.read_text(encoding="utf-8"))
                for value in values:
                    path = Path(str(value.get("filepath", "")))
                    if not path.is_file() or not _inside(path, self.photo_roots):
                        continue
                    try:
                        self._upsert_photo(path, int(value.get("face_count", 0)), validate=True)
                    except AlbumIndexError:
                        continue
                    imported += 1
            except (OSError, ValueError, TypeError):
                imported = 0
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO schema_info(key, value) VALUES('legacy_metadata_imported', ?)",
                (json.dumps({"count": imported, "time": time.time()}),),
            )

    def _backfill_photo_metadata(self):
        """Populate schema-v2 fields without changing original photos or vectors."""
        rows = self._connection.execute(
            "SELECT id, filepath, upload_time FROM photos "
            "WHERE width IS NULL OR height IS NULL OR mime_type IS NULL"
        ).fetchall()
        for row in rows:
            path = Path(row["filepath"])
            if not path.is_file() or path.is_symlink():
                continue
            metadata = _extract_metadata(path)
            with self._connection:
                self._connection.execute(
                    """UPDATE photos SET capture_time=COALESCE(capture_time, ?),
                       capture_time_source=CASE WHEN capture_time IS NULL THEN ? ELSE capture_time_source END,
                       upload_time=COALESCE(upload_time, ?), width=?, height=?, mime_type=?, updated_at=?
                       WHERE id=?""",
                    (metadata["capture_time"], metadata["capture_time_source"], metadata["upload_time"],
                     metadata["width"], metadata["height"], metadata["mime_type"], time.time(), row["id"]),
                )

    def _validate_path(self, path: Path, require_allowed_root: bool = True) -> Path:
        path = path.expanduser()
        if path.is_symlink():
            raise AlbumIndexError(f"symbolic links are not accepted: {path}")
        resolved = path.resolve()
        if require_allowed_root and not _inside(resolved, self.photo_roots):
            raise AlbumIndexError(f"path is outside SMART_ALBUM_PHOTO_ROOTS: {resolved}")
        if not resolved.is_file():
            raise AlbumIndexError(f"photo does not exist: {resolved}")
        if resolved.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise AlbumIndexError(f"unsupported image extension: {resolved.suffix}")
        try:
            with Image.open(resolved) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise AlbumIndexError(f"photo exceeds {MAX_IMAGE_PIXELS} pixels: {resolved}")
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise AlbumIndexError(f"photo cannot be decoded: {resolved}") from exc
        return resolved

    def discover(self, directory: str) -> List[Path]:
        root = Path(directory).expanduser()
        if root.is_symlink():
            raise AlbumIndexError(f"symbolic directory is not accepted: {root}")
        root = root.resolve()
        if not root.is_dir():
            raise AlbumIndexError(f"photo directory does not exist: {root}")
        if not _inside(root, self.photo_roots):
            raise AlbumIndexError(f"directory is outside SMART_ALBUM_PHOTO_ROOTS: {root}")
        files = []
        for candidate in root.rglob("*"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if _inside(candidate, (self.upload_tmp_dir,)):
                continue
            if candidate.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                files.append(candidate.resolve())
        return sorted(set(files))

    def _decode_bgr(self, path: Path):
        try:
            import cv2
        except ImportError as exc:
            raise AlbumIndexError("OpenCV is required to index photos") from exc
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise AlbumIndexError(f"OpenCV cannot decode photo: {path}")
        return image

    def _count_faces(self, image_bgr) -> int:
        if self._face_cascade is None:
            return 0
        import cv2

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        faces = self._face_cascade.detectMultiScale(
            gray, scaleFactor=HAAR_SCALE_FACTOR, minNeighbors=HAAR_MIN_NEIGHBORS
        )
        return int(len(faces))

    def _unchanged_photo_id(self, path: Path) -> Optional[int]:
        """Return the existing id when ``path`` is byte-identical and unchanged.

        Validation still happens in :meth:`_validate_path`, but the expensive
        OpenCV decode and Haar pass are unnecessary for an unchanged file.  A
        content hash is checked after the cheap stat comparison so a file that
        was edited while retaining its size and mtime is never treated as
        unchanged.
        """
        existing = self._connection.execute(
            "SELECT id, sha256, size_bytes, mtime_ns, available "
            "FROM photos WHERE filepath=?",
            (str(path),),
        ).fetchone()
        if existing is None or int(existing["available"]) != 1:
            return None
        stat = path.stat()
        if int(existing["size_bytes"]) != int(stat.st_size) or int(existing["mtime_ns"]) != int(stat.st_mtime_ns):
            return None
        if existing["sha256"] != _sha256(path):
            return None
        return int(existing["id"])

    def _upsert_photo(self, path: Path, face_count: int, validate: bool = False, metadata: Optional[dict] = None):
        path = self._validate_path(path) if validate else path.resolve()
        stat = path.stat()
        digest = _sha256(path)
        metadata = dict(_extract_metadata(path), **(metadata or {}))
        existing_digest = self._connection.execute(
            "SELECT * FROM photos WHERE sha256=?", (digest,)
        ).fetchone()
        existing_path = self._connection.execute(
            "SELECT * FROM photos WHERE filepath=?", (str(path),)
        ).fetchone()
        if existing_digest and (not existing_path or existing_digest["id"] != existing_path["id"]):
            return int(existing_digest["id"]), "duplicate"
        now = time.time()
        if existing_path:
            unchanged = (
                existing_path["sha256"] == digest
                and existing_path["size_bytes"] == stat.st_size
                and existing_path["mtime_ns"] == stat.st_mtime_ns
                and existing_path["available"] == 1
            )
            if unchanged:
                return int(existing_path["id"]), "unchanged"
            with self._connection:
                self._connection.execute(
                    """UPDATE photos SET filename=?, sha256=?, size_bytes=?, mtime_ns=?,
                       face_count=?, available=1, capture_time=?, capture_time_source=?, upload_time=?,
                       width=?, height=?, mime_type=?, tags=?, deleted_at=NULL, updated_at=? WHERE id=?""",
                    (path.name, digest, stat.st_size, stat.st_mtime_ns, face_count,
                     metadata.get("capture_time"), metadata.get("capture_time_source", "upload_time"),
                     metadata.get("upload_time", now), metadata.get("width"), metadata.get("height"),
                     metadata.get("mime_type"), metadata.get("tags", ""), now, existing_path["id"]),
                )
                self._connection.execute("DELETE FROM embeddings WHERE photo_id=?", (existing_path["id"],))
            return int(existing_path["id"]), "changed"
        with self._connection:
            cursor = self._connection.execute(
                """INSERT INTO photos(filepath, filename, sha256, size_bytes, mtime_ns,
                   face_count, available, capture_time, capture_time_source, upload_time,
                   width, height, mime_type, tags, indexed_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(path), path.name, digest, stat.st_size, stat.st_mtime_ns, face_count,
                 metadata.get("capture_time"), metadata.get("capture_time_source", "upload_time"),
                 metadata.get("upload_time", now), metadata.get("width"), metadata.get("height"),
                 metadata.get("mime_type"), metadata.get("tags", ""), now, now),
            )
        return int(cursor.lastrowid), "new"

    def _embedding_exists(self, photo_id: int, model_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM embeddings WHERE photo_id=? AND model_id=?",
            (photo_id, model_id),
        ).fetchone() is not None

    def _store_embedding(self, photo_id: int, model_id: str, vector: np.ndarray):
        vector = l2_normalize(vector).astype(np.float32)
        with self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO embeddings(photo_id, model_id, dimension, vector, updated_at)
                   VALUES(?, ?, ?, ?, ?)""",
                (photo_id, model_id, vector.size, vector.tobytes(), time.time()),
            )
        self._indexes.pop(model_id, None)

    def index_directory(
        self,
        directory: str,
        model_ids: Optional[Sequence[str]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        progress_reporter: Optional[Callable[[dict], None]] = None,
    ) -> ImportSummary:
        return self.index_paths(
            self.discover(directory),
            model_ids,
            progress_callback,
            progress_reporter,
        )

    def index_paths(
        self,
        paths: Iterable[Path],
        model_ids: Optional[Sequence[str]] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        progress_reporter: Optional[Callable[[dict], None]] = None,
    ) -> ImportSummary:
        started = time.time()
        paths = list(dict.fromkeys(Path(path) for path in paths))
        if model_ids is None:
            model_ids = self.manager.registry.ids() if self.manager else ()
        if model_ids and self.manager is None:
            raise AlbumIndexError("a ModelManager is required to generate embeddings")

        def report(phase: str, **values):
            if progress_reporter:
                progress_reporter({"phase": phase, **values})

        indexed = unchanged = duplicates = skipped = 0
        # Keep only paths and small metadata in the work list. Decoded BGR
        # arrays can be very large; decode one photo at a time during each
        # model pass so a large folder does not retain every image in RAM.
        work = []
        report(
            "validating",
            files_completed=0,
            files_total=len(paths),
            embedding_completed=0,
            embedding_total=0,
            current_model=None,
        )
        for offset, candidate in enumerate(paths, start=1):
            image = None
            try:
                path = self._validate_path(candidate)
                unchanged_id = self._unchanged_photo_id(path)
                if unchanged_id is not None:
                    # Keep the metadata and existing face count intact.  The
                    # embedding pass below still fills any missing model
                    # vectors for this photo.
                    photo_id, state = unchanged_id, "unchanged"
                else:
                    image = self._decode_bgr(path)
                    photo_id, state = self._upsert_photo(path, self._count_faces(image))
                if state == "duplicate":
                    duplicates += 1
                    continue
                missing = [model_id for model_id in model_ids if not self._embedding_exists(photo_id, model_id)]
                if not missing and state == "unchanged":
                    unchanged += 1
                else:
                    work.append((photo_id, path, set(missing)))
            except Exception:
                skipped += 1
            finally:
                image = None
                report(
                    "validating",
                    files_completed=offset,
                    files_total=len(paths),
                    embedding_completed=0,
                    embedding_total=0,
                    current_model=None,
                    duplicates=duplicates,
                    skipped=skipped,
                )
        failed_photos = set()
        embedding_total = sum(len(missing) for _, _, missing in work)
        embedding_completed = 0
        report(
            "embedding",
            files_completed=len(paths),
            files_total=len(paths),
            embedding_completed=embedding_completed,
            embedding_total=embedding_total,
            current_model=None,
            duplicates=duplicates,
            skipped=skipped,
        )
        # Keep one model active for the whole pass. This avoids loading and
        # unloading multiple OM files for every photograph in a mixed index.
        for model_id in model_ids:
            for photo_id, path, missing in work:
                if model_id not in missing:
                    continue
                image = None
                try:
                    image = self._decode_bgr(path)
                    vector = self.manager.encode_image(model_id, image)
                    self._store_embedding(photo_id, model_id, vector)
                except Exception:
                    failed_photos.add(photo_id)
                finally:
                    image = None
                    embedding_completed += 1
                    report(
                        "embedding",
                        files_completed=len(paths),
                        files_total=len(paths),
                        embedding_completed=embedding_completed,
                        embedding_total=embedding_total,
                        current_model=model_id,
                        duplicates=duplicates,
                        skipped=skipped,
                    )
            self._persist_index(model_id)
        indexed = len(work) - len(failed_photos)
        skipped += len(failed_photos)
        if not model_ids:
            indexed = len(work)
        report(
            "finalizing",
            files_completed=len(paths),
            files_total=len(paths),
            embedding_completed=embedding_completed,
            embedding_total=embedding_total,
            current_model=None,
            duplicates=duplicates,
            skipped=skipped,
        )
        for offset in range(1, len(paths) + 1):
            if progress_callback:
                progress_callback(offset, len(paths))
        self.mark_unavailable()
        return ImportSummary(
            discovered=len(paths),
            indexed=indexed,
            unchanged=unchanged,
            duplicates=duplicates,
            skipped=skipped,
            unavailable=self.stats()["unavailable_photos"],
            elapsed_seconds=time.time() - started,
        )

    def import_uploads(
        self,
        uploads: Iterable[str],
        model_ids: Optional[Sequence[str]] = None,
        progress_reporter: Optional[Callable[[dict], None]] = None,
    ):
        destination = _managed_directory(self.import_dir, self.photo_roots, create=True)
        accepted = []
        duplicates = 0
        # A folder can contain the same bytes under different names.  Track
        # digests within this request as well as in SQLite so the summary
        # reflects the number of actual new photos, not multipart parts.
        seen_digests = set()
        uploads = list(uploads or ())
        if progress_reporter:
            progress_reporter(
                {
                    "phase": "importing",
                    "files_completed": 0,
                    "files_total": len(uploads),
                    "accepted": 0,
                    "duplicates": 0,
                }
            )
        for offset, value in enumerate(uploads, start=1):
            source = self._validate_path(Path(value), require_allowed_root=False)
            digest = _sha256(source)
            if digest in seen_digests:
                duplicates += 1
            else:
                seen_digests.add(digest)
                existing = self._connection.execute(
                    "SELECT filepath FROM photos WHERE sha256=?", (digest,)
                ).fetchone()
                if existing:
                    duplicates += 1
                else:
                    target = destination / f"{digest[:16]}{source.suffix.lower()}"
                    if target.is_symlink():
                        raise AlbumIndexError(f"symbolic upload target is not accepted: {target}")
                    if not target.exists():
                        shutil.copy2(source, target)
                    accepted.append(target)
            if progress_reporter:
                progress_reporter(
                    {
                        "phase": "importing",
                        "files_completed": offset,
                        "files_total": len(uploads),
                        "accepted": len(accepted),
                        "duplicates": duplicates,
                    }
                )
        summary = self.index_paths(
            accepted,
            model_ids=model_ids,
            progress_reporter=progress_reporter,
        )
        return ImportSummary(**{**summary.to_dict(), "duplicates": summary.duplicates + duplicates})

    def mark_unavailable(self) -> int:
        rows = self._connection.execute("SELECT id, filepath, available FROM photos").fetchall()
        changed = 0
        with self._connection:
            for row in rows:
                available = int(Path(row["filepath"]).is_file())
                if available != row["available"]:
                    self._connection.execute(
                        "UPDATE photos SET available=?, updated_at=? WHERE id=?",
                        (available, time.time(), row["id"]),
                    )
                    changed += 1
        if changed:
            self._indexes.clear()
        return changed

    def _rows_for_model(self, model_id: str):
        return self._connection.execute(
            """SELECT p.id, e.dimension, e.vector FROM embeddings e
               JOIN photos p ON p.id=e.photo_id
               WHERE e.model_id=? AND p.available=1 AND p.deleted_at IS NULL ORDER BY p.id""",
            (model_id,),
        ).fetchall()

    def _build_index(self, model_id: str):
        rows = self._rows_for_model(model_id)
        if not rows:
            return None
        dimensions = {int(row["dimension"]) for row in rows}
        if len(dimensions) != 1:
            raise AlbumIndexError(f"mixed embedding dimensions for {model_id}: {dimensions}")
        dimension = dimensions.pop()
        vectors = np.stack([np.frombuffer(row["vector"], np.float32) for row in rows])
        ids = np.asarray([row["id"] for row in rows], dtype=np.int64)
        try:
            import faiss
        except ImportError as exc:
            if not self.allow_numpy_fallback:
                raise AlbumIndexError("faiss-cpu==1.7.4 is required in production") from exc
            return _NumpyIndex(vectors, ids)
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
        index.add_with_ids(np.ascontiguousarray(vectors), ids)
        return index

    def _get_index(self, model_id: str):
        if model_id not in self._indexes:
            self._indexes[model_id] = self._build_index(model_id)
        return self._indexes[model_id]

    def _persist_index(self, model_id: str):
        index = self._get_index(model_id)
        if index is None or isinstance(index, _NumpyIndex):
            return
        try:
            import faiss
        except ImportError:
            return
        target = self.index_dir / f"{model_id}.faiss"
        faiss.write_index(index, str(target))

    def search_vector(self, vector: np.ndarray, model_id: str, k: int = TOP_K_RESULTS):
        if k <= 0 or k > 100:
            raise AlbumIndexError("k must be between 1 and 100")
        index = self._get_index(model_id)
        if index is None:
            return []
        query = np.ascontiguousarray(l2_normalize(vector)[None, :])
        scores, ids = index.search(query, k)
        results = []
        for score, photo_id in zip(scores[0], ids[0]):
            if int(photo_id) < 0:
                continue
            row = self._connection.execute(
                "SELECT * FROM photos WHERE id=? AND available=1 AND deleted_at IS NULL", (int(photo_id),)
            ).fetchone()
            if row:
                results.append(
                    SearchResult(
                        photo_id=int(row["id"]),
                        filepath=row["filepath"],
                        filename=row["filename"],
                        face_count=int(row["face_count"]),
                        score=float(score),
                        model_id=model_id,
                    )
                )
        return results

    def search_text(self, query: str, model_id: str, k: int = TOP_K_RESULTS):
        if self.manager is None:
            raise AlbumIndexError("a ModelManager is required for text search")
        return self.search_vector(self.manager.encode_text(model_id, query), model_id, k)

    def search_image(self, image_bgr, model_id: str, k: int = TOP_K_RESULTS):
        if self.manager is None:
            raise AlbumIndexError("a ModelManager is required for image search")
        return self.search_vector(self.manager.encode_image(model_id, image_bgr), model_id, k)

    def list_photos(self, face_filter: str = "all", limit: Optional[int] = None):
        clause = "available=1 AND deleted_at IS NULL"
        if face_filter == "has_people":
            clause += " AND face_count>0"
        elif face_filter == "no_people":
            clause += " AND face_count=0"
        elif face_filter != "all":
            raise AlbumIndexError(f"unsupported face filter: {face_filter}")
        sql = f"SELECT * FROM photos WHERE {clause} ORDER BY filename COLLATE NOCASE"
        parameters: Tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (int(limit),)
        return [dict(row) for row in self._connection.execute(sql, parameters).fetchall()]

    def clear_embeddings(self, confirmed: bool = False):
        if not confirmed:
            raise AlbumIndexError("clearing indexes requires explicit confirmation")
        with self._connection:
            self._connection.execute("DELETE FROM embeddings")
        self._indexes.clear()
        for path in self.index_dir.glob("*.faiss"):
            path.unlink()

    def get_photo(self, photo_id: int, include_deleted: bool = False):
        clause = "id=?" if include_deleted else "id=? AND available=1 AND deleted_at IS NULL"
        return self._connection.execute(f"SELECT * FROM photos WHERE {clause}", (int(photo_id),)).fetchone()

    def find_by_sha256(self, digest: str):
        return self._connection.execute(
            "SELECT * FROM photos WHERE sha256=? AND available=1 AND deleted_at IS NULL", (str(digest),)
        ).fetchone()

    def update_photo_metadata(self, photo_id: int, metadata: dict):
        allowed = {"capture_time", "capture_time_source", "tags"}
        values = {key: metadata[key] for key in allowed if key in metadata}
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._connection:
            self._connection.execute(
                f"UPDATE photos SET {assignments}, updated_at=? WHERE id=?",
                tuple(values.values()) + (time.time(), int(photo_id)),
            )

    def get_display_state(self, device_id: str):
        row = self._connection.execute(
            "SELECT * FROM display_state WHERE device_id=?", (str(device_id),)
        ).fetchone()
        return dict(row) if row else None

    def save_display_state(self, device_id: str, photo_id: Optional[int], slot_key: Optional[str], policy_revision: int, selection_revision: int):
        now = time.time()
        with self._connection:
            self._connection.execute(
                """INSERT INTO display_state(device_id, photo_id, slot_key, policy_revision, selection_revision, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET photo_id=excluded.photo_id,
                   slot_key=excluded.slot_key, policy_revision=excluded.policy_revision,
                   selection_revision=excluded.selection_revision, updated_at=excluded.updated_at""",
                (str(device_id), int(photo_id) if photo_id is not None else None, slot_key, int(policy_revision), int(selection_revision), now),
            )

    def delete_display_state(self, device_id: str):
        """Remove operational display state for a deleted device registration.

        This never touches ``photos`` or ``embeddings``.  Keeping the cleanup
        next to the display-state helpers prevents a re-registered device from
        inheriting a stale selection/history row while preserving all album
        assets.
        """

        with self._connection:
            self._connection.execute(
                "DELETE FROM display_history WHERE device_id=?", (str(device_id),)
            )
            self._connection.execute(
                "DELETE FROM display_state WHERE device_id=?", (str(device_id),)
            )

    def record_display_history(self, device_id: str, photo_id: int, keep: int = 12):
        with self._connection:
            self._connection.execute(
                "INSERT INTO display_history(device_id, photo_id, shown_at) VALUES(?, ?, ?)",
                (str(device_id), int(photo_id), time.time()),
            )
            rows = self._connection.execute(
                "SELECT shown_at FROM display_history WHERE device_id=? ORDER BY shown_at DESC",
                (str(device_id),),
            ).fetchall()
            for row in rows[max(0, int(keep)):]:
                self._connection.execute(
                    "DELETE FROM display_history WHERE device_id=? AND shown_at=?",
                    (str(device_id), row["shown_at"]),
                )

    def display_history_ids(self, device_id: str, limit: int = 12) -> list[int]:
        rows = self._connection.execute(
            "SELECT photo_id FROM display_history WHERE device_id=? ORDER BY shown_at DESC LIMIT ?",
            (str(device_id), int(limit)),
        ).fetchall()
        return [int(row["photo_id"]) for row in rows]

    def display_history(self, device_id: str, limit: int = 12) -> list[dict]:
        """Return audit-safe display history entries newest first."""
        rows = self._connection.execute(
            "SELECT photo_id, shown_at FROM display_history "
            "WHERE device_id=? ORDER BY shown_at DESC LIMIT ?",
            (str(device_id), int(limit)),
        ).fetchall()
        return [{"photo_id": int(row["photo_id"]), "shown_at": float(row["shown_at"])} for row in rows]

    def rewind_display_history(self, device_id: str, photo_id: int) -> bool:
        """Discard entries newer than ``photo_id`` after a touchscreen back action."""
        with self._connection:
            rows = self._connection.execute(
                "SELECT photo_id, shown_at FROM display_history WHERE device_id=? ORDER BY shown_at DESC",
                (str(device_id),),
            ).fetchall()
            position = next((index for index, row in enumerate(rows) if int(row["photo_id"]) == int(photo_id)), None)
            if position is None:
                return False
            for row in rows[:position]:
                self._connection.execute(
                    "DELETE FROM display_history WHERE device_id=? AND shown_at=?",
                    (str(device_id), row["shown_at"]),
                )
        return True

    def pop_display_history(self, device_id: str, expected_photo_id: Optional[int] = None) -> Optional[int]:
        """Remove and return the current top of a display back stack."""
        with self._connection:
            row = self._connection.execute(
                "SELECT photo_id, shown_at FROM display_history WHERE device_id=? ORDER BY shown_at DESC LIMIT 1",
                (str(device_id),),
            ).fetchone()
            if row is None or (expected_photo_id is not None and int(row["photo_id"]) != int(expected_photo_id)):
                return None
            self._connection.execute(
                "DELETE FROM display_history WHERE device_id=? AND shown_at=?",
                (str(device_id), row["shown_at"]),
            )
        return int(row["photo_id"])

    def delete_photo(self, photo_id: int, confirmed: bool = False) -> dict:
        if not confirmed:
            raise AlbumIndexError("deleting a photo requires explicit confirmation")
        row = self.get_photo(photo_id)
        if row is None:
            raise AlbumIndexError("photo does not exist or is unavailable")
        path = Path(row["filepath"]).resolve()
        managed_import_dir = _managed_directory(self.import_dir, self.photo_roots)
        if not _inside(path, (managed_import_dir,)):
            raise AlbumIndexError("dataset and external photos are read-only")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE photos SET available=0, deleted_at=?, updated_at=? WHERE id=?",
                (time.time(), time.time(), int(photo_id)),
            )
            self._connection.execute("DELETE FROM embeddings WHERE photo_id=?", (int(photo_id),))
        self._indexes.clear()
        if path.is_file():
            path.unlink()
        return {"photo_id": int(photo_id), "deleted": True}

    def clear_model_embeddings(self, model_id: str, confirmed: bool = False) -> int:
        """Remove one admitted model's derived vectors without touching photos."""
        if not confirmed:
            raise AlbumIndexError("clearing a model index requires explicit confirmation")
        if self.manager is None or model_id not in self.manager.registry.ids():
            raise AlbumIndexError(f"model is not available for a rebuild: {model_id}")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM embeddings WHERE model_id=?", (model_id,)
            )
        self._indexes.pop(model_id, None)
        (self.index_dir / f"{model_id}.faiss").unlink(missing_ok=True)
        return int(cursor.rowcount)

    def stats(self):
        total = self._connection.execute("SELECT COUNT(*) AS n FROM photos WHERE deleted_at IS NULL").fetchone()["n"]
        available = self._connection.execute(
            "SELECT COUNT(*) AS n FROM photos WHERE available=1 AND deleted_at IS NULL"
        ).fetchone()["n"]
        faces = self._connection.execute(
            "SELECT COUNT(*) AS n FROM photos WHERE available=1 AND deleted_at IS NULL AND face_count>0"
        ).fetchone()["n"]
        embeddings = {
            row["model_id"]: int(row["n"])
            for row in self._connection.execute(
                "SELECT model_id, COUNT(*) AS n FROM embeddings GROUP BY model_id"
            ).fetchall()
        }
        return {
            "total_photos": int(total),
            "available_photos": int(available),
            "unavailable_photos": int(total - available),
            "photos_with_faces": int(faces),
            "embeddings_by_model": embeddings,
        }

    def close(self):
        self._connection.close()


PhotoIndex = AlbumIndex
