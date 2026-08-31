"""Dataset inventory, integrity checks, and cross-session record parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Iterable
import zipfile

from ..config import ROOT


MANIFEST_PATH = ROOT / "dataset_manifest.json"
PALMMATCH_SUBJECT = re.compile(r"(?i)(?<![a-z0-9])([mf])\s*0*(\d{1,3})(?![a-z0-9])")
PALMMATCH_SIDE = re.compile(r"(?i)(?<![a-z])\b(left|right)\b(?![a-z])")
PALMMATCH_CAPTURE = re.compile(r"(?i)\bei_(\d{10,})\b")


@dataclass(frozen=True)
class PalmRecord:
    path: Path
    identity: str
    session: int
    sample: int
    spectrum: str | None = None


def load_dataset_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported dataset manifest schema")
    return payload


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_entry(dataset_id: str) -> dict:
    for item in load_dataset_manifest()["datasets"]:
        if item["id"] == dataset_id:
            return item
    raise KeyError(f"Unknown dataset: {dataset_id}")


def audit_archive(dataset_id: str) -> dict:
    item = dataset_entry(dataset_id)
    archive = (ROOT / item["archive"]).resolve()
    result = {
        "dataset_id": dataset_id,
        "archive": str(archive),
        "downloaded": archive.is_file(),
        "expected_size": item.get("size"),
        "expected_sha256": item.get("sha256"),
    }
    if archive.is_file():
        result["actual_size"] = archive.stat().st_size
        result["actual_sha256"] = sha256_file(archive)
        result["integrity_ok"] = (
            result["actual_size"] == result["expected_size"]
            and result["actual_sha256"] == result["expected_sha256"]
        )
    else:
        result["integrity_ok"] = False
    return result


def parse_palmmatch_member(name: str) -> dict[str, object]:
    """Parse only auditable identity hints from a PalmMatchDB ZIP member path."""

    path = PurePosixPath(name)
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return {"is_image": False}
    subject_tokens: list[str] = []
    deepest_subject: str | None = None
    for part in path.parts:
        matches = list(PALMMATCH_SUBJECT.finditer(part))
        if matches:
            normalized = [f"{match.group(1).upper()}{int(match.group(2)):03d}" for match in matches]
            subject_tokens.extend(normalized)
            deepest_subject = normalized[-1]
    sides = {match.group(1).lower() for match in PALMMATCH_SIDE.finditer(name)}
    captures = PALMMATCH_CAPTURE.findall(name)
    return {
        "is_image": True,
        "subject": deepest_subject,
        "subject_tokens": sorted(set(subject_tokens)),
        "subject_conflict": len(set(subject_tokens)) > 1,
        "side": next(iter(sides)) if len(sides) == 1 else None,
        "side_conflict": len(sides) > 1,
        "capture_time_proxy": captures[-1] if captures else None,
    }


def audit_palmmatchdb_zip(
    archive_audit: dict | None = None, *, verify_integrity: bool = True
) -> dict:
    """Audit PalmMatchDB without inventing session labels from filenames."""

    item = dataset_entry("palmmatchdb")
    archive = (ROOT / item["archive"]).resolve()
    if archive_audit is None:
        archive_audit = audit_archive("palmmatchdb") if verify_integrity else {
            "downloaded": archive.is_file(),
            "archive": str(archive),
        }
    else:
        # Callers may add this result back onto their own archive report.  Keep
        # a value copy here so JSON report serialization cannot form a cycle.
        archive_audit = dict(archive_audit)
    result: dict[str, object] = {
        "archive": archive_audit,
        "zip_ok": False,
        "accuracy_eligible": False,
        "session_ready": False,
        "reason": "Archive is not available",
    }
    if not archive.is_file() or (verify_integrity and not archive_audit.get("integrity_ok")):
        return result
    try:
        with zipfile.ZipFile(archive) as bundle:
            corrupt_member = bundle.testzip()
            entries = bundle.infolist()
            members = [member.filename for member in entries if not member.is_dir()]
    except (OSError, zipfile.BadZipFile) as error:
        result["reason"] = f"ZIP audit failed: {error}"
        return result

    parsed = [parse_palmmatch_member(name) for name in members]
    images = [item for item in parsed if item["is_image"]]
    subject_resolved = [item for item in images if item["subject"]]
    side_resolved = [item for item in subject_resolved if item["side"]]
    valid = [
        item
        for item in side_resolved
        if not item["subject_conflict"] and not item["side_conflict"]
    ]
    identities = {f"{item['subject']}-{item['side']}" for item in valid}
    capture_groups: dict[str, set[str]] = {}
    for item in valid:
        capture = item["capture_time_proxy"]
        if capture:
            identity = f"{item['subject']}-{item['side']}"
            capture_groups.setdefault(identity, set()).add(str(capture))
    result.update(
        {
            "zip_ok": corrupt_member is None,
            "zip_corrupt_member": corrupt_member,
            "zip_entries": len(entries),
            "non_directory_entries": len(members),
            "image_files": len(images),
            "png_files": sum(
                PurePosixPath(name).suffix.lower() == ".png" for name in members
            ),
            "jpg_files": sum(
                PurePosixPath(name).suffix.lower() in {".jpg", ".jpeg"} for name in members
            ),
            "subject_resolved_images": len(subject_resolved),
            "subject_conflict_images": sum(item["subject_conflict"] for item in images),
            "side_resolved_images": len(side_resolved),
            "side_unresolved_images": len(subject_resolved) - len(side_resolved),
            "side_conflict_images": sum(item["side_conflict"] for item in images),
            "valid_identity_images": len(valid),
            "person_side_identities": len(identities),
            "capture_time_proxy_identities_ge_2": sum(
                len(values) >= 2 for values in capture_groups.values()
            ),
            "session_ready": False,
            "accuracy_eligible": False,
            "reason": (
                "No explicit session/visit/day field exists in the archive; capture timestamps are only a "
                "proxy and are excluded from cross-session accuracy evaluation."
            ),
        }
    )
    return result


def records(dataset_id: str, spectrum: str = "B") -> list[PalmRecord]:
    item = dataset_entry(dataset_id)
    root = (ROOT / item["extract_dir"]).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Dataset is not extracted: {root}")
    parser = item["parser"]
    if parser == "tongji":
        return list(_tongji_records(root))
    if parser == "polyu":
        return list(_polyu_records(root, spectrum.upper()))
    raise ValueError(f"{dataset_id} has no validated identity/session parser")


def _tongji_records(root: Path) -> Iterable[PalmRecord]:
    pattern = re.compile(r"^(\d{5})\.bmp$", re.IGNORECASE)
    for session in (1, 2):
        folder = root / f"session{session}"
        for path in sorted(folder.glob("*.bmp")):
            match = pattern.match(path.name)
            if not match:
                continue
            number = int(match.group(1)) - 1
            yield PalmRecord(
                path=path,
                identity=f"tongji-palm-{number // 10 + 1:03d}",
                session=session,
                sample=number % 10 + 1,
            )


def _polyu_records(root: Path, spectrum: str) -> Iterable[PalmRecord]:
    if spectrum not in {"B", "G", "I", "R"}:
        raise ValueError("PolyU spectrum must be B, G, I, or R")
    pattern = re.compile(r"^([12])_(\d{2})_s\.bmp$", re.IGNORECASE)
    base = root / f"Multispectral_{spectrum}"
    for identity_dir in sorted(base.glob("[0-9][0-9][0-9]")):
        for path in sorted(identity_dir.glob("*.bmp")):
            match = pattern.match(path.name)
            if not match:
                continue
            yield PalmRecord(
                path=path,
                identity=f"polyu-palm-{identity_dir.name}",
                session=int(match.group(1)),
                sample=int(match.group(2)),
                spectrum=spectrum,
            )


def audit_extracted(dataset_id: str, spectrum: str = "B") -> dict:
    item = dataset_entry(dataset_id)
    try:
        parsed = records(dataset_id, spectrum)
    except (FileNotFoundError, ValueError) as exc:
        return {"dataset_id": dataset_id, "ready": False, "reason": str(exc)}
    identities = {record.identity for record in parsed}
    sessions = {record.session for record in parsed}
    counts: dict[str, int] = {}
    for record in parsed:
        key = f"{record.identity}/session{record.session}"
        counts[key] = counts.get(key, 0) + 1
    expected_per_session = int(item["samples_per_identity_per_session"])
    bad_groups = sum(value != expected_per_session for value in counts.values())
    expected_images = int(item["images"])
    if dataset_id == "polyu":
        expected_images //= len(item["spectra"])
    return {
        "dataset_id": dataset_id,
        "ready": (
            len(parsed) == expected_images
            and len(identities) == int(item["palm_identities"])
            and sessions == {1, 2}
            and bad_groups == 0
        ),
        "parsed_images": len(parsed),
        "palm_identities": len(identities),
        "sessions": sorted(sessions),
        "bad_identity_sessions": bad_groups,
        "spectrum": spectrum if dataset_id == "polyu" else None,
    }


def record_as_dict(record: PalmRecord) -> dict:
    result = asdict(record)
    result["path"] = str(record.path)
    return result
