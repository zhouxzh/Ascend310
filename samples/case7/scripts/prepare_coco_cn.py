#!/usr/bin/env python3
"""Prepare a fixed COCO-CN retrieval gallery and provenance manifest.

COCO-CN annotations are downloaded from the configured Hugging Face mirror.
The referenced MS-COCO 2014 image bytes and English captions are fetched from
the official COCO endpoints, then recorded as one reproducible COCO-CN test
fixture.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import ssl
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from typing import Optional
from pathlib import Path

from PIL import Image, UnidentifiedImageError


DATASET_ID = "COCO-CN"
COCO_CN_REPO = "AIMClab-RUC/COCO-CN"
COCO_CN_ARCHIVE = "coco-cn-version1805v1.1.tar.gz"
COCO_CN_ARCHIVE_SHA256 = "6c126cd8455363a404806e452ec75066a8fc96d73922d9357d993fcdd1d40b8a"
# The board's bundled CA store rejects the current images.cocodataset.org
# certificate. The official COCO object store also serves these immutable
# archives over HTTP; record the exact URL in the manifest.
COCO_CAPTIONS_URL = "http://images.cocodataset.org/annotations/annotations_trainval2014.zip"
COCO_IMAGE_ROOT = "http://images.cocodataset.org"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"

ENGLISH_QUERIES = (
    ("dog", ("dog",)),
    ("cat", ("cat",)),
    ("horse", ("horse",)),
    ("bicycle", ("bicycle", "bike")),
    ("car", ("car",)),
    ("bus", ("bus",)),
    ("train", ("train",)),
    ("airplane", ("airplane", "plane")),
    ("street", ("street", "road")),
    ("kitchen", ("kitchen",)),
    ("beach", ("beach",)),
    ("park", ("park",)),
    ("snow", ("snow",)),
    ("pizza", ("pizza",)),
    ("cake", ("cake",)),
    ("baseball", ("baseball",)),
    ("laptop", ("laptop", "computer")),
    ("chair", ("chair",)),
    ("table", ("table",)),
    ("boat", ("boat",)),
)

CHINESE_QUERIES = (
    ("狗", ("狗",)),
    ("猫", ("猫",)),
    ("马", ("马",)),
    ("自行车", ("自行车", "单车")),
    ("汽车", ("汽车", "轿车")),
    ("公交车", ("公交车", "公共汽车", "巴士")),
    ("火车", ("火车", "列车")),
    ("飞机", ("飞机", "航空")),
    ("街道", ("街道", "马路")),
    ("厨房", ("厨房",)),
    ("海滩", ("海滩", "沙滩")),
    ("公园", ("公园",)),
    ("雪", ("雪", "滑雪")),
    ("披萨", ("披萨", "比萨")),
    ("蛋糕", ("蛋糕",)),
    ("棒球", ("棒球",)),
    ("电脑", ("电脑", "笔记本")),
    ("椅子", ("椅子", "座椅")),
    ("桌子", ("桌子", "书桌")),
    ("船", ("船", "帆船")),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(
    url: str,
    destination: Path,
    mirror_endpoint: Optional[str] = None,
    insecure_tls: bool = False,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    current_url = url
    for _ in range(8):
        request = urllib.request.Request(current_url, headers={"User-Agent": "ascend-case7/1.0"})
        try:
            context = ssl._create_unverified_context() if insecure_tls else None
            response = urllib.request.urlopen(request, timeout=60, context=context)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 308 or not exc.headers.get("Location"):
                raise
            # hf-mirror.com intentionally returns a 308 to the Hub resolver.
            # The request has already entered through the mirror; follow its
            # signed resolver URL for the actual bytes instead of looping back
            # to the same mirror URL.
            current_url = urllib.parse.urljoin(current_url, exc.headers["Location"])
    else:
        raise RuntimeError(f"too many redirects while downloading {url}")
    if insecure_tls:
        print("[dataset] WARNING: HF mirror TLS certificate verification is disabled")
    with response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    temporary.replace(destination)


def _download_image(record: dict, image_dir: Path) -> tuple[dict, Path, int, int]:
    filename = f"{record['image_id']}.jpg"
    destination = image_dir / filename
    url = _image_url(record["image_id"])
    if not destination.is_file():
        _download(url, destination)
    try:
        with Image.open(destination) as image:
            image.verify()
        with Image.open(destination) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"invalid COCO image {destination}: {exc}") from exc
    return record, destination, width, height


def _safe_extract_tar(archive: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        root = target.resolve()
        for member in members:
            member_path = (target / member.name).resolve()
            if os.path.commonpath((str(root), str(member_path))) != str(root):
                raise RuntimeError(f"unsafe COCO-CN archive member: {member.name}")
        handle.extractall(target)
    directories = [path for path in target.iterdir() if path.is_dir()]
    if len(directories) != 1:
        raise RuntimeError("COCO-CN archive must contain one top-level directory")
    return directories[0]


def _safe_extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        root = target.resolve()
        for name in handle.namelist():
            member_path = (target / name).resolve()
            if os.path.commonpath((str(root), str(member_path))) != str(root):
                raise RuntimeError(f"unsafe COCO captions archive member: {name}")
        handle.extractall(target)


def _parse_cn_lines(path: Path, split_ids: set[str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or "\t" not in line:
                continue
            key, text = line.split("\t", 1)
            image_id = key.split("#", 1)[0]
            if image_id in split_ids and text.strip():
                values.setdefault(image_id, []).append(text.strip())
    return values


def _parse_tags(path: Path, split_ids: set[str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.strip().split()
            if len(parts) < 2 or parts[0] not in split_ids:
                continue
            values[parts[0]] = parts[1:]
    return values


def _parse_english_captions(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values: dict[str, list[str]] = {}
    for item in payload.get("annotations", []):
        image_id = int(item["image_id"])
        caption = str(item.get("caption", "")).strip()
        if not caption:
            continue
        values.setdefault(str(image_id), []).append(caption)
    return values


def _numeric_id(image_id: str) -> str:
    return image_id.rsplit("_", 1)[1]


def _image_url(image_id: str) -> str:
    split = "train2014" if "_train2014_" in image_id else "val2014"
    return f"{COCO_IMAGE_ROOT}/{split}/{image_id}.jpg"


def _matches(record: dict, keywords: tuple[str, ...], language: str) -> bool:
    if language == "en":
        haystack = " ".join(record.get("caption_en", [])).lower()
    else:
        haystack = " ".join(record.get("caption_zh", []) + record.get("tags_zh", []))
    return any(keyword.lower() in haystack for keyword in keywords)


def build_queries(records: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {"en": [], "zh": []}
    for language, definitions in (("en", ENGLISH_QUERIES), ("zh", CHINESE_QUERIES)):
        for query, keywords in definitions:
            matching = [record["image_id"] for record in records if _matches(record, keywords, language)]
            if len(matching) < 3:
                raise RuntimeError(
                    f"query {language}:{query!r} has only {len(matching)} relevant images; "
                    "use a larger COCO-CN split or revise the fixed query list"
                )
            result[language].append(
                {"query": query, "keywords": list(keywords), "relevant_image_ids": matching}
            )
    return result


def prepare(args: argparse.Namespace) -> dict:
    photos_root = Path(args.photos_root).resolve()
    report_dir = Path(args.report_dir).resolve()
    work_dir = report_dir / "source"
    archive = work_dir / COCO_CN_ARCHIVE
    hf_endpoint = (args.hf_endpoint or os.environ.get("HF_ENDPOINT") or DEFAULT_HF_ENDPOINT).rstrip("/")
    archive_url = f"{hf_endpoint}/datasets/{COCO_CN_REPO}/resolve/main/{COCO_CN_ARCHIVE}"
    if not archive.is_file() or sha256_file(archive) != COCO_CN_ARCHIVE_SHA256:
        print(f"[dataset] downloading COCO-CN annotations from {hf_endpoint}")
        _download(
            archive_url,
            archive,
            mirror_endpoint=hf_endpoint,
            insecure_tls=args.insecure_hf_tls,
        )
    actual_archive_hash = sha256_file(archive)
    if actual_archive_hash != COCO_CN_ARCHIVE_SHA256:
        raise RuntimeError(f"COCO-CN archive SHA-256 mismatch: {actual_archive_hash}")

    extract_root = work_dir / "coco_cn_extract"
    source_root = extract_root / "coco-cn-version1805v1.1"
    marker = source_root / "imageid.human-written-caption.txt"
    if not marker.is_file():
        source_root = _safe_extract_tar(archive, extract_root)
    split_path = source_root / f"coco-cn_{args.split}.txt"
    split_ids = [line.strip() for line in split_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    split_set = set(split_ids)
    zh_captions = _parse_cn_lines(source_root / "imageid.human-written-caption.txt", split_set)
    zh_tags = _parse_tags(source_root / "imageid.human-written-tags.txt", split_set)

    captions_zip = work_dir / "annotations_trainval2014.zip"
    if not captions_zip.is_file():
        print("[dataset] downloading official COCO 2014 captions")
        _download(COCO_CAPTIONS_URL, captions_zip)
    captions_root = work_dir / "coco_captions"
    train_json = captions_root / "annotations" / "captions_train2014.json"
    val_json = captions_root / "annotations" / "captions_val2014.json"
    if not train_json.is_file() or not val_json.is_file():
        _safe_extract_zip(captions_zip, captions_root)
    en_captions = {}
    en_captions.update(_parse_english_captions(train_json))
    en_captions.update(_parse_english_captions(val_json))

    records_by_id = {}
    for image_id in split_ids:
        numeric = _numeric_id(image_id)
        record = {
            "image_id": image_id,
            "caption_en": en_captions.get(str(int(numeric)), []),
            "caption_zh": zh_captions.get(image_id, []),
            "tags_zh": zh_tags.get(image_id, []),
        }
        if record["caption_en"] and record["caption_zh"]:
            records_by_id[image_id] = record
    available = [records_by_id[image_id] for image_id in split_ids if image_id in records_by_id]
    if len(available) < args.limit:
        raise RuntimeError(f"only {len(available)} COCO-CN records have bilingual captions")

    required_ids: list[str] = []
    for language, definitions in (("en", ENGLISH_QUERIES), ("zh", CHINESE_QUERIES)):
        for _, keywords in definitions:
            matches = [record["image_id"] for record in available if _matches(record, keywords, language)]
            if len(matches) < 3:
                raise RuntimeError(f"not enough images for {language} keywords {keywords}")
            for image_id in matches[:3]:
                if image_id not in required_ids:
                    required_ids.append(image_id)
    selected_ids = required_ids[:]
    selected_ids.extend(record["image_id"] for record in available if record["image_id"] not in selected_ids)
    selected_ids = selected_ids[: args.limit]
    selected = [records_by_id[image_id] for image_id in selected_ids]
    queries = build_queries(selected)

    image_dir = photos_root / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    completed = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = [executor.submit(_download_image, record, image_dir) for record in selected]
        for count, future in enumerate(concurrent.futures.as_completed(pending), start=1):
            record, destination, width, height = future.result()
            completed[record["image_id"]] = (record, destination, width, height)
            if count == 1 or count % 25 == 0 or count == len(selected):
                print(f"[dataset] verified {count}/{len(selected)} images")

    output_records = []
    for image_id in selected_ids:
        record, destination, width, height = completed[image_id]
        url = _image_url(record["image_id"])
        output_records.append(
            {
                "image_id": record["image_id"],
                "path": str(destination),
                "source_url": url,
                "source_dataset": DATASET_ID,
                "license": "MS-COCO Terms of Use; verify source attribution before redistribution",
                "author": "MS-COCO",
                "sha256": sha256_file(destination),
                "size_bytes": destination.stat().st_size,
                "width": width,
                "height": height,
                "caption_en": record["caption_en"],
                "caption_zh": record["caption_zh"],
                "tags_zh": record["tags_zh"],
            }
        )
    manifest = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "dataset_revision": "coco-cn-version1805v1.1",
        "split": args.split,
        "limit": args.limit,
        "image_count": len(output_records),
        "annotation_archive": {
            "url": archive_url,
            "sha256": actual_archive_hash,
            "size_bytes": archive.stat().st_size,
            "huggingface_endpoint": hf_endpoint,
        },
        "english_caption_source": COCO_CAPTIONS_URL,
        "image_source": COCO_IMAGE_ROOT,
        "records": output_records,
        "queries": queries,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = report_dir / "coco_cn_case7_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[dataset] wrote {manifest_path} ({len(output_records)} images)")
    return manifest


def verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if payload.get("dataset_id") != DATASET_ID or len(records) != payload.get("limit"):
        raise RuntimeError("invalid COCO-CN manifest identity or image count")
    for record in records:
        path = Path(record["path"]).resolve()
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise RuntimeError(f"COCO-CN image hash mismatch: {path}")
    queries = payload.get("queries", {})
    if len(queries.get("en", [])) != 20 or len(queries.get("zh", [])) != 20:
        raise RuntimeError("COCO-CN manifest must contain 20 English and 20 Chinese queries")
    print(f"[dataset] verified {len(records)} images and 40 queries")
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("prepare", "verify"))
    value.add_argument("--photos-root", default="photos/datasets/coco_cn_case7")
    value.add_argument("--report-dir", default="reports/datasets")
    value.add_argument("--manifest", default="reports/datasets/coco_cn_case7_manifest.json")
    value.add_argument("--split", choices=("test", "val", "train"), default="test")
    value.add_argument("--limit", type=int, default=500)
    value.add_argument("--workers", type=int, default=8)
    value.add_argument("--hf-endpoint", default=os.environ.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT))
    value.add_argument(
        "--insecure-hf-tls",
        action="store_true",
        help="disable TLS certificate verification for the HF annotation archive only",
    )
    return value


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.workers < 1 or args.workers > 16:
        raise SystemExit("--workers must be between 1 and 16")
    if args.command == "prepare":
        prepare(args)
        return 0
    return verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
