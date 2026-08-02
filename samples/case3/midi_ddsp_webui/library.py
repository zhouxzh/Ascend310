from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Callable, Iterable
from contextlib import contextmanager


SCHEMA_VERSION = 1

REPORT_SUMMARY_FIELDS = (
    "duration_seconds",
    "architecture",
    "expression_inference_count",
    "synthesis_block_count",
    "synthesis_render_mean_ms",
    "synthesis_render_median_ms",
    "synthesis_render_p95_ms",
    "synthesis_render_max_ms",
    "reverb_enabled",
    "reverb_length_samples",
    "dry_peak",
    "dry_rms",
    "reverberated_peak",
    "reverberated_rms",
    "preclip_peak",
    "clipped_samples",
    "audio_peak",
    "audio_rms",
    "underruns",
    "overruns",
    "mix_gain",
    "mix_gain_db",
    "peak_protection_enabled",
    "cache_hit",
    "inference_and_dsp_wall_seconds",
    "render_wall_seconds",
    "playback_wall_seconds",
    "total_wall_seconds",
    "realtime_factor",
    "model_load_seconds",
    "npu_inference_seconds",
    "dsp_seconds",
    "resampling_seconds",
    "write_disk_seconds",
)

METADATA_SUMMARY_FIELDS = (
    "midi_id",
    "midi_name",
    "voice_count",
    "instrument_id",
    "instrument_ids",
    "notes",
    "source_track_count",
    "voice_config_id",
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


class MidiDdspLibrary:
    """Rebuildable SQLite index for rendered MIDI-DDSP artifacts."""

    def __init__(self, report_root: Path, job_root: Path) -> None:
        self.report_root = report_root
        self.job_root = job_root
        self.path = report_root / "library.sqlite3"
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.report_root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            if quick_check != "ok":
                raise sqlite3.DatabaseError(f"Audio library quick_check failed: {quick_check}")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Audio library schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version == 0:
                connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS midi_sources (
                            source_id TEXT PRIMARY KEY,
                            midi_sha256 TEXT NOT NULL UNIQUE,
                            midi_id TEXT,
                            display_name TEXT NOT NULL,
                            duration_seconds REAL NOT NULL DEFAULT 0,
                            note_count INTEGER NOT NULL DEFAULT 0,
                            track_count INTEGER NOT NULL DEFAULT 0,
                            legacy INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS render_versions (
                            render_id TEXT PRIMARY KEY,
                            source_id TEXT NOT NULL REFERENCES midi_sources(source_id),
                            configuration_hash TEXT NOT NULL,
                            version_label TEXT NOT NULL,
                            state TEXT NOT NULL,
                            model_bundle_id TEXT,
                            model_bundle TEXT,
                            voice_instruments_json TEXT,
                            instrument_ids_json TEXT NOT NULL,
                            seed INTEGER NOT NULL DEFAULT 0,
                            output_gain_db REAL NOT NULL DEFAULT 0,
                            tail_seconds REAL NOT NULL DEFAULT 0,
                            sample_rate INTEGER NOT NULL DEFAULT 0,
                            reverb TEXT,
                            wav_relative_path TEXT NOT NULL,
                            report_relative_path TEXT,
                            metadata_json TEXT NOT NULL,
                            report_json TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS render_versions_source_created
                            ON render_versions(source_id, created_at DESC);
                        CREATE TABLE IF NOT EXISTS library_preferences (
                            source_id TEXT PRIMARY KEY REFERENCES midi_sources(source_id) ON DELETE CASCADE,
                            preferred_render_id TEXT REFERENCES render_versions(render_id) ON DELETE SET NULL,
                            updated_at TEXT NOT NULL
                        );
                        PRAGMA user_version = 1;
                        """
                )
            else:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                required = {"midi_sources", "render_versions", "library_preferences"}
                if not required.issubset(tables):
                    raise sqlite3.DatabaseError("Audio library schema tables are incomplete")

    def _quarantine_corrupt_database(self) -> None:
        marker = f".corrupt-{time.time_ns()}"
        for path in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if path.exists():
                path.replace(path.with_name(f"{path.name}{marker}"))

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            try:
                self._initialize_schema()
            except sqlite3.DatabaseError:
                self._quarantine_corrupt_database()
                self._initialize_schema()
            self._initialized = True

    @staticmethod
    def _source_identity(
        metadata: dict[str, object],
        midi_lookup: dict[str, dict[str, object]],
        sha256_file: Callable[[Path], str] | None,
    ) -> tuple[str, str, bool, dict[str, object]]:
        midi_id = str(metadata.get("midi_id") or "")
        midi = midi_lookup.get(midi_id, {})
        digest = str(metadata.get("midi_sha256") or midi.get("sha256") or "")
        if not digest and midi and sha256_file is not None:
            raw_path = midi.get("path")
            if isinstance(raw_path, str) and Path(raw_path).is_file():
                digest = sha256_file(Path(raw_path))
        legacy = not bool(digest)
        if legacy:
            digest = hashlib.sha256(
                f"legacy\0{midi_id}\0{metadata.get('midi_name', '')}".encode("utf-8")
            ).hexdigest()
        source_id = ("legacy-" if legacy else "midi-") + digest[:20]
        return source_id, digest, legacy, midi

    @staticmethod
    def _configuration_hash(metadata: dict[str, object]) -> str:
        fields = {
            key: metadata.get(key)
            for key in (
                "model_bundle_id",
                "voice_analysis_id",
                "voice_instruments",
                "instrument_ids",
                "instrument_id",
                "seed",
                "sample_rate",
                "output_gain_db",
                "tail_seconds",
                "reverb",
                "reverb_ir_sha256",
            )
        }
        return hashlib.sha256(_json(fields).encode("utf-8")).hexdigest()

    @staticmethod
    def _version_label(job: dict[str, object], metadata: dict[str, object]) -> str:
        instruments = metadata.get("instrument_ids")
        if not isinstance(instruments, list):
            instruments = [metadata.get("instrument_id", 0)]
        instrument_label = "/".join(str(value) for value in instruments)
        model = str(metadata.get("model_bundle") or metadata.get("model_bundle_id") or "MIDI-DDSP")
        created_at = str(job.get("created_at") or "").replace("T", " ").replace("Z", "")
        return f"{model} · I{instrument_label} · {created_at}"

    def index_job(
        self,
        job: dict[str, object] | object,
        *,
        midi_lookup: dict[str, dict[str, object]] | None = None,
        sha256_file: Callable[[Path], str] | None = None,
    ) -> bool:
        if not isinstance(job, dict):
            public = getattr(job, "public", None)
            if public is None:
                return False
            job = public()
        kind = str(job.get("kind") or "")
        state = str(job.get("state") or "")
        render_id = str(job.get("id") or "")
        if kind not in {"midi-ddsp-render", "midi-ddsp-play"} or state != "succeeded" or not render_id:
            return False
        wav_path = self.job_root / render_id / "output.wav"
        metadata = dict(job.get("metadata") or {})
        report = metadata.get("report") if isinstance(metadata.get("report"), dict) else None
        lookup = midi_lookup or {}
        source_id, digest, legacy, midi = self._source_identity(metadata, lookup, sha256_file)
        created_at = str(job.get("created_at") or job.get("updated_at") or "")
        updated_at = str(job.get("updated_at") or created_at)
        instrument_ids = metadata.get("instrument_ids")
        if not isinstance(instrument_ids, list):
            instrument_ids = [int(metadata.get("instrument_id", 0))]
        relative_wav = wav_path.resolve().relative_to(self.report_root.resolve()).as_posix()
        report_path = self.job_root / render_id / "report.json"
        relative_report = (
            report_path.resolve().relative_to(self.report_root.resolve()).as_posix()
            if report_path.is_file()
            else None
        )
        with self._lock:
            self.initialize()
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO midi_sources(
                        source_id, midi_sha256, midi_id, display_name, duration_seconds,
                        note_count, track_count, legacy, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(midi_sha256) DO UPDATE SET
                        midi_id=COALESCE(excluded.midi_id, midi_sources.midi_id),
                        display_name=excluded.display_name,
                        duration_seconds=MAX(excluded.duration_seconds, midi_sources.duration_seconds),
                        note_count=MAX(excluded.note_count, midi_sources.note_count),
                        track_count=MAX(excluded.track_count, midi_sources.track_count),
                        updated_at=excluded.updated_at
                    """,
                    (
                        source_id,
                        digest,
                        str(metadata.get("midi_id") or midi.get("id") or ""),
                        str(metadata.get("midi_name") or midi.get("name") or "Untitled MIDI"),
                        _number(metadata.get("duration_seconds"), _number((report or {}).get("duration_seconds"))),
                        int(metadata.get("notes") or midi.get("note_count") or 0),
                        int(metadata.get("source_track_count") or midi.get("track_count") or 0),
                        int(legacy),
                        created_at,
                        updated_at,
                    ),
                )
                actual_source = connection.execute(
                    "SELECT source_id FROM midi_sources WHERE midi_sha256 = ?", (digest,)
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO render_versions(
                        render_id, source_id, configuration_hash, version_label, state,
                        model_bundle_id, model_bundle, voice_instruments_json,
                        instrument_ids_json, seed, output_gain_db, tail_seconds,
                        sample_rate, reverb, wav_relative_path, report_relative_path,
                        metadata_json, report_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(render_id) DO UPDATE SET
                        state=excluded.state,
                        metadata_json=excluded.metadata_json,
                        report_json=excluded.report_json,
                        wav_relative_path=excluded.wav_relative_path,
                        report_relative_path=excluded.report_relative_path,
                        updated_at=excluded.updated_at
                    """,
                    (
                        render_id,
                        actual_source,
                        self._configuration_hash(metadata),
                        self._version_label(job, metadata),
                        state,
                        metadata.get("model_bundle_id"),
                        metadata.get("model_bundle"),
                        _json(metadata.get("voice_instruments")) if metadata.get("voice_instruments") is not None else None,
                        _json(instrument_ids),
                        int(metadata.get("seed") or 0),
                        _number(metadata.get("output_gain_db")),
                        _number(metadata.get("tail_seconds")),
                        int(metadata.get("sample_rate") or 0),
                        metadata.get("reverb"),
                        relative_wav,
                        relative_report,
                        _json({key: value for key, value in metadata.items() if key != "report"}),
                        _json(report) if report is not None else None,
                        created_at,
                        updated_at,
                    ),
                )
        return True

    def synchronize(
        self,
        jobs: Iterable[dict[str, object]],
        midi_files: Iterable[dict[str, object]],
        *,
        sha256_file: Callable[[Path], str] | None = None,
    ) -> None:
        lookup = {str(item.get("id")): item for item in midi_files}
        for job in jobs:
            self.index_job(job, midi_lookup=lookup, sha256_file=sha256_file)

    def _version_public(self, row: sqlite3.Row) -> dict[str, object]:
        wav_path = self.report_root / str(row["wav_relative_path"])
        report_path = (
            self.report_root / str(row["report_relative_path"])
            if row["report_relative_path"]
            else None
        )
        raw_metadata = json.loads(row["metadata_json"])
        raw_report = json.loads(row["report_json"]) if row["report_json"] else None
        metadata = {
            key: raw_metadata[key]
            for key in METADATA_SUMMARY_FIELDS
            if key in raw_metadata
        }
        report = (
            {
                key: raw_report[key]
                for key in REPORT_SUMMARY_FIELDS
                if key in raw_report
            }
            if raw_report is not None
            else None
        )
        return {
            "render_id": row["render_id"],
            "source_id": row["source_id"],
            "configuration_hash": row["configuration_hash"],
            "version_label": row["version_label"],
            "state": row["state"],
            "model_bundle_id": row["model_bundle_id"],
            "model_bundle": row["model_bundle"],
            "voice_instruments": json.loads(row["voice_instruments_json"]) if row["voice_instruments_json"] else None,
            "instrument_ids": json.loads(row["instrument_ids_json"]),
            "seed": row["seed"],
            "output_gain_db": row["output_gain_db"],
            "tail_seconds": row["tail_seconds"],
            "sample_rate": row["sample_rate"],
            "reverb": row["reverb"],
            "available": wav_path.is_file(),
            "artifact": {
                "id": f"{row['render_id']}--output.wav",
                "name": "output.wav",
                "size_bytes": wav_path.stat().st_size if wav_path.is_file() else 0,
            },
            "report_available": bool(report_path and report_path.is_file()),
            "metadata": metadata,
            "report": report,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def versions(self, source_id: str) -> list[dict[str, object]]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM render_versions WHERE source_id = ? ORDER BY created_at DESC, render_id DESC",
                (source_id,),
            ).fetchall()
        if not rows:
            raise KeyError(source_id)
        return [self._version_public(row) for row in rows]

    def list_tracks(self) -> list[dict[str, object]]:
        self.initialize()
        with self._connection() as connection:
            sources = connection.execute(
                """
                SELECT s.*, p.preferred_render_id,
                       COUNT(v.render_id) AS version_count
                FROM midi_sources s
                LEFT JOIN render_versions v ON v.source_id = s.source_id
                LEFT JOIN library_preferences p ON p.source_id = s.source_id
                GROUP BY s.source_id
                ORDER BY MAX(v.created_at) DESC, s.display_name COLLATE NOCASE
                """
            ).fetchall()
        result = []
        for source in sources:
            versions = self.versions(str(source["source_id"]))
            available = [version for version in versions if version["available"]]
            preferred = str(source["preferred_render_id"] or "")
            default = next(
                (version for version in available if version["render_id"] == preferred),
                available[0] if available else None,
            )
            result.append(
                {
                    "source_id": source["source_id"],
                    "midi_sha256": source["midi_sha256"],
                    "midi_id": source["midi_id"],
                    "display_name": source["display_name"],
                    "duration_seconds": source["duration_seconds"],
                    "note_count": source["note_count"],
                    "track_count": source["track_count"],
                    "legacy": bool(source["legacy"]),
                    "version_count": source["version_count"],
                    "available_version_count": len(available),
                    "preferred_render_id": source["preferred_render_id"],
                    "default_render_id": default["render_id"] if default else None,
                    "default_version": default,
                    "updated_at": source["updated_at"],
                }
            )
        return result

    def set_preference(self, source_id: str, render_id: str | None, updated_at: str) -> dict[str, object]:
        self.initialize()
        with self._lock, self._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM midi_sources WHERE source_id = ?", (source_id,)
            ).fetchone() is None:
                raise KeyError(source_id)
            if render_id is not None:
                row = connection.execute(
                    "SELECT source_id FROM render_versions WHERE render_id = ?", (render_id,)
                ).fetchone()
                if row is None or row["source_id"] != source_id:
                    raise ValueError("Preferred render does not belong to this MIDI source")
            connection.execute(
                """
                INSERT INTO library_preferences(source_id, preferred_render_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    preferred_render_id=excluded.preferred_render_id,
                    updated_at=excluded.updated_at
                """,
                (source_id, render_id, updated_at),
            )
        return next(track for track in self.list_tracks() if track["source_id"] == source_id)

    def version(self, render_id: str) -> dict[str, object]:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM render_versions WHERE render_id = ?", (render_id,)
            ).fetchone()
        if row is None:
            raise KeyError(render_id)
        return self._version_public(row)
