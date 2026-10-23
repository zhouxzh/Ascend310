---
name: ascend310b-writing-case
description: Ascend310 实践案例教程写作。用于案例1至案例9的 src/experiment 正文、案例索引、sidebar、自包含链接、样例代码入口和案例验收叙述；特别保护 case4 掌纹识别不依赖其他仓库文档，不用于理论章节、通用代码 docs 或课件排版。案例发布测试还必须从 GitHub 固定 commit 获取程序、从 Hugging Face 固定 revision 下载模型。
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

## 章节编号

案例的 H1 保留冻结标题 `# 案例 N：任务名`；H2–H6 只写语义标题，不手写 `1.`、`1.1` 或 `4.1`。最终 PDF 会按“实践案例”部分的案例章自动生成 `4.1`、`4.1.1`，VuePress 若需要编号也由渲染层生成。

```markdown
# 案例 4：掌纹识别

## 案例目标 {#src-experiment-case4-goal}

### 环境检查 {#src-experiment-case4-environment-check}
```

不要为了得到 `4.1` 在 `case4.md` 中手写标题编号。手写编号会在章节移动、插入和 PDF 自动编号时重复或漂移；审计规则为 `heading-manual-number`。

## 案例结构

建议每章回答：

1. 这个案例解决什么实际问题；
2. 输入、输出和硬件/软件前提；
3. 最短可运行路径；
4. 关键实现和失败边界；
5. 如何验证数值、任务指标、性能和 UI；
6. 已知限制与下一步。

案例教程可以链接自己的 `samples/caseN/`、图片和代码，但不要依赖其他章节或案例的未保证内容。运行命令必须写出工作目录、环境激活和前置条件。

## 源码、模型与发布复现

案例正文的“运行”步骤必须从公开来源开始，不能假设读者拥有作者电脑上的 `samples/` 或模型目录：

- 程序：从 GitHub 仓库在板端检出**完整 commit SHA**，记录仓库 URL、commit、`git status --short` 和源码目录；不要在正文写“把本地 samples 目录复制到板上”。
- 模型：从 Hugging Face 仓库按**固定 revision** 下载，默认使用 `HF_ENDPOINT=https://hf-mirror.com`；记录 repo、revision、文件清单、字节数、SHA-256 和许可证。
- 发布检查必须从固定源码 commit 和固定 HF revision 重建；作者本地模型、ONNX、OM、数据集或未提交源码只能作为临时诊断材料，不能作为教程的复现证据。
- 需要离线包时，正文必须明确 `offline-bundle` 例外及其来源/哈希，不能写成“HF 下载成功”。
- 正文至少列出可复制的获取命令示例，或链接到案例 README 中同等严格的命令；不要只写 `<本地模型路径>`。

具体门禁和命令模板见 `ascend310b-case-testing`。模型、源码和配置的边界应写入案例的证据表，而不是只留在作者聊天或本地日志里。

案例正文是学生读的完成稿。图后不要写 `.dot` 路径、字体名、pt/dpi 或排版自检；这些属于 DOT skill 和生产备注。图片用一句说明图在讲什么即可。

## 证据边界

- 摄像头：区分 MIPI/CSI 与 USB、实际分辨率和读帧结果。
- 服务：HTTP 健康通过不等于 NPU 推理通过。
- 模型：模型、OM、数据和运行报告不能用截图替代。
- 性能：标明 8T/20T、warmup、iterations、repeats 和报告路径。
- 无法在板端验证的内容写 `untested` 或 `blocked`。

## 本地网页与人工校验

案例正文修改后必须确认 `pnpm run docs:dev` 正在运行，并打开对应的 `experiment/caseN.html`。运行 `node scripts/check_page_layout.mjs --url <URL>`，检查桌面与移动视口的图片、SVG、表格和代码块；用户还需要人工检查案例步骤、样例入口和证据表。本地 HMR、HTTP 200、`docs:build` 或自动 audit 通过都不能代替这次检查。最终状态先写 `human-review: pending`，用户确认后才能通过。

## 停止条件

- 案例标题、编号或 sidebar 不一致。
- `case4.md` 依赖其他仓库文档。
- 链接/图片目标不存在。
- 样例入口和正文命令不一致。
- 没有本地网页人工校验，却被描述为最终完成。
- 案例正文出现 `.dot` 路径、字体名、pt/dpi 或作者自检备注。
- 板端证据缺失却要写“通过”。
