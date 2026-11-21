#!/usr/bin/env python
"""Verify that red/blue passages in a response letter match the manuscript LaTeX.

Both documents are LaTeX, so text fidelity is checked source-to-source; the PDF
is only needed for page/column positions (see locate_manuscript_positions.py).

Rules:
  red  passage: must exist in the manuscript source (verbatim preferred)
  blue passage: must NOT exist in the manuscript source

Usage:
    python verify_revision_text.py \
        --manuscript sn-article.tex \
        --response response-letter.tex \
        --markdown work/text-audit.md

Exit code: 0 all checks pass, 1 mismatch, 2 unreadable input or manual
author review required for CLOSE/REVIEW passages.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

BLOCK_RE = re.compile(r"\\begin\{(addedblock|deletedblock)\}(.*?)\\end\{\1\}", re.S)
INLINE_RE = re.compile(
    r"\\textcolor\{(addedred|deletedblue)\}\{((?:[^{}]|\{[^{}]*\})*)\}", re.S
)

MATH_ENVS = re.compile(r"\\begin\{equation\*?\}.*?\\end\{equation\*?\}", re.S)
DOLLAR_BLOCK = re.compile(r"\$\$.*?\$\$", re.S)
DOLLAR_INLINE = re.compile(r"\$[^$]*\$", re.S)
BRACKET_MATH = re.compile(r"\\\[.*?\\\]", re.S)
PAREN_MATH = re.compile(r"\\\(.*?\\\)", re.S)
CITE = re.compile(r"\\(?:cite|citep|citet|ref|eqref|autoref|label)\*?(?:\[[^\]]*\])?\{[^{}]*\}")
REF = re.compile(r"\\(?:ref|eqref|autoref)\{([^}]+)\}")
CITE_ARG = re.compile(r"\\cite[tp]?\*?(?:\[[^\]]*\])?\{([^}]+)\}")
NEWLABEL = re.compile(r"\\newlabel\{([^}]+)\}\{\{([^}]+)\}")
BIBCITE = re.compile(r"\\bibcite\{([^}]+)\}\{([^}]+)\}")


def load_aux(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Parse .aux for label->number and citation-key->number."""
    labels, cites = {}, {}
    if not path.is_file():
        return labels, cites
    text = path.read_text(encoding="utf-8", errors="replace")
    labels = {key: value for key, value in NEWLABEL.findall(text)}
    cites = {key: value for key, value in BIBCITE.findall(text)}
    return labels, cites
HREF = re.compile(r"\\href\{[^{}]*\}\{([^{}]*)\}")
TEXTCOLOR = re.compile(r"\\textcolor\{[^{}]*\}")
FORMAT_CMD = re.compile(
    r"\\(?:textbf|textit|emph|textrm|texttt|textsf|textsc|text|mathrm|mathbf|"
    r"mathit|underline|mbox|textsuperscript|textsubscript|small|footnotesize|"
    r"scriptsize|normalsize|large|Large)\b"
)
ANY_CMD = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?")
BRACKET_REF = re.compile(r"\{\[\}\s*\d+(?:\s*,\s*\d+)*\s*\{\]\}|\[\s*\d+(?:\s*,\s*\d+)*\s*\]")


def latex_to_words(
    tex: str,
    labels: dict[str, str] | None = None,
    cites: dict[str, str] | None = None,
) -> str:
    labels = labels or {}
    cites = cites or {}
    tex = re.sub(r"(?m)(?<!\\)%.*$", "", tex)
    # Resolve cross-references and citations with the numbers LaTeX actually
    # printed (from the .aux file), so "Fig. 8" matches "Fig. \ref{fig_C1}".
    tex = REF.sub(lambda m: f" {labels.get(m.group(1).strip(), ' ')} ", tex)
    tex = CITE_ARG.sub(
        lambda m: " " + " ".join(cites.get(key.strip(), " ") for key in m.group(1).split(",")) + " ",
        tex,
    )
    tex = MATH_ENVS.sub(" ", tex)
    tex = DOLLAR_BLOCK.sub(" ", tex)
    tex = BRACKET_MATH.sub(" ", tex)
    tex = PAREN_MATH.sub(" ", tex)
    tex = DOLLAR_INLINE.sub(" ", tex)
    tex = CITE.sub(" ", tex)
    tex = BRACKET_REF.sub(" ", tex)
    # Drop circuit-variable tokens (CGS, CGD, VGS, C1, D1, RG, VCC ...); the
    # manuscript mixes plain text and math forms for the same symbol.
    tex = re.sub(r"\b(?:[A-Z]{2,}[0-9_]*|[A-Z][0-9]+|R[Gg]|V[a-z]{2,})\b", " ", tex)
    tex = HREF.sub(r" \1 ", tex)
    tex = TEXTCOLOR.sub("", tex)
    tex = FORMAT_CMD.sub("", tex)
    tex = ANY_CMD.sub(" ", tex)
    tex = tex.replace("~", " ").replace("\\\\", " ")
    tex = re.sub(r"[{}]", " ", tex)
    tex = re.sub(r"[^\w\s]", " ", tex, flags=re.UNICODE)
    # Single letters are usually math leftovers (C$_{GS}$ -> "c"); drop them.
    words = [word for word in tex.split() if len(word) > 1]
    return " ".join(words).lower()


def best_match(probe: str, corpus: str) -> tuple[float, str]:
    """Return (covered fraction, context) around the best matching window."""
    matcher = difflib.SequenceMatcher(None, probe, corpus, autojunk=False)
    anchor = matcher.find_longest_match(0, len(probe), 0, len(corpus))
    start = max(anchor.b - 200, 0)
    window = corpus[start: start + len(probe) + 400]
    local = difflib.SequenceMatcher(None, probe, window, autojunk=False)
    matched = sum(block.size for block in local.get_matching_blocks())
    coverage = matched / max(len(probe), 1)
    context = window[max(anchor.b - start - 40, 0): max(anchor.b - start - 40, 0) + 160]
    return coverage, context


def extract(tex: str) -> list[dict]:
    items = []
    for match in BLOCK_RE.finditer(tex):
        before = tex[max(0, match.start() - 240): match.start()]
        if "References section" in before:
            items.append({"kind": "reference", "text": latex_to_words(match.group(2))})
            continue
        kind = "red" if match.group(1) == "addedblock" else "blue"
        items.append({"kind": kind, "text": latex_to_words(match.group(2))})
    for match in INLINE_RE.finditer(tex):
        kind = "red" if match.group(1) == "addedred" else "blue"
        plain = latex_to_words(match.group(2))
        if len(plain.split()) >= 3:
            items.append({"kind": kind, "text": plain})
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare red/blue response text with the manuscript LaTeX.")
    parser.add_argument("--manuscript", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--bib", type=Path, help="bibliography .bib; reference entries live here")
    parser.add_argument("--aux", type=Path, help="LaTeX .aux; supplies real figure/equation/citation numbers")
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    for path in (args.manuscript, args.response):
        if not path.is_file():
            print(f"ERROR: cannot read {path}", file=sys.stderr)
            return 2

    labels, cites = load_aux(args.aux) if args.aux else ({}, {})
    manuscript = latex_to_words(
        args.manuscript.read_text(encoding="utf-8", errors="replace"), labels, cites
    )
    if args.bib and args.bib.is_file():
        manuscript += " " + latex_to_words(args.bib.read_text(encoding="utf-8", errors="replace"))

    response_tex = args.response.read_text(encoding="utf-8", errors="replace")
    body = response_tex.split(r"\begin{document}", 1)[-1]
    items = extract(body)

    rows, failures, reviews = [], 0, 0
    for index, item in enumerate(items, start=1):
        probe = item["text"]
        if not probe:
            continue
        if item["kind"] == "reference":
            status, coverage, context = "REFERENCE (skip)", 0.0, ""
        elif item["kind"] == "red":
            if probe in manuscript:
                status, coverage, context = "VERBATIM", 1.0, ""
            else:
                coverage, context = best_match(probe, manuscript)
                if coverage >= 0.85:
                    status = "CLOSE"
                    reviews += 1
                elif coverage >= 0.30:
                    status = "REVIEW"
                    reviews += 1
                else:
                    status = "MISSING"
                    failures += 1
        else:
            if probe in manuscript:
                status, coverage, context = "STILL-PRESENT", 1.0, ""
                failures += 1
            else:
                status, coverage, context = "DELETED (ok)", 0.0, ""
        rows.append({
            "index": index, "kind": item["kind"], "status": status,
            "coverage": round(coverage, 3), "probe": probe[:90],
            "context": context[:160],
        })

    print(f"{'#':>2}  {'kind':<5} {'status':<16} {'cover':<6} probe")
    print("-" * 110)
    for row in rows:
        print(f"{row['index']:>2}  {row['kind']:<5} {row['status']:<16} "
              f"{row['coverage']:<6} {row['probe'][:60]}")
        if row["status"] in {"CLOSE", "REVIEW"}:
            print(f"    manuscript context: {row['context'][:120]}")

    if args.markdown:
        lines = ["| # | Color | Status | Coverage | Probe |", "|---|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| {row['index']} | {row['kind']} | {row['status']} | "
                f"{row['coverage']} | {row['probe'].replace('|', '/')} |"
            )
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nMarkdown: {args.markdown}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {args.json}")

    if failures:
        print(f"\nFAIL: {failures} passage(s) do not match the manuscript source.")
        return 1
    if reviews:
        print(f"\nMANUAL_REVIEW_REQUIRED: {reviews} passage(s) are CLOSE/REVIEW and must be confirmed by the author.")
        return 2
    print("\nPASS: every red passage is verbatim and every blue passage is absent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
