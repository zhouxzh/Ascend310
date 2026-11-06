# 仓库目录与文档职责

本文规定仓库的目录归属和文档落位规则。`AGENTS.md` 是代理执行规范的入口；涉及昇腾 310B 工程实现时，还必须同时遵守 [开发标准](../../tools/dsh-plugin-ascend310b-debug/docs/ascend310b-development-standard.md)。

## 入口职责

| 入口 | 面向对象 | 负责内容 | 不负责内容 |
| --- | --- | --- | --- |
| [`README.md`](../../README.md) | 第一次访问仓库的人 | 项目定位、快速起步和顶层入口 | 案例内部实现、历史实验记录 |
| [`docs/README.md`](../README.md) | 维护者和发布者 | 仓库治理、发布流程、环境矩阵和审计索引 | 教材正文、案例操作步骤 |
| [`src/README.md`](../../src/README.md) | 教材读者 | VuePress 站点首页和阅读入口 | 板端部署手册 |
| [`src/book/README.md`](../../src/book/README.md) | 理论学习者 | 理论章节目录和学习路线 | 案例运行状态 |
| [`src/experiment/README.md`](../../src/experiment/README.md) | 实践学习者 | 案例 1 至案例 9 的教程目录 | 工程运行细节和历史证据 |
| [`samples/README.md`](../../samples/README.md) | 操作者和开发者 | 可运行代码、案例入口和工程文档导航 | 全仓库发布治理 |
| `samples/caseN/README.md` | 单个案例操作者 | 最短启动、部署和排障路径 | 长篇理论 |
| `samples/caseN/docs/README.md` | 单个案例维护者 | 案例工程文档索引 | 教材正文 |

根 README 回答“项目是什么、怎样开始”；`docs/README.md` 只回答“仓库如何维护和发布”。

## 顶层目录

```text
Ascend310/
├── README.md                 # 项目首页、快速起步和入口地图
├── docs/                     # 仓库治理、发布流程、环境矩阵和跨项目审计
│   ├── README.md
│   ├── governance/
│   ├── release/
│   └── audits/
├── src/                      # VuePress/Pandoc 教程源文件
│   ├── book/                 # 理论章节
│   ├── experiment/           # 实践案例教程
│   ├── appendix/             # 附录
│   ├── presentation/         # Marp 课件源
│   └── .vuepress/            # 站点配置和静态资源
├── samples/                  # 可运行代码和案例工程文档
├── tools/                    # DSH 插件源码、文档和数据
├── scripts/                  # 教材课件、网页和证据处理脚本
├── dependencies/             # Python 环境锁
├── tmp/                      # 仓库唯一临时工作区；README 跟踪，其余内容忽略
├── latex/                    # 生成的 LaTeX/PDF 输出
└── .github/                  # CI 和仓库自动化配置
```

## 内容放置规则

- 原理、案例教程和附录只写入 `src/`。
- 可运行代码、测试、案例运行命令和案例工程文档只写入 `samples/caseN/`。
- 插件实现、插件文档和机器可读审计数据只写入 `tools/`。
- 根 `docs/` 只保存仓库治理、发布流程、环境矩阵和跨项目审计摘要。
- 根 `docs/` 不保存原始日志、截图、数据集、模型、案例命令或案例排障说明。
- `latex/`、`src/.vuepress/dist/` 和课件 HTML 属于生成物；修改时回到源文件。
- DSH skill 的源文件放在插件 `skills/`，项目级副本放在 `.dsh/skills/`；修改源文件后运行 `npm run skills:sync`。

## 案例目录契约

```text
samples/caseN/
├── README.md                 # 最短运行手册
├── app.py 或语义包/          # 运行时代码
├── scripts/                  # 准备、部署、诊断和板端命令
├── tests/                    # 单元和合同测试
├── docs/                     # 编号工程文档、审查和验收记录
├── frontend/ 或 web/         # 前端源码
├── models/                   # 模型配置；二进制默认忽略
├── data/                     # 数据和运行时状态；默认忽略
├── reports/                  # 实验报告；默认忽略
└── third_party/              # 必要的第三方源码
```

案例工程文档统一使用 `docs/`。新增文档必须使用数字前缀，并登记到该案例的 `docs/README.md`。Case 4 的独立教程 `src/experiment/case4.md` 必须自包含，不得依赖案例工程文档。

## 根 docs 契约

```text
docs/
├── README.md
├── governance/
│   ├── repository-structure.md
│   └── documentation-ownership.md
├── release/
│   ├── release-checklist.md
│   └── environment-matrix.md
└── audits/
    └── YYYYMMDD-<scope>.md
```

- `governance/` 保存稳定的仓库与文档归属规则。
- `release/` 保存发布流程和环境兼容状态。
- `audits/` 只保存带日期的跨项目审计摘要，不保存原始证据。
- 不新增其他根级 Markdown 文件或主题目录，除非先修改 `AGENTS.md` 和本文。

## 维护检查

提交前至少运行：

```bash
pnpm skills:check
pnpm plugins:check
pnpm docs:slides:check
pnpm docs:build
git diff --check
```

发现入口职责冲突时，先修改本规范和对应索引，不在多个 README 中复制同一段说明。
