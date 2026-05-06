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
│   ├── case1/         # 图像分类 (ResNet)
│   ├── case2/         # 目标检测 (YOLO)
│   ├── case3/         # 智能电子琴 (MIDI + 3D打印)
│   └── case9/         # 边缘智能聊天机器人
└── CLAUDE.md
```

## 项目背景

这是一本关于昇腾310B AI 应用开发的教程书籍。各案例涵盖从模型部署到完整应用的端到端流程。读者在真实昇腾310B 硬件上操作。
