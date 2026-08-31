# CLAUDE.md — Ascend310 项目开发环境

## 环境说明

- **当前系统**: WSL (Windows Subsystem for Linux)，不是昇腾310B硬件设备
- **用途**: 代码编写、文档撰写、教程开发 — 所有代码在 WSL 上完成
- **目标平台**: 昇腾310B 开发者套件（代码最终运行在真实硬件上）

## 关键约束

- **禁止**在 WSL 上安装或运行昇腾310B 相关代码（如 PyACL、CANN、ATC 转换、NPU 推理等）
- **禁止**安装仅昇腾设备需要的系统依赖（如 portaudio19-dev、espeak、pyaudio 等音频库），除非用户明确要求
- `prepare_models.py`（ONNX 导出 + ATC 转换）只能在昇腾设备上运行
- 本项目的 Python 代码在 WSL 上仅做**语法检查**和**文档撰写**，不在本地实际运行

## 项目结构

```
Ascend310/
├── src/book/          # 教程 markdown 源文件
├── src/experiment/    # 各案例详细教程 (case1.md ~ case9.md)
├── samples/           # 各案例配套代码
│   ├── case1/         # 人脸考勤（face-attendance）
│   ├── case2/         # 目标检测 (YOLO)
│   ├── case3/         # DDSP 音乐工作站（ddsp-music-workstation）
│   └── case9/         # 边缘智能聊天机器人
└── CLAUDE.md
```

## 项目背景

这是一本关于昇腾310B AI 应用开发的教程书籍。各案例涵盖从模型部署到完整应用的端到端流程。读者在真实昇腾310B 硬件上操作。


CLAUDE.md
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
