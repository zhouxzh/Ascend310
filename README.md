# 昇腾310B实战——从入门到精通边缘计算与人工智能

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![VuePress](https://img.shields.io/badge/VuePress-2.0-3aab95)](https://vuejs.press/)
[![pnpm](https://img.shields.io/badge/pnpm-10-ff6b35)](https://pnpm.io/)

> 基于昇腾310B的边缘计算与人工智能推理部署开源教材，理论与实践结合，项目驱动教学。

**作者：** 周贤中 | **邮箱：** zhouxzh@gdut.edu.cn

---

## 关于本书

本书跳出单纯的理论讲解，通过真实的AI推理部署案例，系统化讲解昇腾310B的硬件架构、Atlas工具链使用逻辑与端侧AI项目开发流程。从模型适配、量化优化到推理服务部署，每个知识点都配套可落地的代码示例，帮助读者实现从"了解芯片"到"能用芯片落地项目"的跨越。

### 目标读者

- **高校学生 / 科研新人** — 系统化路径快速理解边缘AI硬件与部署流程
- **嵌入式 / IoT 工程师** — 将AI模型真正跑在边缘端并做性能调优
- **AI应用开发者 / 创客** — 将训练好的模型迁移到昇腾310B进行高效推理与产品化落地

### 全书结构

| 部分 | 内容 | 说明 |
| :-- | :-- | :-- |
| **理论教程** | Chapter 1~9 | 从边缘计算基础到项目交付方法论 |
| **实践案例** | Case 1~9 | 九个端侧 AI 项目案例，从人脸识别到聊天机器人 |
| **附录** | 附录 1~3（持续扩展） | 开发板与基础环境、昇腾 310B Linux 命令教程，以及工具、FAQ 和参数模板 |

实践案例保留 `case1` 至 `case9` 的稳定路径，并在 [案例索引](samples/case-index.json) 中提供面向读者的功能关键词。

---

## 目录结构

这里只列出主要源码、样例代码和关键构建产物；`node_modules`、VuePress 缓存、LaTeX 中间文件和编辑器临时文件不作为项目结构说明。

```
Ascend310/
├── README.md                    # 项目总说明
├── LICENSE
├── CLAUDE.md                    # 协作与代理说明
├── package.json                 # VuePress / pnpm 脚本与依赖
├── pnpm-lock.yaml
├── tsconfig.json
├── convert-vuepress.sh          # Markdown -> LaTeX/PDF 转换脚本
├── deploy.sh                    # VuePress 构建与 GitHub Pages 部署脚本
├── src/                         # VuePress 文档源码
│   ├── README.md                # 站点首页
│   ├── portfolio.md             # 项目展示页
│   ├── book/                    # 理论教程 Markdown
│   │   ├── README.md            # 前言 / 理论教程首页
│   │   ├── chapter1.md ... chapter9.md
│   │   ├── ssd_optimize.md
│   │   └── img2/ img3/ img4/ img5/
│   ├── experiment/              # 实践案例 Markdown
│   │   ├── README.md
│   │   ├── case1.md ... case9.md
│   │   └── img1/ img2/ img3/
│   ├── appendix/                # 附录 Markdown
│   │   ├── README.md
│   │   ├── appendix1.md appendix2.md appendix3.md
│   │   └── img1/
│   └── .vuepress/               # VuePress 配置、主题、样式与 public 资源
├── samples/                     # 教程与实践案例配套源码
│   ├── case1/ ... case9/        # 实践案例源码（Case 1 为 face-attendance）
│   ├── chapter2/                # ResNet 快速入门示例
│   ├── chapter3/                # PyTorch / torch_npu 迁移与训练示例
│   ├── chapter4/                # PyACL 模型推理示例
│   ├── chapter5/                # DVPP / VENC / VDEC / VPC / JPEG / WebRTC 示例
│   ├── chapter6/                # 自定义算子开发示例
│   ├── chapter7/                # 性能分析与优化示例
│   └── chapter8/                # 模型量化与精度性能对比示例
├── latex/                       # LaTeX 模板、生成结果与转换辅助文件
│   ├── book.tex                 # 正式 PDF 主控文件
│   ├── book.pdf                 # 生成的教材 PDF
│   ├── chapters/                # Pandoc 生成的理论章节 tex 与图片
│   ├── cases/                   # Pandoc 生成的实验章节 tex 与图片
│   ├── appendices/              # Pandoc 生成的附录 tex、包含清单与图片
│   ├── remove-numbering.lua
│   └── replace_block.py
└── notebook/                    # Jupyter Notebook 与导出辅助脚本
    ├── reinforcement_learning.ipynb
    └── ipynb2tex.sh
```

---

## 本地开发

### 环境要求

- [Node.js](https://nodejs.org/) >= 18
- [pnpm](https://pnpm.io/) >= 10
- Bash：用于运行 `convert-vuepress.sh`，Windows 建议使用 WSL
- [Pandoc](https://pandoc.org/)：用于 Markdown 转 LaTeX
- TeX Live / MiKTeX：需包含 XeLaTeX、latexmk、KOMA-Script、xeCJK 等常用宏包
- Noto 字体族：`Noto Serif CJK SC`、`Noto Sans CJK SC`、`Noto Sans Mono CJK SC`
- Graphviz：用于将 DOT 图转换为 PNG

### 安装与运行

```bash
# 安装依赖
pnpm install

# 启动开发服务器
pnpm docs:dev

# 构建静态站点
pnpm docs:build

# 导出网页 PDF（基于 VuePress 页面，不是正式教材 PDF）
pnpm export-pdf
```

开发服务器默认运行在 `http://localhost:8080`。

### 生成 LaTeX / PDF

如需生成纸质版或离线 PDF，可使用提供的转换脚本：

```bash
./convert-vuepress.sh
```

脚本会读取 `src/book`、`src/experiment` 与 `src/appendix` 下的 Markdown，生成 `latex/chapters/*.tex`、`latex/cases/*.tex` 和 `latex/appendices/*.tex`，并通过 `latex/book.tex` 编译出 `latex/book.pdf`。这是正式教材 PDF 的生成入口，详细说明见 [latex/README.md](latex/README.md)。

---

## 双格式写作与转换规范

本项目的正式书稿需要同时满足两条输出链路：

- VuePress：`src` 目录转换为 GitHub Pages 网页。
- Pandoc + XeLaTeX：Markdown 转换为 LaTeX，再生成正式 PDF。

因此，`src/book`、`src/experiment` 和 `src/appendix` 中的正文 Markdown 必须使用两边都稳定支持的语法。不要依赖只在浏览器中生效的 Vue 组件、HTML 片段或 Mermaid 渲染结果作为正式书稿内容。

### 标题层级

- `src/book/chapter*.md` 不使用一级标题 `#`。这些章节已经在 `latex/book.tex` 中通过 `\chapter{}` 统一组织，正文最高层级从 `##` 开始。
- `src/experiment/case*.md` 与 `src/appendix/appendix*.md` 使用一级标题作为案例或附录标题；附录文件按 `appendix1.md`、`appendix2.md`、`appendix3.md` 的形式连续编号。
- 正文标题只使用 `##`、`###`、`####`。不要使用 `#####` 或更深层级。
- 五级标题在 LaTeX 中容易转换为 run-in 形式的 `\paragraph`，如果后面紧跟代码块、图片或表格，会出现标题贴着代码框上边线、断句异常等问题。
- 更细的小节标注使用加粗文字，例如 `**(1) 测试参数**`，不要继续增加 Markdown 标题层级。
- 需要交叉引用的标题使用显式锚点，例如 `## VENC — 硬件视频编码 {#ch5-venc}`。锚点必须全书唯一，避免 LaTeX duplicate label 警告。

### 图片与流程图

- 正式书稿优先插入静态图片，不在 `src/book/chapter*.md` 中保留 Mermaid 作为最终图示。
- 流程图、结构图统一使用 DOT 源文件生成 PNG。每章图片放在对应目录，例如 Chapter 5 使用 `src/book/img5/`。
- DOT 源文件与 PNG 成品都应保留在同一图片目录，便于后续维护。
- PNG 图用于网页和 PDF 两端，生成时按 300 dpi 要求处理：

```bash
dot -Gdpi=300 -Tpng src/book/img5/example.dot -o src/book/img5/example.png
```

- 图片文件名使用英文小写、数字、下划线或短横线，避免空格和中文文件名。
- Markdown 插图采用统一写法：

```markdown
![VENC 编码端到端流程](img5/venc_encode_flow.png){#fig:venc_encode_flow width=100% .center}
```

- `#fig:` 标签必须全书唯一；`width` 使用百分比，避免使用 HTML `<img>`。

### Markdown 语法

- Pandoc 输入格式由 `convert-vuepress.sh` 固定为：

```text
markdown+yaml_metadata_block+tex_math_dollars+pipe_tables+header_attributes+link_attributes
```

- 表格使用普通 Markdown pipe table。避免合并单元格、复杂嵌套列表、大段 `<br>`、HTML 表格或过宽列。
- 过宽表格会导致 PDF 中出现 overfull。正式教材中应优先拆成多个小表，或改写为列表。
- 代码块必须使用 fenced code block，并标注正确语言名，例如 `python`、`bash`、`cpp`、`json`、`text`。不要写错为 `pyhon` 等无效 highlighter 名称。
- 长代码行、长 URL、长文件路径要主动换行，避免 PDF 右侧溢出。
- 数学公式使用 `$...$` 或 `$$...$$`。不要依赖只在 KaTeX/VuePress 中可用的自定义 HTML 或浏览器渲染。
- 正文链接必须使用相对路径，并以 `pnpm docs:build` 不出现 broken links 为准。

### 文字标注

- 正式学术教材正文避免使用 emoji 作为提示符号或状态标记。
- 不建议直接在正文中使用 `→`、`≥`、`≤`、`✓`、`✗` 等特殊符号；PDF 字体可能缺字。优先使用中文表述，或在数学环境中写成 `$\rightarrow$`、`$\ge$`、`$\le$`。
- “正确 / 错误 / 注意 / 说明 / 小结”等标注使用文字表达，不用图标替代。
- 章节中的图题、表题、术语、锚点命名应保持正式、稳定、可引用。

### LaTeX/PDF 版式

- 项目专用 PDF 版式集中维护在 `latex/book.tex`，不要修改外部 KOMA-Script 模板。
- 当前 PDF 使用 A4、双面排版、`BCOR=6mm` 装订修正、`DIV=11` 版心设置，适合作为正式教材初稿。
- `latex/chapters/*.tex`、`latex/cases/*.tex` 和 `latex/appendices/*.tex` 是转换生成文件。正文修改应优先改 `src/book/*.md`、`src/experiment/*.md` 与 `src/appendix/*.md`，再运行转换脚本重新生成。

### 提交前检查

每次修改正式书稿后，至少执行：

```bash
pnpm docs:build
./convert-vuepress.sh
```

检查要求：

- VuePress 构建不应出现 broken links。
- LaTeX 不应出现 fatal error。
- duplicate label、missing character、overfull hbox 等警告需要逐项判断；正式发布前应清理到可接受范围。

---

## 阅读路线推荐

| 读者类型 | 推荐路径 | 目标 |
| :-- | :-- | :-- |
| 零基础学生 | 附录 1 → 附录 2 → Ch1 → Ch2 → Case 1 | 跑通首个模型 |
| 嵌入式工程师 | Ch4 → Ch5 → Ch6 | 掌握底层开发与优化 |
| AI应用开发者 | Ch2 → Ch3 → 选读案例 | 快速场景落地 |
| 技术负责人 | Ch1 → Ch7 → Ch8 → Ch9 | 构建量化评估、性能验收与交付方法论 |

---

## 当前状态

**v0.1** — 初稿与转换流程持续审校中。

- [x] 理论教程 Chapter 1~9 Markdown 初稿
- [x] 实践案例 Case 1~9 Markdown 初稿
- [x] 附录 1~3 Markdown 初稿
- [x] VuePress 站点构建与 GitHub Pages 部署脚本
- [x] Pandoc + XeLaTeX 正式 PDF 生成流程
- [x] Chapter 5 图示改为 DOT 源文件 + PNG 静态图片
- [ ] 全书学术化文字审校、术语统一与交叉引用校对
- [ ] 清理 duplicate label、missing character、overfull hbox 等 LaTeX 警告

---

## 开源协作

欢迎通过 Issue / PR 参与共建：

- 增补新模型 / 新任务的部署范式
- 分享自定义算子优化经验
- 提交性能测试报告（含硬件信息 + 指标）
- 文档校对与翻译

---

## 许可证

本书内容采用 [Apache 2.0](LICENSE) 许可证。

引用本书请注明：

> 《昇腾310B实战：从入门到精通边缘计算与人工智能》（GitHub: zhouxzh/Ascend310）
