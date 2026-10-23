---
name: ascend310b-writing-vuepress
description: Ascend310 开源网页教材的 VuePress 站点模板与配置。用于 src/.vuepress 的主题、navbar、sidebar、base、frontmatter、静态资源、GitHub Pages 和 docs:dev/docs:build；改站点外壳、导航或网页模板时使用。不手改 dist/.temp/.cache，不把生成站点当成教材源，也不用本 skill 写案例正文或 Marp 课件。
---

# VuePress 网页教材模板

本仓库是面向学生的开源网页教程，站点外壳是 VuePress 2 + vuepress-theme-hope。本 skill 只处理**网页模板和站点配置**，不写理论章节、案例正文或课件内容。

## 范围

只改这些源文件：

| 文件 | 作用 |
| --- | --- |
| `src/.vuepress/config.ts` | `base`、语言、bundler、Markdown 扩展、忽略规则 |
| `src/.vuepress/theme.ts` | hope 主题、作者、logo、repo、footer、markdown 插件 |
| `src/.vuepress/navbar.ts` | 顶栏：理论 / 实践 / 附录 / 教学演示 |
| `src/.vuepress/sidebar.ts` | 各分区侧栏；课件侧栏由脚本生成 |
| `src/.vuepress/client.ts` | 客户端增强 |
| `src/.vuepress/styles/` | 站点样式，不是 Marp 主题 |
| `src/.vuepress/public/` | favicon、logo、生成后的课件 HTML |
| 分区 README | `src/book/README.md`、`src/experiment/README.md`、`src/appendix/README.md`、`src/presentation/README.md` |

不改：

- `src/.vuepress/dist/`、`.temp/`、`.cache/`
- `src/presentation/NN-*.md`（Marp 源，走 `ascend310b-writing-presentation`）
- 案例/理论正文（走 case/theory skill）

## 站点模板契约

当前站点必须保持：

```text
base: /Ascend310/
lang: zh-CN
title: 昇腾310B实战
theme: vuepress-theme-hope
hostname: https://zhouxzh.github.io
repo: zhouxzh/Ascend310
docsDir: src
```

顶栏四个入口固定为理论教程、实践案例、附录、教学演示。不要把案例页挂到 `/book/`，也不要把课件 Markdown 再编成 VuePress 页面：`pagePatterns` 必须继续排除 `presentation/[0-9][0-9]-*.md`。

课件 HTML 由 `pnpm run docs:slides` 写入 `src/.vuepress/public/presentation/`。sidebar 中课件链接指向 `.html`，由 `pnpm run docs:slides:index:write` 生成；不要手改 `BEGIN GENERATED PRESENTATION SIDEBAR` 区块。

## 页面模板

学生可见页面使用 Markdown 源，不要发明 HTML 模板语言。标准形状：

```markdown
---
title: 案例 9：智能聊天机器人
---

# 案例 9：智能聊天机器人
```

- `title` 必须与冻结名一致。
- 第一章级标题只出现一次，且与文件职责一致。
- H2–H6 只写语义标题，不手写 `1.`、`1.1`、`4.1`；需要网页编号时由主题/计数器/渲染插件生成，保持与 PDF 自动编号一致。
- 图片用 `![说明](相对路径)`，alt 写给学生看的职责/流程，不写 `.dot`、字体或 dpi。
- 代码块必须有语言标记。
- 内部跳转用仓库相对 Markdown 路径；已发布课件里的“对应教材”用 GitHub Pages HTML。
- frontmatter 只写 VuePress/theme-hope 支持的字段。

新增页面时一次改全：源 Markdown、对应 README 索引、`sidebar.ts`、必要时 `navbar.ts` 和冻结名表。

## 工作流

1. 运行 `ascend310b_writing_audit`，先处理 Markdown error。
2. 改 `.vuepress` 配置或分区 README，不改生成目录。
3. 新增/改名页面时同步 sidebar、README 和冻结名。
4. 运行或复用 `pnpm run docs:dev`，打开对应本地 URL。
5. 运行 `node scripts/check_page_layout.mjs --url <URL>`。
6. 用 `pnpm docs:build` 验证静态构建；成功不等于人工通过，状态先写 `human-review: pending`。
7. 推送前运行 `ascend310b_writing_push_check`，推送后运行 `ascend310b_writing_ci`。

## 本地网页与人工校验

站点外壳改动必须让用户在浏览器里看首页、侧栏、目标路由和移动宽度。HMR、HTTP 200 和自动审计不能代替这次检查。

## 路由与资源

- 页面链接使用仓库内相对路径，不用裸 URL 代替内部链接。
- `public/` 资源路径以站点 `base` `/Ascend310/` 为基准。
- 移动、改名页面后同步检查图片和课件 HTML 路径。
- 链接到生成物、截图或报告时，确认 CI 新检出中仍然存在。
- 页面出现在 sidebar 之前，先在对应 README 登记。

## 停止条件

- 改了 `base`、hostname、repo 或 docsDir，却没有同步 Pages/CI。
- 手改了 generated presentation sidebar 或 `dist/`。
- 把 Marp 源编进 VuePress 页面，或把案例正文写进站点配置 skill。
- 页面不在 sidebar 或对应 README 中。
- 相对链接、图片或 public 资源无法解析。
- 没有用户在本地网页上的明确确认，却被描述为最终页面。
