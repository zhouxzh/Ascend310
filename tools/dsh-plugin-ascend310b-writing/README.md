# dsh-plugin-ascend310b-writing

教程写作守门员 —— 为 Ascend310《昇腾 310B 边端 AI 实战》教材仓库定制的
DeepSeek Harness 插件（bundle）。

它把仓库 `AGENTS.md` 里的冻结命名规则、Markdown 卫生要求、LaTeX 渲染与产物
新鲜度、VuePress 页面真实排版、演示文稿的真实浏览器布局、推送前门槛和写作进度，变成十个模型可直接调用的工具和一个 Web 侧边栏面板。

## 组成

| 面 | 文件 | 作用 |
| --- | --- | --- |
| 主机端 | `lib/index.js` | 注册 `ascend310b_writing_audit`、`ascend310b_writing_page_layout`、`ascend310b_writing_deck_render`、`ascend310b_writing_render`、`ascend310b_writing_push_check`、`ascend310b_writing_ci`、`ascend310b_writing_toolchain`、`ascend310b_writing_progress`、`ascend310b_writing_build`、`ascend310b_writing_style` 十个工具；组成里有 Web 服务器时额外注册 `/ascend310b-writing/report.json` 只读路由 |
| 审计内核 | `lib/audit-core.js` | 正文审计、行尾分级与进度统计；纯 Node ESM，无 Harness 依赖，可单独运行与测试 |
| 跨产物检查 | `lib/checks.js` | 读 Markdown 之外的证据：`AGENTS.md` 冻结名表、LaTeX 构建日志、生成物与源的锚点差、Git 提交树 |
| 工具链体检 | `lib/toolchain.js` + `lib/doctor.js` | 探测 bash/pandoc/latexmk/xelatex/python3/pnpm/node（含 Anaconda/conda），给按平台分类的安装命令，判断哪些能力可用 |
| 平台适配 | `drivers/env-windows.mjs` + `drivers/env-linux.mjs` | Windows（Git Bash / WSL）与 POSIX 各自怎么起 shell、怎么翻译路径、怎么满足 `python3` |
| 浏览器端 | `lib/client.js` | 侧边栏「写作」入口与紧凑工作区：概览、分部进度、代码文档、问题清单四个可切换视图，带问题搜索和只看问题筛选 |
| 技能 | `skills/*/SKILL.md` | 总守门规则、theory/case/presentation/code docs 等专用写作 skill，并内置 `scientific-writing` 与 `nature-polishing`；`nature-shared` 是后者的支持目录 |
| 组合 | `cordis.patch.yml` | 声明本包的 bundle patch，把 Host 行插入组合 |

十个工具各回答一个不同的问题：

| 工具 | 回答的问题 |
| --- | --- |
| `ascend310b_writing_toolchain` | 这台机器 + 这个仓库现在能做什么，缺什么、怎么装 |
| `ascend310b_writing_audit` | 正文本身对不对（含 `AGENTS.md` 冻结名表的双向比对），以及演示文稿与站点产物是否过期 |
| `ascend310b_writing_audit` + `rev` | 这次提交发布出去会不会断链 |
| `ascend310b_writing_deck_render` | 生成后的 1280×720 课件有没有真实裁切、图片未加载 |
| `ascend310b_writing_page_layout` | 本地 VuePress 页面在桌面/移动视口有没有横向溢出、图片/SVG 越界，以及原图被过度缩小 |
| `ascend310b_writing_render` | 渲染出来的 PDF 有没有坏字符，生成物是否落后于源 |
| `ascend310b_writing_ci` | 读取最近一次 GitHub Pages 工作流运行、提交、失败步骤与日志 |
| `ascend310b_writing_push_check` | 现在能不能推，以及哪一项只有人能确认 |
| `ascend310b_writing_build` | 构建（走仓库自己的转换器），并在同一任务里核对产物 |
| `ascend310b_writing_style` | 哪些句子读起来像产品宣传稿/AI 输出（只做诊断，不代替润色） |
| `ascend310b_writing_progress` | 篇幅和剩余工作量 |

## 内置学术写作 Skills

| Skill | 上游 | 许可证 | 安装内容 |
| --- | --- | --- | --- |
| `scientific-writing` | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) `593377b` | MIT | 完整 skill：SKILL、references、assets、离线 Python CLI |
| `nature-polishing` | [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills) `233ed09` | Apache-2.0 | 完整润色路由、manifest、static 片段、references |
| `nature-shared` | 同上 | Apache-2.0 | 仅供 `nature-polishing` 相对引用的支持语料，不含 `SKILL.md`，不会出现在 skill 列表中 |

来源、提交与本地修正分别记录在各目录的 `SOURCE.md` 和 `LICENSE`。同步到项目级目录：

```bash
pnpm run skills:sync
pnpm run skills:check
```

`nature-shared` 是支持目录而不是第三个 skill；同步脚本和安装脚本都会复制它，但 DSH 不会把它注册为独立 skill。新增 skill 后需要新建 DSH 会话，当前会话的 skill 目录不会热更新。

## 安装

包目录本身就是一个 bundle。装进某个 profile 需要两步：

1. 让 profile 能解析到本包名。桌面版 profile 由 Electron 独占，
   `dsh plugin` 不能管理它，因此直接复制到 profile 的 `node_modules`：

   ```powershell
   Copy-Item -Recurse -Force "<仓库>\tools\dsh-plugin-ascend310b-writing" `
     "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-plugin-ascend310b-writing"
   ```

2. 把包名加入该 profile `package.json` 的 `dsh.profile.bundles` 列表：

   ```json
   "bundles": [
     "@deepseek-ai/dsh-base",
     "@deepseek-ai/dsh-web-app",
     "dsh-plugin-ascend310b-writing"
   ]
   ```

重启 Harness 后，侧边栏出现「写作」入口，模型获得这些写作工具。

技能是纯文件，复制到技能目录即可被 `dsh-skill-filesystem` 扫描到。插件包含一个总守门 skill 和十一个专用 skill：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.dsh\skills" | Out-Null
Copy-Item -Recurse -Force `
  "<仓库>\tools\dsh-plugin-ascend310b-writing\skills\*" `
  "$env:USERPROFILE\.dsh\skills"
```

专用入口：

- `ascend310b-writing-theory`：理论章节和附录理论内容；
- `ascend310b-writing-case`：案例1至案例9；
- `ascend310b-writing-presentation`：Marp 课件；
- `ascend310b-writing-docs`：代码工程 README、`docs/`、API/部署/验证文档，以及每个 case 统一的 `docs/troubleshooting.md` 问题台账（测试设备、服务、模型或 UI 出问题后必须登记）；
- `ascend310b-code-docs`：源码 API、CLI、docstring、README 和可运行示例；
- `ascend310b-writing-academic`：教材润色、去 AI 味、claim-evidence-citation、方法、实验、结果和限制，输出可替换正文与 Revision notes；
- `ascend310b-curriculum-planning`：章节、案例、样例和课件的学习目标、先修和评估矩阵；
- `ascend310b-writing-vuepress`：开源网页教材的主题、navbar、sidebar、base、frontmatter 和 GitHub Pages；
- `ascend310b-writing-latex`：LaTeX/PDF、字体、锚点和构建日志；
- `ascend310b-writing-markdown`：CommonMark/GFM、Markdownlint 和正文卫生；
- `ascend310b-writing-dot`：Graphviz DOT 架构图、数据流图和程序流程图；
- `ascend310b-writing`：跨对象共同规则和路由。

教材、案例和课件是学生读的完成稿。`.dot` 路径、Graphviz 字体名、pt/dpi、画布自检和 `human-review: pending` 属于生产备注，只能留在写作 skill、仓库源文件和课件页脚 HTML 来源链接中，不能进入读者可见正文。

## 本地网页与人工验收

修改任何会进入 VuePress 的教材、案例、代码 docs 或 Presentation 后，必须启动或复用 `pnpm run docs:dev`，把对应本地 URL 交给用户。开发服务器 HMR、HTTP 200、`docs:build` 和自动 audit 只是机械证据；最终状态先写 `human-review: pending`，只有用户明确确认本地网页内容正确后才算完成。

- 正文：打开对应的 `book/`、`experiment/` 或 `appendix/` 页面；
- 课件：打开对应的 `presentation/<deck>.html`，同时完成真实 1280×720 浏览器检查；
- 页面排版：运行 `node scripts/check_page_layout.mjs --url <URL>`，检查 1440px 和 390px 视口的横向溢出、图片/SVG 越界、内部滚动，以及正文图片自然尺寸/显示宽度是否超过 2.5× 或 3×；
- 没有本地人工确认，不得把结果描述为最终交付或 push。

## 配置

仓库根目录必须来自下面三者之一——**插件不内置任何路径**。硬编码一个检出目录
就是"主机耦合进可移植包"，而且指错了地方会静默审计错的对象。

| 来源 | 键 | 说明 |
| --- | --- | --- |
| 调用参数 | `root` | 单次调用的仓库根目录覆盖 |
| 插件配置 | `config.repoRoot` | **本机持久值放这里**，写在 profile 的 `cordis.patch.yml` |
| 环境变量 | `TUTORIAL_WRITING_ROOT` | 进程级覆盖 |

三者都没有时调用失败，并列出这三条设置途径（`ascend310b_writing_audit` 的提示词里也写明了）。
本机之前用的是内置默认值，现在改为在 desktop profile 的 patch 里配置：

```yaml
- id: dsh-plugin-ascend310b-writing
  name: dsh-plugin-ascend310b-writing
  config:
    repoRoot: D:/Github/Ascend310
```

## 环境要求与可移植性

插件按一条界线分层：**仓库耦合**（知道这本书的布局）留在插件内，**主机耦合**
（知道 bash/pandoc/解释器在哪、端口、PDF 被占用要重试）放到包外的
`dsh-plugin-ascend310b-writing-tools/`。判定标准是：一段代码如果在别人机器上会
因为你无法控制的原因失败，它就不该进插件包。

使用这个插件**只需要 Node**：

| 依赖 | 缺了会怎样 |
| --- | --- |
| `AGENTS.md` | 冻结名表交叉校验报告"跳过"，并说明原因；其余规则照常 |
| `latex/` 或 `latex/book.log` | 渲染体检与产物新鲜度报告 `available: false`，并提示先运行 `./convert-vuepress.sh`；不报假问题 |
| `git` 或不可读的 revision | 产出一条 `commit-unreadable`，不抛异常；push gate 相应地判为 blocked |

**不需要** pandoc、TeXLive 或 Python 就能使用全部检查——它们只在你真的要出
PDF 时才需要，而那一步由仓库自己的 `convert-vuepress.sh` 完成，插件只负责
**探测环境并说明怎么补齐**。

### 构建工具链（可选）

`ascend310b_writing_toolchain` 工具与 `scripts/doctor.mjs` 回答"缺什么、怎么装"：

```powershell
node scripts/doctor.mjs <仓库根目录>        # 人读报告；LaTeX 路径不可用时退出码为 1
node scripts/doctor.mjs <仓库根目录> --json # 机器可读，便于安装脚本判断
```

它探测 `bash`、`pandoc`、`latexmk`、`xelatex`、`python3`、`pnpm`、`node`，报告
每项的版本与来源，给出**按平台分类**的安装命令（Windows 用 winget、Ubuntu/WSL
用 apt、macOS 用 brew），并明确列出每条命令对应哪个组件。

三处容易踩的环境细节由插件处理，不需要使用者自己判断：

| 情况 | 插件的处理 |
| --- | --- |
| **用 Anaconda / Miniconda** | Anaconda 默认**不把 python 加进 PATH**，看起来像"没有 Python"。插件会从 `CONDA_PREFIX`、`CONDA_EXE`、`%USERPROFILE%\anaconda3`（含 `envs/*` 命名环境）、`%ProgramData%`、`/opt/conda` 等由环境变量推导的位置探测，并标明用的是哪个环境 |
| **Windows 上的 `bash` 是 WSL 的** | 只按路径猜会翻译错（`/d/` 与 `/mnt/d/`），所以插件从 `--version` 横幅判断：`x86_64-pc-msys` 是 Git Bash，`pc-linux-gnu` 是 WSL。默认优先 Git Bash，因为 WSL 看不到装在 Windows 侧的 pandoc/TeXLive；真用 WSL 时会明确警告 |
| **Store 占位符 / `.cmd` 垫片** | `WindowsApps\python.exe` 能执行但退出码 9009、无输出，不算"找到"；`pnpm` 这类 `.cmd` 垫片用 `cmd.exe /c` 探测 |

工具装在非 PATH 位置时不必改代码：在 profile 的 `cordis.patch.yml` 里给
`dsh-plugin-ascend310b-writing` 设置 `config.toolchain`，例如
`{ python3: "C:/path/to/envs/pdf/python.exe" }`。

`test/portability.mjs` 把可移植界线钉住：扫描插件包内所有可执行文件，出现检出
路径、家目录、DSH 运行时目录、应用安装路径、profile 布局或固定端口就失败
（`C:\Program Files\Git`、`C:\texlive` 这类**文档化的软件安装根**是允许的，
因为探测它们正是职责所在）；同时验证缺 `AGENTS.md`/`latex/`/`git` 时四类检查
都如实降级。规则里刻意**不**禁止 `src/book`、`sidebar.ts`、`AGENTS.md` 这类
仓库耦合——那是插件的用途。

## 构建

`ascend310b_writing_build` 走仓库自己的 `convert-vuepress.sh` / `pnpm run docs:build`，**不重新实现转换**，
只负责三件手工容易出错的事：

1. **环境发现**：由适配器决定 shell 是 Git Bash 还是 WSL、检出路径怎么拼给它、以及转换器调用的
   `python3` 是否需要垫片（Anaconda 用户没有 `python3`，构建期间临时提供一个指向它的 shim）。
2. **构建后立即核对**：同一任务里读新日志查缺字形/未定义引用，并把生成的 `\label` 与源里的 `{#id}` 比对。
   这一步最容易忘，现在结论随构建一起到。
3. **后台任务**：整本书约 10 分钟。返回 `jobId`，用平台自带的 `job_output` 读流式输出、`job_kill` 停止。
   失败会分类：`book.pdf` 被占用时从已生成的 `book.xdv` 定点重写，不盲重试；未知失败如实上报。

**不带 `confirm: true` 时只做预检**：报告工具链状态、将要执行的完整命令、拦截原因，不执行、不写盘。
这是刻意对齐 `AGENTS.md` 的"只有用户要求时才生成 LaTeX/PDF"。

该工具只在组成里**有后台任务服务**时注册（需要 `dsh-jobs-local` + `dsh-tool-jobs`）；
没有后台任务的组成不含这个工具，此时用上面"开发与验证"里的方式手工构建。
## 文风体检

`ascend310b_writing_style` 回答的是另一个问题：**文字读起来像不像人写的教材**。它不做判定，只把
25 万字里最值得人读一遍的句子挑出来，按四类分组：

| 分组 | 含义 | 改写方向 |
| --- | --- | --- |
| 营销腔 | 极致、出色、无缝、全能型选手、杀手锏、赋能、打造、一站式… | 换成数字、接口名、行为对比 |
| 空转套话 | 综上所述、众所周知、不难发现、值得注意的是… | 整句删掉 |
| 元话语 | 本文将、本节将、下面让我们… | 直接进入内容 |
| 对话残留 | 正如您所指出的、您提到、希望对您有帮助… | 删掉称呼与收尾语（最强的 AI 痕迹） |
| 名词化空动词 | 对……进行……、加以说明、使……得到提升 | 直接写动词 |
| 无源权威 | 研究表明、数据显示、专家指出（无出处） | 教材里是硬伤：给出处或删铺垫 |
| 机器残留 | `oaicite`、`turn0search`、`[cite: 1]` | 从模型输出粘进来的 token，直接删 |

**排版痕迹**单独计数（破折号 `——`、三点省略号、六角括号），并且**只在正文里数**：
本书实测原始统计有 71 处三点省略号，排除代码块与行内代码后只剩 1 处——不过滤就会造出 70 条假阳性。
破折号一段内两次以上单列，全书 5 段。

词表与判据合并自公开的中文写作规则集：[textlint-rule-preset-zh-technical-writing](https://github.com/darkyzhou/textlint-rule-preset-zh-technical-writing)、
[anti-vibe-writing](https://github.com/weijt606/anti-vibe-writing)、[stop-slop-zh](https://github.com/VincentOld/stop-slop-zh)，
并参考了 DSH 上的另一个写作插件 [dsh-writing-workbench](https://www.npmjs.com/package/dsh-writing-workbench) 的架构（配置化 skill、过期建议保护）。

另外报告段落/句子长度分布与对举句式（不仅…而且、既…又、不是…而是）计数——段落长度过于均匀
是机械感的一个来源。

**词表是校准过的，不是猜的**：`首先` 在本书出现 44 次，抽样确认全部是合法步骤词
（"首先调用 acl.init"），因此**刻意不报**——误报会让作者直接忽略这条工具。首跑结果：全书
246,616 字、84 处命中（营销腔 64 / 空转套话 12 / 元话语 7 / 对话残留 1），密度 0.34 次/千字，
最高的是 `chapter1.md` 3.57 次/千字（产品综述那几节）。

**程度副词（非常/十分/相当/特别）刻意不逐条报**：在技术中文里 相当于、特别是、需特别注意 都是正常表达，逐条报会瞬间淹没真信号。进行推理/进行训练/进行测试 同理——它们是技术写作的常规动词搭配。

**永不阻塞**：不进推送门槛、不产生 error。同一个词在某个句子里可能恰好准确，判断权在作者。
改写方法见技能文档的「去 AI 味」一节。

## DOT 图画布

系统架构图和流程图必须按教材载体控制尺寸，而不是生成一张任意大的图片：

| 载体 | 推荐画布 | 最大像素 |
| --- | --- | --- |
| 教材网页/PDF | `size="7.2,4.2"`，160–180 dpi | 约 1600×1000 |
| Presentation 16:9 | `size="8.5,4.8"`，`ratio="fill"`，144 dpi | 1280×720 |

`ascend310b-writing-dot` 提供：

- [`architecture-textbook.dot`](skills/ascend310b-writing-dot/templates/architecture-textbook.dot)：教材正文两列纵向架构图模板；
- [`architecture-slide.dot`](skills/ascend310b-writing-dot/templates/architecture-slide.dot)：16:9 系统架构图模板；
- [`flow-textbook.dot`](skills/ascend310b-writing-dot/templates/flow-textbook.dot)：教材纵向流程图模板。

节点超过 8 个、需要缩小字体或图片超过画布时，必须拆成“总览图 + 细节图”。模板渲染和尺寸检查由 `test/dot-templates.mjs` 验证。

## 课件规范检查

已发布课件的“对应教材”必须链接 GitHub Pages HTML（例如 `https://zhouxzh.github.io/Ascend310/experiment/case9.html`），“配套样例”必须链接 GitHub `samples/<name>/` 目录；不要在生成后的课件中保留 `../experiment/*.md` 等本地相对教材导航。该规则由 `ascend310b-writing-presentation` skill 和 `test/skills.mjs` 共同约束。

| 规则 | 级别 | 判据 |
| --- | --- | --- |
| `deck-scaffold` | info | 占位课件（`presentation-scaffold` 标记），不计为完成件 |
| `deck-content-thin` | warning | 少于 6 页或少于 800 字 |
| `deck-slide-underfull` | warning | 散文页正文少于 40 字且无代码/表格/图片 |
| `deck-cover-missing` | warning | 缺少 `_class: cover` 封面 |
| `deck-cover-meta-label` | warning | 封面出现“教师授课版/理论章节课件”等自我标注 |
| `deck-cover-byline-missing` | warning | 封面缺作者与邮箱 |
| `deck-summary-missing` | warning | 缺少小结或课堂任务 |
| `deck-title-too-long` | warning | 页面标题超过 30 字 |
| `deck-theme-unsupported` | warning | 未使用统一的 `ascend310`（Metropolis 风格）主题 |
| `deck-web-density-low` | warning | 生成网页的单页信息量低于学术报告下限 |
| `deck-web-density-high` | warning | 生成网页的单页信息量超过上限（表格行/列、代码行） |
| `deck-web-density-uneven` | warning | 同一课件正文/表格页密度跨度 ≥ 2.8 倍 |
| `deck-web-theme-drift` | warning | 生成页面不是统一的 `ascend310` 主题 |
| `deck-slide-overflow` | error | 源码估算高度超过安全高度 |
| `deck-render-overflow` | error | 真实浏览器中纵向或横向裁切 |
| `deck-render-image-missing` | error | 真实浏览器中图片未加载 |
| `deck-render-no-decks` | warning | 指定的课件尚未构建 |
| `deck-render-unavailable` | warning | 缺少 Node、Puppeteer 或浏览器 |
| `deck-code-too-long` | warning | 单页代码超过 22 行 |
| `deck-table-too-wide` | warning | 表格超过 7 列 / 10 行 |
| `deck-font-too-small` | error | 正文低于 26px，或代码/表格低于 20px |
| `deck-source-missing` | error | 没有指向教材或样例的来源 |
| `deck-metadata-missing` | warning | 缺 Marp 全局指令 |
| `deck-image-no-alt` | error | 图片缺 alt（MD045） |
| `deck-code-no-language` | warning | 代码块无语言（MD040） |
| `deck-bare-url` | warning | 正文裸 URL（MD034） |
| `deck-notes-sparse` | info | 讲师备注覆盖低于 20% |
| `deck-code-untraceable` | info | 代码块未标来源 |
| `deck-visual-missing` | info | 案例课件少于 2 张图 |
| `deck-tier-claim` | error | 只写 20T/310B1，缺少 8T/310B4 适用范围 |
| `deck-frozen-mismatch` | warning | 课件标题与冻结名不一致 |
| `deck-readme-order` | warning | README 目录顺序与文件编号不一致 |
| `ci-pages-failed` | error | GitHub Pages 最近一次构建失败（含失败步骤摘要） |
| `ci-pages-pending` | info | Pages 构建仍在运行 |
| `ci-run-missing` | warning | 找不到 Pages 工作流运行记录 |
| `ci-remote-behind` | info | 本地 HEAD 尚未推送，远端构建不代表本地 |
| `ci-gh-unavailable` | info | gh 不可用，无法读取 Pages 状态 |
| `ci-query-failed` | warning | gh 查询失败 |

容量超出时用 `node scripts/split-decks.mjs --write <课件>` 分页，不要缩小字号。真实裁切用 `ascend310b_writing_deck_render` 或 `pnpm run docs:slides:render-check` 复核。

## 新电脑安装插件

前置：安装 DSH 桌面版，克隆本仓库。插件必须**先打包成 tarball 再安装**：

```bash
node scripts/install-dsh-plugins.mjs            # 打包并安装写作 + 调试插件
node scripts/install-dsh-plugins.mjs --check    # 只核对安装方式与版本
node scripts/install-dsh-plugins.mjs --profile <名称>
```

原因：`dsh plugin add <源码目录>` 会生成 `link:` 依赖。Node 会按仓库真实路径解析插件的 `@deepseek-ai/dsh-tools` 等 DSH host 包，而仓库里没有这些包，插件启动时会导入失败。

脚本会：

1. 在 `~/.dsh/plugin-packs/` 生成两个 `.tgz`；
2. 从 profile 移除旧的 `link:` 依赖；
3. 用 `dsh plugin add <tarball...>` 安装真实副本；
4. 核对 profile 中记录的是 `file:`，不是 `link:`。

也可以在 DSH 的 **插件 → 添加插件** 中直接选择这两个 tarball。修改插件源码后重新运行脚本，并在 DSH 中重启；项目级 skill 由仓库 `.dsh/skills` 自动加载，不需要额外安装。

## Skill 安装

插件通过 `skills/` 携带写作 skill，但 **DSH 只从 `$DSH_HOME/skills/<name>/SKILL.md` 读取**，不会自动从已安装插件里复制。安装或更新：

```bash
node scripts/install-skill.mjs          # 安装/更新到 ~/.dsh/skills
node scripts/install-skill.mjs --check  # 只核对，不一致时退出码 1
```

`package.json` 里对应 `pnpm run skills:install` 与 `pnpm run skills:check`。

### 项目级 skill（推荐）

DSH 的 skill 搜索顺序是：

| rank | 来源 | 路径 |
| --- | --- | --- |
| 100 | `project-dsh` | `<仓库>/.dsh/skills` |
| 200 | `project-agents` | `<仓库>/.agents/skills` |
| 300 | `custom` | 配置里的自定义目录 |
| 之后 | 用户级 | `~/.dsh/skills` |

因此仓库里额外维护 `.dsh/skills/`：新克隆的仓库**无需安装**就能拿到本仓库的写作与调试规则，且优先于用户目录里的旧版本。它由脚本从两个插件生成，避免出现第三份手抄：

```bash
node scripts/sync-repo-skills.mjs          # 生成/更新 .dsh/skills
node scripts/sync-repo-skills.mjs --check  # 只核对，不一致退出码 1
```

仓库根对应 `pnpm run skills:sync` 与 `pnpm run skills:check`。

DSH 在**会话启动时**加载 skill：更新后新建会话才会拿到新内容。当前会话的 skill 目录里能看到 `ascend310b-writing`，说明加载链路本身是通的；`--check` 用来防止"仓库里改了、实际用的还是旧版"。
## 参考项目

课件规范不是凭空写的，以下开源项目的规则被直接采纳或改写：

| 项目 | 采用的内容 |
| --- | --- |
| [Marp CLI](https://github.com/marp-team/marp-cli) / [Marp Core](https://github.com/marp-team/marp-core) | 16:9 = 1280×720；全局指令（`theme/paginate/size`）；讲师备注与 `--notes`/`--pdf-notes`；官方说明"自动缩放只解决横向"——纵向溢出必须分页 |
| [markdownlint](https://github.com/DavidAnson/markdownlint) | MD045 图片 alt、MD040 代码语言、MD034 禁止裸 URL（行内代码例外），以及 accessibility 规则分组 |
| [Slidev](https://github.com/slidevjs/slidev) | Markdown-first 的课件组织方式；演讲者模式、代码高亮、图表与 PDF/PNG 导出作为"基本课件能力"的参照 |
| [reveal.js](https://github.com/hakimel/reveal.js) | 演讲者视图、PDF 导出、代码高亮与嵌套页面结构的同类实践 |

## 网页教材检查

`ascend310b_writing_audit` 不只审 Markdown 和 PDF，也读**构建后的站点**（`src/.vuepress/dist`），因为读者打开的是网页：

| 规则 | 级别 | 判据 |
| --- | --- | --- |
| `site-not-built` | error | `dist/index.html` 不存在 |
| `site-page-missing` | error | 某篇正文没有对应的 `.html` 页面 |
| `site-link-broken` | error | 站内链接指向不存在的页面 |
| `site-fragment-missing` | error | `#锚点` 在目标页中不存在 |
| `site-asset-missing` | error | `src` 指向不存在的资源（CSS/JS/图片） |
| `site-unrendered` | error | 正文里残留 `{#…}`、`:::` 等未渲染标记（代码块内不算） |
| `site-empty-page` | warning | 页面没有 h1 或正文过短（404/作者页除外） |
| `site-sitemap-missing` | warning | 缺少 `sitemap.xml` 或 `robots.txt` |

实测：当前站点 71 页、369 个资源，断链 0、缺失资源 0，检查耗时约 100 ms。

VuePress 发布前还要运行 `ascend310b_writing_page_layout`（或 `node scripts/check_page_layout.mjs --url <本地页面 URL>`）：在 1440×1000 和 390×844 视口检查页面横向滚动、图片/SVG 越界，并把代码块/表格内部滚动单独报告。正文图片在桌面视口自然宽度超过显示宽度 2.5× 会警告，超过 3× 会失败；移动端只报告过度缩放，不升级为硬失败。该工具只做机械排版检查，不能替代人工视觉验收。

## 图表与排版检查

图片与结构图同属"会同时破坏 PDF 和网页"的一类问题：

| 规则 | 级别 | 判据 |
| --- | --- | --- |
| `image-too-heavy` | warning / error | 单张图片 > 1 MB warning，> 1.5 MB error |
| `image-too-wide` | warning / error | > 1600×1000 warning，> 2400×1500 error；教材图片硬上限 |
| `image-alt-missing` | warning | `![](…)` 缺少替代文字 |
| `image-duplicate` | warning | 两张图片字节完全相同，不能冒充不同板端/状态证据 |
| `image-baseline-stale` | info | 历史超限图已修复，删除对应的 baseline 记录 |
| `image-orphan` | info | 图片没有被任何正文引用（同类超过 8 条时汇总） |
| `dot-pair-missing` | error | `.dot` 没有同名 PNG |
| `dot-png-stale` | error | PNG 早于 `.dot`（改图后未重新渲染） |
| `dot-invalid` | error | 缺少 `graph`/`digraph` 声明 |
| `dot-cjk-font` | warning | 含中文但没指定中文字体（渲染成方框） |
| `dot-size-missing` | warning | DOT 没有显式 `graph.size` |
| `dot-canvas-too-large` | warning / error | DPI 换算后超过 `1600×1000` / `2400×1500` |
| `dot-label-too-long` | warning | 节点标签单行 > 36 字 |
| `dot-orphan` | info | 结构图 PNG 没有被正文引用 |

排版规则同时作用于 PDF 与网页：

| 规则 | 级别 | 判据 |
| --- | --- | --- |
| `layout-table-wide` | warning | Markdown 表格 > 7 列（A4 与网页内容列都放不下） |
| `layout-code-long` | warning | 单个代码块 > 80 行 |

当前实测：198 张源图片、52 个 `.dot`；2 张超重、5 个结构图标签过长、1 个中文图未设字体。
## 审计规则

正文规则（`ascend310b_writing_audit`，作用于工作区）：

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `title-missing` / `title-format` / `title-number-mismatch` | error | `第N章：`、`案例 N：`、`附录 N：` 的形状与编号 |
| `frozen-title-mismatch` / `frozen-source-missing` / `frozen-table-missing` | error | `AGENTS.md` 冻结名表与正文标题的双向比对 |
| `anchor-prefix` | error | 显式锚点必须用本文件自己的 `src-<部分>-<文件名>-` 前缀 |
| `h1-count` | error | 案例与附录正文必须恰好一个一级标题 |
| `image-missing` / `link-missing` | error | 相对图片与链接目标必须存在 |
| `sidebar-missing` | error | 正文必须出现在 `src/.vuepress/sidebar.ts` |
| `empty-body` | error | 正文不得为空 |
| `heading-jump` | warning | 标题层级不得跳跃 |
| `heading-manual-number` | error | 教材 H2-H6 不得手写 `1.`、`1.1`、`4.1`；章节编号由站点/PDF 生成，按文件汇总 |
| `trailing-whitespace` | warning | 行尾多余空白，且**已证明不影响渲染**（单空格 / 整行只有空格 / 下一行是空行或块级起点）；可安全删除 |
| `hard-break-effective` | warning | 行尾 ≥2 个空格构成 Markdown 硬换行；附带同一列表的一致性证据（「另外 N 项都没有这样写」），要留要删由作者定 |
| `tab-indent` / `crlf` / `no-final-newline` | warning | 行尾与换行卫生 |
| `cjk-halfwidth-punct` / `cjk-halfwidth-bracket` | warning | 中文之间的半角标点与括号（行内代码除外） |
| `cjk-double-space` | info | 中文之间的连续空格 |

代码交叉检查规则（正文与 `samples/` 下真实代码的对齐，回答"文档和代码还一致吗"）：

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `sample-path-missing` | error | 正文引用的 `samples/…`、`scripts/…`、`tools/…` 路径在仓库里找不到 |
| `sample-path-generated` | info | 引用的是被 gitignore 的运行时产物，确认正文写清了怎么生成 |
| `doc-code-drift` | warning | 被引用的代码文件在这段正文之后改过（只在推送门槛里跑，走近期 git 历史） |
| `code-docs-manual-heading-number` | error | `samples/caseN/docs/` 与 case README 的 H2-H6 不得手写章节编号；文件名或标题层级负责顺序，按文件汇总 |

**路径怎么解析**（这是这类检查最容易误报的地方）：`scripts/xxx.py` 在书里通常是相对于**案例自己的目录**，
甚至相对于某个子工程。所以一个 token 命中以下任一条就算存在：仓库根目录；该文对应的 `samples/caseN`
（章节为 `samples/chapterN`）目录；该样例目录下任意深度的**后缀匹配**
（例如 `samples/chapter4/SSDLite/scripts/inference_npu.py` 可被正文的 `scripts/inference_npu.py` 命中）。
刻意宽松：误报会让作者忽略规则，而"文件彻底消失"这类真问题依然会被抓到。

另外两类**不检查**，因为它们不是仓库路径：附录 2 那类通用教程命令（没有对应样例目录）、
以及 `samples/caseN/README.md` 这类模板占位符。
交叉产物规则（同样是 `ascend310b_writing_audit` 的一部分，描述正文之外的两件产物）：

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `deck-frozen-mismatch` | warning | 演示文稿封面标题或 `footer` 里的「第N章／案例 N／附录 N：…」不是冻结名表里的名字——改名时 deck 最容易漏 |
| `slide-stale` | warning | 放映页（`public/presentation/*.html`）早于源文稿，重新跑 `pnpm run docs:slides` |
| `slide-missing` | info | 源文稿还没有构建出的放映页 |
| `site-stale` | warning | 站点 `dist` 早于最新源文件，预览或推送前先 `pnpm run docs:build` |
| `site-missing` | info | 还没有构建过站点 |

VuePress 配置规则（读 `src/.vuepress/` 的声明式配置，覆盖"Markdown → 站点"这条线上的断链与漂移）：

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `vuepress-page-missing` | error | `navbar.ts` / `sidebar.ts` 里的路由没有对应页面（改名后最容易漏） |
| `vuepress-nav-missing` | error | 导航栏指向的页面不存在 |
| `vuepress-deck-link-broken` | error | 侧边栏指向的放映页 `.html` 没有源文稿（文稿被改名/删除） |
| `deck-unlinked` | warning | 演示文稿存在但 sidebar 里没有入口，站点上访问不到 |
| `vuepress-deck-unbuilt` | info | 侧边栏的放映页还没构建（`public/presentation/` 是 gitignore 的产物） |
| `vuepress-asset-missing` | error | `theme.logo` 或 `config.head` 里的 favicon 在 `public/` 下不存在 |
| `vuepress-frozen-mismatch` | warning | sidebar 的 `text:` 用了冻结名的形状但不在冻结名表里 |
| `vuepress-base-mismatch` | warning | `config.ts` 的 `base` 与 `client.ts` 里写死的前缀不一致（放映页跳转会失效） |
| `vuepress-repo-mismatch` | warning | `theme.ts` 的 `repo` 与 git 远端不一致 |
| `vuepress-hostname-mismatch` | warning | `theme.ts` 的 `hostname` 与 `<user>.github.io` 不一致 |

这几条都在 `ascend310b_writing_audit` 与推送门槛里，和正文规则一起出结论。
`src/presentation/` 作为**配套演示**分部纳入统计与审计，但标记为 `assets: true`：文稿是 Marp 课件而不是正文，
一份文稿可能有几十个一级标题、也不进 VuePress sidebar，所以标题形状、单 H1、标题层级、sidebar 覆盖、
冻结名表这几类规则对它**不适用**；通用 Markdown 卫生与链接/图片检查照常。

提交树规则（`ascend310b_writing_audit` 带 `rev` 时）：

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `commit-link-missing` | error | 该提交内引用的目标不在该提交里（工作区有、没提交进去） |
| `commit-unreadable` | error | revision 不存在或不是 Git 仓库 |

渲染与产物规则（`ascend310b_writing_render`）：

| 规则 | 级别 | 说明 |
| --- | --- | --- |
| `render-missing-character` | error | 字体缺字形，PDF 里该字符会缺失或落到替换字形 |
| `render-undefined-reference` | error | 交叉引用或文献引用未定义 |
| `render-hard-error` | error | 以 `!` 开头的 LaTeX 硬错误 |
| `artifact-anchor-unparsed` | error | 源里声明的 `{#id}` 没能进入生成物，且证据表明**转换器无法表达它**（标题被转义成字面文本，或 `{#id}` 被当成标题文字）——修源 Markdown，重新构建本身修不好 |
| `artifact-anchor-stale` | warning | 源锚点缺失，且源比产物新：生成物落后于源，重新构建即可 |
| `artifact-anchor-missing` | warning | 源锚点缺失，但源并不比产物新：先重新构建，仍缺失再检查标题写法 |
| `artifact-pdf-stale` | warning | `book.pdf` 早于最新源或最新 `.tex` |
| `render-overfull-box` | warning | 超页宽 ≥20pt 的盒子，只报最大的 5 个（全书可能有 80+ 个小的） |
| `render-log-stale` | warning | 日志早于最新正文改动，不要据旧日志下结论 |

## 推送前检查

`ascend310b_writing_push_check` 把可机械核验的项和只有人能确认的项分开：

| 项 | 是否阻塞 | 说明 |
| --- | --- | --- |
| `manuscript-audit` | 阻塞 | 正文 error 必须为 0（含冻结名表比对） |
| `commit-tree` | 阻塞 | 将要推送的那个提交内部不能有断链 |
| `uncommitted-manuscripts` | 提示 | `src/` 下有未提交改动时，推上去的是它们上一次提交的版本 |
| `render` | 提示 | LaTeX 日志的渲染结论；日志过期时只提醒先重建（LaTeX 不参与 Pages 发布） |
| `artifacts` | 提示 | 生成物是否落后于源 |
| 站点预览确认 | **不判定** | `AGENTS.md` 规定未经用户确认不要 push；这一步只有人能确认，输出的最后一行会点名 |

结果按级别排序后再截断，因此一条噪声大的 info 规则不会淹没后面的 error。

## 开发与验证

六个检查都不需要重启桌面应用（都从当前目录取仓库根，所以先在仓库里 `cd`）：

```powershell
cd D:\Github\Ascend310

# 1. 正文审计内核（无 Harness 依赖）
node <插件目录>\test\smoke.mjs

# 2. 行尾分级规则：fixture 回归
node <插件目录>\test\manuscript-rules.mjs

# 3. 跨产物检查：fixture 回归 + 真实仓库 + gate 判定
node <插件目录>\test\checks.mjs

# 4. 可移植性守卫：主机耦合扫描 + 缺依赖降级
node <插件目录>\test\portability.mjs

# 5. 工具链探测：组件探测、平台适配、Anaconda 推导、安装提示
node <插件目录>\test\toolchain.mjs

# 6. 浏览器端注册契约（桩加载器 + 桩 React）
node <插件目录>\test\client-mount.mjs
```

第 7 个（主机端真实挂载：临时 profile + 备用端口 + 跑完自动清理）需要本机
工具目录，因为它依赖 Windows、DSH profile 布局和一个固定端口：

```powershell
powershell -File <tools 目录>\scripts\verify-profile.ps1
```

第 2 步用合成 fixture 固定每条规则的答案（表里标题漂移、文件不存在、正文未登记、
缺字形去重、Overfull 阈值、日志过期与否、硬换行的可删/不可删分界），防止以后
某次改动把规则变成误报源——这个插件已经在 `cjk-double-space` 上栽过一次，
707 条误报全部来自"删掉行内代码后人为粘出空格"。第 5 步同理：bash 横幅到 shell
类型的映射、conda 根推导、python 的 native/shim/missing 三态都由 fixture 固定。

第 7 步会建一个一次性的 `twcheck` profile、在 19399 端口启动一个临时实例、
在 profile patch 层写入 `config.repoRoot`（顺带验证配置链路）、请求面板路由、
确认浏览器 bundle 出现在 boot graph 里，然后杀掉进程并删掉 profile。
桌面 profile 由 Electron 独占，`dsh --profile desktop` 会拒绝执行，所以这是改
插件后唯一能在重启前验证主机端的方式。

插件包内的独立脚本（跨平台）：

```powershell
# 工具链体检（安装后第一件事）
node scripts/doctor.mjs <仓库根目录>

# 只做提交树引用检查（与 ascend310b_writing_audit 的 rev 参数共用同一份实现）
node scripts/check-commit-links.mjs . HEAD

# 按审计报告清理可证明无害的行尾/换行问题（默认 dry-run，加 --apply 生效）
node scripts/fix-hygiene.mjs . --apply
```
