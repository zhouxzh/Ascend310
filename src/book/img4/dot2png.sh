#!/usr/bin bash

for file in *.dot; do
    if [ -f "$file" ]; then
        dot -Tpng -Gdpi=300 "$file" -o "${file%.dot}.png"
    fi
done