# MindSpore LLM 候选模型清单

_清单版本：1.2｜盘点日期：2026-09-02｜更新日期：2026-09-04｜范围：Ascend310B4/8T 与 Ascend310B1/20T_

---

本文是 Case9 的模型候选研究清单。8T 当前连接入口为 `192.168.1.90`；
`192.168.11.14` 和 `192.168.8.178` 是同一块板的历史 IP 别名，仅用于报告 provenance。
它回答“哪些模型值得测试”，不把模型卡、框架
支持矩阵或官方 Gradio 示例误写成当前开发板已经通过。真实可用性只能由本仓库的
逐板验收记录确认。

## 1. 🔍 证据分级

| 级别 | 含义 | 可以得出的结论 |
| --- | --- | --- |
| `official-board-example` | 官方 Orange Pi 示例明确给出模型、MindSpore/CANN 和算力等级 | 值得在对应板卡优先复现；仍需 Case9 API、稳定性和质量测试 |
| `native-mindspore-candidate` | 有 MindSpore/Modelers 权重或 loader，且不需要新增推理框架 | 可以进入本轮下载和加载门 |
| `framework-support-only` | MindFormers/MindSpeed-LLM 列出模型，但没有当前 310B 证据 | 只能作为兼容性候选，不能宣称可运行 |
| `conditional` | 模型卡要求 Torch、Torch-NPU、vLLM、MindIE、新 CANN 或自定义运行时 | 登记但不安装、不加载，状态为 `blocked` 或 `not-run` |
| `measured` | Case9 已保存该 Profile 的真实板端报告 | 仅报告中对应的 SoC、工件和环境有效 |

框架支持不等于板端支持；HF Safetensors 也不等于原生 MindSpore checkpoint，更不等于
ONNX/OM。每个结论必须同时注明来源、revision、工件哈希、SoC、CANN、MindSpore 和
原始报告路径。

## 2. 📋 首批可执行 Profile

| Profile | 模型/权重来源 | 8T B4 (`192.168.1.90`; historical `.11.14`/`.178`) | 20T B1 (`192.168.1.95`) | 首轮策略 |
| --- | --- | --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | `Qwen/Qwen1.5-0.5B-Chat` | `blocked`：历史报表 9/9，但严格身份门 8/9，health/snapshot 缺 `npu_model` | `experimental_dirty_base`：缺口机器门 9/9 | 复用已锁定工件；补齐 B4 身份证据后再解除 |
| `tinyllama-1.1b-mindspore` | `TinyLlama/TinyLlama-1.1B-Chat-v1.0` | 已测，长输出失败 | 已测，长输出失败 | 保留失败证据；英文/中文分开评分 |
| `deepseek-r1-qwen-1.5b-mindspore` | `MindSpore-Lab/DeepSeek-R1-Distill-Qwen-1.5B-FP16` | 已有兼容性批次 | 优先板卡，已有隔离批次 | 先复核固定 revision 和中文质量 |
| `qwen2.5-0.5b-mindspore` | `Qwen/Qwen2.5-0.5B-Instruct` 固定 commit `7ae557...`，HF Safetensors | `blocked`：工件已锁定；当前地址 context-only 诊断加载 `48.649923 s`、生成 2 token 用时 `15.225400 s`，但严格 placement 证据缺失 | `not-run` | 先解决严格 placement/NPU 旁证；不能用诊断输出启动服务；详见 [当前地址短生成记录](39-qwen25-05b-context-only-20260904.md) |
| `qwen2.5-1.5b-mindspore` | `Qwen/Qwen2.5-1.5B-Instruct`，固定 commit `989aa79...`，HF Safetensors（完整权重 `3,087,467,144` bytes） | `blocked`：七个文件已同步并通过大小/SHA-256 校验；正确 CANN v2/v3b/v4 曾加载并生成短文本，但重启后的正确 CANN 复测在生成阶段出现 `Alloc failed, size:481482240`（NPU 约 `7703 -> 14993 / 15610 MB`），placement/API/长输出/稳定性/质量/性能未完成 | `not-run` | 首轮 SIGSEGV 仅为 PYTHONPATH 污染历史证据；重启后内存失败是独立阻断，见 [8T 内存门记录](37-qwen25-1.5b-8t-memory-gate-20260904.md)，20T 仍需独立测试 |
| `qwen3-0.6b-mindspore` | `MindSpore-Lab/Qwen3-0.6B`（Modelers） | `blocked`：当前 MindNLP `0.4.1` 无 Qwen3 loader | `not-run` | 不升级环境；`enable_thinking=False` 仅在兼容性分支使用 |
| `qwen3-1.7b-mindspore` | `MindSpore-Lab/Qwen3-1.7B`（Modelers） | `blocked`：当前 MindNLP `0.4.1` 无 Qwen3 loader | `not-run` | 不升级环境；不能由 0.6B/其他模型推断 |
| `minicpm3-4b-mindspore` | `MindSpore-Lab/MiniCPM3-4B-FP16` | 不测试 | 仅 20T 测试 | 官方 20T/24G 示例，禁止移到 8T |

> **📌 双板矩阵说明：** 注册表为保持 8T/20T 矩阵和候选 UI 的固定形状，
> 对 MiniCPM3、MiniCPM4 和 MiniCPM5 也保留 `Ascend310B4` 的
> `not-run`/`blocked` 行。这些行只是边界和未执行状态的记录，不是 8T 兼容性、
> NPU 推理或模型支持证据。MiniCPM3 的实际候选执行范围仍是 20T/B1，8T 行不得
> 下载、启动或切换；MiniCPM4/5 是 `conditional`，两块板都不得下载、启动或切换。

同一 Profile 的 8T 与 20T 结果分开保存。仅改变 IP 不会制造新的硬件结果；已有报告
只有在模型哈希、运行环境和 SoC 完全匹配时才可复用，缺少的门必须补测。

## 3. 🔗 官方和社区依据

官方 Orange Pi 在线示例列出 Qwen1.5-0.5B、TinyLlama、DeepSeek-R1-Distill-Qwen-1.5B
和 MiniCPM3-4B，并为不同模型指定不同 CANN、MindSpore 和算力等级。MiniCPM3 示例使用
`MindSpore-Lab/MiniCPM3-4B-FP16`、MindNLP 专用类和 Modelers 镜像；这构成 20T 的
优先复现依据，但不是 Case9 API 验收结果。

- [Orange Pi Online 示例清单](https://github.com/candle-org/orange-pi-mindspore/blob/dev/applications/online/README.md)
- [MiniCPM3 官方示例代码](https://github.com/candle-org/orange-pi-mindspore/blob/dev/applications/online/inference/19_minicpm3/minicpm_gradio.py)
- [MiniCPM3 Ascend 技术文章](https://www.hiascend.com/developer/techArticles/20250612-1)
- [Qwen1.5 Orange Pi 技术文章](https://www.hiascend.com/developer/techArticles/20250424-3)
- [DeepSeek Orange Pi 技术文章](https://www.hiascend.com/developer/techArticles/20250526-20)
- [MindFormers 模型支持列表](https://www.mindspore.cn/mindformers/docs/en/r1.8.0/introduction/models.html)
- [MindSpeed-LLM 模型支持列表](https://github.com/Ascend/MindSpeed-LLM/blob/master/docs/en/mindspore/models/supported_models.md)
- [HF MindSpore 标签筛选](https://huggingface.co/models?other=mindspore)

Qwen2.5-0.5B/1.5B 和 Qwen3-0.6B/1.7B 具有中文或多语言能力，适合作为新增候选，
但其模型卡示例通常使用 Transformers/PyTorch；当前 Case9 不因模型卡而安装这些依赖。
Qwen3 首轮还必须确认现有 MindNLP 版本能够识别其架构和 chat template。

## 4. 🛡️ 条件候选登记表

以下条目进入候选清单，供 UI 展示研究状态，但不进入下载、加载或模型切换流程：

| Profile | 主要阻断原因 | 处理 |
| --- | --- | --- |
| `openpangu-embedded-1b-conditional` | 官方路径要求 CANN 8.1、Torch/Torch-NPU、MindIE | `blocked`，不安装 |
| `ee-model-1.5b-conditional` | MindSpore/Ascend 标签存在，但没有可复核的当前 loader/部署代码 | `not-run`，等待独立证据 |
| `telechat-1b-conditional` | 发布推理路径为 Transformers/PyTorch/custom code | `blocked`，不安装 |
| `bitcpm-cann-1b-conditional` | 使用 Torch、Torch-NPU/MindSpeed 和特定量化运行时 | `blocked`，不安装 |
| `minicpm5-1b-ascend-conditional` | 需要 vLLM、Torch-NPU、CANN 8.5/Python 3.11 | `blocked`，不安装 |
| `minicpm4-0.5b-conditional` | 当前公开示例不是本项目已验证的 MindSpore 310B 路线 | `not-run` |
| `minimind3-64m-conditional` | Transformers/PyTorch；只有 910B 证据，非 310B | `not-run` |
| `haidass-143m-conditional` | 原始预训练模型而非聊天模型，且无 310B MindSpore 证据 | `not-run` |
| `bloomz-560m-research-only` | 仅作为外部研究对照，没有当前板端原生权重 | `not-run` |
| `qwen2-0.5b-instruct-mindspore` | 固定 HF revision 已登记，但当前 310B loader、工件哈希和 NPU placement 未验证 | `not-run`，仅元数据 |
| `qwen2-1.5b-instruct-mindspore` | 1.5B 内存、loader、工件和 NPU placement 尚未验证 | `not-run`，仅元数据 |
| `qwen2-math-1.5b-instruct-mindspore` | 数学专用模型，缺少通用聊天质量和当前 310B 路径 | `blocked`，仅元数据 |
| `qwen2.5-coder-0.5b-instruct-mindspore` | 编程专用模型，当前 loader、工件哈希和 NPU placement 未验证 | `not-run`，仅元数据 |
| `qwen2.5-math-1.5b-instruct-mindspore` | 数学专用模型，缺少通用聊天质量和当前 310B 路径 | `blocked`，仅元数据 |
| `llama3.2-1b-instruct-mindspore` | HF gated license，revision 未固定且无当前 310B loader 证据 | `blocked`，仅元数据 |

新增六个条目使用 `candidate_kind=conditional`、`runtime.provider=unsupported`，工件
大小和 SHA-256 留空；同步脚本、服务和模型切换器必须拒绝下载或启动。条件候选的登记不代表推荐。任何要求新增 Torch、Torch-NPU、vLLM、MindIE、CANN、
驱动或 OPP 的方案必须另行批准；本轮失败时不自动切换到这些方案。

## 5. 🖥️ 候选列表显示规则

候选 UI 展示模型名称、语言、目标 SoC、运行方式、状态、质量标签和阻断原因；不展示
权重路径、内部 token 或管理密钥。

- 逐板 G0-G8 技术门全部通过且使用共享 `base` 的 Profile：显示
  `experimental_dirty_base`，可在板端显式实验开关下切换。任一身份、协议或资源门
  缺失时保持 `blocked`，即使曾经产生过输出也不能切换。
- 中文质量未完成：显示“质量待审核”，不影响技术实验切换。
- `blocked`、`not-run` 或 `conditional`：显示研究状态但禁用切换。
- `admitted` 只能人工设置，不自动替换正式 `Qwen2.5 ACL` 路线。注册表校验器和服务
  会 fail-closed：必须同时有 `quality.reviewed=true`、`quality.human_review=approved`
  （或等价的明确批准值）、整数人工计数至少 `8/10` 且声明的中文语言状态为
  `passed`；机器 `quality_machine` 或 `machine_valid_count` 不能代替人工证据。

当前正式模型仍为 `8080 -> 7861 -> 7865` 的 Qwen2.5 静态 KV ACL；候选链为
`7868 -> 7867 -> 8090`，两条链互斥。

## 6. 🧪 当前已知事实

截至本清单更新，实际在 MindSpore/NPU 上启动并产生过正式候选输出的文本模型仍是
Qwen1.5、TinyLlama 和 DeepSeek。Qwen2.5-0.5B 在 8T 已有当前地址的
`context_only` loader 诊断（固定工件、NPU SoC、短输出和资源快照已记录），但严格
placement gate 拒绝，因此仍是 `blocked`，不能写成“已支持”。Qwen3 的 B4 `blocked` 来自当前 MindNLP
`0.4.1` 缺少架构 loader；Qwen2.5-1.5B 在 8T 已完成七个文件的同步与哈希核验；正确
CANN v2/v3b/v4 已产生短生成，v4 的加载和双 token 数值已记录，但 placement、API、质量或性能门仍未完成，因此该板状态为
`blocked`（见 [8T 内存门记录](37-qwen25-1.5b-8t-memory-gate-20260904.md)）。
更早的配置级预检仍保留在 [8T 预检记录](36-qwen25-1.5b-8t-preflight-20260904.md)；
MiniCPM3 和 20T 新批次仍 `not-run`。当前地址的只读 loader 复核确认
Qwen2/Qwen2Moe、Llama、DeepseekV2 和 MiniCPM3 类可见，但没有 Qwen3 loader；
详见 [loader 能力记录](33-mindspore-llm-candidate-validation-record.md#78-2026-09-04-当前地址-mindnlp-loader-能力复核)。
Qwen2.5-1.5B 的活动目录随后完成 7/7 文件校验（报告见验收记录 §7.9），但这只解除
工件缺失，不解除 placement 或后续 NPU/API 门。重启后的单 token 复测进一步在正确
CANN 路径下复现了 `Alloc failed, size:481482240`，所以该 Profile 仍不能作为可用聊天
服务。
共享 `base`、中文质量、API/候选链和正式准入仍按逐板报告处理，不能由一次诊断或另一块
板的结果推断。

本次新增的六个 Qwen2/Qwen2.5 专项与 Llama 3.2 条目只完成来源、许可证和固定 revision
登记，尚未下载或加载；详见[候选检索记录](42-mindspore-candidate-search-record.md)。

历史 `.11.14` 只读健康快照还记录了周期性 `DRV_LPM_FAULT 0x80E3A203`；重启后 1.5B
失败期间峰值约 `14993/15610 MB`，进程退出后的跟进快照回落到 `3003/15610 MB`。
在设备恢复前不追加重模型
加载或长压测。该诊断不把 `Health: Alarm` 单独当作模型失败门，详见
[验收记录 §7.7、§7.10 和 §7.11](33-mindspore-llm-candidate-validation-record.md)。
