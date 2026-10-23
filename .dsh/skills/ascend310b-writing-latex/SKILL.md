---
name: ascend310b-writing-latex
description: Ascend310 教材 LaTeX/PDF 写作与渲染验收。用于 convert-vuepress.sh、latex/ 生成物、XeLaTeX 日志、缺字形、未定义引用、锚点丢失、PDF 产物新鲜度和构建失败；必须改 Markdown 源，不手改生成的 latex/*.tex 或 PDF。
---

# LaTeX / PDF 写作验收

本 skill 用于 Ascend310 教材中面向 310B 的 LaTeX/PDF 链路。

## 范围

本仓库的正式链路是：

```text
Markdown 源 -> convert-vuepress.sh -> latex/*.tex -> XeLaTeX -> PDF
```

`src/` 是源，`latex/chapters/`、`latex/cases/`、`latex/appendices/`、`latex/*.tex` 和 PDF 是生成物。生成物不手工编辑。

案例在“实践案例”部分按章自动编号：`案例 4` 对应 `4.1`、`4.2`、`4.2.1`。这些编号由 LaTeX 的 `chapter/section/subsection` 生成，不能通过修改生成的 `.tex` 或 PDF 补齐，也不能在 Markdown H2–H6 中手写。

## 工作流

1. 先运行 `ascend310b_writing_audit`，清理 Markdown 标题、锚点、链接和图片错误。
2. 需要预览或生成 PDF 时，先运行 `ascend310b_writing_build` 的预检，不带 `confirm`。
3. 用户明确要求生成后，再带 `confirm: true` 启动构建；长任务使用后台 `job_output` 读取。
4. 构建后检查：
   - `latex/book.log` 的 LaTeX error；
   - 缺字形；
   - undefined reference/citation；
   - 页面宽度严重溢出；
   - 每个源 `{#id}` 在生成物中有对应 `\label{id}`。
5. `ascend310b_writing_render` 只用于独立日志体检；如果 build 已自动执行 render，不要重复跑。
6. 推送前运行 `ascend310b_writing_push_check`。

## 字体与特殊字符

- 缺字形先改源 Markdown，不修改生成 `.tex`。
- 数学箭头、符号优先用数学模式，例如 `$\\to$`。
- 中文正文使用全角标点；代码块和行内代码保留代码语法。
- 标题前保留空行，避免 `{#id}` 被解析为普通文本。
- 不把 PDF 页面截图当作数值、性能或硬件验收证据。

## 构建失败处理

| 现象 | 先查 | 处理 |
| --- | --- | --- |
| XeLaTeX error | 最新日志和失败行 | 修源 Markdown 或转换器，不手补生成物 |
| 缺字形 | 日志 glyph 名称 | 换数学表达或字体，修源 |
| 未定义引用 | 锚点和目录 | 修标题/引用 |
| `artifact-anchor-unparsed` | 标题前空行、`_` 转义 | 重建无效，修源 |
| PDF 文件被占用 | 生成 `book.xdv` | 用 build 的定点重试，不盲删 PDF |
| 生成物过期 | 源文件与日志时间 | 重新构建再判断 |

## 停止条件

- 日志早于最新源文件。
- 缺字形或未定义引用未清理。
- 生成物锚点与源不一致。
- 只构建成功但用户尚未确认页面/PDF。
