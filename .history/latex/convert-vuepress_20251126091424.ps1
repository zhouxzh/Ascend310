pandoc src/book/README.md --top-level-division=chapter --lua-filter=remove-numbering.lua -t latex -o latex/chapters/chapter0.tex
pandoc src/book/chapter1.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/chapters/ --resource-path=src/book -o latex/chapters/chapter1.tex
pandoc src/book/chapter2.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/chapters/ --resource-path=src/book -o latex/chapters/chapter2.tex
pandoc src/book/chapter3.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter3.tex
pandoc src/book/chapter4.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter4.tex
pandoc src/book/chapter5.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter5.tex
pandoc src/book/chapter6.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter6.tex
pandoc src/book/chapter7.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter7.tex
pandoc src/book/chapter8.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter8.tex
pandoc src/book/chapter9.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter9.tex
pandoc src/book/chapter10.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex -o latex/chapters/chapter10.tex
pandoc src/experiment/case0.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case0.tex
pandoc src/experiment/case1.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case1.tex
pandoc src/experiment/case2.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case2.tex
pandoc src/experiment/case3.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case3.tex
pandoc src/experiment/case4.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case4.tex
pandoc src/experiment/case5.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case5.tex
pandoc src/experiment/case6.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case6.tex
pandoc src/experiment/case7.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case7.tex
pandoc src/experiment/case8.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case8.tex
pandoc src/experiment/case9.md --top-level-division=chapter --syntax-highlighting=idiomatic -t latex --extract-media=latex/cases/ --resource-path=src/experiment -o latex/cases/case9.tex
xelatex book
xelatex book
