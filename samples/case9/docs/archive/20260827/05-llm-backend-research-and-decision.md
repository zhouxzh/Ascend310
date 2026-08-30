# 310B4 LLM 后端研究与决策记录

## 记录范围

本文记录案例 9 在 `HwHiAiUser@192.168.8.178`（Ascend 310B4 / 8T）上的本地生成式
LLM 后端审计。它是工程决策和证据索引，不是模型质量报告，也不把通用 ONNX、编译
成功或设备可见性当作 NPU 推理通过。

当前硬约束如下：

- 开发板禁止安装或导入 `torch`、`torch_npu`、`torchaudio`、`mindtorch` 及其隐式
  依赖；不修改现有 `base` 环境和系统启动文件。
- 不安装 vLLM、MindIE 或未经审核的自定义 OPP。
- 本地 LLM 只允许目标板真实 ACL/OM/NPU 路径；失败后停止验收，不切换 CPU、云端或
  其他模型。
- XiaoZhi 服务端暂停，直到本地 LLM 通过并完成独立的无 Torch 语音依赖审核。

## 当前状态

截至 2026-08-21，已经有可运行的网关、浏览器本地聊天、PulseAudio 音频、sherpa-onnx
ASR/TTS、前端测试证据，以及板端 no-Torch ACL 环境检查证据。TinyLlama 预编译 OM
已经通过真实 ACL 生成和文本 API；中文质量、音频闭环和 XiaoZhi 仍未完成：

| 项目 | 证据状态 | 说明 |
| --- | --- | --- |
| case9 网关历史协议 | 通过协议验收 | 早期 `7861` 的鉴权、JSON、SSE 使用受控 stub 验证；该记录不代表真实 LLM |
| 本地聊天 UI | 通过静态检查 | `npm test` 4/4、`npm run build` 通过 |
| ASR/TTS | 板端基础检查通过 | sherpa runtime、模型导入、PulseAudio 采样/播放通过；未完成 10 轮语音闭环 |
| Qwen GGUF | 工件完整性通过 | 仅作为历史候选保留 |
| llama.cpp CANN | 构建失败 | CANN 8.0 缺少 `aclnnop/aclnn_recurrent_gated_delta_rule.h`；没有生成 `llama-server` |
| TinyLlama 预编译 OM | ACL/NPU 文本实验通过 | 独立的 TinyLlama 8080 JSON/SSE 与随后重启后的 7861 网关转发按 `docs/09` 的最终报告记录；中文质量和稳定性残余风险单独记录 |
| ACL/OM Qwen | 历史候选，契约阻断 | 工件通过字节/SHA-256 校验，但 ONNX 契约拒绝动态 KV-cache 图；未执行 ATC、加载或推理 |
| XiaoZhi | 暂缓 | 上游默认依赖包含 Torch/Torchaudio，且设备当前不可用 |

已有 GGUF 工件的历史证据：Qwen2.5-0.5B Q4_0，`428,730,208` bytes，revision
`12145bd1d629190a4d44254073650877954d02c9`，SHA-256
`7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed`。它不能替代
ACL/OM 验收。

## 候选后端比较

| 候选 | 与 310B4/无 Torch 的关系 | 决策 |
| --- | --- | --- |
| `llama.cpp` CANN + GGUF | 官方 CANN 文档主要列出 310P/910B 等硬件，当前固定 revision 在本板 CANN 8.0 编译缺头文件 | 保留历史失败证据，不作为当前首选 |
| 通用 HF Qwen ONNX -> ATC -> OM -> ACL | 不需要 Torch 运行时；当前图契约在本板阻断 | 保留历史证据，不自动重试 |
| TinyLlama ManualReset 预编译 OM -> ACL | 社区预编译 OM；本板 descriptor 与执行已通过，CANN/模型来源仍属实验性 | 当前文本实验候选，严格 fail-closed |
| `onnxruntime-cann` | 官方社区 EP 文档的依赖版本与本板现有 CANN 不匹配，且没有本项目 Qwen/310B4 证据 | 不安装、不作为首轮后端 |
| `yinghuo302/ascend-llm` | Orange Pi/Ascend 310 的 ONNX->OM->ACL 研究项目，但需要自定义 OPP，且有公开 OM 输出异常问题 | 不安装；除非另行批准 OPP 和适配工作 |
| `Tlntin/qwen-ascend-llm` | 目标偏 310B1/20T，源码和 requirements 直接导入 Torch | 明确排除 |
| MindSpore/MindNLP 官方 8T 样例 | 有 Orange Pi 8T/Qwen1.5-0.5B 的官方示例，但走 MindSpore runtime，不是本轮 ACL/OM 路径 | 仅后备研究资料，不自动切换 |
| vLLM-Ascend/MindIE | 310B 支持边界与本板目标不符，并涉及被禁止的 Torch/框架依赖 | 排除 |

参考资料：

- [llama.cpp CANN backend](https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/CANN.md)
- [Hugging Face Qwen1.5-0.5B-Chat-ONNX](https://huggingface.co/onnx-community/Qwen1.5-0.5B-Chat-ONNX/tree/main)
- [Orange Pi 8T MindSpore Qwen 示例](https://www.hiascend.com/developer/techArticles/20250424-3)
- [Orange Pi MindSpore 版本对应表](https://raw.githubusercontent.com/mindspore-courses/orange-pi-mindspore/master/README.md)
- [ONNX Runtime CANN Execution Provider](https://onnxruntime.ai/docs/execution-providers/community-maintained/CANN-ExecutionProvider.html)
- [ascend-llm 研究项目](https://gitee.com/yinghuo302/ascend-llm/blob/main/readme.md)
- [qwen-ascend-llm](https://github.com/Tlntin/qwen-ascend-llm)

## 已验证的 TinyLlama 工件

当前文本实验只绑定一个固定候选，避免将不同导出、量化格式或硬件证据混在一起：

| 字段 | 值 |
| --- | --- |
| 仓库 | `wan-zutao/tiny-llama-manual-reset` |
| revision | `114a158718411d8b0a252806ca14144c01a7e3db` |
| 模型文件 | `tiny-llama.om` |
| 实测大小/SHA-256 | `1,493,077,371` bytes / `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967` |
| tokenizer | `tokenizer.zip`, `709,459` bytes |
| tokenizer SHA-256 | `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` |
| 服务模型名 | `tiny-llama-1.1b-acl-om` |
| 目标 | `Ascend310B4`, batch 1, loopback `127.0.0.1:8080`（最终服务；8081 仅为隔离验证端口） |

该 OM 是社区历史预编译工件，不带当前 CANN 8.0 的 ATC 日志或官方 310B4 兼容声明。
本板 descriptor、ACL execute 和 API 证据只代表实验性可运行，不代表中文能力或商业
可部署性。完整输入输出契约、报告路径和内存观察见 `docs/09`。

Qwen ONNX 的固定 revision、大小和 SHA 仍保留在 `local_model_manifest.json`，但属于
独立的历史阻断候选，不能与本节 TinyLlama 证据混用。

## 失败策略

以下任一条件出现，当前候选标记为 `blocked`，保留原始日志和去敏元数据：

1. 文件不是完整 ONNX、大小或 SHA 不匹配。
2. 输入输出、opset、动态维度、KV-cache 或算子无法形成已审核 ACL 契约。
3. `atc --soc_version=Ascend310B4` 非零退出，或成功但未生成 OM。
4. ACL 无法初始化设备、模型、context、stream 或内存。
5. 中文 greedy 生成失败、输出不符合 tokenizer/停止词契约，或 `npu-smi` 无法提供
   推理前后证据。
6. OpenAI JSON/SSE 服务无法稳定完成 `/v1/models` 和 `/v1/chat/completions`。

阻塞时不得删除失败工件或改写报告来制造通过结果；不得自动安装 Torch、切换
MindSpore/vLLM、改用 CPU、云端或其他板卡。

## 本轮板端阻断证据

板端 `192.168.8.178` 的 `case9-acl-om` 环境已通过：Python `3.9.25`、`acl` 可导入，
禁止的 Torch 系列模块均不存在，CANN toolkit 目录为 `8.0.0`（组件版本
`7.6.0.1.220`），`npu-smi` 识别 `310B4`。固定 ONNX URL 的 IPv4 HTTPS 探测和下载
返回 `curl_exit=28`（连接超时）；随后通过 ModelScope 对象存储取得同一内容，模型
`928499243` 字节、tokenizer `11418266` 字节的 SHA-256 均与固定 HF manifest 匹配。
原始日志见 `docs/07-acl-om-validation-record.md` 所列板端报告路径。图检查随后发现
动态/符号维度、48 组 KV-cache 输入输出以及未放行的 `ai.onnx:Sigmoid`，因此 ONNX 契约
门失败；这不是 ATC 或 ACL 失败，后续门保持未执行。

## 后续边界

只有 ACL/OM LLM 的文本 JSON、SSE、网关转发和 NPU 证据全部通过，才恢复本地网页文本
聊天。ASR/TTS 音频闭环仍是独立验收，不能用文本通过替代。XiaoZhi 仍不安装、不启动、
不访问其 OTA/WebSocket 端口；后续需另行审核无 Torch 的 ASR/VAD/TTS 依赖并确认设备可用。
