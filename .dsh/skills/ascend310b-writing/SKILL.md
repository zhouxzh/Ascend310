---
name: ascend310b-writing
description: Ascend310 教材仓库的中文技术写作规范与守门流程。在新增、重命名或改写章节/案例/附录，调整 sidebar 与索引，审查 Markdown 命名、锚点、链接、图片、标点与行尾，出 LaTeX/PDF 后检查缺字形与产物新鲜度，准备 commit/push 前做推送门槛检查，或需要统计写作进度与篇幅、以及用户要求润色/去 AI 味时使用。触发后先跑 ascend310b_writing_audit，再按规则修正文，复跑确认；出 PDF 后跑 ascend310b_writing_render，推送前跑 ascend310b_writing_push_check。
---

# 教程写作守门

本仓库是《昇腾 310B 边端 AI 实战》教材，正文以 Markdown 为唯一真源，
VuePress 站点与 LaTeX/PDF 都是生成物。写作改动的判据只有一条：
**改 Markdown 源文件，跑检查，让生成物自己更新。**

## 先看规则，再动手

- 章节、案例、附录的公开名称是冻结的（见仓库 `AGENTS.md` 的
  「Frozen textbook naming」）。`第N章：`、`案例 N：`、`附录 N：` 的
  编号、空格与全角冒号都不得改，除非用户明确要求改名。
- 改名必须一次改全：源文件标题、`src/.vuepress/sidebar.ts`、对应
  `README.md` 索引，以及 `src/presentation/` 下配对的 Marp 幻灯。
- 不要把 `src/experiment/case4.md` 改成依赖其他仓库文档；它必须自包含。

## 标准流程

0. **换机器或第一次用**：先跑 `ascend310b_writing_toolchain`。它报告这台机器缺哪些构建
   工具、按平台给出安装命令（含 Anaconda/conda、Git Bash 与 WSL 的区别），
   并说明哪些能力可用。正文检查不需要任何外部工具；只有出 PDF 才需要工具链。
   本仓库里 `python3` 缺失不算故障——若你用 Anaconda，插件会自动找到
   `anaconda3` 的 python 并在构建时提供 `python3`。
1. 跑 `ascend310b_writing_audit`（可带 `scope` 限定 book/experiment/appendix）。
   先看 error，再看 warning；info 是风格提示。它会顺带把 `AGENTS.md`
   冻结名表和正文实际标题双向比对。
2. 按规则修**源 Markdown**，不要改生成物
   （`latex/`、`src/.vuepress/dist`、`src/.vuepress/.cache`）。
3. 复跑 `ascend310b_writing_audit`，直到 error 归零；warning 若有意保留
   （例如 Markdown 强制换行的行尾空格），在回复里说明。
4. 需要站点预览时跑 `pnpm run docs:dev`，把本地地址交给用户确认；
   未经用户确认不要 push，也不要因为 `docs:build` 通过就认为可以 push。
5. 要出 PDF 时用 `ascend310b_writing_build`（先不带 `confirm` 看预检，确认后带
   `confirm: true` 起后台任务，用 `job_output` 读）。它会在同一任务里自动跑渲染体检与
   产物锚点核对，所以**不需要**再单独跑 `ascend310b_writing_render`。
   若报 `artifact-anchor-unparsed`（标题被转义成字面文本、或 `{#id}` 被当成标题文字），
   **重新构建修不好**，要改源 Markdown；常见原因是标题行前面少了空行，或标题里有未转义的 `_`。
   缺字形同样改源 Markdown（例如把 `→` 写成 `$\to$`），不要改生成的 `latex/*.tex`。
   组成里没有后台任务时，退回 `./convert-vuepress.sh` + `ascend310b_writing_render`。
6. 准备 push 前跑 `ascend310b_writing_push_check`。它把阻塞项（正文 error、提交树断链）
   和提示项（未提交正文、渲染结论、产物新鲜度）分开列，并在最后点名唯一
   无法机械验证的一项：站点预览是否已由用户确认。
7. 要汇报篇幅或剩余工作量时跑 `ascend310b_writing_progress`。

## 课件制作规范（Marp）

演示文稿不是教材的摘要，而是**上课直接照着讲的材料**。规范如下，`ascend310b_writing_audit` 会逐条检查。

### 课件视觉规范（Metropolis 风格）

本仓库采用 Marp + **Metropolis 风格**主题（`src/presentation/theme.css`，参考 Beamer 的 Metropolis，MIT）：

- 深板岩底色 `#23373B` 用于封面与章节页，内容页白底；
- 全篇只用一个强调色：橙色 `#EB811B`（引用块左边线、表格表头下划线、代码块左边线、顶部细线）；
- 标题深墨色 `#1B2A2F`，正文青绿色只用于 `strong` 与链接；
- 封面只放标题、一句话内容承诺、作者/邮箱、来源；不放“教师授课版”等行政标签；
- 引用块按 Beamer `block` 使用：一个结论、一个边界或一条课堂提示；
- 表格整宽、深色表头；代码块深色 + 橙色左边线；
- 页脚固定显示作者/邮箱与页码，页眉显示教材名。

不允许逐份课件自行改配色、字号体系或页脚位置；主题漂移由 `deck-theme-unsupported` 拦截。
### 基本课件要求（缺一项就不算完成件）

| 要求 | 判据 | 规则 |
| --- | --- | --- |
| 封面页 | 第一页带 `<!-- _class: cover -->` | `deck-cover-missing` |
| 学习目标或主线 | 回答"这讲解决什么问题"，不要从背景铺垫开始 | 人工检查 |
| 每页一个论点 | 标题是断言，不是话题（"板端最小验证顺序" ✓，"关于验证" ✗） | `deck-title-too-long` |
| 小结或课堂任务 | 最后必须有收束与自检 | `deck-summary-missing` |
| 讲师备注 | 每页至少一句"强调什么/常见误区/提问点" | `deck-notes-sparse` |
| 来源可追溯 | 写明对应 `src/` 或 `samples/` 路径 | `deck-source-missing` |
| 脚手架标记 | 占位课件必须带 `<!-- presentation-scaffold: id -->` | `deck-scaffold` |

### 远端 Pages 构建

push 之后必须用 `ascend310b_writing_ci` 确认 GitHub Pages 工作流成功，而不是只看本地 `docs:build`。本地构建成功只证明源码能构建；Actions 是全新检出，可能因为被忽略的模型文件、大小写路径或缺锁文件而失败。

- `ci-pages-failed`：读 run 日志里的失败步骤与错误行，先修源码，再推送；
- `ci-pages-pending`：等待完成；
- `ci-remote-behind`：本地还有未推送提交，远端构建不代表它们；
- 失败日志里的路径如果指向 `.gitignore` 中的运行时产物（如 `.om`），应修 CI 校验逻辑，不要把模型文件塞进仓库。
### 封面写法

封面只放四样东西：

1. 标题（章节/案例名，冻结写法）；
2. 一句内容承诺：学生学完能判断什么、做出什么；
3. 作者与邮箱：`周贤中 · zhouxzh@gdut.edu.cn`，用 `<div class="byline">`；
4. 依据与样例路径（可放在页脚）。

不要出现“教师授课版”“理论章节课件”“案例课件”这类自我标注。读者打开的就是课件，不需要被告知这是课件；这类标签由 `deck-cover-meta-label` 拦截，缺署名由 `deck-cover-byline-missing` 拦截。
### 生成网页的密度审计

课件最终以 Marp 生成的网页放映。插件读取 `dist/presentation/*.html` 的每个 `<section>`，按“中文 1 字 = 1 单位、英文 1 词 = 2 单位”计算：

| 页面类型 | 合理范围 | 规则 |
| --- | --- | --- |
| 正文页 | 70–280 单位 | `deck-web-density-low` / `high` |
| 表格页 | 90–340 单位；2–10 行；2–7 列 | 同上 |
| 代码页 | 4–30 行；70–320 单位 | 同上 |
| 图片页 | 至少 15 单位说明（这张图证明什么） | `deck-web-density-low` |
| 整份课件 | 正文/表格页最大值 / 最小值 < 2.8，或差 < 90 单位 | `deck-web-density-uneven` |
| 主题 | 每页 `data-theme="ascend310"` | `deck-web-theme-drift` |

封面、章节分隔页不参与密度比较。学术报告的节奏应当稳定：同层次的相邻页信息量接近，不能一页三行、下一页两百字。
### 信息密度（本轮新增）

课件要"讲得满"，不是"字满"：每页都要有可讲的内容，但不要用大段文字填满。

| 指标 | 下限 | 规则 |
| --- | --- | --- |
| 单页正文（散文页，无代码/表格/图） | ≥ 40 字 | `deck-slide-underfull` |
| 整份课件 | ≥ 6 页且 ≥ 800 字 | `deck-content-thin` |
| 页面标题 | ≤ 30 字 | `deck-title-too-long` |

实测当前 28 份课件：散文页共 90 页，没有一页低于 40 字；真正稀疏的是 9 份 **4 页脚手架**（约 380 字），它们被单独标为 `deck-scaffold`，不计入完成件。
### 结构与元数据

frontmatter 必填四项，缺一不可：`marp: true`、`theme: ascend310`、`paginate: true`、`size: 16:9`（16:9 = 1280×720）。
`footer` 写 AGENTS.md 的冻结名（如 `案例 1：智能考勤机`），`header` 统一为教材名。每个课时用二级标题分段。

### 每页容量（超出必须分页，不许缩字号）

| 元素 | 上限 |
| --- | --- |
| 正文汉字 | 320 字 |
| 代码行 | 22 行（纯代码页） |
| 表格 | 6 列 × 10 行 |
| 列表 | 8 条 |

主题把每页钉在 720px 且 `overflow: hidden`，**超出的内容会直接消失**，不会滚动。Marp 的自动缩放只解决横向，
官方文档也明确写了纵向仍可能溢出，所以纵向必须用分页解决。分页脚本：`node scripts/split-decks.mjs --write <文件>`。

### 投影可读性

代码与表格字号**不得低于 20px**，正文 26px，表格 21px。低于这个值在教室后排看不见——宁可多分两页，也不要缩字号。

### 可追溯

- 课件必须写明对应教材：`src/book/…`、`src/experiment/…`、`src/appendix/…` 或 `samples/…`；
- 代码页要标出代码来自哪个文件，否则课件会和代码脱节；
- 图片必须有 alt 文字，并给出图源。

### 无障碍与排版（合并自 markdownlint）

| 规则 | 来源 | 要求 |
| --- | --- | --- |
| `MD045` | markdownlint | 图片必须有 alt |
| `MD040` | markdownlint | 代码块必须有语言标记 |
| `MD034` | markdownlint | 禁止裸 URL（正文里写 `[说明](url)`；行内代码里的地址不算） |

### 讲师备注

Marp 支持演讲者备注（HTML 注释），放映时按 `p` 打开演讲者视图；`marp --notes` 导出文本，`--pdf-notes` 写进 PDF。
备注放"这页要强调什么、常见误区、提问点"，不要把讲稿搬到幻灯片上。

> 规范来源：[Marp CLI](https://github.com/marp-team/marp-cli)（备注、`--notes`、元数据、自动缩放限制）、
> [Marp Core](https://github.com/marp-team/marp-core)（16:9 尺寸、`size` 指令、auto-scaling 仅横向）、
> [markdownlint](https://github.com/DavidAnson/markdownlint)（MD034 / MD040 / MD045，accessibility 标签）。

## 去 AI 味（用户要求润色时才做）

这是这本书最常被抱怨的问题：**内容是对的，但读起来像产品宣传稿或大模型输出**。先跑
`ascend310b_writing_style` 拿到候选句，再按下面的方法改。**它是提示不是判定**：同一个词在某句
里可能恰好准确，逐句判断，不要机械替换。

### 四类问题与改法

| 类型 | 典型表达 | 改法 |
| --- | --- | --- |
| **营销腔** | 极致、出色、卓越、无缝、全能型选手、杀手锏、护城河、脱颖而出、赋能、助力、打造、一站式、全方位 | 用**可验证的事实**替换：数字、接口名、行为对比。形容词删掉不丢信息就说明它本来没信息 |
| **空转套话** | 综上所述、总而言之、众所周知、不难发现、由此可见、值得注意的是、毋庸置疑 | 整句删掉。真要提醒就写具体："注意：先 `source set_env.sh`" |
| **元话语** | 本文将、本节将、下面让我们、让我们一起来、读者可以 | 直接进入内容。读者不需要被告知"接下来要讲什么" |
| **对话残留** | 正如您所指出的、您提到、希望对您有帮助、如有需要 | 删掉称呼与收尾语，改成直接陈述。**这是最强的 AI 痕迹**，通常来自把聊天回答直接贴进正文 |
| **名词化空动词** | 对……进行……、加以说明、使……得到提升 | 直接写动词："优化算法"而不是"对算法进行优化" |
| **无源权威** | 研究表明、数据显示、业内普遍认为、专家指出（后面没有出处） | 教材里这是硬伤：写清谁说的、哪来的数据，否则删掉铺垫直接给判断 |
| **机器残留** | `oaicite`、`turn0search`、`[cite: 1]` | 从模型输出里粘进来的 token，直接删 |

### 前后对照（取自本书真实句子）

| 改前 | 改后 | 为什么 |
| --- | --- | --- |
| `**英特尔**则展现出"全能型选手"的特质。它不仅以CPU处理通用计算任务，还通过Movidius视觉处理单元等专用芯片强化AI视觉推理。` | `英特尔同时提供通用 CPU 与 Movidius 视觉处理单元：CPU 承担通用计算，VPU 加速视觉推理。` | "全能型选手"是宣传语；去掉引号里的评价，只留它到底提供了什么 |
| `这款芯片提供极致的性能与出色的能效。` | `该芯片在 5–8 W 典型功耗下提供 8 TOPS INT8 推理算力。` | 形容词换成可核对的数字 |
| `边缘节点的硬件选择，正如您所指出的，本质上是功耗、体积和成本之间的权衡。` | `边缘节点的硬件选择，本质上是功耗、体积和成本之间的权衡。` | 删掉对话残留的称呼 |
| `综上所述，CANN 在异构计算中具有重要地位。` | （整句删除，或在确有结论时写：`CANN 把框架差异收敛到 ATC 一次转换里`） | 空转套话 + 空洞判断 |
| `首先调用 acl.init，随后创建 Context。` | **保留** | 这是合法步骤词，工具已刻意不报 |

### 排版痕迹（密度比单次出现更能说明问题）

公开的中文去 AI 味清单都把这三样列在最前。它们不是词汇问题，是**排版习惯**，数得清：

| 痕迹 | 判据 | 改法 |
| --- | --- | --- |
| 破折号 `——` | **一段里出现两次以上**基本就是机器手笔 | 按它在干的活换掉：引出判断→句号，补充→括号，总结→冒号 |
| 三点省略号 `...` | 中文省略号用 `……` | 注意：代码块和行内代码里的 `...` 不算，工具已排除 |
| 【】段头 | 营销/公众号排版 | 直接说事 |

`ascend310b_writing_style` 报的是**正文**里的密度（已排除代码块与行内代码——本书实测：原始统计 71 处三点省略号，排除代码后只剩 1 处）。

> 词表与判据的公开来源：[textlint-rule-preset-zh-technical-writing](https://github.com/darkyzhou/textlint-rule-preset-zh-technical-writing)（标点与中英间距规则）、
> [anti-vibe-writing](https://github.com/weijt606/anti-vibe-writing)（名词化、翻译腔、破折号密度）、
> [stop-slop-zh](https://github.com/VincentOld/stop-slop-zh)（12 类套话词表）。
> 本插件的词表是按**本书实际语料**校准的，不是照搬：例如 `首先` 在本书 44 次全是合法步骤词，因此不报。

### 改写的六条硬规则

1. **一个形容词都不留如果它能被数字替换**："性能大幅提升" → "端到端延迟从 42 ms 降到 23 ms"。
2. **每段第一句必须是事实或动作**，不是评价（"这一设计很重要" ×）。
3. **步骤写命令**，不写"我们需要……"；读者要能照抄。
4. **标题直接命名内容**，不用"是什么/为什么/怎么办"三段式。
5. **节奏要有长短**：连续三段长度接近就调整（报告里的段落长度 ± 值可以参考）。
6. **改完必须复跑 `ascend310b_writing_audit`**，确认没破坏锚点、链接、冻结名。

### 边界

- 不要把技术判断改成情绪化表达，也不要为了"像人"而加口语废话。
- 案例里的厂商/产品对比属于教材内容，可以保留事实性描述（价格、算力、功耗），删的是**评价性形容**。
- `samples/` 下的代码注释不在本规则范围内，除非用户明确要求。
## 高频规则速查

| 规则 | 含义 | 正确做法 |
| --- | --- | --- |
| `title-format` | 标题形状不符 | 理论教程写在 frontmatter `title`；案例与附录写在正文首个一级标题 |
| `title-number-mismatch` | 标题编号与文件名不一致 | `case7.md` 的标题必须是「案例 7：…」 |
| `frozen-title-mismatch` | 冻结名表与正文标题不一致 | 改名要同时改：正文标题、`AGENTS.md` 冻结名表、`sidebar.ts`/索引 |
| `frozen-table-missing` | 编号正文没登记进冻结名表 | 新增章节/案例/附录后，在 `AGENTS.md` 对应表格补一行 |
| `anchor-prefix` | 显式锚点用了别的文件前缀 | 锚点要写成 `{#src-<部分>-<文件名>-h<n>}`，附录文件不要沿用案例的 `src-experiment-caseN-` |
| `heading-jump` | 标题层级跳跃 | 从 H2 到 H3，不要直接跳到 H4 |
| `image-missing` / `link-missing` | 相对图片或链接目标不存在 | 图片放在同级 `img1/` 等目录，路径相对当前 Markdown |
| `commit-link-missing` | 提交树里引用的目标没进这次提交 | 检查是不是把被引用目录排除在提交之外了 |
| `render-missing-character` | 字体没有该字形 | 该字符在 PDF 里会缺失；改用数学模式（`$\to$`）或换成有该字形的字体 |
| `artifact-anchor-unparsed` | 声明的锚点没进生成物，且转换器表达不了 | **改源 Markdown**：标题前补空行，或把 `_` 之类特殊字符转义。重建无效 |
| `artifact-anchor-stale` | 生成物落后于源 | 重新运行 `./convert-vuepress.sh` |
| `render-log-stale` | 日志早于最新正文 | 先重新构建再据日志下结论 |
| `sidebar-missing` | 正文未出现在 sidebar.ts | 新章节要同时加入 `sidebar.ts` 与对应 `README.md` |
| `cjk-halfwidth-punct` | 中文之间用了半角标点 | 正文用全角 `，；：！？`，代码与行内代码保持半角 |
| `trailing-whitespace` | 行尾多余空白，**已证明不影响渲染** | 直接删；`scripts/fix-hygiene.mjs --apply` 只处理这一类 |
| `hard-break-effective` | 行尾 ≥2 空格构成硬换行 | 会真的断行。先看提示里"同列表另外 N 项是否也这样写"，再决定留或删，别默认删 |
| `crlf` | 文件是 CRLF 行尾 | 统一转为 LF |
| `no-final-newline` | 末尾缺换行 | 文件以单个换行结束 |

## 边界

- 只读检查，不改文件；要改正文时用编辑工具逐个改，不要整体重写章节。
- 不要把模型权重、ONNX/OM、数据集、截图放进 Git（用户明确要求的小样本除外）。
- 涉及板端才能验证的结论（CANN、ATC、ACL、NPU 性能），本地不得下判断，
  按仓库 `AGENTS.md` 的证据分级记录为 `untested` 或 `documented`。
