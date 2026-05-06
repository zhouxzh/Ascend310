# 案例 9：边缘智能聊天机器人

基于昇腾 310B 的边缘端智能聊天机器人，集成文本嵌入（NPU）、RAG 向量检索、语音交互和可选云端 LLM。

## 项目说明

本项目采用**三层混合架构**，在昇腾 310B 有限的硬件条件下实现实用的聊天机器人：

| 层级 | 位置 | 组件 | 作用 |
| :--- | :--- | :--- | :--- |
| 1 | Ascend 310B NPU | 文本嵌入模型 (all-MiniLM-L6-v2) | 将查询和文档转为 384 维向量 |
| 2 | CPU | FAISS 索引 + 对话管理 | 向量检索、状态跟踪、模板回复 |
| 3 | 云端 (可选) | OpenAI 兼容 LLM API | 增强回复质量，支持复杂对话 |

## 目录结构

```text
case9/
├── app.py                 # Gradio Web 界面入口
├── ascend_inference.py    # Ascend NPU 嵌入模型推理封装
├── config.py              # 配置常量
├── dialogue.py            # 对话管理器 + 回复生成
├── knowledge_base.py      # RAG 引擎 (FAISS + 中文分块)
├── voice_io.py            # 语音识别 & 语音合成
├── prepare_models.py      # 模型下载 & ONNX → OM 转换
├── setup.sh               # 一键环境安装
├── requirements.txt       # Python 依赖
├── data/
│   ├── sample_knowledge.txt  # 示例知识库（昇腾/边缘计算）
│   └── sample_faq.json       # 示例 FAQ 问答对
├── models/                   # 模型文件目录
└── README.md
```

## 快速开始

### 1. 环境安装

```bash
bash setup.sh
```

### 2. 启动服务

```bash
python3 app.py
```

打开 http://127.0.0.1:7860 进入聊天界面。

### 3. 命令行选项

```bash
python3 app.py --port 8080        # 指定端口
python3 app.py --share            # 生成公网分享链接
```

## 运行模式

### 离线模式（默认）

无需联网，使用模板 + RAG 检索回答昇腾和边缘计算相关问题。

### 云端增强模式

在设置面板中启用云端 LLM 并填入 API Key（兼容 OpenAI/Ollama/vLLM），回复质量显著提升。

### CPU 回退

没有 Ascend 310B 时，嵌入模型自动回退到 CPU（sentence-transformers），功能不受影响。

## 依赖说明

| 包 | 用途 | 必需？ |
| :--- | :--- | :--- |
| gradio | Web 聊天界面 | ✓ |
| sentence-transformers | 嵌入模型 / CPU 推理 | ✓ |
| faiss-cpu | 向量相似度搜索 | ✓ |
| jieba | 中文文本分块 | ✓ |
| SpeechRecognition | 语音输入 | 可选 |
| pyaudio + pyttsx3 | 语音输入输出 | 可选 |
| torch + transformers | ONNX 模型导出 | 仅 prepare_models.py |
| acl (CANN) | NPU 推理 | 仅昇腾设备 |

## 使用建议

1. 先以 CPU 模式启动，体验 RAG 检索和对话功能
2. 阅读 [config.py](config.py) 了解可调整的参数
3. 在设置面板中配置云端 API，对比模板 vs LLM 的回复质量
4. 向 `data/sample_knowledge.txt` 添加自己的知识库内容
5. 在昇腾设备上运行，观察 NPU 推理加速效果
