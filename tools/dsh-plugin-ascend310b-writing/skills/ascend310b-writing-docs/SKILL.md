---
name: ascend310b-writing-docs
description: Ascend310 代码工程文档写作。用于 samples/caseN 的 README、docs/ 下的部署/API/配置/验证/限制文档、代码入口与测试命令说明、证据记录和跨案例工程索引；不用于理论章节、9 个案例教材正文或 Presentation 课件。
---

# 代码工程 Docs 写作

## 范围

处理**可运行代码的工程文档**：

- `samples/caseN/README.md`：最短启动路径；
- `samples/caseN/docs/`：部署、API、配置、验证、限制和专题设计；
- `samples/caseN/scripts/`、`tests/` 和入口脚本的说明；
- `samples/README.md`、`samples/case-index.json` 的工程入口；
- 必要的仓库级 `docs/` 工程审查文档。

不把理论教材、案例教学正文或 Marp 课件改写塞入本流程。

## 文档分层

```text
README.md          最短运行、前置条件、一条成功路径
docs/README.md     代码工程的文档地图
docs/NN-*.md       部署/API/配置/验证/限制等专题
docs/             截图、原始日志和证据的引用位置
```

新增文档使用两位数字前缀和 ASCII slug，例如：

```text
00-document-index.md
01-deployment.md
02-api-and-security.md
03-validation.md
04-limitations.md
```

标题直接命名内容，不用“结果”“相关工作”等空泛标题。运行命令必须写清 cwd、环境激活、输入、预期结果和失败停止条件。

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
5. 如果文档进入 VuePress，确认 `pnpm run docs:dev` 正在运行，并打开对应本地网页让用户人工检查；构建和 HTTP 200 不等于人工通过。
6. 推送前运行 `ascend310b_writing_push_check`。

## 本地网页与人工校验

代码 docs 的 Markdown 进入站点后，和教材正文一样必须做本地网页验收。运行 `node scripts/check_page_layout.mjs --url <URL>` 检查桌面与移动视口的横向溢出和图片/SVG 越界；最终状态先写 `human-review: pending`；用户确认页面、命令、示例和限制后才算完成。没有人工确认时不得把它描述为最终交付。

## 停止条件

- README 的命令无法表达 cwd、环境或前置条件。
- 文档引用的文件、图片、API 或测试不存在。
- 证据层级被混淆。
- 文档把未验证的板端结果写成 observed-pass。
- 没有本地网页人工校验，却被描述为最终完成。
- 需要提交模型、OM、数据集、日志或截图才能“解释”文档时，先确认仓库资产边界。
