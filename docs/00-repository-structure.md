# 仓库目录与文档职责

本文是仓库目录结构的唯一规范。它解决“应该从哪个 README 开始”和“某类内容应该放在哪里”两个问题。

## 1. 入口职责

仓库只保留以下几类入口，每个入口只有一个职责：

| 入口 | 面向对象 | 负责内容 | 不负责内容 |
| --- | --- | --- | --- |
| [`README.md`](../README.md) | 第一次访问仓库的人 | 项目定位、三分钟起步、顶层目录、入口链接 | 案例细节、历史报告、完整理论 |
| [`docs/README.md`](README.md) | 维护者和排障者 | 工程文档总索引、按任务查找、证据边界 | 教材正文、可执行命令的完整实现 |
| [`src/README.md`](../src/README.md) | 教材读者 | VuePress 站点首页和阅读入口 | 板端部署手册 |
| [`src/book/README.md`](../src/book/README.md) | 理论学习者 | 理论章节目录和学习路线 | 案例运行状态 |
| [`src/experiment/README.md`](../src/experiment/README.md) | 实践学习者 | Case 1 至 Case 9 的教程目录 | 工程运行细节和历史证据 |
| [`samples/README.md`](../samples/README.md) | 操作者和开发者 | 可运行代码目录和案例入口 | 全仓库文档审查 |
| `samples/caseN/README.md` | 单个案例操作者 | 该案例的最短启动、部署和排障路径 | 长篇理论和历史报告 |
| `samples/caseN/docs/README.md` | 单个案例维护者 | 该案例工程文档地图 | 教材正文 |

根 README 链接到 `docs/README.md` 是有意的：前者回答“项目是什么”，后者回答“我需要查哪份工程文档”。两者不是两个相互竞争的项目首页。

## 2. 顶层目录

```text
Ascend310/
├── README.md                 # 项目首页、快速起步和入口地图
├── docs/                     # 仓库级工程文档、结构规范和案例审查
│   ├── README.md             # 工程文档总索引
│   ├── 00-repository-structure.md
│   └── case-reviews/         # 跨案例审查，不是运行手册
├── src/                      # VuePress/Pandoc 书稿源文件
│   ├── book/                 # 理论章节
│   ├── experiment/           # 实践案例教程
│   ├── appendix/             # 附录
│   └── .vuepress/            # 站点配置和公开静态资源
├── samples/                  # 与书稿配套的可运行代码
│   ├── README.md             # 案例代码总览
│   ├── case-index.json       # 案例机器可读注册表
│   ├── CASE_LAYOUT.md        # 案例目录契约
│   ├── case1/ ... case9/     # 稳定案例路径
│   └── chapter2/ ... chapter8/ # 章节级代码示例
├── scripts/                  # 仓库级构建、发布和文档工具
├── package.json              # VuePress、幻灯片和 PDF 的本地工具链
├── latex/                    # 生成的 LaTeX/PDF 输出，禁止手工修改
└── .github/                  # CI、Issue 和仓库自动化配置
```

## 3. 案例目录契约

每个 `samples/caseN/` 的稳定入口不改名；机器使用 `caseN`，读者通过 `case-index.json` 查看语义名称。新案例和逐案迁移后的案例使用以下角色：

```text
caseN/
├── README.md                 # 最短运行手册
├── app.py 或语义包/          # 运行时代码
├── scripts/                  # 准备、部署、诊断和板端命令
├── tests/                    # 单元和合同测试
├── docs/                     # 编号工程文档和验收证据
├── frontend/ 或 web/         # 前端源码；以现有运行入口为准
├── models/                   # 模型配置；二进制默认忽略
├── data/                     # 数据和运行时状态；默认忽略
├── reports/                  # 实验报告；默认忽略
└── third_party/              # 必要的第三方源码
```

`doc/`、`webui/`、多个历史编号段等现有目录暂时保留兼容，不在一次重命名中强行移动。新增文档统一使用 `docs/`，案例 README 必须明确指出历史目录的边界。

## 4. 内容放置规则

- 原理、模型结构、方法论和教材叙述只写入 `src/`。
- 当前命令、部署参数、API、故障处理和验收证据写入对应案例的 `docs/`。
- 只需要让操作者跑起来的内容写入案例 `README.md`，并链接到专题文档，不复制长篇内容。
- 跨案例的审查、结构规范和文档治理写入根 `docs/`。
- CANN、ATC、ACL、照片、数据库、FAISS、OM、日志和个人数据属于运行时资产，不写入书稿源文件。
- `latex/`、`src/.vuepress/dist/` 和 VuePress 缓存属于生成物；修改必须回到 `src/` 或脚本源文件。

## 5. 迁移顺序

目录治理按以下顺序进行，避免破坏已有板端命令：

1. 先补齐入口 README 和案例索引，建立链接闭环。
2. 再逐个案例把运行命令、工程文档和历史证据分开；保留旧路径兼容链接。
3. 最后才考虑物理移动目录。移动前必须更新导入、脚本、VuePress、PDF 转换和 CI 引用，并通过对应测试。

当前 `case1` 已完成第一轮布局迁移；`case2` 至 `case9` 仍按稳定路径运行，迁移状态以 [`samples/case-index.json`](../samples/case-index.json) 为准。

## 6. 维护检查

提交前至少检查：

```bash
rg -n "README|docs/|samples/|src/|latex/" README.md docs samples src --glob "*.md"
git diff --check
pnpm docs:build
```

发现入口职责冲突时，优先修改本规范和对应索引，不在多个 README 中复制同一段说明。
