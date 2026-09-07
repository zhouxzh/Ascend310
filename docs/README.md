# 工程文档总索引

_这是工程文档入口，不是项目首页，也不是教材正文。项目定位和本地起步请看根 [`README.md`](../README.md)；目录职责请看 [`00-repository-structure.md`](00-repository-structure.md)。_

---

## 📍 从哪里开始

| 你的目标 | 先读这里 | 说明 |
| --- | --- | --- |
| 运行一个案例 | 对应 `samples/caseN/README.md` | 依赖、启动命令、板端边界和常见故障 |
| 学习理论 | [`src/book/README.md`](../src/book/README.md) | 全书章节和推荐学习路线 |
| 阅读实践教程 | [`src/experiment/README.md`](../src/experiment/README.md) | Case 1 到 Case 9 的理论源稿 |
| 查板端命令 | [`src/appendix/appendix2.md`](../src/appendix/appendix2.md) | Linux、CANN、服务和证据边界 |
| 查验收审查 | [`case-reviews/00-index.md`](case-reviews/00-index.md) | 各案例的风险和未解决问题，不是运行手册 |
| 查看本次文档整理记录 | [`documentation-audit-20260907.md`](documentation-audit-20260907.md) | 本轮发现、取舍和未解决事项 |
| 查看仓库目录规范 | [`00-repository-structure.md`](00-repository-structure.md) | README、源码、案例和生成物的职责边界 |
| 查 Case7 | [`samples/case7/docs/README.md`](../samples/case7/docs/README.md) | 智能相册的按任务文档地图 |

## 🗂️ 文档分层

| 层级 | 唯一路径 | 应该放什么 |
| --- | --- | --- |
| 教材理论 | `src/book/`、`src/experiment/`、`src/appendix/` | 原理、方法、教程正文；是书稿源文件 |
| 案例运行 | `samples/caseN/README.md` | 可执行的最短路径和当前默认参数 |
| 案例工程 | `samples/caseN/docs/` 或历史兼容的 `doc/` | API、部署、模型、故障和验收证据 |
| 全仓库审查 | `docs/case-reviews/` | 以审查日期为基线的风险清单和修复建议 |
| 生成输出 | `latex/`、`src/.vuepress/dist/` | 构建结果；禁止手工修改，需回到 `src/` 改源稿 |

`samples/CASE_LAYOUT.md` 规定了案例目录的稳定路径。已有案例中的 `doc/`、多个编号段或历史归档不在本轮强制重命名；导航会明确它们的作用和边界。

## 🧭 按任务查找

| 任务 | 入口 |
| --- | --- |
| 第一次准备开发板 | [`src/appendix/appendix1.md`](../src/appendix/appendix1.md) |
| CANN、ATC、PyACL 基础 | [`src/book/chapter2.md`](../src/book/chapter2.md)、[`src/book/chapter4.md`](../src/book/chapter4.md) |
| 310B Linux 命令 | [`src/appendix/appendix2.md`](../src/appendix/appendix2.md) |
| 选择一个实践案例 | [`src/experiment/README.md`](../src/experiment/README.md) 和对应 `samples/caseN/README.md` |
| 检查某案例是否适合发布 | [`docs/case-reviews/00-index.md`](case-reviews/00-index.md) |
| 修改教材正文 | 修改 `src/` 下源稿，然后运行 `pnpm docs:build`；不要改 `latex/` 或 `.vuepress/dist/` |
| 生成电子书图示 | 按根 [`README.md`](../README.md) 的 DOT/PNG 和转换规则执行 |

## 🧩 全部实践案例入口

案例 ID 是稳定路径，中文名称和关键词来自 [`samples/case-index.json`](../samples/case-index.json)。每个案例先读源码教程，再读样例 README；工程专题目录名称以实际目录为准。

| 案例 | 理论源稿 | 可运行入口 | 工程文档位置 |
| --- | --- | --- | --- |
| Case 1 人脸考勤 | [`case1.md`](../src/experiment/case1.md) | [`samples/case1/README.md`](../samples/case1/README.md) | `samples/case1/docs/` |
| Case 2 目标跟踪 | [`case2.md`](../src/experiment/case2.md) | [`samples/case2/README.md`](../samples/case2/README.md) | README 与代码内说明 |
| Case 3 智能电子琴 | [`case3.md`](../src/experiment/case3.md) | [`samples/case3/README.md`](../samples/case3/README.md) | `samples/case3/doc/`（历史兼容目录） |
| Case 4 掌纹识别 | [`case4.md`](../src/experiment/case4.md) | [`samples/case4/README.md`](../samples/case4/README.md) | `samples/case4/docs/` |
| Case 5 数据采集 | [`case5.md`](../src/experiment/case5.md) | [`samples/case5/README.md`](../samples/case5/README.md) | `samples/case5/docs/` |
| Case 6 智能小车 | [`case6.md`](../src/experiment/case6.md) | [`samples/case6/README.md`](../samples/case6/README.md) | README 与代码内说明 |
| Case 7 智能相册 | [`case7.md`](../src/experiment/case7.md) | [`samples/case7/README.md`](../samples/case7/README.md) | [`samples/case7/docs/README.md`](../samples/case7/docs/README.md) |
| Case 8 手势识别 | [`case8.md`](../src/experiment/case8.md) | [`samples/case8/README.md`](../samples/case8/README.md) | README 与代码内说明 |
| Case 9 RAG 网关 | [`case9.md`](../src/experiment/case9.md) | [`samples/case9/README.md`](../samples/case9/README.md) | `samples/case9/docs/00-case9-current-runbook.md`；其余编号文档按索引和归档说明阅读 |

### Case7 智能相册

Case7 的入口已经按“运行、接口、设备、模型、证据、理论”分层，具体顺序见 [`samples/case7/docs/README.md`](../samples/case7/docs/README.md)。不要从历史主动推送报告或跨板报告开始；先读 [`samples/case7/README.md`](../samples/case7/README.md) 完成服务启动和照片上传。

## 🔍 阅读和搜索规则

1. `README.md` 是一个目录或案例的第一入口；编号文档是该入口下的专题，不要求按文件名顺序通读。
2. 标题含“历史”“对照”“报告”“审查”的文档只提供证据和背景，不自动改变当前运行配置。
3. “当前”只表示文档开头声明的板卡、IP、软件版本和日期；换板或换版本后，必须重新获取板端证据。
4. 没有报告、日志、模型 hash 或板端输出支撑的数字，按“待验证”理解，不当作已验收结论。
5. 在仓库根目录搜索内容：

   ```bash
   rg -n "关键词" README.md docs src samples --glob "*.md" --glob "!src/.vuepress/**"
   ```

## 🏷️ 证据状态

| 标记 | 含义 |
| --- | --- |
| `documented` | 外部文档或代码已明确说明 |
| `inferred` | 根据现有资料推导，尚未完成目标硬件验证 |
| `observed-pass` | 在指定硬件、版本、输入和命令下实测通过 |
| `observed-fail` | 在指定条件下实测失败，结论只对该组合负责 |
| `untested` | 尚未执行，不能据此判断支持或不支持 |

详细的证据边界和全案例问题列表见 [`docs/case-reviews/00-index.md`](case-reviews/00-index.md)。

## 🧹 文档维护约定

- 教材内容只在 `src/` 维护；`latex/` 和 `src/.vuepress/dist/` 是生成物。
- 案例 README 只保留启动、部署、用户操作和最短排障路径；长篇理论放入对应 `src/experiment/caseN.md`。
- 工程文档使用两位数字前缀和描述性文件名；历史证据保留原文件，不与当前命令混写。
- 文档中的板端 IP、端口、模型 ID、版本和 hash 必须与同一份代码或报告相互引用；发生变化时更新索引和当前入口。
- 不把模型、照片、数据库、FAISS、OM、日志或私人图片复制进教材源文件。
