#!/usr/bin/env python
"""Export a LaTeX response letter to paste-ready plain text.

Web submission systems usually provide a single textarea: no PDF, no colour,
no LaTeX. This exporter keeps the comment/response structure and emits each
paragraph as ONE line (no 80-column hard wrapping).

Usage:
    python export_response_letter_text.py response-letter.tex -o response-letter.txt
Exit code: 0 success, 2 unreadable input.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STRUCT = re.compile(
    r"\\section\*\{([^}]*)\}"
    r"|\\subsection\*\{([^}]*)\}"
    r"|\\begin\{reviewercomment\}\{([^}]*)\}(.*?)\\end\{reviewercomment\}"
    r"|\\begin\{response\}(.*?)\\end\{response\}"
    r"|\\begin\{addedblock\}(.*?)\\end\{addedblock\}"
    r"|\\begin\{deletedblock\}(.*?)\\end\{deletedblock\}",
    re.S,
)

MATH_ENV = re.compile(r"\\begin\{equation\*?\}(.*?)\\end\{equation\*?\}", re.S)
DISPLAY_MATH = re.compile(r"\\\[(.*?)\\\]", re.S)
INLINE_MATH = re.compile(r"\\\((.*?)\\\)", re.S)
DOLLAR_MATH = re.compile(r"\$([^$]*)\$", re.S)
GRAPHICS = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}")
ENV_MARK = re.compile(r"\\(?:begin|end)\{[^}]*\}")
TEXTCOLOR = re.compile(r"\\textcolor\{[^{}]*\}")
SPACING = re.compile(r"\\\\\[[^\]]*\]")
CMD_WITH_ARG = re.compile(
    r"\\(?:textit|textbf|emph|textrm|texttt|textsf|textsc|text|mathrm|mathbf|"
    r"mathit|underline|mbox|textsuperscript|textsubscript)\s*\{([^{}]*)\}"
)
FORMAT_CMD = re.compile(
    r"\\(?:small|footnotesize|scriptsize|normalsize|large|Large|centering|noindent|par|medskip|smallskip)\b"
)
ANY_CMD = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?")


def expand_fracs(tex: str) -> str:
    """Turn \\frac{a}{b} into ((a)/(b)), handling nested braces."""
    def read_group(source: str, index: int) -> tuple[str, int]:
        depth = 0
        for pos in range(index, len(source)):
            if source[pos] == "{":
                depth += 1
            elif source[pos] == "}":
                depth -= 1
                if depth == 0:
                    return source[index + 1:pos], pos + 1
        return source[index + 1:], len(source)

    while True:
        start = tex.find(r"\frac{")
        if start < 0:
            return tex
        numerator, after_num = read_group(tex, start + len(r"\frac"))
        denominator, after_den = read_group(tex, after_num)
        tex = tex[:start] + f"(({numerator})/({denominator}))" + tex[after_den:]


def math_to_text(tex: str) -> str:
    tex = expand_fracs(tex)
    tex = tex.replace(r"^\circ", "°").replace(r"\circ", "°")
    tex = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", tex)
    tex = re.sub(r"\\text\{([^{}]*)\}", r"\1", tex)
    tex = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", tex)
    tex = tex.replace(r"\left", "").replace(r"\right", "")
    tex = re.sub(r"\\[A-Za-z]+", " ", tex)
    tex = tex.replace("_{", "_").replace("^{", "^")
    tex = re.sub(r"[{}]", "", tex)
    tex = re.sub(r"[~]", " ", tex)
    return re.sub(r"\s+", " ", tex).strip()


def latex_to_text(tex: str) -> str:
    tex = re.sub(r"(?m)(?<!\\)%.*$", "", tex)
    tex = GRAPHICS.sub(" [Figure] ", tex)
    def equation_text(match: re.Match) -> str:
        body = match.group(1)
        tag = re.search(r"\\tag\{([^}]*)\}", body)
        body = re.sub(r"\\tag\{[^}]*\}", "", body)
        label = f"Equation ({tag.group(1)}): " if tag else "Equation: "
        return f" {label}{math_to_text(body)} "

    tex = MATH_ENV.sub(equation_text, tex)
    tex = DISPLAY_MATH.sub(equation_text, tex)
    tex = INLINE_MATH.sub(lambda m: " " + math_to_text(m.group(1)) + " ", tex)
    tex = DOLLAR_MATH.sub(lambda m: " " + math_to_text(m.group(1)) + " ", tex)
    tex = ENV_MARK.sub(" ", tex)
    tex = TEXTCOLOR.sub("", tex)
    tex = SPACING.sub(" ", tex)
    tex = CMD_WITH_ARG.sub(r"\1", tex)
    tex = FORMAT_CMD.sub(" ", tex)
    tex = ANY_CMD.sub(" ", tex)
    tex = tex.replace("\\\\", " ").replace("~", " ")
    tex = tex.replace(r"\%", "%").replace(r"\&", "&").replace(r"\_", "_")
    tex = re.sub(r"[{}]", "", tex)
    tex = tex.replace("``", '"').replace("''", '"')
    tex = tex.replace("--", "-")
    tex = re.sub(r"[ \t]+", " ", tex)
    tex = re.sub(r"\s+([,.;:])", r"\1", tex)
    return re.sub(r"\s+", " ", tex).strip()


def paragraphs(tex: str) -> list[str]:
    """Split on blank lines, then make each paragraph a single line."""
    result = []
    for chunk in re.split(r"\n\s*\n", tex):
        text = latex_to_text(chunk)
        if text:
            result.append(text)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Export response letter to paste-ready plain text.")
    parser.add_argument("tex", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--title", default="")
    parser.add_argument("--manuscript-id", default="")
    parser.add_argument("--reviewer-box", type=Path, help="also write the comment/response-only text for the web form")
    args = parser.parse_args()

    if not args.tex.is_file():
        print(f"ERROR: cannot read {args.tex}", file=sys.stderr)
        return 2

    tex = args.tex.read_text(encoding="utf-8", errors="replace")
    body = tex.split(r"\begin{document}", 1)[-1]

    title = args.title
    manuscript_id = args.manuscript_id
    if not title:
        m = re.search(r"\\newcommand\{\\papertitle\}\{([^}]*)\}", tex)
        title = m.group(1).strip() if m else ""
    if not manuscript_id:
        m = re.search(r"\\newcommand\{\\manuscriptid\}\{([^}]*)\}", tex)
        manuscript_id = m.group(1).strip() if m else ""

    # Expand the letter's own metadata macros before stripping commands.
    journal = ""
    macro = re.search(r"\\newcommand\{\\journalname\}\{([^}]*)\}", tex)
    if macro:
        journal = macro.group(1).strip()
    for name, value in (("papertitle", title), ("manuscriptid", manuscript_id), ("journalname", journal)):
        body = body.replace("\\" + name, value)

    lines: list[str] = ["Response to Reviewers"]
    if manuscript_id:
        lines.append(f"Manuscript ID: {manuscript_id}")
    if title:
        lines.append(f"Title: {title}")
    lines.append("")

    # Keep the opening letter text (submission statement, main changes,
    # declarations), but drop the formal letterhead above it.
    # scrartcl response letters use a literal greeting, not scrlttr2's \opening.
    opening = re.search(r"Dear Editor[^,\n]*,", body)
    if opening:
        end = body.find(r"\vspace{1.2em}", opening.end())
        intro = body[opening.end(): end if end > 0 else opening.end()]
        lines.extend([opening.group(0).strip(), ""])
        for para in paragraphs(intro):
            lines.extend([para, ""])

    # Skip the rest of the letterhead; start at the first section heading.
    start = body.find(r"\section*{")
    if start > 0:
        body = body[start:]

    def emit(segment: str) -> None:
        cursor = 0
        for match in STRUCT.finditer(segment):
            for para in paragraphs(segment[cursor: match.start()]):
                lines.extend([para, ""])
            cursor = match.end()

            section, subsection, comment_label, comment_body, response_body, added, deleted = match.groups()
            if section:
                lines.extend([f"== {latex_to_text(section)} ==", ""])
            elif subsection:
                lines.extend([latex_to_text(subsection), ""])
            elif comment_label is not None:
                lines.append(f"Reviewer comment ({latex_to_text(comment_label)}):")
                emit(comment_body)
                lines.append("")
            elif response_body is not None:
                lines.append("Response:")
                emit(response_body)
                lines.append("")
            elif added is not None:
                lines.append("Revised manuscript text:")
                emit(added)
                lines.append("")
            elif deleted is not None:
                lines.append("Deleted manuscript text:")
                emit(deleted)
                lines.append("")

        for para in paragraphs(segment[cursor:]):
            lines.extend([para, ""])

    emit(body)

    # Drop the trailing \end{document} residue and collapse blank runs.
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    output = args.output or args.tex.with_suffix(".txt")
    output.write_text(text, encoding="utf-8")
    print(f"wrote {output} ({len(text)} chars, {len(text.splitlines())} lines)")

    if args.reviewer_box:
        marker = text.find("== ")
        if marker < 0:
            print("ERROR: no section heading found for --reviewer-box", file=sys.stderr)
            return 1
        args.reviewer_box.parent.mkdir(parents=True, exist_ok=True)
        args.reviewer_box.write_text(text[marker:].strip() + "\n", encoding="utf-8")
        print(f"wrote {args.reviewer_box} (comment/response block only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
