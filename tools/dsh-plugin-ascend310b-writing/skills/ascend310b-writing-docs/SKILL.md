---
name: ascend310b-writing-docs
description: Ascend310 代码工程文档与测试问题台账写作。用于 samples/caseN 的 README、docs/ 下的部署/API/配置/验证/限制文档、统一 NN-troubleshooting.md 问题记录、代码入口与测试命令说明、证据记录和跨案例工程索引；测试设备/服务/模型出问题后必须使用本 skill 登记复现、证据、根因与回归；不用于理论章节、9 个案例教材正文或 Presentation 课件。
---

# 代码工程 Docs 写作

## 范围

处理**可运行代码的工程文档**：

- `samples/caseN/README.md`：最短启动路径；
- `samples/caseN/docs/`：部署、API、配置、验证、限制和专题设计；
- `samples/caseN/scripts/`、`tests/` 和入口脚本的说明；
- `samples/README.md`、`samples/case-index.json` 的工程入口。

根 `docs/` 只放面向维护者的仓库治理、发布流程、环境矩阵和跨项目审计摘要，不属于学生教材，也不进入 VuePress；不接收案例部署、API、模型、排障或原始证据文档。案例审查也放在对应 `samples/caseN/docs/`。

不把理论教材、案例教学正文、Marp 课件或 VuePress 主题改写塞入本流程。源码 docstring 和 CLI 参考表走 `ascend310b-code-docs`。

## 语言要求

仓库自有的样例`README.md`、活动`docs/*.md`、更新记录和测试台账必须使用简体中文。代码、命令、API、路径、环境变量、模型名、版本号和日志原文保留英文，但步骤、解释、表头、告警、结论和证据边界必须用中文写。上游英文材料的逐字引用放在引用块或代码块中，并补中文解释；整段英文教程不能通过。`prose-language`是新写或改写文档的阻断项。

## 文档分层

```text
samples/caseN/README.md          最短运行、前置条件、一条成功路径
samples/caseN/docs/README.md     该案例代码工程的文档地图
samples/caseN/docs/NN-*.md       部署、API、配置、验证、限制和审查
samples/caseN/docs/evidence/     用户明确要求随仓库保留时的脱敏证据
```

文件名用两位数字前缀表示阅读顺序；README 和 docs 文件内的 H2–H6 **不写** `1.`、`1.1` 等章节编号。标题只命名内容，编号由索引或渲染层负责。`code-docs-manual-heading-number` 会报告 README/docs 正文中的手写编号。

新增文档使用两位数字前缀和 ASCII slug，例如：

```text
00-document-index.md
01-deployment.md
02-api-and-security.md
03-validation.md
04-limitations.md
```

标题直接命名内容，不用“结果”“相关工作”等空泛标题。运行命令必须写清 cwd、环境激活、输入、预期结果和失败停止条件。

## 面向读者的文档分层

- 面向学生的样例 README 保留短前置条件、固定版本的源码和模型获取命令、可执行运行路径、预期结果和必要限制；不能只链接部署文档，不要塞入全量环境哈希、原始日志或根因台账。
- 根目录 `docs/` 仅供维护者记录仓库治理、发布策略、环境矩阵摘要和跨项目审计；它不进入 VuePress，不存放案例排障台账或单案例工程说明。
- `samples/caseN/docs/` 与 troubleshooting 是面向工程读者的文档，可公开发布，并非天然私有；记录详细环境、证据摘要与路径、根因台账、修复前后的回归结果和剩余边界。
- 原始日志、截图和测试输出默认留在板端报告目录；只有用户明确授权保留时，才脱敏后存入 `samples/caseN/docs/evidence/` 或指定外部归档，不得自动收进教材正文或根 `docs/`。
- 完整 SHA-256、精确字节数、绝对路径、运行 ID、时间戳与原始命令属于工程证据：可以写在发布复现、证据清单或问题台账中，但学生教材、案例正文和课件只保留可解释的结果、方法和边界。
- README 可以链接工程文档补充细节，但不能用链接、日志或问题记录替代学生可执行的源码/模型获取与最短运行路径。

## 源码、模型和部署来源

部署文档必须让读者能从公开来源重建现场：

- 程序从 GitHub 的固定 commit 获取，记录仓库 URL、commit 和干净工作树；不要把作者电脑上的 `samples/caseN` 复制到板端写成安装步骤。
- 模型从 Hugging Face 的固定 revision 获取，默认使用 `HF_ENDPOINT=https://hf-mirror.com`；记录 repo、revision、文件清单、字节数、SHA-256 和许可证。
- `scp`/`rsync` 只可用于明确标注的本地调试或离线例外，不能作为默认发布链路。离线包必须标 `offline-bundle` 并带完整来源/哈希，不能声称是 HF 在线下载。
- 部署文档应链接 `ascend310b-case-testing` 的来源门，并在验证报告中记录源码 commit、模型 revision 和重跑命令。

## 测试问题统一记录

每个案例的测试问题进入该案例唯一的 `samples/caseN/docs/NN-troubleshooting.md`，并从 `samples/caseN/docs/README.md` 登记入口。案例台账归属于样例工程，不得在根维护者文档目录创建案例排障台账。首次创建时复制 [troubleshooting-template.md](references/troubleshooting-template.md)。问题 ID 使用 `CASE<N>-ISS-YYYYMMDD-NN`。禁止把失败原因只写在聊天记录、临时日志、提交说明或另一个专题文档里。

每个问题至少记录：

- 测试环境：板卡、8T/20T、CANN、驱动、Python、模型 revision、代码提交、输入和命令；
- 预期行为、实际症状和最早发现时间；
- 最小复现步骤、退出码/HTTP 状态、原始日志和证据路径；
- 判定层级：device、driver、CANN、ATC、model-load、contract、task-quality、performance、UI 或 test-harness；
- 根因状态：`confirmed`、`hypothesis` 或 `unknown`；
- 临时处理、实际修复、未采用方案；
- 修复前/后的回归结果和剩余边界；
- 关联的部署、验证和 API 文档。

设备未开机、SSH/mDNS 不可达或网络迁移属于环境/传输问题，不得写成 310B 不支持，也不得与另一块在线板的结果合并。8T 和 20T 的问题必须分条记录。

修复问题后保留原条目，把状态改为 `resolved` 或 `mitigated`，补充回归证据；不要删除失败历史。验证文档只保留结论和台账链接，避免同一问题在多处出现互相矛盾的版本。

## 代码文档必须说明

- 代码入口、服务和端口；
- Python/CANN/conda 前置条件；
- 模型、数据、配置和生成物边界；
- 如何启动、如何测试、如何停止；
- API/输入输出合同和鉴权边界；
- 摄像头/设备资源和释放要求；
- 8T 与 20T 的证据是否需要分开；
- 失败时保留哪些日志、命令和原始报告。

## 证据边界

把以下证据分开写，不互相替代：

```text
语法/静态检查
构建/转换
ACL 加载
数值一致性
任务准确率
性能
UI/API 服务
硬件验收
```

没有在板端执行的 NPU 结论写 `untested`；失败但没有确定根因写 `blocked`；不要把本地 CPU、模拟器或 910/950 结果写成 310B 的生产证据。

## 流程

1. 运行 `ascend310b_writing_audit` 检查 Markdown、链接、图片和锚点。
2. 修改代码工程文档和必要的索引；不改生成物。
3. 复跑 `ascend310b_writing_audit`。
4. 如果文档引用了接口、脚本或测试，实际运行对应本地检查；板端依赖标记并交给目标板验证。
5. 测试一旦失败，先更新该 case 的 `samples/caseN/docs/NN-troubleshooting.md`，再更新相关验证/限制文档的结论和链接；不得创建根 `docs/troubleshooting.md`。
6. 如果样例工程文档进入 VuePress，确认 `pnpm run docs:dev` 正在运行，并打开对应本地网页让用户人工检查；根 `docs/` 不进入 VuePress，非站点文档按实际阅读载体审查。构建和 HTTP 200 不等于人工通过。
7. 推送前运行 `ascend310b_writing_push_check`。

## 本地网页与人工校验

样例工程 docs 的 Markdown 进入站点后，和教材正文一样必须做本地网页验收；根 `docs/` 不进入 VuePress，非站点文档在实际阅读载体中审查。站点页面运行 `node scripts/check_page_layout.mjs --url <URL>` 检查桌面与移动视口的横向溢出和图片/SVG 越界。`human-review: pending` 只保留在外部任务或审查元数据中，不写入发布的 Markdown 正文或课件；用户确认相应呈现、命令、示例和限制后才算通过人工审查。没有人工确认时不得把它描述为最终交付。

## 停止条件

- README 的命令无法表达 cwd、环境或前置条件。
- 文档引用的文件、图片、API 或测试不存在。
- 证据层级被混淆。
- 测试发现问题但没有登记到该 case 的唯一 troubleshooting 台账。
- 问题台账没有环境、复现、原始证据、根因状态或回归结果。
- 文档把未验证的板端结果写成 observed-pass。
- `prose-language`报告正文以英文为主，却没有改成中文。
- 没有本地网页人工校验，却被描述为最终完成。
- 需要提交模型、OM、数据集、日志或截图才能“解释”文档时，先确认仓库资产边界。
