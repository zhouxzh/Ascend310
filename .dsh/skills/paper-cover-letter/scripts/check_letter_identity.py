#!/usr/bin/env python
"""Compare identity metadata between a cover letter and a response letter.

The two documents may use different layouts, but these fields must match
verbatim in the same submission round:
  title, journal, manuscript ID, date, author, e-mail, affiliation

Usage:
    python check_letter_identity.py cover-letter.tex response-letter.tex
Exit code: 0 all fields present and identical, 1 missing/different fields,
2 unreadable input.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Semantic key -> accepted LaTeX macro names, in priority order.
FIELDS = {
    "title": ["papertitle", "ManuscriptTitle", "title"],
    "journal": ["journalname", "JournalName"],
    "manuscript_id": ["manuscriptid", "ManuscriptID"],
    "date": ["revisiondate", "LetterDate", "date"],
    "author": ["correspondingauthor", "AuthorName", "fromname"],
    "email": ["correspondingemail", "AuthorEmail", "fromemail"],
    "affiliation": ["affiliation", "AuthorAffiliation", "fromaddress"],
    "affiliation_lines": ["affiliationlines", "AuthorAffiliationLines"],
}


def read_definition(text: str, name: str) -> str | None:
    """Return the braced argument of \\newcommand{\\name}{...} with nesting."""
    pattern = r"\\newcommand\{\\" + re.escape(name) + r"\}"
    match = re.search(pattern, text)
    if not match:
        return None
    i = match.end()
    while i < len(text) and text[i] in " \t":
        i += 1
    if i >= len(text) or text[i] != "{":
        return None
    depth = 1
    i += 1
    start = i
    while i < len(text) and depth > 0:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        i += 1
    return text[start : i - 1]


def normalize(value: str) -> str:
    value = re.sub(r"(?m)%.*$", "", value)          # drop TeX comments
    # Keep the visible text of \href{...}{visible}.
    value = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\texttt\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\emph\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\textbf\{([^}]*)\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z]+\*?", " ", value)   # drop remaining commands
    value = value.replace("\\\\", " ")
    value = re.sub(r"[{}~]", " ", value)
    value = value.replace("\\", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"[\s.,;:]+$", "", value)


def extract(text: str, names: list[str]) -> str:
    for name in names:
        raw = read_definition(text, name)
        if raw is not None:
            return normalize(raw)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare cover/response letter identity fields.")
    parser.add_argument("cover_letter", type=Path)
    parser.add_argument("response_letter", type=Path)
    args = parser.parse_args()

    for path in (args.cover_letter, args.response_letter):
        if not path.is_file():
            print(f"ERROR: cannot read {path}", file=sys.stderr)
            return 2

    cover = args.cover_letter.read_text(encoding="utf-8", errors="replace")
    response = args.response_letter.read_text(encoding="utf-8", errors="replace")

    missing = 0
    differences = 0
    print(f"{'field':<14} {'cover letter':<52} response letter")
    print("-" * 120)
    for field, names in FIELDS.items():
        left = extract(cover, names)
        right = extract(response, names)
        if not left or not right:
            status = "MISSING"
            missing += 1
        elif left == right:
            status = "OK"
        else:
            status = "DIFF"
            differences += 1
        print(f"{field:<14} {left[:52]:<52} {right[:52]}  [{status}]")

    failures = missing + differences
    if failures:
        print(f"\nFAIL: {missing} missing and {differences} differing identity field(s).")
        return 1
    print("\nPASS: identity metadata is present and matches verbatim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
