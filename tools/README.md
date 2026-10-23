# 仓库开发工具

这个目录存放与教材配套、但独立于书稿构建的 DSH 插件。每个子目录本身就是一个可复制安装的
DSH bundle，不依赖仓库根目录的 `node_modules`。

| 插件 | 可见名称 | 用途 | 源码与安装说明 | 审核记录 |
| --- | --- | --- | --- | --- |
| `dsh-plugin-ascend310b-debug` | 昇腾310B调试 | 板端事实探测、版本配套判定、分层调试计划、样例索引、只读诊断、显式任务执行与模型下载 | [`dsh-plugin-ascend310b-debug/README.md`](dsh-plugin-ascend310b-debug/README.md) | [`installation-audit.md`](dsh-plugin-ascend310b-debug/docs/installation-audit.md) |

## 随插件发布的 Skills

插件源码内的 `skills/` 是源文件，根目录 `.dsh/skills/` 是项目级安装副本：

| Skill | 用途 | 源文件 |
| --- | --- | --- |
| `ascend310b-cann-development` | 310B CANN 程序开发、PyACL/ATC/DVPP/摄像头、接口合同和 M0-M7 验收门 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-cann-development/SKILL.md) |
| `ascend310b-case-testing` | 案例从 GitHub 固定 commit、模型从 Hugging Face 固定 revision 下载，板端复现和发布证据 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-case-testing/SKILL.md) |
| `ascend310b-camera-debug` | V4L2/USB/MIPI 摄像头枚举、格式、真实帧采集和采集到处理链的分层验收 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-camera-debug/SKILL.md) |
| `ascend310b-microphone-debug` | ALSA/PulseAudio 麦克风枚举、录音合同、WAV 和电平验证 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-microphone-debug/SKILL.md) |
| `ascend310b-speaker-debug` | ALSA/PulseAudio 喇叭/耳机/HDMI 路由、音量、静音和真实播放验证 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-speaker-debug/SKILL.md) |
| `ascend310b-debug` | 失败分层、探针、诊断命令和停止条件 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-debug/SKILL.md) |
| `ascend310b-npu-arch` | 310B1/310B4 架构、8T/20T 和平台差异核对 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-npu-arch/SKILL.md) |
| `ascend310b-env-check` | 板端 CANN/ACL/Python/ATC/设备环境门 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-env-check/SKILL.md) |
| `ascend310b-api-best-practices` | Ascend C/ACL API、对齐、dtype、Buffer 和平台假设审查 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-api-best-practices/SKILL.md) |
| `ascend310b-runtime-debug` | import、返回码、Tiling、Kernel 和 plog 分层排查 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-runtime-debug/SKILL.md) |
| `ascend310b-crash-debug` | 崩溃、卡死、退出码、设备异常和内存问题 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-crash-debug/SKILL.md) |
| `ascend310b-precision-debug` | golden、容差、dtype/layout 和数值门 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-precision-debug/SKILL.md) |
| `ascend310b-sync-audit` | SetFlag/WaitFlag、EnQue/DeQue、CrossCore 和 stream 同步审计 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-sync-audit/SKILL.md) |
| `ascend310b-code-review` | 310B 代码审查、平台边界、资源和证据检查 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-code-review/SKILL.md) |
| `ascend310b-writing` | 写作总守门和跨对象规则 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing/SKILL.md) |
| `ascend310b-writing-theory` | 第1章至第10章理论教程 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-theory/SKILL.md) |
| `ascend310b-writing-case` | 案例1至案例9实践教程 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-case/SKILL.md) |
| `ascend310b-writing-presentation` | Marp/Presentation 课件 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-presentation/SKILL.md) |
| `ascend310b-writing-docs` | 代码工程 README、`docs/`、API/部署/验证说明 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-docs/SKILL.md) |
| `ascend310b-code-docs` | 源码 API、CLI、docstring 和可运行示例 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-code-docs/SKILL.md) |
| `ascend310b-writing-academic` | 教材学术论证、引用、方法、实验和限制 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-academic/SKILL.md) |
| `ascend310b-curriculum-planning` | 教材学习目标、先修关系、样例和评估矩阵 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-curriculum-planning/SKILL.md) |
| `ascend310b-writing-vuepress` | VuePress 页面、sidebar、路由和站点构建 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-vuepress/SKILL.md) |
| `ascend310b-writing-latex` | LaTeX/PDF、字体、锚点和构建日志 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-latex/SKILL.md) |
| `ascend310b-writing-markdown` | CommonMark/GFM、Markdownlint 和正文卫生 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-markdown/SKILL.md) |
| `ascend310b-writing-dot` | Graphviz DOT 架构图、数据流图和程序流程图 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-dot/SKILL.md) |

修改源 skill 后运行 `npm run skills:sync`，再用 `npm run skills:check` 检查项目级副本。

Skill 命名规范：目录名与 frontmatter `name` 必须一致，并统一使用 `ascend310b-` 前缀；专项调试使用 `ascend310b-<domain>-debug`，因此摄像头、麦克风和喇叭固定为 `ascend310b-camera-debug`、`ascend310b-microphone-debug`、`ascend310b-speaker-debug`。不要只改目录、只改 frontmatter 或只改文档引用。

## Skill 参考来源

这些 skill 以官方/长期维护资料为参考，不把第三方仓库原文整包复制；外部规则进入 310B 流程前都经过本仓库边界和板端证据重写。

| Skill | 参考来源 | 采用内容 |
| --- | --- | --- |
| `ascend310b-npu-arch` 及 8 个 310B 调试/API 专项 skill | [CANNBot Skills](https://gitcode.com/cann/cannbot-skills)、[功能清单](https://gitcode.com/cann/cannbot-skills/blob/master/docs/feature-list.md) | 环境检查、API 限制、runtime/crash/precision/sync 审查思路；保留 310B 边界，不引用 A2/A3/950 结论 |
| `ascend310b-camera-debug` | [Linux V4L2 API](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/v4l2.html)、[v4l2-ctl 手册](https://manpages.debian.org/bookworm/v4l-utils/v4l2-ctl.1.en.html)、[OpenCV VideoCapture](https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html)、[GStreamer v4l2src](https://gstreamer.freedesktop.org/documentation/video4linux2/v4l2src.html) | 设备枚举、格式协商、真实读帧和低帧率/黑屏/EBUSY 分层检查 |
| `ascend310b-microphone-debug` | [ALSA arecord/aplay](https://manpages.debian.org/bookworm/alsa-utils/arecord.1.en.html)、[ALSA project](https://www.alsa-project.org/wiki/)、[pactl](https://manpages.debian.org/bookworm/pulseaudio-utils/pactl.1.en.html)、[SoX](https://sox.sourceforge.net/sox.html) | 录音设备、硬件能力、WAV 采样合同、mute/增益和电平验证 |
| `ascend310b-speaker-debug` | [ALSA aplay](https://manpages.debian.org/bookworm/alsa-utils/aplay.1.en.html)、[speaker-test](https://manpages.debian.org/bookworm/alsa-utils/speaker-test.1.en.html)、[pactl](https://manpages.debian.org/bookworm/pulseaudio-utils/pactl.1.en.html) | 播放 device、默认 sink、音量/静音、声道和真实发声验证 |
| `ascend310b-writing-vuepress` | [VuePress Getting Started](https://vuepress.vuejs.org/guide/getting-started.html)、[VuePress Configuration](https://vuepress.vuejs.org/guide/configuration.html)、[VuePress Assets](https://vuepress.vuejs.org/guide/assets.html) | 源目录/生成目录分离、sidebar、路由、静态资源和 `docs:dev/docs:build` |
| `ascend310b-writing-latex` | [LaTeX Project Documentation](https://www.latex-project.org/help/documentation/)、[Learn LaTeX](https://www.learnlatex.org/)、[CTAN](https://ctan.org/) | 源 Markdown 到 LaTeX/PDF、日志、字体、锚点和生成物边界 |
| `ascend310b-writing-markdown` | [CommonMark Spec](https://spec.commonmark.org/)、[Markdown Guide](https://www.markdownguide.org/)、[markdownlint](https://github.com/DavidAnson/markdownlint) | 标题、链接、图片、表格、代码围栏、标点和机械卫生规则 |
| `ascend310b-writing-dot` | [Graphviz DOT Language](https://graphviz.org/doc/info/lang.html)、[Graphviz Command Line](https://graphviz.org/doc/info/command.html)、[Graphviz Gallery](https://graphviz.org/gallery/) | 架构图/流程图语法、16:9 与教材画布、统一中文字体、模板、输出尺寸和版面检查 |
| `ascend310b-writing-presentation` | [Marp CLI](https://github.com/marp-team/marp-cli)、[Marp Core](https://github.com/marp-team/marp-core)、[markdownlint](https://github.com/DavidAnson/markdownlint) | Marp 元数据、讲师备注、页面容量、真实浏览器验收 |
| `ascend310b-writing-theory` / `case` / `docs` | 本仓库 `AGENTS.md`、`docs/00-repository-structure.md`、`src/`、`samples/` | 已冻结标题、章节/案例分层、代码工程文档和 310B 证据边界 |
| `ascend310b-code-docs` | [mcollina/skills 的 Documentation skill](https://github.com/mcollina/skills/tree/main/skills/documentation)、[Diátaxis](https://diataxis.fr/) | 教程/How-to/Reference/Explanation 分层，从代码和测试反推 API 文档 |
| `ascend310b-writing-academic` | [PaperOrchestra](https://github.com/Ar9av/PaperOrchestra)、[latex-arxiv-SKILL](https://github.com/appautomaton/latex-arxiv-SKILL)、[PaperFit](https://github.com/OpenRaiser/PaperFit) | claim-evidence-citation、方法/实验/结果/限制和版面验证；不复制论文生成流水线 |

参考来源是方法论输入，不是对第三方规则适用性的背书。实际操作仍以当前仓库规则、`ascend_probe` 和板端验证为准。

写作插件还约定：已发布 Presentation 的“对应教材”链接必须使用 GitHub Pages HTML，“配套样例”必须使用 GitHub `samples/` 目录；已发布课件不得保留 `../experiment/*.md` 等本地相对教材导航。

所有进入 VuePress 的写作改动，完成后都必须启动或复用 `pnpm run docs:dev`，由用户在本地网页上人工查看最新内容。可用 `node scripts/check_page_layout.mjs --url <URL>` 检查 1440px/390px 横向溢出；`docs:build`、HTTP 200 和自动审计通过不能替代人工验收；最终状态保持 `human-review: pending`，用户确认后才能通过。

### GitHub 高星参考核对

以下 star 数是 2026-10-18 通过 GitHub Search API 查询到的快照，会随时间变化；它们是**参考来源**，不是本仓库直接复制或依赖的上游包。

| 主题 | 高星/官方参考 | 判断 |
| --- | --- | --- |
| Markdown | [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills)（约 49.7k）、[markdown-viewer/skills](https://github.com/markdown-viewer/skills)（约 3.4k） | 采用 skill 组织方式、Markdown 规则和图表入口；不采用 Obsidian 专属 CLI |
| LaTeX | [PaperOrchestra](https://github.com/Ar9av/PaperOrchestra)（约 682）、[latex-arxiv-SKILL](https://github.com/appautomaton/latex-arxiv-SKILL)（约 464）、[PaperFit](https://github.com/OpenRaiser/PaperFit)（约 334） | 参考门控式写作、编译/版面验证思路；不采用论文生成流程作为教材写作流程 |
| DOT/图 | [markdown-viewer/skills](https://github.com/markdown-viewer/skills)（约 3.4k）、[Graphviz Gallery](https://graphviz.org/gallery/) | 参考架构图/流程图表达；具体语法以 Graphviz DOT 官方文档为准 |
| VuePress | [liyupi/ai-guide](https://github.com/liyupi/ai-guide)（约 21.3k）、[VuePress 官方文档](https://vuepress.vuejs.org/guide/getting-started.html) | 高星项目是 VuePress 站点案例，不是 VuePress skill；本仓库采用官方 VuePress 配置规则 |
| Agent Skills 目录 | [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills)（约 77.4k）、[VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills)（约 35.9k） | 用于发现候选 skill 和比较结构；不把通用 skill 直接当成 310B 适用 |
| CANNBot | [cannbot-skills](https://gitcode.com/cann/cannbot-skills)（页面约 2.2k stars） | 作为通用 CANN 参考；310B 规则已由本仓库重新验证和改写 |

没有发现高星的“VuePress + Ascend 310B 专用 skill”或“Graphviz + Ascend 310B 专用 skill”。因此这 4 个 skill 采用**官方规范 + 本仓库规则 + 适配版能力**，不是复制某个高星项目。

### 两个 awesome 仓库的适配筛选

这些是候选来源，不会自动安装到本仓库。标记“适合”的原因是对应到了本仓库的真实任务，而不是因为 star 高。

| 仓库 | 适合本仓库的能力 | 对应本仓库 |
| --- | --- | --- |
| [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) | [`obra/systematic-debugging`](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md)、[`root-cause-tracing`](https://github.com/obra/superpowers/blob/main/skills/root-cause-tracing/SKILL.md)、[`test-driven-development`](https://github.com/obra/superpowers/blob/main/skills/test-driven-development/SKILL.md) | 先根因、可复现、失败测试；补充 `ascend310b-runtime-debug` / `crash-debug` |
| VoltAgent | [`GarethManning/learning-target-authoring-guide`](https://github.com/GarethManning/education-agent-skills/tree/main/skills/original-frameworks/learning-target-authoring-guide)、[`assessment-validity-checker`](https://github.com/GarethManning/education-agent-skills/tree/main/skills/curriculum-assessment/assessment-validity-checker)、[`competency-unpacker`](https://github.com/GarethManning/education-agent-skills/tree/main/skills/curriculum-assessment/competency-unpacker) | 教材学习目标、能力拆解、评估有效性；写入教材计划时使用 |
| VoltAgent | [`openai/jupyter-notebook`](https://officialskills.sh/openai/skills/jupyter-notebook)、[`anthropics/doc-coauthoring`](https://officialskills.sh/anthropics/skills/doc-coauthoring) | 可复现实验、教程和共同编辑流程 |
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) | [`webapp-testing`](https://github.com/ComposioHQ/awesome-claude-skills/tree/master/webapp-testing)、[`content-research-writer`](https://github.com/ComposioHQ/awesome-claude-skills/tree/master/content-research-writer)、[`document-skills`](https://github.com/ComposioHQ/awesome-claude-skills/tree/master/document-skills) | UI 回归、带来源的内容研究、DOCX/PDF/PPTX 文档输出 |
| Composio | [`skill-creator`](https://github.com/ComposioHQ/awesome-claude-skills/tree/master/skill-creator)、[`developer-growth-analysis`](https://github.com/ComposioHQ/awesome-claude-skills/tree/master/developer-growth-analysis) | skill 结构维护、开发复盘；不直接作为 310B 调试图 |

教材计划优先吸收“学习目标/评估/可复现材料”这三类能力，而不是把论文生成器直接套进教程。程序调试优先吸收“根因、失败复现、回归测试”，而不是复制无关云平台或 Web 框架规则。

## 通用安装步骤

1. 进入对应插件目录，先跑该目录 README 里列出的离线测试。
2. 在仓库根目录运行 `node scripts/install-dsh-plugins.mjs --profile <profile>`；安装器会打包 tarball、调用 `dsh plugin add`，并校验版本和文件内容。
3. 在该 profile 的 `cordis.patch.yml` 里写本机配置。板卡地址、仓库路径和 SSH 用户只放在 profile，不写进插件源码。
4. 如果插件带有 `skills/`，在仓库根目录运行 `npm run skills:sync` 和 `npm run skills:check`；从仓库根目录启动 DSH 会读取已提交的 `.dsh/skills/` 项目级副本。
5. 重启 DSH，确认侧栏入口和工具出现。

桌面版 DSH 的 profile 由应用托管，不能在 shell 里直接启动；安装器完成 tarball/profile 变更后必须重启桌面应用。

## 边界

- `dsh-plugin-ascend310b-debug` 只适用于**昇腾 310B1 / 310B4**。探测到其他芯片时，插件会标记为不适用，不返回 310B 调试程序。
- 本机不是 Ascend 设备：CANN、ATC、PyACL、OM、`npu-smi`、DVPP 和摄像头检查必须到真实开发板执行。
- 插件源码不包含实际部署的板卡 IP、个人 SSH 凭据、本机检出路径或用户主目录；测试中的 `192.0.2.x`、`198.51.100.x` 和标准户名 `HwHiAiUser` 都是保留值。相关测试在各自的 `test/portability.mjs` 中。
