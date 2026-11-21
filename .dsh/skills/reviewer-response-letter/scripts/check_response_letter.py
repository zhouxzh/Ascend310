#!/usr/bin/env python
"""Static checks for a LaTeX reviewer response letter.

Catches the recurring defects this skill is designed to prevent:
  * leftover "Revision markup" duplicate blocks
  * figures still referenced outside images/ (e.g. ../FIG2.png) or via media/
  * manually numbered inline formulas such as quote + "(16)"
  * non-upright multi-letter subscripts T_{UP}, T_{delay,driver}, ...
  * units left outside math, e.g. "\\(C_1=0.81\\)nF"
  * duplicated figure captions (old DOCX caption + LaTeX caption)
  * missing figure files

Usage:
    python check_response_letter.py response-letter.tex
Exit code: 0 = pass (warnings allowed), 1 = errors found, 2 = cannot read file.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ERRORS: list[str] = []
WARNINGS: list[str] = []


def check_tex(tex: Path) -> None:
    text = tex.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    root = tex.parent

    # 1. Duplicate revision-markup blocks.
    if re.search(r"Revision markup from", text, re.I):
        ERRORS.append("Found duplicate 'Revision markup' block(s): color the original response text inline instead.")

    # 2. Figure sources must be local images/.
    for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", text):
        target = match.group(1)
        line = text[: match.start()].count("\n") + 1
        if target.startswith("media/"):
            ERRORS.append(f"line {line}: DOCX media path '{target}' - copy manuscript figures into images/ instead.")
        elif target.startswith("../") or target.startswith("/"):
            ERRORS.append(f"line {line}: external figure path '{target}' - copy into images/ for standalone build.")
        elif not target.startswith("images/"):
            WARNINGS.append(f"line {line}: figure '{target}' is outside images/.")
        path = (root / target).resolve()
        if not path.is_file():
            ERRORS.append(f"line {line}: missing figure file '{target}'.")

    # 3. Manual formula numbering inside quote environments.
    for match in re.finditer(r"\\begin\{quote\}(.*?)\\end\{quote\}", text, re.S):
        if re.search(r"\(\d+\)", match.group(1)):
            line = text[: match.start()].count("\n") + 1
            ERRORS.append(f"line {line}: quote environment with manual '(n)' numbering - use equation + \\tag{{n}}.")

    # 4. Non-upright multi-letter subscripts.
    for match in re.finditer(r"(?<!\\)T_\{([^}]*)\}", text):
        subscript = match.group(1)
        if subscript in {"UP", "DOWN", "Miller", "delay,driver", "D_OFF", "D_ON"}:
            line = text[: match.start()].count("\n") + 1
            ERRORS.append(f"line {line}: T_{{{subscript}}} should be T_{{\\mathrm{{{subscript}}}}}.")

    # 5. Units outside math.
    for pattern, unit in ((r"\\\)\s*(nF|pF|mF|uF|µF)", "capacitance"),
                          (r"\\\)\s*(MHz|kHz|GHz)", "frequency"),
                          (r"\\\)\s*(ns|us|µs)", "time")):
        for match in re.finditer(pattern, text):
            line = text[: match.start()].count("\n") + 1
            ERRORS.append(f"line {line}: unit '{match.group(1)}' outside math ({unit}) - use '~\\mathrm{{{match.group(1)}}}'.")

    # 6. Digits glued to math close without unit space are suspicious only if followed by letters.
    for match in re.finditer(r"\\\)([A-Za-z])", text):
        line = text[: match.start()].count("\n") + 1
        WARNINGS.append(f"line {line}: no space after '\\)' before '{match.group(1)}'.")

    # 7. Duplicate figure captions: same literal caption text twice.
    caption_like = [ln.strip() for ln in lines if re.match(r"^\\small Fig\.|^\\textcolor\{addedred\}\{\\small Fig\.", ln.strip())]
    seen: dict[str, int] = {}
    for cap in caption_like:
        normalized = re.sub(r"\s+", " ", cap)
        seen[normalized] = seen.get(normalized, 0) + 1
    for cap, count in seen.items():
        if count > 1:
            ERRORS.append(f"duplicate caption ({count}x): {cap[:80]}")

    # 8. Report equation tags so numbers can be checked against the compiled manuscript PDF.
    tags = re.findall(r"\\tag\{([^}]+)\}", text)
    if tags:
        WARNINGS.append("formula tags to verify against the compiled manuscript PDF: " + ", ".join(tags))

    # 9. Informational counts.
    added = len(re.findall(r"\\begin\{addedblock\}", text))
    deleted = len(re.findall(r"\\begin\{deletedblock\}", text))
    inline = len(re.findall(r"\\textcolor\{deletedblue\}", text))
    print(f"addedblock={added} deletedblock={deleted} inline-blue={inline} tags={len(tags)}")

    # 10. Unbalanced block environments.
    for env in ("addedblock", "deletedblock", "reviewercomment", "response", "equation", "equation*"):
        begin = text.count(f"\\begin{{{env}}}")
        end = text.count(f"\\end{{{env}}}")
        if begin != end:
            ERRORS.append(f"unbalanced environment '{env}': begin={begin}, end={end}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Static checks for a LaTeX response letter.")
    parser.add_argument("tex", type=Path, help="response-letter.tex path")
    args = parser.parse_args()

    if not args.tex.is_file():
        print(f"ERROR: cannot read {args.tex}", file=sys.stderr)
        return 2

    check_tex(args.tex)

    if WARNINGS:
        print("\nWARNINGS")
        for item in WARNINGS:
            print(f"  - {item}")
    if ERRORS:
        print("\nERRORS")
        for item in ERRORS:
            print(f"  - {item}")
        return 1

    print("\nPASS: no blocking issues found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
