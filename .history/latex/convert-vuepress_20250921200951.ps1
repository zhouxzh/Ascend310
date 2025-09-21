pandoc ../src/book/README.md --top-level-division=chapter --lua-filter=remove-numbering.lua -t latex -o chapters/chapter0.tex
pandoc ../src/book/chapter1.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter1.tex
pandoc ../src/book/chapter2.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter2.tex
pandoc ../src/book/chapter3.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter3.tex
pandoc ../src/book/chapter4.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter4.tex
pandoc ../src/book/chapter5.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter5.tex
pandoc ../src/book/chapter6.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter6.tex
pandoc ../src/book/chapter7.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter7.tex
pandoc ../src/book/chapter8.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter8.tex
pandoc ../src/book/chapter9.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter9.tex
pandoc ../src/book/chapter10.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter10.tex
pandoc ../src/experiment/case0.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/media --resource-path=../src/experiment -o cases/case0.tex
