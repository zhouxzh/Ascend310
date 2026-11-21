---
name: ascend310b-writing
description: Ascend310 教材写作总路由和跨对象发布门。用于判断理论章节、实践案例、课件、工程 docs、代码参考、课程计划、VuePress、LaTeX、Markdown、Graphviz、神经网络框图或中文学术润色应加载哪些专用 skill，并统一冻结命名、证据边界、读者稿边界、人工验收和推送条件。
---

# 教材写作总守门

本 skill 只负责**路由、跨对象约束和发布门**。专题规则由对应 skill 维护，本文件不复制其完整内容。

教材正文以 Markdown 为唯一真源；VuePress、LaTeX、PDF、HTML 和截图均为生成物。修改内容时先改源文件，再运行检查并让生成物更新。

## 根目录与执行环境

- DSH 中通过 `tutorialRoot` 或共享 `repoRoot` 指定 Ascend310 检出目录；MCP 可传 `root` 或设置 `TUTORIAL_WRITING_ROOT`。
- 换机器后先运行 `ascend310b_writing_toolchain`，确认 pandoc、LaTeX、Python、pnpm 等构建依赖。
- 只读审计默认不修改文件。用户明确要求修改后才编辑源文件。

## Skill 路由

| 当前任务 | 主 skill | 可叠加 |
| --- | --- | --- |
| 第1–10章、附录理论 | `ascend310b-writing-theory` | latex / markdown / vuepress / dot / academic |
| 案例1–9学生教材正文 | `ascend310b-writing-case` | curriculum / latex / markdown / vuepress / dot / academic |
| Marp 课件 | `ascend310b-writing-presentation` | markdown / vuepress / dot |
| 样例 README、部署、验证、问题台账 | `ascend310b-writing-docs` | case-testing / markdown |
| docstring、API、CLI 参考 | `ascend310b-code-docs` | markdown |
| 章节—案例—代码—课件规划 | `ascend310b-curriculum-planning` | case / academic |
| VuePress 外壳、navbar、sidebar、Pages | `ascend310b-writing-vuepress` | markdown |
| LaTeX/PDF 生成与渲染检查 | `ascend310b-writing-latex` | 当前内容 skill |
| Markdown 语法、链接、图片、标点 | `ascend310b-writing-markdown` | 当前内容 skill |
| Graphviz 系统图、流程图 | `ascend310b-writing-dot` | 当前内容 skill |
| TikZ 神经网络结构图 | `ascend310b-writing-neural-diagram` | latex / diagram-design |
| 通用技术图形构建 | `diagram-design` | dot / neural-diagram / patent-disclosure |
| 中文教材学术论证或润色 | `ascend310b-writing-academic` | theory / case |
| 英文科学论文写作、证据或投稿 | `paper-evidence` / `scientific-writing` / `nature-polishing` | paper-latex-cn / latex-paper-en |

使用“一个主内容 skill + 必要辅助 skill”，不要一次加载全部专题 skill。

## 规则所有权

专题规则只允许一个权威版本：

- Markdown 语法与卫生：`ascend310b-writing-markdown`
- Marp 结构与真实渲染：`ascend310b-writing-presentation`
- VuePress、navbar、sidebar 和生成区块：`ascend310b-writing-vuepress`
- LaTeX 构建和产物验收：`ascend310b-writing-latex`
- Graphviz 画布、字体和引用：`ascend310b-writing-dot`
- 中文教材论证、claim/evidence 和润色：`ascend310b-writing-academic`
- 课程教学矩阵：`ascend310b-curriculum-planning` 的 `docs/curriculum_plan.md`
- 样例 README 运行/部署段：`ascend310b-writing-docs`
- 样例 README 的 API/CLI 段：`ascend310b-code-docs`

发生冲突时，使用上表指定 skill，不在本文件中维护第二套详细规则。

## 语言

仓库自有学生教材、案例正文、样例 README、活动工程 docs、理论章节、附录和课件默认使用简体中文。英文只用于代码、命令、API、路径、环境变量、模型名、版本号、日志原文、外部引用和许可证。

- 新写或改写内容必须清除英文主导正文；`prose-language` 是阻断项。
- `scientific-writing` 和 `nature-polishing` 只用于英文论文或中译英；中文教材润色走 `ascend310b-writing-academic`。

## 读者稿与证据

- 学生教材、案例和课件是完成稿，不是作者工作台。
- 学生可见正文不得出现 `.dot` 路径、字体名、pt/dpi、工具名、审计规则名、`human-review: pending`、个人绝对路径、临时目录、完整哈希、字节数、运行 ID 或原始日志回显。
- 工程证据保留在 `samples/caseN/docs/evidence/`、受控工程文档、发布复现记录或 `tmp/` 学习记录。
- 经验性或板端主张必须区分事实、推断、实测和未验证结果；没有证据写 `untested`，不得写 `observed-pass`。
- 8T 和 20T 结果分开；不得把 910/950 的数字或结论直接用于 310B。
- `case4.md` 必须自包含，只链接自身源码、图片和外部来源。

## 冻结命名与编号

- 章节、案例、附录的公开名称来自仓库 `AGENTS.md`，包括 `第N章：`、`案例 N：`、`附录 N：` 的编号、空格和全角冒号。
- 改名必须由用户明确批准，并一次更新正文标题、冻结名表、索引和所需导航；sidebar 由 `ascend310b-writing-vuepress` 修改。
- H1/frontmatter 保留冻结编号；H2–H6 不手写 `1.`、`1.1`、`4.1` 等编号。
- 标题前保留空行；显式锚点使用 `{#src-<部分>-<文件名>-h<n>}`。

## 标准流程

1. 选定主内容 skill，再按需加载辅助 skill。
2. 运行 `ascend310b_writing_audit`；学生未发布记录使用 `ascend310b_writing_record_audit`。
3. 修改源 Markdown/docstring/源配置，不修改生成物。
4. 复跑 audit，直到 error 归零；`cjk-latin-space` 等新写内容规则不得作为可保留 warning。
5. 修改进入 VuePress 的内容后，确认 `pnpm run docs:dev` 正在运行，并提供目标本地 URL。
6. 运行 `ascend310b_writing_page_layout` 或 `node scripts/check_page_layout.mjs --url <URL>`；桌面和移动视口都要检查。
7. 课件修改先运行 `pnpm run docs:slides`，再运行 `ascend310b_writing_deck_render`；源码高度估算不能代替真实 1280×720 渲染。
8. 需要 PDF 时先用 `ascend310b_writing_build` 预检，再由用户确认并带 `confirm: true` 构建。`writing_build` 已包含 render 检查和产物锚点核对，不再重复运行 `writing_render`。
9. 准备推送前运行 `ascend310b_writing_push_check`；推送后运行 `ascend310b_writing_ci`。
10. 需要报告篇幅或剩余工作量时运行 `ascend310b_writing_progress`。

## 人工验收

- 自动 audit、`docs:build`、HTTP 200 和 HMR 不等于人工验收。
- `human-review: pending` 只保留在外部任务或审查元数据，不写入发布页面。
- 用户明确确认本地页面、PDF 或课件后才能描述为最终通过。
- 未经用户确认，不把未验收产物描述成发布结果，也不 push。

## Push 门

`ascend310b_writing_push_check` 至少覆盖：

- 正文 Markdown error；
- 代码工程文档语言和结构 error；
- Git 提交树中的断链；
- 高置信度凭证、Token、私钥和密码扫描；
- 真实课件裁切和图片加载；
- LaTeX 渲染与产物新鲜度（存在时）；
- 用户对站点预览的人工确认。

具体 block/warn 结果以工具输出为准；不能只看本地构建成功。

## 外部能力

以下能力不属于本 skill 本体，必须存在或明确降级：

- `ascend310b-case-testing`：案例发布来源与板端复现门；
- `ascend310b-env-check` 和 Ascend 板端工具：真实环境与硬件事实；
- `office` / `office-docx` / `office-render`：Word 生成与渲染验收；
- `latex-paper-en`：英文期刊 LaTeX；中文论文工作区使用 `paper-latex-cn`。

缺失时说明未验证，不能假设通过。

## 停止条件

- 冻结标题、编号或文件编号不一致。
- 新建页面没有源文件、索引目标或所需导航意图。
- 图片、链接或锚点目标不存在，或 `case4` 依赖其他仓库文档。
- Markdown/工程 docs 仍以英文为主。
- 源文件修改后没有重新构建，却引用旧 PDF/HTML 结论。
- 真实页面、PDF 或课件尚未由用户确认，却被描述为最终完成。
- 本地构建通过但板端结论未验证，却被写成 `observed-pass`。
- 出现凭证、私钥或未脱敏的原始日志。
