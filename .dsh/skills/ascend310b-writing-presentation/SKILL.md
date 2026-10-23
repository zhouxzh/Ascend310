---
name: ascend310b-writing-presentation
description: Ascend310 教材演示文稿写作与验收。用于 src/presentation 下 Marp 课件、主题、封面/目标/章节/正文/小结、讲师备注、来源和真实浏览器版面检查；不用于理论章节、案例正文或代码工程 docs。
---

# Presentation 写作

只处理 Ascend310 教材中面向 310B 的 Marp 课件，不处理教材正文或代码工程 docs。

## 范围

只处理 `src/presentation/` 的 Marp 源文件、主题和课件索引。生成物在 `src/.vuepress/public/presentation/`，不得手工编辑。

## 发布链接规则

只要课件会发布到 GitHub Pages，课件中的导航就必须指向发布后的目标，而不是仓库构建目录里的相对 Markdown：

| 导航 | 必须使用 | 示例 |
| --- | --- | --- |
| 对应教材/案例 | GitHub Pages HTML | `https://zhouxzh.github.io/Ascend310/experiment/case9.html` |
| 配套代码/样例 | GitHub 仓库目录 | `https://github.com/zhouxzh/Ascend310/tree/main/samples/case9` |

- 已发布课件禁止使用 `../experiment/*.md`、`../book/*.md` 或本地 checkout 路径作为教材导航。
- 理论章节对应 `/book/chapterN.html`，实践案例对应 `/experiment/caseN.html`；不要把所有页面都链接到站点首页。
- 教材锚点可以附加到 Pages URL；样例目录应链接到对应 `samples/<name>/`，不要链接到本机文件。
- 每次改动生成源后，重新运行 `pnpm run docs:slides`，并用真实浏览器检查外链和页面裁切。
- 生成 HTML 后抽查：教材链接应出现 `zhouxzh.github.io/Ascend310/`，样例链接应出现 `github.com/zhouxzh/Ascend310/tree/main/samples/`，不得残留本地相对的目标链接。

## 固定结构

每份课件必须有：

- `marp: true`;
- `theme: ascend310`;
- `paginate: true`;
- `size: 16:9`;
- 封面页；
- 明确的学习目标或主线；
- 正文按“每页一个论点”组织；
- 小结或课堂任务；
- 每页讲师备注；
- 对应 `src/` 或 `samples/` 的来源。

封面只写标题、内容承诺、作者/邮箱和来源，不写“教师授课版”等行政标签。页脚使用冻结名，页眉使用教材名，不自行更换主题、字号体系或页脚位置。

## 流程

1. 运行 `ascend310b_writing_audit scope=presentation`。
2. 确认源 Markdown 是唯一真源，先改源文件。
3. 运行 `pnpm run docs:slides` 生成静态课件。
4. 运行 `ascend310b_writing_deck_render`，按真实 1280×720 浏览器检查：
   - 0 纵向/横向裁切；
   - 0 图片未加载；
   - 主题一致；
   - 封面、目标、章节、内容和小结页逐页检查。
5. 源码高度估算只能预警，不能代替真实渲染。
6. 打开本地 `presentation/<deck>.html` 页面，让用户人工检查封面、内容、链接、字号和课堂节奏；可用 `node scripts/check_page_layout.mjs --url <URL>` 补充检查图片/SVG 越界，最终状态先写 `human-review: pending`。
7. 推送前运行 `ascend310b_writing_push_check`，再按 `ascend310b_writing_ci` 检查 Pages 构建。

## 内容规则

- 标题写断言，不写空泛话题。
- 正文页保持可讲内容，不靠缩小字号塞入。
- 代码页如果展示学生要读的命令，用代码块即可；不要在可见区再写一行 `源码：path`。页脚 HTML 来源链接已经承担追溯。
- 图片必须有 alt，并解释这张图证明什么。
- 不把 `.dot` 路径、字体名、pt/dpi 或排版自检写进可见幻灯。
- 不在课件里复制整段教材；课件是课堂讲授材料，应与教材和样例互补。
- 8T/20T、CANN 和硬件事实来自 `ascend_probe` 或明确证据，不把 910/950 的数字带入。

## 停止条件

- 主题、封面、页脚、来源或元数据缺失。
- 真实浏览器存在裁切/图片失败。
- 页面密度过高、字号低于可读下限，且无法通过分页解决。
- 课件与当前教材/样例源不一致。
- 仅本地估算通过但未做真实浏览器验收。
- 可见幻灯出现 `.dot` 路径、字体名、pt/dpi 或作者自检备注。
- 没有用户在本地网页上的明确确认，却被描述为最终课件。
