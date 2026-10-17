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

### 前后对照（取自本书真实句子）

| 改前 | 改后 | 为什么 |
| --- | --- | --- |
| `**英特尔**则展现出"全能型选手"的特质。它不仅以CPU处理通用计算任务，还通过Movidius视觉处理单元等专用芯片强化AI视觉推理。` | `英特尔同时提供通用 CPU 与 Movidius 视觉处理单元：CPU 承担通用计算，VPU 加速视觉推理。` | "全能型选手"是宣传语；去掉引号里的评价，只留它到底提供了什么 |
| `这款芯片提供极致的性能与出色的能效。` | `该芯片在 5–8 W 典型功耗下提供 8 TOPS INT8 推理算力。` | 形容词换成可核对的数字 |
| `边缘节点的硬件选择，正如您所指出的，本质上是功耗、体积和成本之间的权衡。` | `边缘节点的硬件选择，本质上是功耗、体积和成本之间的权衡。` | 删掉对话残留的称呼 |
| `综上所述，CANN 在异构计算中具有重要地位。` | （整句删除，或在确有结论时写：`CANN 把框架差异收敛到 ATC 一次转换里`） | 空转套话 + 空洞判断 |
| `首先调用 acl.init，随后创建 Context。` | **保留** | 这是合法步骤词，工具已刻意不报 |

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
