---
name: ascend310b-writing-theory
description: Ascend310 教材理论章节写作。用于新增、改写、重命名第1章至第10章及附录理论内容，维护 src/book、sidebar、README 、冻结名、章节结构和理论教程的 Markdown/LaTeX/PDF 证据；不用于案例运行手册、代码 docs 或课件排版。
---

# 理论章节写作

## 范围

只处理 `src/book/` 下的理论章节和对应索引。章节的冻结名称、编号和全角冒号来自 `AGENTS.md`，除非用户明确要求改名，否则不得改变：

```text
第1章：昇腾310B边缘计算基础
...
第10章：AI项目交付闭环
```

不把案例代码、部署命令、API 手记或课件版面塞进理论章节。理论章节负责解释原理、边界和概念之间的关系。

## 写作流程

1. 先运行 `ascend310b_writing_audit scope=book`。
2. 阅读 `src/book/README.md`、`src/.vuepress/sidebar.ts` 和对应章节的现有结构。
3. 只修改 `src/book/*.md` 和必要的索引/sidebar；不改 `latex/chapters/` 或站点生成物。
4. 修改标题、锚点、图片或链接后复跑 `ascend310b_writing_audit scope=book`。
5. 需要 PDF 时使用 `ascend310b_writing_build`，先预检，再显式启动构建。
6. 推送前运行 `ascend310b_writing_push_check`。

## 内容要求

- 先给读者一个可验证的问题，再解释机制、接口和边界。
- 事实、推断、实测结果分开写；没有板端证据的 NPU 结论标为 `untested`。
- 不把 910/950 的架构和性能直接写成 310B 结论。
- 章节之间的引用使用仓库内稳定的相对链接。
- 标题前保留空行，避免 `{#id}` 被转换器当成普通文本。
- 图片必须放在章节可解析的相对目录，并提供 alt 文本。
- 需要改写文字风格时再运行 `ascend310b_writing_style`；它是提示，不是自动改稿器。

## 改名/新增章节

一次改全以下表面：

- `src/book/chapterN.md` 的 frontmatter/H1；
- `src/.vuepress/sidebar.ts`；
- `src/book/README.md`；
- `AGENTS.md` 冻结名表（仅用户明确要求改名时）；
- 如有配对课件，更新 `src/presentation/` 和 `src/presentation/README.md`。

## 本地网页与人工校验

理论章节修改后必须确认 `pnpm run docs:dev` 正在运行，并打开对应 `book/...html` 页面。运行 `node scripts/check_page_layout.mjs --url <URL>` 检查桌面与移动视口；内容、公式、图片、锚点和 sidebar 都必须由用户在本地网页上人工确认。`docs:build` 和自动 audit 通过只算机械证据；最终状态先写 `human-review: pending`，用户确认后才能通过。

## 停止条件

- 冻结标题或编号冲突。
- 正文标题与文件编号不一致。
- 新增章节没有 sidebar 或索引。
- 图片/链接目标不存在。
- 没有本地网页人工校验，却被描述为最终完成。
- 仅本地构建通过但板端结论尚未验证时，不得写成 `observed-pass`。
