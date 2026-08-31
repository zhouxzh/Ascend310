#!/usr/bin/env python3
"""Validate a prebuilt palmprint browser bundle without requiring Node.js."""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

from palmprint_workbench.config import ROOT


def bundle_sha256(dist: Path) -> str:
    """Hash every bundle file with its sorted relative path and content hash.

    Hashing the per-file digest keeps the release check bounded in memory while
    making renames and content changes visible. The record format is
    ``relative-posix-path NUL sha256(file) LF`` for each sorted file.
    """

    digest = hashlib.sha256()
    resolved = dist.resolve()
    files = sorted(
        (path for path in resolved.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(resolved).as_posix(),
    )
    for path in files:
        relative = path.relative_to(resolved).as_posix().encode("utf-8")
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(file_digest)
        digest.update(b"\n")
    return digest.hexdigest()


class _IndexAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.styles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("src"):
            self.scripts.append(str(attributes["src"]))
        elif tag == "link" and attributes.get("href"):
            rel = str(attributes.get("rel") or "").lower()
            if "stylesheet" in rel:
                self.styles.append(str(attributes["href"]))


def _local_asset_path(dist: Path, reference: str) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or reference.startswith("//"):
        return None
    # Decode before resolving so an encoded ``..`` cannot bypass the boundary
    # check. Query strings and fragments do not participate in filesystem
    # lookup.
    decoded_path = unquote(parsed.path)
    # Reject traversal components before resolving.  ``Path.resolve`` alone
    # would turn ``assets/%2e%2e/outside.js`` into a path inside ``dist`` and
    # report it as merely missing, which hides a malformed bundle reference.
    if any(part == ".." for part in decoded_path.replace("\\", "/").split("/")):
        raise ValueError(f"asset path escapes dist: {reference}")
    relative = decoded_path.lstrip("/")
    if not relative:
        return None
    candidate = (dist / relative).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError as exc:
        raise ValueError(f"asset path escapes dist: {reference}") from exc
    return candidate


def validate_dist(dist: Path, strict: bool = False) -> dict[str, object]:
    dist = dist.resolve()
    if not dist.is_dir():
        raise FileNotFoundError(f"frontend bundle directory is missing: {dist}")
    index = dist / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"frontend bundle is missing {index}; build frontend/dist on the development machine first"
        )
    try:
        index.resolve().relative_to(dist)
    except ValueError as exc:
        raise ValueError("frontend index.html escapes dist") from exc

    parser = _IndexAssetParser()
    parser.feed(index.read_text(encoding="utf-8"))
    references = [*parser.scripts, *parser.styles]
    missing: list[str] = []
    checked: list[str] = []
    external: list[str] = []
    for reference in references:
        asset = _local_asset_path(dist, reference)
        if asset is None:
            external.append(reference)
            continue
        relative = asset.relative_to(dist).as_posix()
        checked.append(relative)
        if not asset.is_file():
            missing.append(relative)

    if strict and not parser.scripts:
        raise ValueError("frontend index.html has no script bundle")
    if strict and external:
        raise ValueError(
            "frontend bundle has external references; copy a self-contained dist: "
            + ", ".join(external)
        )
    if strict and not any(_local_asset_path(dist, reference) for reference in parser.scripts):
        raise ValueError("frontend index.html has no local script bundle")
    if missing:
        raise FileNotFoundError(f"frontend bundle references missing files: {', '.join(missing)}")

    return {
        "ok": True,
        "dist": str(dist),
        "index": str(index),
        "bundle_sha256": bundle_sha256(dist),
        "script_count": len(parser.scripts),
        "style_count": len(parser.styles),
        "checked_assets": checked,
        "external_references": external,
        "local_script_count": sum(
            _local_asset_path(dist, reference) is not None for reference in parser.scripts
        ),
        "node_runtime_required": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "frontend" / "dist")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="require a local browser script and reject external bootstrap assets",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate_dist(args.dist, strict=args.strict)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
