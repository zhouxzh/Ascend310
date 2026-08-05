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

pandoc_to_file() {
    local output=$1
    local temp_output
    shift

    temp_output="$(mktemp "${TMPDIR:-/tmp}/ascend310-pandoc.XXXXXX.tex")"
    if ! pandoc "$@" -o "$temp_output"; then
        rm -f "$temp_output"
        return 1
    fi

    rm -f "$output"
    cp "$temp_output" "$output"
    rm -f "$temp_output"
}

convert_markdown() {
    local input=$1
    local output=$2
    local media_dir=$3
    local resource_path=$4

    pandoc_to_file "$output" \
        -f "$PANDOC_FROM" \
        "$input" \
        --top-level-division=chapter \
        --lua-filter=local-md-links.lua \
        --syntax-highlighting=idiomatic \
        -t latex \
        --extract-media="$media_dir" \
        --resource-path="$resource_path"
}

check_book_heading_levels

cd "$LATEX_DIR"

mkdir -p chapters cases

# 使用 --syntax-highlighting=idiomatic 替代已废弃的 --idiomatic
pandoc_to_file "chapters/preface.tex" \
    -f "$PANDOC_FROM" \
    ../src/book/README.md \
    --top-level-division=chapter \
    --lua-filter=remove-numbering.lua \
    --lua-filter=local-md-links.lua \
    --syntax-highlighting=idiomatic \
    -t latex \
    --extract-media=chapters/ \
    --resource-path=../src/book

for chapter in {1..9}; do
    convert_markdown \
        "../src/book/chapter${chapter}.md" \
        "chapters/chapter${chapter}.tex" \
        "chapters/" \
        "../src/book"
done

convert_markdown \
    "../src/book/appendix.md" \
    "chapters/appendix.tex" \
    "chapters/" \
    "../src/book"

for case_number in {0..9}; do
    convert_markdown \
        "../src/experiment/case${case_number}.md" \
        "cases/case${case_number}.tex" \
        "cases/" \
        "../src/experiment"
done

replace_temp="$(mktemp "${TMPDIR:-/tmp}/ascend310-replace-block.XXXXXX.tex")"
python3 replace_block.py chapters/chapter3.tex "$replace_temp"
rm -f chapters/chapter3.tex
cp "$replace_temp" chapters/chapter3.tex
rm -f "$replace_temp"

# 修复 Pandoc 生成无标题 longtable 时引入的错误代码
find chapters cases -name '*.tex' -type f -exec sed -i 's/\\def\\LTcaptype{none}//g' {} +

latexmk -xelatex -interaction=nonstopmode -file-line-error -synctex=1 -halt-on-error book.tex
