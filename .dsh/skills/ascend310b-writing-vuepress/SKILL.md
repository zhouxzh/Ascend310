---
name: ascend310b-writing-vuepress
description: Ascend310 教材 VuePress 站点写作与配置。用于 src/.vuepress、sidebar、站点配置、Markdown 页面路由、静态资源和 docs:dev/docs:build；只改源配置和 Markdown，不手改 dist/.temp/.cache，不把生成站点当成教材源文件。
---

# VuePress 站点写作

本 skill 用于 Ascend310 教材中面向 310B 的 VuePress 页面。

## 范围

只处理 `src/` 下的 VuePress 源内容和 `src/.vuepress/` 配置：

- `src/.vuepress/config.ts`、`sidebar.ts` 和主题配置；
- `src/book/README.md`、`src/experiment/README.md`、`src/appendix/README.md`；
- 页面 frontmatter、路由、静态资源路径；
- `pnpm docs:dev` 和 `pnpm docs:build` 验证。

`src/.vuepress/dist/`、`.temp/`、`.cache/` 是生成物，不手工编辑。

## 工作流

1. 运行 `ascend310b_writing_audit`，先处理 Markdown error。
2. 修改对应页面源 Markdown 或 `.vuepress` 配置。
3. 新增/改名页面时，同时更新 `sidebar.ts`、对应 `README.md` 索引和冻结名表（若用户明确要求改名）。
4. 用 `pnpm docs:dev` 启动或复用本地预览，确认路由、sidebar、图片和链接；把 URL 交给用户人工查看最新页面。
5. 用 `pnpm docs:build` 验证静态构建；构建成功和 HTTP 200 不等于用户已确认页面内容，最终状态先写 `human-review: pending`。
6. 推送前运行 `ascend310b_writing_push_check`，推送后运行 `ascend310b_writing_ci` 检查 Pages 工作流。

## 本地网页与人工校验

修改 VuePress 页面后必须让用户在本地网页上查看对应路由。运行 `node scripts/check_page_layout.mjs --url <URL>` 检查 1440px 和 390px 视口；只看到开发服务器启动、HMR 更新或自动审计通过，不能宣称页面已通过。用户明确确认正文、图片、链接和 sidebar 无误后，才把人工验收状态改为通过。

## 路由与资源

- 页面链接使用仓库内相对路径，不用裸 URL 代替内部链接。
- `public/` 资源路径以站点 base 为基准；移动页面时同步检查图片路径。
- frontmatter 只写 VuePress 支持的字段，未知字段不要在模板中臆造。
- 链接到生成物、截图或报告时，确认目标在 CI 新检出中仍然存在。
- 页面出现在 sidebar 之前，先在对应 README 登记；不要让页面只靠搜索可达。
- 需要显示代码时使用带语言的 fenced code block，不用 HTML 手写代码块。

## 停止条件

- 页面不在 sidebar 或对应 README 中。
- 相对链接、图片或 public 资源无法解析。
- 修改了生成目录而不是源文件。
- 本地预览存在 route/asset 错误。
- 页面改名没有同步冻结名表或索引。
- 没有用户在本地网页上的明确确认，却被描述为最终页面。
