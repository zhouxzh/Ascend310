#!/usr/bin/env python
"""Audit revision colors in a response-letter DOCX.

Blue  (#215E99) = text deleted from the original manuscript.
Red   (#EE0000) = text revised or added in the manuscript.

Usage:
    python audit_docx_colors.py "response letter english.docx"
    python audit_docx_colors.py "response letter english.docx" --json out.json

Requires: python-docx
Exit code: 0 on success, 2 on missing dependency/file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BLUE = "215E99"
RED = "EE0000"


def load_document(path: Path):
    try:
        from docx import Document  # type: ignore
    except ImportError:
        print("ERROR: python-docx is required (pip install python-docx).", file=sys.stderr)
        raise SystemExit(2)
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    return Document(str(path))


def audit(path: Path) -> dict:
    doc = load_document(path)
    current = "Preamble"
    paragraphs = []
    counts = {"deleted": 0, "added": 0, "colored_paragraphs": 0}

    for index, paragraph in enumerate(doc.paragraphs, start=1):
        plain = paragraph.text.strip()
        if plain.lower().startswith("comment "):
            current = plain.rstrip(":")

        runs = []
        has_revision_color = False
        for run in paragraph.runs:
            text = run.text.replace("\n", " ")
            if not text:
                continue
            rgb = None
            if run.font.color is not None and run.font.color.rgb is not None:
                rgb = str(run.font.color.rgb)
            kind = {"215E99": "deleted", "EE0000": "added"}.get(rgb, "other")
            if kind != "other":
                has_revision_color = True
                counts[kind] += 1
            runs.append({"text": text, "rgb": rgb, "kind": kind})

        if has_revision_color:
            counts["colored_paragraphs"] += 1
            paragraphs.append(
                {
                    "paragraph": index,
                    "comment": current,
                    "plain": plain,
                    "runs": runs,
                }
            )
    return {"source": str(path), "counts": counts, "paragraphs": paragraphs}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit blue/red revision runs in a DOCX.")
    parser.add_argument("docx", type=Path, help="response-letter .docx path")
    parser.add_argument("--json", type=Path, help="write full audit JSON to this path")
    args = parser.parse_args()

    result = audit(args.docx)
    counts = result["counts"]
    print(f"Source: {args.docx}")
    print(f"Colored paragraphs: {counts['colored_paragraphs']}")
    print(f"Blue (deleted) runs: {counts['deleted']}")
    print(f"Red (added) runs:    {counts['added']}")
    print()

    by_comment: dict[str, dict[str, int]] = {}
    for item in result["paragraphs"]:
        bucket = by_comment.setdefault(item["comment"], {"deleted": 0, "added": 0})
        for run in item["runs"]:
            if run["kind"] in bucket:
                bucket[run["kind"]] += 1
    for comment, stat in by_comment.items():
        print(f"{comment}: blue={stat['deleted']}, red={stat['added']}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON written: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
