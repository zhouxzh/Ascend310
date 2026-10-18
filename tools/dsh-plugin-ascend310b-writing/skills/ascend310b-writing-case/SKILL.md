---
name: ascend310b-writing-case
description: Ascend310 实践案例教程写作。用于案例1至案例9的 src/experiment 正文、案例索引、sidebar、自包含链接、样例代码入口和案例验收叙述；特别保护 case4 掌纹识别不依赖其他仓库文档，不用于理论章节、通用代码 docs 或课件排版。
---

# 实践案例写作

只处理 Ascend310 教材中面向 310B 的实践案例，不处理理论章节或通用代码 docs。

## 范围

只处理 `src/experiment/case1.md` 到 `case9.md` 及其索引。案例的冻结标题来自 `AGENTS.md`，必须保持：

```text
案例 N：<纯中文任务名>
```

不得给案例标题增加硬件型号前缀、英文副标题或自造编号。`case4.md` 对应“案例 4：掌纹识别”，它必须自包含，不能把其他仓库 Markdown 作为前置依赖。

## 写作流程

1. 运行 `ascend310b_writing_audit scope=experiment`。
2. 先读对应 `samples/caseN/README.md` 和 `samples/case-index.json`，确认案例入口、服务、模型和运行环境。
3. 只改案例源 Markdown、索引和必要的 sidebar；不要改 `latex/cases/`。
4. 修改案例编号、名称或链接时，同时更新案例正文、sidebar、案例索引和冻结名表。
5. 复跑 `ascend310b_writing_audit scope=experiment`。
6. 涉及真实板端、摄像头、模型或服务的结果，按证据门记录；本地检查不能替代板端验证。
7. 推送前运行 `ascend310b_writing_push_check`。

## 案例结构

建议每章回答：

1. 这个案例解决什么实际问题；
2. 输入、输出和硬件/软件前提；
3. 最短可运行路径；
4. 关键实现和失败边界；
5. 如何验证数值、任务指标、性能和 UI；
6. 已知限制与下一步。

案例教程可以链接自己的 `samples/caseN/`、图片和代码，但不要依赖其他章节或案例的未保证内容。运行命令必须写出工作目录、环境激活和前置条件。

## 证据边界

- 摄像头：区分 MIPI/CSI 与 USB、实际分辨率和读帧结果。
- 服务：HTTP 健康通过不等于 NPU 推理通过。
- 模型：模型、OM、数据和运行报告不能用截图替代。
- 性能：标明 8T/20T、warmup、iterations、repeats 和报告路径。
- 无法在板端验证的内容写 `untested` 或 `blocked`。

## 停止条件

- 案例标题、编号或 sidebar 不一致。
- `case4.md` 依赖其他仓库文档。
- 链接/图片目标不存在。
- 样例入口和正文命令不一致。
- 板端证据缺失却要写“通过”。
