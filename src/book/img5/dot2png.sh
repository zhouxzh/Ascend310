#!/usr/bin/env bash
set -euo pipefail

for file in *.dot; do
    [ -f "$file" ] || continue
    dot -Tpng -Gdpi=300 "$file" -o "${file%.dot}.png"
done
