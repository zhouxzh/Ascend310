#!/usr/bin bash

for file in *.dot; do
    if [ -f "$file" ]; then
        echo $file
        dot -Tpng -Gdpi=300 "$file" -o "${file%.dot}.png"
    fi
done