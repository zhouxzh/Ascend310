---
name: ascend310b-writing
description: Ascend310 教材仓库的中文技术写作规范与守门流程。在新增、重命名或改写章节/案例/附录，调整 sidebar 与索引，审查 Markdown 命名、锚点、链接、图片、标点与行尾，出 LaTeX/PDF 后检查缺字形与产物新鲜度，准备 commit/push 前做推送门槛检查，或需要统计写作进度与篇幅时使用。触发后先跑 ascend310b_writing_audit，再按规则修正文，复跑确认；出 PDF 后跑 ascend310b_writing_render，推送前跑 ascend310b_writing_push_check。
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
5. 要出 PDF 时跑 `./convert-vuepress.sh`，然后**必须**跑
   `ascend310b_writing_render`：字体缺字形、未定义引用、硬错误，以及"源里声明的锚点
   没能进入生成物"只有在这一步才看得见。若报 `artifact-anchor-unparsed`
   （标题被转义成字面文本、或 `{#id}` 被当成标题文字），**重新构建修不好**，
   要改源 Markdown；常见原因是标题行前面少了空行，或标题里有未转义的 `_`。
   发现缺字形同样改源 Markdown（例如把 `→` 写成 `$\to$`），不要改生成的 `latex/*.tex`。
6. 准备 push 前跑 `ascend310b_writing_push_check`。它把阻塞项（正文 error、提交树断链）
   和提示项（未提交正文、渲染结论、产物新鲜度）分开列，并在最后点名唯一
   无法机械验证的一项：站点预览是否已由用户确认。
7. 要汇报篇幅或剩余工作量时跑 `ascend310b_writing_progress`。

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
