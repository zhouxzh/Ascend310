#!/usr/bin/env bash

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LATEX_DIR="$REPO_ROOT/latex"
readonly PANDOC_FROM="markdown+yaml_metadata_block+tex_math_dollars+pipe_tables+header_attributes+link_attributes"

check_book_heading_levels() {
    local failed=0
    local file

    for file in "$REPO_ROOT"/src/book/chapter*.md; do
        if ! awk '
            BEGIN { in_fence = 0; in_yaml = 0; found = 0 }
            NR == 1 && $0 == "---" { in_yaml = 1; next }
            in_yaml && $0 == "---" { in_yaml = 0; next }
            in_yaml { next }
            /^[[:space:]]*(```|~~~)/ { in_fence = !in_fence; next }
            !in_fence && /^#[[:space:]]+/ {
                printf "%s:%d:%s\n", FILENAME, NR, $0
                found = 1
            }
            END { exit found ? 1 : 0 }
        ' "$file"; then
            failed=1
        fi
    done

    if [[ "$failed" -ne 0 ]]; then
        printf 'error: book chapters are included under explicit \\chapter{} entries in latex/book.tex.\n' >&2
        printf '       Use "##" as the top-level heading in src/book/chapter*.md, not "#".\n' >&2
        exit 1
    fi
}

convert_markdown() {
    local input=$1
    local output=$2
    local media_dir=$3
    local resource_path=$4

    pandoc \
        -f "$PANDOC_FROM" \
        "$input" \
        --top-level-division=chapter \
        --syntax-highlighting=idiomatic \
        -t latex \
        --extract-media="$media_dir" \
        --resource-path="$resource_path" \
        -o "$output"
}

check_book_heading_levels

cd "$LATEX_DIR"

mkdir -p chapters cases

# 使用 --syntax-highlighting=idiomatic 替代已废弃的 --idiomatic
pandoc \
    -f "$PANDOC_FROM" \
    ../src/book/README.md \
    --top-level-division=chapter \
    --lua-filter=remove-numbering.lua \
    --syntax-highlighting=idiomatic \
    -t latex \
    --extract-media=chapters/ \
    --resource-path=../src/book \
    -o chapters/preface.tex

for chapter in {1..9}; do
    convert_markdown \
        "../src/book/chapter${chapter}.md" \
        "chapters/chapter${chapter}.tex" \
        "chapters/" \
        "../src/book"
done

for case_number in {0..9}; do
    convert_markdown \
        "../src/experiment/case${case_number}.md" \
        "cases/case${case_number}.tex" \
        "cases/" \
        "../src/experiment"
done

python3 replace_block.py chapters/chapter3.tex

# 修复 Pandoc 生成无标题 longtable 时引入的错误代码
find chapters cases -name '*.tex' -type f -exec sed -i 's/\\def\\LTcaptype{none}//g' {} +

latexmk -xelatex -interaction=nonstopmode -file-line-error -synctex=1 -halt-on-error book.tex
