---
name: ascend310b-writing-theory
description: Ascend310 教材理论章节写作。用于新增、改写、重命名第1章至第10章及附录理论内容，维护 src/book、sidebar、README 、冻结名、章节结构和理论教程的 Markdown/LaTeX/PDF 证据；不用于案例运行手册、代码 docs 或课件排版。
---

# 理论章节写作

## 范围

处理 `src/book/` 下的理论章节、`src/appendix/` 下的附录理论内容及其对应索引。章节和附录的冻结名称、编号和全角冒号来自 `AGENTS.md`，除非用户明确要求改名，否则不得改变：

```text
第1章：昇腾310B边缘计算基础
...
第10章：AI项目交付闭环
附录 1：开发板环境
...
附录 7：CANN 升级
```

不把案例代码、部署命令、API 手记或课件版面塞进理论章节。理论章节负责解释原理、边界和概念之间的关系。

理论正文和附录必须使用简体中文。命令、API、路径、模型名、版本号和必要的英文术语保留原形；定义、推导、例子、边界和练习用中文表达。`prose-language`报告的英文主导正文是阻断项。

## 章节编号

理论章节 frontmatter 的 `title` 保留冻结章号，例如 `第4章：PyACL应用开发基础`；附录保留 `附录 N：` 对应的冻结标题。正文 H2–H6 使用语义标题，不手写章节编号。PDF 的编号由 LaTeX 自动生成，VuePress 若显示编号由渲染层生成。审计规则 `heading-manual-number` 会报告手写编号标题。

## 写作流程

1. 按对象运行 `ascend310b_writing_audit scope=book` 或 `scope=appendix`。
2. 阅读对应的 `src/book/README.md` 或 `src/appendix/README.md`、`src/.vuepress/sidebar.ts` 和现有正文结构。
3. 只修改相关 `src/book/*.md` 或 `src/appendix/*.md` 和必要的索引/sidebar；不改 `latex/chapters/`、`latex/appendices/` 或站点生成物。
4. 修改标题、锚点、图片或链接后复跑对应 scope 的 `ascend310b_writing_audit`。
5. 需要 PDF 时使用 `ascend310b_writing_build`，先预检，再显式启动构建。
6. 推送前运行 `ascend310b_writing_push_check`。

## 内容要求

- 面向初学者时，可按问题情境灵活递进：提出动机问题并确认必要先修概念，再把核心概念拆成小步解释，配一个 worked example 或图示，说明适用边界，设置理解检查，最后安排练习或迁移。顺序可随内容调整；相关文献仅在有助于回答学习者的问题时引入。
- 先给读者一个可验证的问题，再解释机制、接口和边界。
- 事实、推断、实测结果分开写；没有板端证据的 NPU 结论标为 `untested`。
- 不把 910/950 的架构和性能直接写成 310B 结论。
- 章节之间的引用使用仓库内稳定的相对链接。
- 标题前保留空行，避免 `{#id}` 被转换器当成普通文本。
- 图片必须放在章节可解析的相对目录，并提供 alt 文本；正文说明图证明的现象和边界，不写个人电脑路径、临时目录、完整哈希、精确字节数或原始日志。
- 板端实测可以写成“在 8T/20T 上观察到什么”，但版本全表、文件摘要、运行 ID 和原始命令保留在工程证据；不要把工程台账搬进理论或附录正文。
- 需要改写文字风格时再运行 `ascend310b_writing_style`；它是提示，不是自动改稿器。

## 改名/新增章节

一次改全以下表面：

- `src/book/chapterN.md` 或 `src/appendix/appendixN.md` 的 frontmatter/H1，附录保留 `附录 N：` 冻结编号；
- `src/.vuepress/sidebar.ts`；
- 对应 `src/book/README.md` 或 `src/appendix/README.md`；
- `AGENTS.md` 冻结名表（仅用户明确要求改名时）；
- 如有配对课件，更新 `src/presentation/` 和 `src/presentation/README.md`。

## 本地网页与人工校验

理论章节或附录修改后必须确认 `pnpm run docs:dev` 正在运行，并打开对应 `book/...html` 或 `appendix/...html` 页面。运行 `node scripts/check_page_layout.mjs --url <URL>` 检查桌面与移动视口；内容、公式、图片、锚点和 sidebar 都必须由用户在本地网页上人工确认。`docs:build` 和自动 audit 通过只算机械证据；`human-review: pending` 只保留在外部任务或审查元数据中，不写入发布正文，用户确认后才能通过。

## 停止条件

- 冻结标题或编号冲突。
- 正文标题与文件编号不一致。
- 新增章节没有 sidebar 或索引。
- 图片/链接目标不存在。
- 理论正文出现 `.dot` 路径、字体名、pt/dpi 或作者自检备注。
- `prose-language`报告理论正文或附录以英文为主。
- 没有本地网页人工校验，却被描述为最终完成。
- 仅本地构建通过但板端结论尚未验证时，不得写成 `observed-pass`。
