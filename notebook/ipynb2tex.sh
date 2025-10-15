#!/bin/bash
# Convert Jupyter notebook to LaTeX with Chinese support and compile to PDF using xelatex.
# Usage: ./ipynb2tex.sh notebook.ipynb [AUTHOR]
# Example: ./ipynb2tex.sh reinforcement_learning.ipynb "张三"

set -euo
# enable pipefail when running under bash (dash /bin/sh doesn't support `-o pipefail`)
if [ -n "${BASH_VERSION:-}" ]; then
    set -o pipefail
fi


NOTEBOOK="${1:-reinforcement_learning.ipynb}"
AUTHOR="${2:-}"

command -v jupyter >/dev/null 2>&1 || { echo "jupyter not found. Install it (pip install jupyter) and try again." >&2; exit 1; }
command -v xelatex >/dev/null 2>&1 || { echo "xelatex not found. Install a TeX distribution with xetex (e.g. texlive-xetex) and try again." >&2; exit 1; }

[ -f "$NOTEBOOK" ] || { echo "Notebook '$NOTEBOOK' not found." >&2; exit 1; }

# Convert to LaTeX
jupyter nbconvert --to latex "$NOTEBOOK"

TEX="${NOTEBOOK%.*}.tex"
[ -f "$TEX" ] || { echo "Conversion failed: $TEX not found." >&2; exit 1; }

BASENAME="${TEX%.*}"
XELATEX_OPTS="-interaction=nonstopmode -halt-on-error"

# Replace documentclass 'article' with 'ctexart' (preserve options) and insert/replace author if needed
if grep -q '\\author{' "$TEX"; then
    # 如果已有 \author，则插入字体与日期；如果传入 AUTHOR 则替换现有 \author
    awk -v author="$AUTHOR" '
    BEGIN { done=0 }
    /^\s*\\documentclass/ && !done {
        line=$0
        # replace {article} with {ctexart} (preserve options if any)
        gsub(/\{[ \t]*article[ \t]*\}/,"{ctexart}",line)
        print line
        print "\\setCJKmainfont{Noto Serif CJK SC}"
        print "\\setCJKsansfont{Noto Sans CJK SC}"
        print "\\setCJKmonofont{Noto Sans Mono CJK SC}"
        print "\\date{\\today}"
        done=1
        next
    }
    /^\s*\\author\{/ {
        if (author != "") {
            print "\\author{" author "}"
        } else {
            print $0
        }
        next
    }
    { print }
    ' "$TEX" > "$TEX.tmp" && mv "$TEX.tmp" "$TEX"
else
    # 如果没有 \author，则在 documentclass 后插入字体、作者（传入或默认）和日期
    awk -v author="$AUTHOR" '
    BEGIN { done=0 }
    /^\s*\\documentclass/ && !done {
        line=$0
        gsub(/\{[ \t]*article[ \t]*\}/,"{ctexart}",line)
        print line
        print "\\setCJKmainfont{Noto Serif CJK SC}"
        print "\\setCJKsansfont{Noto Sans CJK SC}"
        print "\\setCJKmonofont{Noto Sans Mono CJK SC}"
        if (author != "") {
            print "\\author{" author "}"
        } else {
            print "\\author{周贤中}"
        }
        print "\\date{\\today}"
        done=1
        next
    }
    { print }
    ' "$TEX" > "$TEX.tmp" && mv "$TEX.tmp" "$TEX"
fi

# --- 将指定的 section 删除（若带有前置 \hypertarget 行也一并删除），并把 \title 改为中文标题
# 更新 \title 为 "强化学习------学习笔记"
sed -i 's/\\title{[^}]*}/\\title{强化学习------学习笔记}/' "$TEX"

# 删除与该节相关的 hypertarget + section（或单独的 section 行）
awk '
{
    if ($0 ~ /^\s*\\hypertarget/) {
        saved = $0
        if (getline nextline > 0) {
            if (nextline ~ /^\s*\\section\{强化学习------学习笔记\}/) {
                # skip both lines
                next
            } else {
                print saved
                print nextline
                next
            }
        } else {
            print saved
        }
    } else if ($0 ~ /^\s*\\section\{强化学习------学习笔记\}/) {
        # 单独的 section 行，删除
        next
    } else {
        print
    }
}
' "$TEX" > "$TEX.tmp" && mv "$TEX.tmp" "$TEX"

# Compile: run xelatex a couple of times. If a .bib exists or \bibliography used, run bibtex.
echo "Compiling $TEX -> ${BASENAME}.pdf (using xelatex)..."
xelatex $XELATEX_OPTS "$TEX"
# If bibliography present, try bibtex (if using biber this script won't handle that automatically)
if grep -q '\\bibliography{' "$TEX" || [ -f "${BASENAME}.bib" ]; then
    if command -v bibtex >/dev/null 2>&1; then
        bibtex "$BASENAME" || true
    else
        echo "bibtex not found; skipping bibtex step." >&2
    fi
fi
# Run xelatex twice more to resolve references
xelatex $XELATEX_OPTS "$TEX"
xelatex $XELATEX_OPTS "$TEX"

echo "Done. PDF: ${BASENAME}.pdf"