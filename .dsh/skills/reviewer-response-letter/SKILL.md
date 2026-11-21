---
name: reviewer-response-letter
description: 审稿人回复意见（response letter / rebuttal letter）的 LaTeX 工作流。用于从作者 DOCX 回复稿迁移出点对点回复信，保持蓝色删除、红色新增的修订标记，按正文编号与排版复现公式，引用论文正式图片并独立编译，逐条核对审稿意见与答复。触发词包括 response letter、审稿意见回复、回复信、rebuttal、reviewer response。
license: MIT
compatibility: 需要 XeLaTeX、Poppler（PDF 位置审计）、Python 3；颜色审计脚本需要 python-docx；图片与公式核对需要可编译的论文源文件。
metadata:
  version: "1.0"
  skill-author: local
---

# Reviewer Response Letter

把作者写好的 Word 回复稿，整理成可独立编译、颜色语义正确、公式与图片和正文一致的 LaTeX response letter。

## 何时使用

- 从 `response letter english.docx` 或中文回复稿生成 LaTeX 回复信。
- 修订稿返修后需要更新回复信中的公式编号、图片或参考文献编号。
- 回复信出现颜色漏标、图片放错、公式排版与正文不一致、旧 DOCX 文字残留。
- 提交前检查 response letter 与 DOCX 是否逐条对应。

## 变更审批门（最高优先级）

**未经作者明确同意，不得修改任何文件，也不得运行编译。**

1. **先提案，不落盘**：每次准备修改前，先列出：
   - 文件路径与位置；
   - 当前原文（逐字摘录）；
   - 拟修改后的完整文本；
   - 修改理由和影响范围（是否影响正文、回复信、PDF、纯文本）。
2. **等待明确同意**：只有作者回复“同意 / 可以改 / 按此修改”等明确指令后，才允许编辑文件。
   - “继续”“看看”“然后呢”不等于批准具体文字修改。
   - 作者要求调整措辞时，重新提案，再次等待确认。
3. **一次只改获批的内容**：不得顺带改其他段落、重排格式或新增解释。
4. **批准后才编译**：编译仅用于验证已获批的修改；不得在提案阶段运行 LaTeX、覆盖 PDF 或重新生成 TXT。
5. **编译后汇报差异**：列出实际修改的文件、页数和验证结果；若编译产生版面变化，先报告，不擅自再改文字。

## 绝对规则（不得违反）

1. **颜色语义固定**：蓝色 `#215E99` = 从原稿删除的文字；红色 `#EE0000` = 修订或新增的文字。不得自创颜色含义。
2. **颜色必须内嵌在原回复中**：在作者原本展示“原文 / 修订文”的位置直接着色。禁止另建 `Revision markup`、`Change made` 等重复段落重复一遍内容。
3. **短删除用行内标记**：只删一个词或短语（如 ``However``）时用 `\textcolor{deletedblue}{...}`，不要用独立 `deletedblock`，否则句号会被挤到下一行。
4. **整段增删用环境**：整段新增用 `addedblock`，整段删除用 `deletedblock`；环境内文字自动着色。
5. **caption 一字不改**：图注必须与作者 DOCX 完全一致，禁止改写、补 “in the revised manuscript:”、禁止把 `Fig.8` 改成 `Fig.~8` 或把 `C1` 改成 `$C_1$`。原 DOCX 中为红色的图注继续标红。
6. **替换图片后删除旧 caption**：用 `\includegraphics` 插入论文图后，必须删除 DOCX 迁移残留的旧图注文字，避免双图注。
7. **图片用论文正式图**：不得从 DOCX 解包 `word/media/` 当图源。把论文目录中的原图复制到 `response letter/images/`，LaTeX 只引用 `images/...`，保证独立编译。
8. **图片与审稿意见对应**：每条意见用哪张图必须逐一核对（例如 Comment 3-3 用 Fig. 3，不能沿用 Fig. 2）。
9. **公式复现正文排版**：回复信引用正文公式时使用 `equation` / `equation*` 显示公式与 `\mathrm` 下标，禁止 `\(...\)` 行内公式加手写 `(16)`。
10. **公式编号以渲染后的正文 PDF 为准**：`\label{eq18}` 只是标签名，不等于显示编号；必须编译正文后核对 PDF 中的实际编号，再用 `\tag{n}` 标注。同一公式只展示一次。
11. **数字与单位同处数学环境**：写作 `\(C_{1}=0.81~\mathrm{nF}\)`，禁止 `\(C_1=0.81\)nF`。
12. **不改写作者答复内容**：只做排版与颜色迁移。不得新增实验、数据、结论或审稿人原文；缺失内容标记待作者确认。
13. **参考文献重编号要说明**：正文新增文献导致引用编号变化时，在相关回复中补一句说明，例如 “the reference numbers have changed because several references were added”。
14. **标题不缩进**：使用 KOMA-Script `parskip=half`，段间留白、不首行缩进；回复信不使用 `scrlttr2`（长文档易编译卡死）。
15. **版本命名跟随投稿历史**：`V1` = 初投稿，`V2` = 当前返修稿；回复信放 `V2/response letter/`，不得因草稿迭代出现 `V3`。
16. **与 cover letter 共享身份元数据**：同一轮投稿中，cover letter 与 response letter 的作者全名（含称谓）、学院、学校、城市邮编国家、邮箱、期刊名、稿件标题、稿号和落款日期必须完全相同。两份文件的版式可以不同，但这些字段必须逐字一致；改一处就要同步另一处，并用 `paper-cover-letter` 的 `scripts/check_letter_identity.py` 核对。
17. **地址只写到机构级**：收件人写 `Editorial Office` + 期刊名即可；作者地址写学院、学校、城市邮编、国家加邮箱，不写楼栋、房间号和门牌号。只有期刊指南明确要求完整邮政地址时才补充。
18. **位置以实际上传的正文 PDF 为准**：每条回复中的 `page X, left/right column, paragraph Y` 必须来自投稿系统实际使用的那一版 PDF（通常是带红字的 markup 稿，因为干净版由系统根据上传文件生成）。本地另编的无颜色稿可能因颜色命令改变断行而分页不同，不能作为页码依据。正文重新排版后必须重跑定位器并更新全部位置；旧位置一律视为待核查。
19. **红字比对源码，蓝字必须消失**：红色段落必须能在正文 LaTeX 源码中逐字找到（参考文献查 `.bib`）；`CLOSE` / `REVIEW` 说明有改写或编号差异，必须人工确认后才可通过。蓝色段落必须在正文源码中找不到；仍能找到即说明“删了但没删干净”。文本一致性不要用 PDF 比对，PDF 只用于位置。
20. **网页投稿的文本框用手写摘要**：投稿系统通常强制填写“编辑部留言”和“审稿人回复”两个文本框。不要从 LaTeX 批量导出全文，也不要重复 25 条逐条回复；两个框各写 1 段纯文本：编辑部框写材料清单、回复结构、主要修改方向与作者/非一稿多投/利益冲突声明，审稿人框感谢各审稿人并指明详细逐条回复在 response letter PDF 附件中。**每个段落必须一整行，禁止硬换行**；详细回复只维护在 `response-letter.tex` 与其 PDF，避免两处内容不一致。
21. **没有独立 cover letter 时并入首页**：若投稿系统不支持 cover letter，就把 cover letter 的三部分内容并入 response letter 首页：提交修订稿声明、三条左右主要修改、结果与作者/非一稿多投/利益冲突声明；随后删除 `cover-letter/` 目录，避免两份文件内容不一致。
22. **PDF 附件不含首页信头**：当投稿系统另有“编辑部留言”框时，上传的 response PDF 默认只保留 Point-by-Point Response，不重复信头、提交声明和签名；另提供 `response-letter-with-cover.tex` 以 `\def\WITHCOVER{}` 构建完整正式信函版备用。
23. **按投稿字段拆分文本**：系统同时提供“给编辑部留言”和“逐条回复审稿人”两个文本框时：
    - 编辑部留言用短文本，只写材料清单、作者/非一稿多投/利益冲突确认，不重复逐条回复；
    - 审稿人回复框用手写短文本：感谢各审稿人、说明全部意见已逐条回应、指明详细回复与修订位置见 response letter PDF 附件，并列出 3-5 条主要修改要点；不要把逐条回复全文粘进文本框；
    - PDF 回复信作为完整附件上传；若没有附件栏，则只填两个文本框。
24. **回复信不写本地文件名**：不得出现 `manuscript-clean.pdf`、`markup-manuscript.pdf`、`response-letter.pdf` 等本地路径或文件名；投稿系统会重命名文件。需要指代时写 "the revised manuscript"、"the manuscript with highlighted revisions"、"the attached point-by-point response"。
25. **编号必须由命令生成**：正文中的文献、图、表、公式一律用 `\cite`、`\ref`、`\eqref`。表格表头尤其容易写成 `[18][12][13][10]` 这类死数字，重排后全错。提交前运行 `scripts/check_hardcoded_refs.py` 扫描；正文出现硬编码即视为缺陷，审稿意见原文中的历史编号除外。
26. **删除段落也要审计位置**：`deletedblock` 在修改稿中找不到，不能因此跳过。用其后紧跟的替换段落反推页位置，二者必须同页；没有替换段落的删除项必须人工确认，定位器会标记 `DELETED (location unverified)`。
27. **改文即重审**：任何压缩、改写或增删之后，必须重跑三项审计——位置（对 markup 稿）、逐句文本、删除项位置；排版上移会让页码连锁失效，不能沿用上一轮结果。

## 目录约定

```text
V2/response letter/
├── response letter english.docx   # 作者原稿，唯一内容来源
├── response letter中文.docx        # 中文对照，可选
├── response-letter.tex            # 正式 LaTeX
├── response-letter.pdf            # 编译产物
├── comment-response-mapping.md    # 意见/答复映射与差异记录
├── README.md                      # 人工复核入口
├── images/                        # 论文正式图副本（进版本库）
└── work/                          # 提取、转换、脚本、日志、预览（gitignore）
```

根目录只保留人工复核需要的文件；`.aux`、`.log`、`.out`、预览 PNG、Pandoc 中间文件全部放 `work/`。`.gitignore` 至少包含：

```gitignore
out/
*.aux
*.bbl
*.blg
*.fdb_latexmk
*.fls
*.log
*.out
*.synctex.gz
*.xdv
response letter/work/
response letter/*.aux
response letter/*.log
response letter/*.out
response letter/*preview*.png
```

## 工作流

### 0. 变更提案与确认

任何编辑或编译前，先按“变更审批门”给出逐字修改提案；收到作者明确同意后再进入第 1 步。

### 1. 摸清源文件

- 读作者 DOCX、中文对照稿（如有）、投稿意见原文。
- 用 `scripts/audit_docx_colors.py` 导出全部带颜色段落的 JSON，确认蓝/红段落总数。
- 统计审稿人意见条数，与 LaTeX 的 comment 块数量对齐（示例：Editor 1 + R1 6 + R2 4 + R3 14 = 25 块）。

### 2. 搭 LaTeX 骨架

复制 `references/latex-template.tex` 的前言与首页信头，保留：

- KOMA-Script `scrartcl`、A4、11pt、`DIV=12`、`parskip=half`；
- `reviewercomment` 灰底环境与 `response` 蓝底环境；
- `deletedblue`、`addedred`、`addedblock`、`deletedblock`；
- 首页德式信头只出现一次，`\clearpage` 后进入点对点正文。

### 3. 迁移内容

- 按 Reviewer → Comment 顺序迁移意见原文与作者答复。
- 整段新增/删除包环境；词级删除用行内颜色。
- 图注照抄 DOCX；图片改成 `images/xxx` 引用；删掉旧文字。
- 公式改成显示环境、`\mathrm` 下标、正确编号。

### 4. 颜色全量核对

逐段比对 `audit_docx_colors.py` 的输出与 `response-letter.tex`：

- 每个蓝色段落都要在 LaTeX 中出现且标蓝；
- 每个红色段落都要标红；
- 没有 DOCX 颜色依据的内容保持黑色（解释性答复、版式说明）。

常见漏标位置：整段参数敏感性分析、温度边界分析、表格脚注、新增文献正文与参考文献条目、图注。逐页渲染抽检至少覆盖：

```powershell
pdftoppm -f 8 -l 8 -png -r 120 response-letter.pdf work\p8
pdftoppm -f 11 -l 12 -png -r 120 response-letter.pdf work\p11
pdftoppm -f 22 -l 22 -png -r 120 response-letter.pdf work\p22
pdftoppm -f 24 -l 27 -png -r 120 response-letter.pdf work\p24
```

### 5. 公式与编号核对

1. 编译正文 PDF：`pdflatex → bibtex → pdflatex → pdflatex`。
2. `pdftotext -layout` 搜索公式关键字，记录实际显示编号。
3. 用 `\tag{实际编号}` 写入回复信；无编号公式用 `equation*`。
4. 全文搜索 `\(` 中是否还有手写编号、`T_{UP}`、`C1=`、`nF` 出界等旧写法。

### 6. 红蓝文字一致性审查（源码对源码）

两侧都是 LaTeX，所以文本比对直接用源码，不经过 PDF：

```powershell
python scripts/verify_revision_text.py `
  --manuscript V2/sn-article.tex `
  --response "V2/response letter/response-letter.tex" `
  --bib V2/ref.bib `
  --aux V2/sn-article.aux `
  --markdown "V2/response letter/work/text-audit.md"
```

`--aux` 很关键：脚本从 LaTeX 的 `.aux` 读取 `\newlabel` 与 `\bibcite`，把正文里的 `\ref{fig_C1}`、`\eqref{eq18}`、`\cite{...}` 换成最终印刷编号，再与回复信中写死的 `Fig. 8`、`(16)`、`[7]` 比对。不要靠猜编号。

判定：

| 状态 | 含义 | 处理 |
|---|---|---|
| `VERBATIM` | 红字在正文源码中逐字存在（覆盖率 1.0） | 通过 |
| `CLOSE` | 覆盖率 ≥0.85，多为 `Fig.~8` 对 `\ref{...}`、单位空格差异 | 人工确认编号一致 |
| `REVIEW` | 覆盖率 0.30–0.85，红字有改写或句子顺序不同 | 逐条人工核对，必要时改回正文原句 |
| `MISSING` | 覆盖率 <0.30，正文中找不到 | 失败 |
| `DELETED (ok)` | 蓝字已不在正文 | 通过 |
| `STILL-PRESENT` | 蓝字仍留在正文 | 失败 |

注意：

- 参考文献条目自动标为 `REFERENCE (skip)`，不参与正文文本比对；
- `\ref`/`\eqref`/`\cite` 通过 `--aux` 换成真实编号后比对，剩余 `CLOSE` 通常只是单位空格或标点差异；
- 表格脚注、公式重排可能落入 `REVIEW`，以人工比对为准；
- **红字必须是正文原句**。如果一段红字只是“回复里的概述/解释”，应改为黑色；继续标红会被判为 `REVIEW`，因为它在正文中并不逐字存在。
- 只要仍有 `CLOSE` 或 `REVIEW`，脚本退出码为 `2`，状态是 `MANUAL_REVIEW_REQUIRED`；作者签认并消除未决项后才算通过。

### 7. 位置定位与位置审查

审稿人要求“修改处必须给页码/栏/段落”时，用 `scripts/locate_manuscript_positions.py`。它以最终编译 PDF 的坐标为准，不依赖源码行号，因此重排版后仍可重新计算。

```powershell
python scripts/locate_manuscript_positions.py `
  --pdf V2/out/sn-article.pdf `
  --response "V2/response letter/response-letter.tex" `
  --markdown "V2/response letter/work/positions.md"
```

脚本行为：

- 从 `addedblock` / `deletedblock` 抽取修订段落；
- 在 PDF 中按“页 → 栏 → 段落 → 行”定位；
- 读取回复中已写的 `page N ... column ... paragraph N` 并比对；
- `deletedblock` 在修订稿中找不到 = 正确（已删除）；找到了 = `STILL-PRESENT` 错误；
- `deletedblock` 的页码通过其后紧跟的替换段落反查：同页 = `DELETED (location ok)`，不同页 = `DELETED @PAGE a->b` 错误，无替换段落 = `DELETED (location unverified)` 需人工确认；
- 新增段落找不到 = `NOT-FOUND` 错误；
- 页码不符 = `PAGE a->b` 错误；栏/段不符标为 `COLUMN?` / `PARA?`，需人工复核，因为 Poppler 有时把双栏合并成一个 block；
- 若正文 PDF 带 `lineno` 行号，会附带提取印刷行号。

结果写入 `work/positions.md`，不要提交。页码全部通过后才算位置审查完成；脚本退出码 `2` 表示仍有删除项或栏/段位置需要作者签认，不是通过。作者必须处理 `MANUAL_REVIEW_REQUIRED` 后才能继续。

### 8. 网页投稿的两个文本框（手写，不自动导出）

投稿系统强制填写“编辑部留言”和“审稿人回复”两个框，而详细逐条回复已由 `response-letter.pdf` 附件承担，因此两个框均为手写摘要：

- 编辑部留言：材料清单（clean manuscript、markup manuscript、point-by-point response）、回复结构（先编辑部、后 Reviewer 1/2/3）、3-5 条主要修改方向、作者/非一稿多投/利益冲突声明，不重复逐条回复；
- 审稿人回复框：感谢各审稿人、说明全部意见已逐条回应、指明详细回复与修订位置见 PDF 附件，并列出 3-5 条主要修改要点；
- 每个段落必须一整行，不能按列宽硬换行；不出现 LaTeX 命令；
- 两个 txt 直接手工维护，不再由脚本生成；若此前存在自动导出脚本，应删除以免误覆盖手写内容。

### 9. 独立编译与提交检查

```powershell
xelatex -interaction=nonstopmode -halt-on-error response-letter.tex
xelatex -interaction=nonstopmode -halt-on-error response-letter.tex
```

- 在 `response letter/` 目录内编译成功，不依赖论文根目录图片。
- 日志无 `^!`、`Undefined`、`Missing character`、图片缺失。
- 运行 `scripts/check_response_letter.py` 做静态检查；
- 运行 `scripts/check_hardcoded_refs.py V2/<manuscript>.tex --strict`，正文不得残留硬编码编号；回复信运行 `scripts/check_hardcoded_refs.py response-letter.tex --letter --strict`，审稿意见原文中的历史编号可保留。

## 常见故障与修法

| 现象 | 原因 | 修法 |
| --- | --- | --- |
| 句号单独占一行 | 词级删除被包成独立环境 | 改行内 `\textcolor{deletedblue}{...}` |
| 图注重复 | 插图表后旧 DOCX caption 未删 | 删除旧 caption，只留 LaTeX 图注 |
| 图注被改写 | 迁移时“顺手润色” | 逐字恢复 DOCX 原文 |
| 图片放错 | 复用相邻意见的图 | 按审稿意见逐条核对图号 |
| 公式编号错误 | 把 `\label{eq18}` 当显示编号 | 编译正文 PDF 查实际编号 |
| 公式不像正文 | 行内 `\(...\)` + 手写编号 | 改 `equation` + `\tag{n}` |
| 回复信一页彩色缺失 | 整段新增未包 `addedblock` | 对照颜色审计 JSON 逐段补齐 |
| 图片找不到 | 引用 `../FIG.png` | 复制到 `images/` 并改相对路径 |

## 提交前清单

- [ ] 每处文字修改都先给出逐字提案，并获得作者明确同意
- [ ] 未在提案阶段运行编译或覆盖 PDF
- [ ] 25 个 comment/response 块与审稿意见一一对应
- [ ] 蓝色删除、红色新增与 DOCX 逐段一致
- [ ] 无重复 `Revision markup` 段落
- [ ] 图注逐字等于 DOCX 原文，且颜色正确
- [ ] 图片来自 `images/`，每张图与图号匹配
- [ ] 公式为显示环境、`\mathrm` 下标、编号与正文 PDF 一致
- [ ] 数字与单位同处数学环境
- [ ] 参考文献重编号有说明
- [ ] 每条修订段落都有 page/column/paragraph 位置
- [ ] 定位器针对**实际上传的 markup 稿**复核通过（页码全部匹配，栏/段差异已人工确认）
- [ ] 删除段落位置已通过替换段落反查同页，或已人工确认
- [ ] 正文无硬编码文献/图/表/公式编号（`check_hardcoded_refs.py` 无正文命中）
- [ ] 回复信与两个文本框不出现任何本地文件名
- [ ] 红字逐字比对正文 LaTeX 源码（CLOSE/REVIEW 已人工确认）
- [ ] 蓝字在正文源码中已不存在
- [ ] 独立 XeLaTeX 编译无错误
- [ ] `work/` 与构建产物未进入版本库
- [ ] 版本目录为 V1（初投）/ V2（返修）
