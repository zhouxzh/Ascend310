#!/usr/bin/env python
"""Locate revised passages in the compiled manuscript PDF and audit the
page/column/paragraph references written in a response letter.

Why PDF coordinates: page numbers, column and paragraph order change whenever
the manuscript is re-typeset. Source line numbers are therefore useless for a
response letter; the coordinates of the final PDF are the ground truth.

Audit the PDF that is actually uploaded to the submission system (the
highlighted/markup manuscript). A colour-free copy compiled separately can
paginate differently, so it is NOT a valid reference for page numbers.

Deleted passages cannot be found in the revised PDF. For those, the script
locates the replacement passage that follows the deleted block and requires it
to sit on the page stated for the deletion. Deletions without a following
replacement are reported as unverified instead of silently passing.

Usage:
    python locate_manuscript_positions.py \
        --pdf ../out/markup-manuscript.pdf \
        --response response-letter.tex \
        --markdown work/positions.md

    # one-off probe:
    python locate_manuscript_positions.py --pdf paper.pdf --snippet "exact text"

Requires: pdftotext (Poppler) with -bbox-layout support.
Exit code: 0 all stated pages and passages verified, 1 mismatch/missing,
2 input error or manual review required.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- PDF model

@dataclass
class Token:
    word: str
    page: int
    column: str          # left / right / full
    block: int           # 1-based paragraph block in its column on the page
    line: int            # 1-based line in the block
    y: float
    start: int = 0       # char offset in the compact stream


@dataclass
class Document:
    compact: str = ""
    tokens: list[Token] = field(default_factory=list)
    page_width: dict[int, float] = field(default_factory=dict)
    printed_lines: list[tuple[int, float, int]] = field(default_factory=list)

    def locate(self, probe: str) -> tuple[int, int] | None:
        key = compact_text(probe)
        if not key:
            return None
        index = self.compact.find(key)
        if index < 0:
            return None
        first = bisect_right([t.start for t in self.tokens], index) - 1
        last_index = index + len(key) - 1
        starts = [t.start for t in self.tokens]
        last = bisect_right(starts, last_index) - 1
        return max(first, 0), max(last, 0)


def compact_text(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def fuzzy_locate(doc: "Document", probe: str) -> tuple[int, int, int] | None:
    """Token-sequence match tolerant of dropped math symbols and hyphens."""
    import difflib

    probe_words = [compact_text(w) for w in re.findall(r"[A-Za-z0-9]+", probe)]
    probe_words = [w for w in probe_words if len(w) >= 2]
    if len(probe_words) < 6:
        return None
    sequence = [compact_text(token.word) for token in doc.tokens]
    matcher = difflib.SequenceMatcher(None, probe_words, sequence, autojunk=False)
    match = matcher.find_longest_match(0, len(probe_words), 0, len(sequence))
    if match.size >= max(6, int(0.45 * len(probe_words))):
        return match.b, match.b + match.size - 1, match.size
    return None


def locate_probe(doc: "Document", text: str, use_fuzzy: bool = True) -> tuple[int, int] | None:
    """Exact match first, then shorter prefixes or individual sentences.

    Deleted passages use exact matching only: fuzzy matching would latch onto
    the rewritten sentence and falsely report that the deleted text survived.
    """
    candidates = [text[:size].strip() for size in (len(text), 180, 130, 90, 60)]
    candidates += [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(re.findall(r"[A-Za-z0-9]+", sentence)) >= 8
    ]
    for part in candidates:
        if not part:
            continue
        span = doc.locate(part)
        if span is not None:
            return span
        if use_fuzzy:
            fuzzy = fuzzy_locate(doc, part)
            if fuzzy is not None:
                return fuzzy[0], fuzzy[1]
    return None


def latex_to_text(tex: str) -> str:
    tex = re.sub(r"(?m)%.*$", "", tex)
    tex = re.sub(r"\\begin\{equation\*?\}.*?\\end\{equation\*?\}", " ", tex, flags=re.S)
    tex = re.sub(r"\$[^$]*\$", " ", tex)
    tex = re.sub(r"\\\[.*?\\\]", " ", tex, flags=re.S)
    tex = re.sub(r"\\\([^)]*\\\)", " ", tex)
    tex = re.sub(r"\\(?:cite|ref|eqref|label)\{[^}]*\}", " ", tex)
    tex = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", " ", tex)
    tex = tex.replace("~", " ").replace("\\\\", " ")
    tex = re.sub(r"[{}]", " ", tex)
    return re.sub(r"\s+", " ", tex).strip()


def run_pdftotext_bbox(pdf: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-bbox-layout", str(pdf), "-"],
            capture_output=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"pdftotext failed: {exc}")
    if result.returncode != 0 and not result.stdout:
        raise SystemExit(f"pdftotext failed: {result.stderr.decode('utf-8', 'replace')}")
    # Poppler writes UTF-8; never let Windows decode it as GBK.
    return result.stdout.decode("utf-8-sig", "replace")


def sanitize_xml(text: str) -> str:
    # Poppler emits raw control bytes for some math glyphs; XML rejects them.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Escape bare ampersands (keep existing entities intact).
    return re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)", "&amp;", text)


def build_document(xml_text: str) -> Document:
    root = ET.fromstring(sanitize_xml(xml_text))
    # Poppler declares an XHTML default namespace; strip it so plain tag
    # lookups (page/block/line/word) work.
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    doc = Document()
    parts: list[str] = []

    for page_number, page in enumerate(root.iter("page"), start=1):
        width = float(page.get("width", "0") or 0)
        doc.page_width[page_number] = width
        blocks = list(page.iter("block"))
        prepared = []
        for block_index, block in enumerate(blocks, start=1):
            xmin = float(block.get("xMin", "0"))
            xmax = float(block.get("xMax", "0"))
            ymin = float(block.get("yMin", "0"))
            span = xmax - xmin
            if width and span > 0.60 * width:
                column = "full"
            elif width and (xmin + xmax) / 2 < width / 2:
                column = "left"
            else:
                column = "right"
            lines = []
            for line_index, line in enumerate(block.findall("line"), start=1):
                words = [w.text or "" for w in line.findall("word")]
                text = " ".join(words).strip()
                if not text:
                    continue
                y = float(line.get("yMin", "0"))
                lines.append((line_index, y, words))
                for word in words:
                    if re.fullmatch(r"\d{1,3}", word.strip()) and xmin < 55:
                        doc.printed_lines.append((page_number, y, int(word)))
            if lines:
                prepared.append((column, ymin, block_index, lines))

        # Reading order: full-width top matter, then left column, then right.
        order = {"full": 0, "left": 1, "right": 2}
        prepared.sort(key=lambda item: (order[item[0]], item[1]))
        column_counts: dict[str, int] = {}
        for column, _y, _bidx, lines in prepared:
            column_counts[column] = column_counts.get(column, 0) + 1
            paragraph = column_counts[column]
            for line_index, y, words in lines:
                for word in words:
                    token = Token(
                        word=word, page=page_number, column=column,
                        block=paragraph, line=line_index, y=y,
                    )
                    doc.tokens.append(token)
                    parts.append(compact_text(word))

    # Recompute offsets against the final compact stream (simple, exact).
    offset = 0
    for token, piece in zip(doc.tokens, parts):
        token.start = offset
        offset += len(piece)
    doc.compact = "".join(parts)
    return doc


def printed_line_for(doc: Document, token: Token) -> int | None:
    candidates = [
        number for page, y, number in doc.printed_lines
        if page == token.page and abs(y - token.y) <= 6.0
    ]
    return candidates[0] if candidates else None


# ------------------------------------------------------- response extraction

BLOCK_RE = re.compile(r"\\begin\{(addedblock|deletedblock)\}(.*?)\\end\{\1\}", re.S)
STATED_RE = re.compile(
    r"page\s+(\d+)(?:[^.;]{0,80}?)(left|right)[- ]?(?:hand\s+)?column"
    r"(?:[^.;]{0,60}?paragraph\s+(\d+))?",
    re.I,
)


def strip_reference_label(text: str) -> str:
    """Remove a leading citation label such as '[7].' so reference entries match."""
    return re.sub(r"^\s*\[\s*\d+\s*\]\s*[.]?\s*", "", text)


ADDED_RE = re.compile(r"\\begin\{addedblock\}(.*?)\\end\{addedblock\}", re.S)


def replacement_after(tex: str, position: int, window: int = 1500) -> str | None:
    """Return the replacement text that follows a deleted block, if any.

    Deleted passages are absent from the revised PDF, so their stated location
    can only be checked through the passage that replaced them.
    """
    match = ADDED_RE.search(tex, position, position + window)
    if not match:
        return None
    plain = latex_to_text(match.group(1))
    return plain if len(re.findall(r"[A-Za-z0-9]+", plain)) >= 8 else None


def extract_probes(tex: str) -> list[dict]:
    probes = []
    for match in BLOCK_RE.finditer(tex):
        kind, body = match.group(1), match.group(2).strip()
        plain = latex_to_text(body)
        words = re.findall(r"[A-Za-z0-9]+", plain)
        if len(words) < 12:
            continue  # formula-only or too short to locate reliably
        before = tex[max(0, match.start() - 300): match.start()]
        stated = STATED_RE.search(before)
        if stated and stated.end() < len(before) - 90:
            stated = None  # the location sentence belongs to an earlier block
        probes.append({
            "kind": kind,
            "text": plain,
            "probe": plain[:600],
            "stated_page": int(stated.group(1)) if stated else None,
            "stated_column": (stated.group(2).lower() if stated else None),
            "stated_paragraph": int(stated.group(3)) if stated and stated.group(3) else None,
            "replacement": replacement_after(tex, match.end()) if kind == "deletedblock" else None,
        })

    return probes


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate response-letter passages in the manuscript PDF.")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--response", type=Path, help="response-letter.tex")
    parser.add_argument("--snippet", action="append", default=[], help="explicit text probe")
    parser.add_argument("--markdown", type=Path, help="write a markdown report")
    parser.add_argument("--json", type=Path, help="write JSON report")
    args = parser.parse_args()

    if not args.pdf.is_file():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    xml_text = run_pdftotext_bbox(args.pdf)
    doc = build_document(xml_text)

    probes: list[dict] = []
    if args.response:
        if not args.response.is_file():
            print(f"ERROR: response letter not found: {args.response}", file=sys.stderr)
            return 2
        probes.extend(extract_probes(args.response.read_text(encoding="utf-8", errors="replace")))
    for snippet in args.snippet:
        probes.append({"kind": "manual", "text": snippet, "probe": snippet,
                       "stated_page": None, "stated_column": None, "stated_paragraph": None})

    rows, mismatches, warnings = [], 0, 0
    for index, item in enumerate(probes, start=1):
        probe_text = strip_reference_label(item["probe"])
        span = locate_probe(doc, probe_text, use_fuzzy=item["kind"] != "deletedblock")
        if span is None:
            if item["kind"] == "deletedblock":
                # Absence is correct; the stated location is checked through the
                # replacement passage that follows the deleted text.
                replacement = item.get("replacement")
                rep_span = locate_probe(doc, replacement) if replacement else None
                if rep_span is None:
                    status = "DELETED (location unverified)"
                else:
                    rep_page = doc.tokens[rep_span[0]].page
                    if item["stated_page"] is not None and item["stated_page"] != rep_page:
                        status = f"DELETED @PAGE {item['stated_page']}->{rep_page}"
                        mismatches += 1
                    else:
                        status = "DELETED (location ok)"
            else:
                status = "NOT-FOUND"
                mismatches += 1
            rows.append({**item, "status": status, "page": None, "column": None,
                         "paragraph": None, "line": None, "printed_line": None})
            continue
        first, last = doc.tokens[span[0]], doc.tokens[span[1]]
        printed = printed_line_for(doc, first)
        status = "OK"
        if item["kind"] == "deletedblock":
            status = "STILL-PRESENT"
            mismatches += 1
        elif item["stated_page"] is not None and item["stated_page"] != first.page:
            status = f"PAGE {item['stated_page']}->{first.page}"
            mismatches += 1
        elif item["stated_column"] and first.column not in (item["stated_column"], "full"):
            # Poppler sometimes merges both columns into one block; verify manually.
            status = f"COLUMN? {item['stated_column']}->{first.column}"
            warnings += 1
        elif item["stated_paragraph"] and first.block != item["stated_paragraph"]:
            status = f"PARA? {item['stated_paragraph']}->{first.block}"
            warnings += 1
        rows.append({
            "probe": item["probe"][:90], "kind": item["kind"], "status": status,
            "page": first.page, "column": first.column, "paragraph": first.block,
            "line": f"{first.line}-{last.line}" if first.line != last.line else str(first.line),
            "printed_line": printed,
            "end_page": last.page,
            "stated_page": item["stated_page"],
        })

    header = (f"{'#':>2}  {'status':<16} {'page':<5} {'column':<6} "
              f"{'para':<5} {'line':<7} probe")
    print(header)
    print("-" * len(header) + "-----")
    for index, row in enumerate(rows, start=1):
        page = f"{row['page']}" if row["page"] else "-"
        print(f"{index:>2}  {row['status']:<16} {page:<5} "
              f"{str(row['column'] or '-'):<6} {str(row['paragraph'] or '-'):<5} "
              f"{str(row['line'] or '-'):<7} {row['probe'][:50]}")

    if args.markdown:
        lines = ["| # | Position | Page | Column | Paragraph | Line | Probe |",
                 "|---|---|---|---|---|---|---|"]
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"| {index} | {row['status']} | {row['page'] or '-'} | "
                f"{row['column'] or '-'} | {row['paragraph'] or '-'} | {row['line'] or '-'} | "
                f"{row['probe'][:70].replace('|', '/')} |"
            )
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nMarkdown: {args.markdown}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {args.json}")

    unverified = [r for r in rows if r["status"] == "DELETED (location unverified)"]
    if unverified:
        print(f"\nMANUAL_REVIEW_REQUIRED: {len(unverified)} deletion(s) have no replacement passage; "
              "their stated locations need author verification.")
    if warnings:
        print(f"\nMANUAL_REVIEW_REQUIRED: {warnings} passage(s) need manual column/paragraph verification.")
    if mismatches:
        print(f"\nFAIL: {mismatches} passage(s) missing or mislocated.")
        return 1
    if unverified or warnings:
        return 2
    print("\nPASS: all located passages match the stated page numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
