#!/usr/bin/env python
"""Flag hard-coded citation, figure, table and equation numbers in LaTeX.

Numbers must come from \\cite, \\ref and \\eqref so that they renumber
automatically. Hard-coded numbers silently break whenever references or
figures are added, which is the most common corruption in revised manuscripts
and response letters.

Usage:
    python check_hardcoded_refs.py manuscript.tex
    python check_hardcoded_refs.py response-letter.tex --letter --strict

Findings inside reviewer-comment quotations are marked [quote]; they usually
reproduce the reviewer's numbering and may be kept. Everything marked [body]
must be converted to a command-based reference.

Exit codes: 0 no blocking finding, 1 findings under --strict, 2 read error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PATTERNS = [
    # Skip LaTeX optional arguments such as \author*[1] or \affil*[1].
    ("cite", re.compile(r"(?<![\w*\]]) \[\s*\d+\s*\]")),
    ("cite", re.compile(r"\{\[\}\s*\d+\s*\{\]\}")),
    ("figure", re.compile(r"\b(?:Fig\.?|Figure)\s*~?\s*\(?\d+")),
    ("table", re.compile(r"\bTables?\s*~?\s*\d+")),
    ("equation", re.compile(r"\b(?:Eqs?\.?|Equation)\s*~?\s*\(?\d+")),
]

# Class-file boilerplate that is not a cross-reference.
SKIP_LINE = re.compile(r"\\begin\{thebibliography\}|\\author\*|\\affil\*")


def classify(line: str, in_quote: bool) -> str:
    return "quote" if in_quote else "body"


def main() -> int:
    parser = argparse.ArgumentParser(description="Flag hard-coded LaTeX cross-reference numbers.")
    parser.add_argument("tex", type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="return exit code 1 when body findings exist")
    parser.add_argument("--letter", action="store_true",
                        help="response-letter mode: bracketed citation numbers are "
                             "expected quotations and are not treated as findings")
    args = parser.parse_args()

    if not args.tex.is_file():
        print(f"ERROR: not found: {args.tex}", file=sys.stderr)
        return 2

    text = args.tex.read_text(encoding="utf-8", errors="replace")
    in_quote = False
    body_findings = 0
    quote_findings = 0

    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(r"\begin{reviewercomment}"):
            in_quote = True
            continue
        if stripped.startswith(r"\end{reviewercomment}"):
            in_quote = False
            continue
        if stripped.startswith("%"):
            continue

        if SKIP_LINE.search(line):
            continue

        scope = classify(line, in_quote)
        for kind, pattern in PATTERNS:
            if args.letter and kind == "cite":
                continue  # a response letter quotes the final printed numbers
            for match in pattern.finditer(line):
                if scope == "body" and not args.letter:
                    body_findings += 1
                else:
                    quote_findings += 1
                snippet = line.strip()
                snippet = snippet[max(0, match.start() - 30):match.start() + 50]
                print(f"[{scope}] {args.tex.name}:{number}  {kind}: {match.group(0)!r}"
                      f"  ...{snippet}...")

    print(f"\nbody findings: {body_findings}   reviewer-quote findings: {quote_findings}")
    if body_findings:
        print("Replace body findings with \\cite{}, \\ref{} or \\eqref{}.")
    if args.strict and body_findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
