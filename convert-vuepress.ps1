cd latex
pandoc -f gfm ../src/book/README.md --top-level-division=chapter --lua-filter=remove-numbering.lua -t latex -o chapters/preface.tex
pandoc -f gfm ../src/book/chapter1.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=chapters/ --resource-path=../src/book -o chapters/chapter1.tex
pandoc -f gfm ../src/book/chapter2.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=chapters/ --resource-path=../src/book -o chapters/chapter2.tex
pandoc -f gfm ../src/book/chapter3.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=chapters/ --resource-path=../src/book -o chapters/chapter3.tex
pandoc -f gfm ../src/book/chapter4.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter4.tex
pandoc -f gfm ../src/book/chapter5.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter5.tex
pandoc -f gfm ../src/book/chapter6.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter6.tex
pandoc -f gfm ../src/book/chapter7.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter7.tex
pandoc -f gfm ../src/book/chapter8.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter8.tex
pandoc -f gfm ../src/book/chapter9.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o chapters/chapter9.tex
pandoc -f gfm ../src/experiment/case0.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case0.tex
pandoc -f gfm ../src/experiment/case1.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case1.tex
pandoc -f gfm ../src/experiment/case2.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case2.tex
pandoc -f gfm ../src/experiment/case3.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case3.tex
pandoc -f gfm ../src/experiment/case4.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case4.tex
pandoc -f gfm ../src/experiment/case5.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case5.tex
pandoc -f gfm ../src/experiment/case6.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case6.tex
pandoc -f gfm ../src/experiment/case7.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case7.tex
pandoc -f gfm ../src/experiment/case8.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case8.tex
pandoc -f gfm ../src/experiment/case9.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=cases/ --resource-path=../src/experiment -o cases/case9.tex
latexmk -xelatex -interaction=nonstopmode -file-line-error -synctex=1 -halt-on-error book.tex
cd ..