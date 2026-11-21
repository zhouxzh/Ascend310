---
name: paper-latex-cn
description: 中文论文 LaTeX 工作区的结构与排版约定（scrartcl + ctex + biblatex APA + XeLaTeX）。当新建论文工作区、调整 main.tex 前言、增删章节、修 overfull box、处理缺字形或未定义引用、或需要 make pdf 构建与排错时使用。
license: MIT
compatibility: 需要 XeLaTeX + latexmk + biber；这是中文内部工作区格式。英文期刊排版依赖外部 latex-paper-en 或期刊官方模板。
metadata:
  version: "1.0"
  skill-author: local
---

# Paper LaTeX (Chinese)

一套中文论文**内部工作区**的固定约定，不是投稿模板。目标是：新论文开工时不用重新决定排版，编译问题时按固定顺序排查。目标期刊提供官方模板时，官方模板优先；英文 IEEE/期刊排版使用 `latex-paper-en` 或对应期刊 skill，本 skill 不替代它们。

## 何时使用

- 新建一篇论文的工作区。
- 修改 `main.tex` 的前言、宏包或章节顺序。
- 编译报缺字形、未定义引用、overfull box。
- 需要判断 PDF 是否需要重编。

## 工作区约定

```text
<paper>/
├── main.tex              # 前言 + \input 章节
├── Makefile              # make pdf / watch / clean
├── references.bib        # 书目（可另加 references_extra.bib）
├── sections/
│   ├── abstract.tex
│   ├── introduction.tex
│   ├── related_work.tex
│   ├── method.tex
│   ├── experimental_setup.tex
│   ├── results.tex
│   ├── discussion.tex
│   ├── conclusion.tex
│   ├── declarations.tex
│   └── appendix/notes.tex
├── evidence/             # 紧凑证据表
├── provenance/           # 代码仓与 commit
└── audit/                # claim map、作者核验清单
```

- 文档类：`scrartcl`，A4，10pt，`DIV=10`。
- 中文：`\usepackage[UTF8,scheme=plain]{ctex}`；必须用 XeLaTeX 编译。
- 书目：`biblatex` + `style=apa` + `backend=biber`。
- 表格：`booktabs` 三线表，不用竖线。
- 文件首行写 `% !TeX program = xelatex`，让编辑器和构建工具都能识别引擎。

## 章节写法

- **摘要**：问题 → 方法 → 数据与坐标 → 主结果 → 结论边界，五段各一句到两句；不写未绑定数字。
- **引言**：只承诺正文能兑现的贡献；贡献列表控制在 3–4 条。
- **方法**：写“实际做了什么”，不写“应该怎么做”；每个符号只有一种含义。
- **结果**：先给表，再解读；表里出现的每个数字都要能回到 `evidence/`。
- **讨论**：把解释、局限、负结果分开写；不要把相关性写成因果。
- **声明**：作者、基金、利益冲突、伦理、数据与代码可用性、AI 披露，缺一项就留 TODO，不要套模板。

## 表格与图

- 表题在上，图题在下；caption 写清对象、范围和单位。
- 数字位数与正文一致；同一列不要混用不同精度。
- 每张图维护 `figure ID → 源文件 → 生成脚本 → 导出文件 → caption/alt text`；不要只交 PNG。
- 不要用 `\resizebox` 硬压表格来消除 overfull —— 先改列宽或拆表。

## 编译与排错顺序

按这个顺序查，前一项不通过就不要看后一项：

1. **缺字形**（`Missing character`）：字体没有该字符，换字体或改字符，不要忽略。
2. **未定义引用**（`Citation ... undefined` / `Reference ... undefined`）：`biber` 是否跑过、key 是否拼错。
3. **硬错误**（`! ...`）：按日志给出的文件与行号修，不要靠反复重编。
4. **overfull box**：先看 `pt` 数值，超过 10pt 的必须处理。
5. **页数与产物新鲜度**：`main.pdf` 必须比所有 `.tex`、`.bib` 新。

## 与工具的分工

- `paper_scaffold` 生成上面这套骨架；已存在的文件不会被覆盖。
- `paper_build` 跑 latexmk 并返回结构化诊断（引擎、页数、错误、缺字形、overfull）。
- `paper_audit mode=draft|submission` 检查 PDF 新鲜度、引用、证据和 claim 状态；投稿前使用 `submission` 模式。

## 禁止

- 手改 `main.pdf`、`main.bbl` 或 `latex/` 生成物。
- 用 `\nocite{*}` 掩盖“条目没被引用”的问题（除非确实要全量列出）。
- 用 `\vspace`、`\!` 之类硬凑版面来掩盖结构性排版问题。
- 在未确认编译通过前就宣称稿子完成。
