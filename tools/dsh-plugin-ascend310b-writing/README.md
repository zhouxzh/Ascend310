# dsh-plugin-ascend310b-writing

教程写作守门员 —— 为 Ascend310《昇腾 310B 边端 AI 实战》教材仓库定制的
DeepSeek Harness 插件（bundle）。

它把仓库 `AGENTS.md` 里的冻结命名规则、Markdown 卫生要求、LaTeX 渲染与产物
新鲜度、推送前门槛和写作进度，变成四个模型可直接调用的工具和一个 Web 侧边栏面板。

## 组成

| 面 | 文件 | 作用 |
| --- | --- | --- |
| 主机端 | `lib/index.js` | 注册 `ascend310b_writing_audit`、`ascend310b_writing_render`、`ascend310b_writing_push_check`、`ascend310b_writing_toolchain`、`ascend310b_writing_progress` 五个工具；组成里有 Web 服务器时额外注册 `/ascend310b-writing/report.json` 只读路由 |
| 审计内核 | `lib/audit-core.js` | 正文审计、行尾分级与进度统计；纯 Node ESM，无 Harness 依赖，可单独运行与测试 |
| 跨产物检查 | `lib/checks.js` | 读 Markdown 之外的证据：`AGENTS.md` 冻结名表、LaTeX 构建日志、生成物与源的锚点差、Git 提交树 |
| 工具链体检 | `lib/toolchain.js` + `lib/doctor.js` | 探测 bash/pandoc/latexmk/xelatex/python3/pnpm/node（含 Anaconda/conda），给按平台分类的安装命令，判断哪些能力可用 |
| 平台适配 | `drivers/env-windows.mjs` + `drivers/env-linux.mjs` | Windows（Git Bash / WSL）与 POSIX 各自怎么起 shell、怎么翻译路径、怎么满足 `python3` |
| 浏览器端 | `lib/client.js` | 侧边栏「写作」入口与面板：字数卡片、各分部进度、审计发现列表 |
| 技能 | `skills/ascend310b-writing/SKILL.md` | 自然语言触发的中文技术写作流程 |
| 组合 | `cordis.patch.yml` | 声明本包的 bundle patch，把 Host 行插入组合 |

五个工具各回答一个不同的问题：

| 工具 | 回答的问题 |
| --- | --- |
| `ascend310b_writing_toolchain` | 这台机器 + 这个仓库现在能做什么，缺什么、怎么装 |
| `ascend310b_writing_audit` | 正文本身对不对（含 `AGENTS.md` 冻结名表的双向比对） |
| `ascend310b_writing_audit` + `rev` | 这次提交发布出去会不会断链 |
| `ascend310b_writing_render` | 渲染出来的 PDF 有没有坏字符，生成物是否落后于源 |
| `ascend310b_writing_push_check` | 现在能不能推，以及哪一项只有人能确认 |
| `ascend310b_writing_progress` | 篇幅和剩余工作量 |

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

重启 Harness 后，侧边栏出现「写作」入口，模型获得两个新工具。

技能是纯文件，复制到技能目录即可被 `dsh-skill-filesystem` 扫描到：

```powershell
Copy-Item -Recurse -Force "<仓库>\tools\dsh-plugin-ascend310b-writing\skills\ascend310b-writing" `
  "$env:USERPROFILE\.dsh\skills\ascend310b-writing"
```

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
| `trailing-whitespace` | warning | 行尾多余空白，且**已证明不影响渲染**（单空格 / 整行只有空格 / 下一行是空行或块级起点）；可安全删除 |
| `hard-break-effective` | warning | 行尾 ≥2 个空格构成 Markdown 硬换行；附带同一列表的一致性证据（「另外 N 项都没有这样写」），要留要删由作者定 |
| `tab-indent` / `crlf` / `no-final-newline` | warning | 行尾与换行卫生 |
| `cjk-halfwidth-punct` / `cjk-halfwidth-bracket` | warning | 中文之间的半角标点与括号（行内代码除外） |
| `cjk-double-space` | info | 中文之间的连续空格 |

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
