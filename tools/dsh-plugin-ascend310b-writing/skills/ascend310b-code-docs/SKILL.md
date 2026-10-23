---
name: ascend310b-code-docs
description: Ascend310 代码文档写作与维护。用于 samples 下源码的 README、API/CLI 参考、架构说明、配置说明、docstring、示例和测试命令文档；要求从代码、测试和板端事实出发，拒绝编造 API，不把代码文档写成案例教材或论文。
---

# 代码文档写作

本 skill 用于 Ascend310 教材中面向 310B 的代码工程文档。

## 范围

用于代码附近的文档：

- Python/C++ 源文件中的 docstring 和接口注释；
- `scripts/`、`tests/` 和生成脚本的用途、参数、退出码和失败处理；
- 代码 API/CLI 的参考表和最小可运行示例；
- 仅当 README 需要补函数签名或 CLI 表时，才改 `samples/*/README.md` 的参考段落。

部署、验证、限制和文档地图走 `ascend310b-writing-docs`。不用于理论章节、9 个案例的教材正文、论文式论证、Marp 课件或 VuePress 主题。

本 skill 参考 Diátaxis 的教程/操作/参考/解释分层，以及高星文档 skill 的“代码是事实源”原则；它不使用外部项目专属 API。

## 先读代码，再写文档

1. 找到真实入口、导出符号、CLI 参数和测试文件。
2. 运行静态检查或最小测试；不能运行时记录原因。
3. 确认输入、输出、异常、退出码、默认值、环境变量和权限边界。
4. 对照 `AGENTS.md` 的资产边界和本案例 `docs/README.md`。
5. 修改文档，不修改生成 HTML/PDF；需要图时使用 `ascend310b-writing-dot`。

## 文档分层

| 类型 | 放置位置 | 要求 |
| --- | --- | --- |
| Tutorial | 案例 README 或 `docs/01-...` | 从零到第一个成功结果 |
| How-to | `docs/` | 一个任务、前置条件、步骤和停止条件 |
| Reference | `docs/` 或代码注释 | 签名、参数、返回值、错误和版本 |
| Explanation | `docs/` | 设计、边界、取舍和证据 |

不要把所有内容塞进一个 README。教程写路径，参考写合同，解释写原因。

## API 与 docstring

- 函数/类文档说明输入、输出、副作用、异常和线程/设备约束。
- 命令文档说明 cwd、环境、必填/可选参数、默认值、示例和退出码。
- 示例必须能对应真实入口；不能凭接口名称臆造调用。
- API 变更后同时更新 docstring、README、参考表和相关测试。
- 代码引用使用稳定符号或相对路径，不使用本机绝对路径。
- 生成的 API 文档作为派生物；源 docstring/注释才是真源。

## 验证

1. 运行代码级测试或 `--help`/最小输入，保留输出。
2. 检查文档中的路径、命令、环境变量和引用存在。
3. 运行 `ascend310b_writing_audit` 检查 Markdown、链接和图片。
4. 若命令依赖开发板，标注 `untested`，交由 `ascend310b-board` 或板端工具验证。
5. 推送前运行 `ascend310b_writing_push_check`。

## 停止条件

- 文档描述的 API/参数在源码和测试中不存在。
- 示例无法运行且没有明确环境限制。
- 代码、README、docstring 和测试互相矛盾。
- 用生成 HTML/PDF 代替源 Markdown/docstring。
- 把本地静态检查写成板端 `observed-pass`。
- 把 DOT 字体、画布自检或插件内部标注写进学生可见教程。
