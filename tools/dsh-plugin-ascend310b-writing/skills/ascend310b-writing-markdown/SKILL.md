---
name: ascend310b-writing-markdown
description: Ascend310 教材 Markdown 写作规范。用于 CommonMark/GFM 语法、frontmatter、标题层级、锚点、链接、图片、表格、代码块、中文标点、markdownlint 规则和 Markdown 卫生检查；不用于 VuePress 配置、LaTeX 生成物或课件排版。
---

# Markdown 写作规范

本 skill 用于 Ascend310 教材中面向 310B 的 Markdown 正文。

## 源文件优先

本仓库教材的源是 Markdown。VuePress、LaTeX、PDF、HTML 和截图都是生成物；不要在生成物里修内容。

本 skill 采用 CommonMark 的通用语法、Markdown Guide 的常用写法、markdownlint 的机械规则，以及本仓库额外的中文教程规范。若通用规则与仓库规则冲突，以仓库 `AGENTS.md` 和现有正文为准。

## 标题与锚点

- 每篇正文只有一个一级标题，章节层级连续，不跳级。
- 理论章节标题写在 frontmatter/H1；案例和附录按仓库约定写正文 H1。
- 冻结标题的编号、空格和全角冒号不可擅自改变。
- 显式锚点使用本仓库前缀：
  `{#src-<部分>-<文件名>-h<n>}`。
- 标题前保留空行，避免 `{#id}` 被转换器当普通文字。
- 标题直接命名内容，避免“关于……”“结果”“相关工作”这类空标题。

## 链接与图片

- 内部链接使用相对路径；不要用裸 URL 代替仓库链接。
- 图片必须有 alt，并放在页面可解析的相对目录。
- 移动/改名图片时同步更新所有引用。
- 不引用 `.gitignore` 中不会进入新检出的运行时产物，除非正文写清生成命令。
- 图片过宽时先检查站点/PDF 显示尺寸，不靠 CSS 强行缩小。

## 代码与表格

- fenced code block 必须带语言标记。
- 表格列数保持一致；过宽表格优先拆分或改成列表。
- 行内代码与代码块保持半角语法；中文正文使用全角 `，；：！？`。
- 数学/箭头需要正确字体时使用数学模式。
- 删除行尾空白；统一 LF 和最终换行。

## 写作流程

1. 运行 `ascend310b_writing_audit`。
2. 只改源 Markdown，按 error → warning → info 顺序处理。
3. 复跑 audit；冻结名和 sidebar 由交叉检查确认。
4. 确认 `pnpm run docs:dev` 正在运行，并打开对应本地网页让用户人工检查 Markdown 的最终呈现；状态先写 `human-review: pending`。
5. 需要去 AI 味时单独运行 `ascend310b_writing_style`，逐句判断，不机械替换。
6. 推送前运行 `ascend310b_writing_push_check`。

## 本地网页与人工校验

Markdown 源修改后必须进入本地 VuePress 页面查看，同时运行 `node scripts/check_page_layout.mjs --url <URL>` 检查桌面与移动视口的横向溢出。自动 audit、站点构建和 HTTP 200 不是人工验收；只有用户明确确认页面后才算完成。

## 停止条件

- 标题层级、冻结名或文件编号不一致。
- 链接/图片/锚点目标不存在。
- 代码块没有语言标记或表格列数损坏。
- 只改了生成 HTML/PDF，没有改 Markdown 源。
- 没有本地网页人工校验，却被描述为最终完成。
