# 昇腾 310B 实战教材与案例仓库

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![VuePress](https://img.shields.io/badge/VuePress-2.0-3aab95)](https://vuejs.press/)
[![pnpm](https://img.shields.io/badge/pnpm-10-ff6b35)](https://pnpm.io/)

这是一本以昇腾 310B 为目标平台的边缘 AI 教材，同时包含可在真实开发板上运行的章节示例和完整案例。书稿源码、运行代码、工程文档和生成输出分开维护。

作者：周贤中（zhouxzh@gdut.edu.cn）

## 先回答“我该看哪里”

| 目标 | 入口 |
| --- | --- |
| 第一次了解仓库 | 本页 |
| 查找工程文档、部署记录和审查结论 | [`docs/README.md`](docs/README.md) |
| 阅读完整理论教程 | [`src/book/README.md`](src/book/README.md) |
| 阅读实践案例教程 | [`src/experiment/README.md`](src/experiment/README.md) |
| 运行某个案例 | [`samples/README.md`](samples/README.md)，然后进入对应 `samples/caseN/README.md` |
| 运行案例 7：智能相册 | [`samples/case7/README.md`](samples/case7/README.md) |
| 理解本仓库为什么这样分目录 | [`docs/00-repository-structure.md`](docs/00-repository-structure.md) |

根 README 是项目首页和快速起步页；它不替代 `docs/README.md`。根 README 说明“项目是什么、怎样开始”，`docs/README.md` 说明“具体问题应该查哪份工程文档”。完整职责表以 [仓库目录与文档职责](docs/00-repository-structure.md) 为准。

## 仓库结构

```text
Ascend310/
├── README.md                 # 项目首页和快速起步
├── docs/                     # 工程文档总索引、结构规范、跨案例审查
├── src/                      # VuePress/Pandoc 书稿源文件
│   ├── book/                 # 理论教程
│   ├── experiment/           # 实践案例教程
│   └── appendix/             # 附录
├── samples/                  # 章节示例和 case1-case9 可运行代码
├── scripts/                  # 仓库级构建和发布工具
├── latex/                    # 生成的 LaTeX/PDF，不手工编辑
└── .github/                  # CI 和仓库自动化
```

`caseN` 是稳定机器路径，案例的中文名称、关键词和教程路径登记在 [`samples/case-index.json`](samples/case-index.json)。目录的详细契约见 [`samples/CASE_LAYOUT.md`](samples/CASE_LAYOUT.md)。

## 本地书稿开发

本地电脑只负责 VuePress、Markdown、前端和纯 Python 代码检查；CANN、ATC、ACL、OM、`npu-smi` 和 NPU 推理必须在真实昇腾 310B 开发板执行。

环境要求：Node.js 18+、pnpm 10+。安装依赖并启动站点：

```bash
pnpm install
pnpm docs:dev
```

发布前构建：

```bash
pnpm docs:build
git diff --check
```

正式电子书的 Markdown 到 LaTeX/PDF 转换入口是 `convert-vuepress.sh`；生成文件位于 `latex/`，不要直接编辑生成的 `.tex` 或 PDF。

## 案例运行原则

进入具体案例后，始终先阅读该案例的 `README.md`。它只给出当前默认环境、启动命令、部署边界和最短排障路径。需要 API、模型、硬件验收或历史记录时，再从该案例的 `docs/README.md` 进入专题文档。

- 模型、数据集、个人照片、数据库、FAISS、OM 和运行报告默认不提交版本库。
- 文档中的板卡、CANN、驱动、固件、IP 和模型 hash 必须与同一份证据关联。
- “通过”必须区分语法检查、转换、ACL 数值、任务质量、性能、UI 和硬件验收，不能用一种证据替代另一种。
- 仓库服务和案例默认面向局域网；不要把板端管理接口直接暴露到公网。

## 许可证

教材内容和仓库代码采用 [Apache 2.0](LICENSE) 许可证；第三方模型、数据集和固件遵循各自许可证。
