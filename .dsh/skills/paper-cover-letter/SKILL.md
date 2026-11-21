---
name: paper-cover-letter
description: 期刊投稿与返修 cover letter（投稿信/附信）的 LaTeX 工作流。用于初投稿、major/minor revision 返修信，按 KOMA-Script 欧洲学术信函版式生成一页正式英文信，核对标题、稿号、作者、单位、邮箱、图页数与所有量化结论。触发词包括 cover letter、投稿信、附信、submission letter、revision letter。
license: MIT
compatibility: 需要 XeLaTeX；PDF 页数检查需要 pdfinfo；量化结论核对需要 manuscript .tex，页数声明核对需要 compiled manuscript PDF。
metadata:
  version: "1.0"
  skill-author: local
---

# Paper Cover Letter

为期刊投稿或返修生成一页正式英文 cover letter。版式继承作者 V1 的欧洲学术信函样式：右上发件人、左侧编辑部地址、日期、主题行、三段正文、右对齐签名。

## 何时使用

- 初次投稿需要 submission cover letter。
- 收到 major/minor revision，需要 revision cover letter。
- 已有 cover letter 需要按新稿号、新日期、新结论更新。
- 需要核对投稿信中的标题、作者、单位、邮箱、页码与数字是否和正文一致。

## 绝对规则（不得违反）

1. **一页为默认目标**：cover letter 不是摘要复述，超过一页通常说明写多了。删除铺陈，只留“投什么、为什么合适、改了什么、声明什么”。
2. **标题、期刊名、稿号逐字一致**：标题必须与 `sn-article.tex` 的 `\title{}` 完全一致；稿号必须与投稿系统一致；不得凭记忆手打。
3. **量化结论必须有出处**：信中每个百分比、页数、频率、电压都要能在正文或编译后的 PDF 中找到。找不到就不写。
4. **不得声称未完成的合规性**：如“fits within the 10-page limit”，必须先编译正文并核对实际页数；页数超标时不得写此句。
5. **返修信不是新投稿**：revision cover letter 必须写明稿号、原决定（major/minor revision）、已逐条回复审稿意见，不得写“please consider our new submission”。
6. **不承诺未做的事**：禁止“all experiments were repeated”“we have added extensive new experiments”等未经证实表述。
7. **不吹捧**：禁止 “groundbreaking”“novel paradigm”“we are delighted/ thrilled” 一类营销语。用事实句。
8. **作者身份唯一**：发件人与签名统一为通讯作者；共同一作用 † 只在正文中体现，不在信头重复。
9. **日期只出现一次**：信头日期与系统提交日期一致；不要同时写两个日期。
10. **校样后删除占位符**：`[Journal]`、`[Manuscript ID]`、`XX%`、`TBD` 一律不得残留。
11. **与 response letter 共享身份元数据**：同一轮投稿中，cover letter 与 response letter 的作者全名（含称谓）、学院、学校、完整地址、邮箱、期刊名、稿件标题、稿号和落款日期必须完全相同。两份文件版式不同不是问题，但这些字段必须逐字一致；修改后运行 `scripts/check_letter_identity.py` 核对。
12. **网页投稿另存纯文本**：投稿系统只提供文本框、不要求上传 PDF 时，必须同时生成 `<name>.txt`。纯文本只保留日期、`Dear Editor`、正文、`Yours sincerely` 与签名，不含信头、编辑部地址和任何 LaTeX 标记。**每个正文段落必须是一整行，禁止按 80 字符硬换行**；只在日期、称呼、段间、签名之间保留空行或换行，否则粘贴进网页表单会变成断句。
13. **正文要短**：revision cover letter 控制在 220–400 词、3–4 段。只讲“提交修订稿、3 条左右核心修改、结果与声明”，逐条细节留给 response letter。

## 版式约定

- 文档类：`scrlttr2`，`paper=a4`，`fontsize=10pt`，`version=last`。
- 版式文件：随 skill 提供的 `references/european-academic-letter.lco`（DINmtext、`fromalign=right`、`fromrule=afteraddress`、`parskip=half`、`subject=titled`）。
- 字体：`newtxtext` + `microtype`。
- 段落：`parskip=half`，不首行缩进；`\clubpenalty`、`\widowpenalty` 设为 10000。
- 收件人：只写 `Editorial Office` + 期刊名。线上投稿不需要街道地址；仅当期刊投稿指南明确要求邮寄地址时才补全。
- 签名：`Dr. <Corresponding Author>`、`Corresponding Author`、学院、学校、邮箱。
- 信头地址：学院、学校、城市邮编、国家，共三行 + 独立邮箱行；不写楼栋、房间号和门牌号，除非期刊明确要求。禁止依赖自动换行——`scrlttr2` 会把 `No.` 与门牌号拆到两行，既不美观也会造成首页 overfull。用 `\AuthorAffiliationLines` 手写换行，地址正文用 `\scriptsize`，邮箱前用 `\\[-0.6ex]` 收紧。

## 正文结构

### 初投稿（initial submission）

1. **提交声明**：代表全体作者提交标题为 “…” 的稿件，申请作为 Research/Original Article 在 *Journal* 发表。
2. **问题与贡献**：一段说清研究问题、方法核心、关键实验结果（2–3 个数字，全部可追溯）。
3. **期刊契合度与声明**：未一稿多投、全体作者同意、无利益冲突；感谢审阅。

### 返修（revision）

1. **返修声明**：按稿号与决定提交修订稿；附逐条回复信。
2. **主要修改**：压缩成 1 段，只保留 3 条左右最关键的修改；细节不要逐条复制 response letter。
3. **结果与声明**：一句保留核心结果，随后写作者身份、非一稿多投、无利益冲突；感谢编辑与审稿人。
4. **纯文本版**：同步输出 `.txt`，供网页投稿系统直接粘贴。

## 工作流

1. 读正文 `\title{}`、作者块、摘要、结论与关键结果段，记录可引用数字。
2. 读返修决定邮件与 response letter，提炼 3–6 条主要修改。
3. 从 `references/initial-submission.tex` 或 `references/revision.tex` 复制模板。
4. 填写元数据（期刊、稿号、日期、收件人、发件人）。
5. 写正文，控制在一页；编译后用 `pdfinfo` 核对页数。
6. 运行 `scripts/check_cover_letter.py cover-letter.tex --pdf out/cover-letter.pdf --manuscript <manuscript>.tex --manuscript-pdf <compiled-manuscript>.pdf`；有量化结论或页数声明时缺少对应正文证据会阻断。再人工通读一遍。

## 编译

```powershell
xelatex -interaction=nonstopmode -halt-on-error -output-directory=out cover-letter.tex
xelatex -interaction=nonstopmode -halt-on-error -output-directory=out cover-letter.tex
```

输出目录建议：

```text
V2/cover-letter/
├── cover-letter-JPE-revision.tex
├── cover-letter-JPE-revision.txt   # 纯文本备份，便于系统粘贴
├── european-academic-letter.lco
└── out/
    └── cover-letter-JPE-revision.pdf
```

## 检查清单

- [ ] 标题与正文 `\title{}` 完全一致
- [ ] 期刊名、稿号、决定类型正确
- [ ] 日期只有一个且正确
- [ ] 收件人地址完整
- [ ] 三段结构完整，无空话
- [ ] 所有数字可在正文/PDF 中找到
- [ ] 未声称未验证的页数或实验
- [ ] 已附 response letter（返修）
- [ ] 一页以内
- [ ] 无占位符、无 TODO
- [ ] 签名、单位、邮箱完整
- [ ] 正文 220–400 词、3–4 段
- [ ] 网页投稿另有纯文本 `.txt`，无 LaTeX 标记
