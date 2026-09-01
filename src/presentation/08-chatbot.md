---
marp: true
size: 16:9
theme: ascend310
paginate: true
header: "昇腾310B 8周教学"
footer: "第8周：聊天机器人"
---

<!-- _class: cover -->

# 第8周：聊天机器人

昇腾 310B 8周教学计划

每周 3 课时，每课时 45 分钟

对应案例：`samples/case9`

---

## 课程结构：3 课时 × 45 分钟

| 课时 | 主题 | 课堂主线 |
|---|---|---|
| 第1课时 | 项目架构与模型层 | 案例目标、候选架构、模型分层、OpenAI API 契约 |
| 第2课时 | 前端构建与板端候选启动 | 控制机检查、前端构建、MindSpore/ACL 候选启动 |
| 第3课时 | 聊天交互与结课演示 | 文字 UI、JSON/SSE、日志证据、故障规则、演示报告 |

第1课时解决“系统由哪些进程组成”，第2课时解决“如何在板端把候选跑起来”，第3课时解决“如何证明一次完整聊天链路可用”。

---

## 案例目标与暂停范围

- 当前目标：把聊天模型以 OpenAI-compatible JSON/SSE 服务运行在昇腾 NPU 上
- 访问方式：Case9 候选网关 + 文字页面
- 当前只验收文字 LLM，不把文字服务当作 XiaoZhi 设备后端的完整证明
- 音频、麦克风、ASR/TTS、PTT、XiaoZhi、OTA、设备 WebSocket 均暂停：不安装、不启动、不验收

本课围绕文本聊天闭环展开，不引入音频链路验收。

---

## 参考源与关键文件

| 文件 | 作用 |
|---|---|
| `src/experiment/case9.md` | 案例教程、验收门禁、失败规则 |
| `samples/case9/README.md` | 当前状态、候选架构、板端启动命令 |
| `samples/case9/app.py` | Bearer 保护的 OpenAI 兼容网关 |
| `samples/case9/local_app.py` | 板端本地中文聊天服务，当前音频路径暂停 |
| `samples/case9/acl_om_service.py` | loopback ACL/OM OpenAI 服务 |
| `samples/case9/mindspore_chat_service.py` | MindSpore Profile 候选服务 |
| `samples/case9/contract-v2.json` | Qwen2.5 静态 KV OM 契约 |
| `samples/case9/*tokenizer*.py` | 无 Torch tokenizer 适配 |
| `samples/case9/configs/chat_model_profiles.json` | 模型 Profile 注册表 |

`requirements.txt` 只声明网关/UI 依赖：`fastapi`、`httpx`、`pydantic`、`python-dotenv`、`uvicorn[standard]`。

---

## 模型分层与当前状态

| 模型/Profile | 入口 | 当前结论 |
|---|---|---|
| Qwen2.5-0.5B-Instruct 静态 KV ACL | 正式 `8080 -> 7861 -> 7865` | 现有正式基线，本轮不替换 |
| `qwen1.5-0.5b-mindspore` | 候选 `8090 -> 7867 -> 7868` | `experimental_dirty_base`；机器门通过，人工质量/准入待签字 |
| `tinyllama-1.1b-mindspore` | 候选 `8090 -> 7867 -> 7868` | `blocked`；长输出含 `U+FFFD`，CLI 禁止激活 |
| `deepseek-r1-qwen-1.5b-mindspore` | 候选 `8090 -> 7867 -> 7868` | `blocked`；中文质量、dirty-base 和正式准入未完成 |

机器门只表示协议、资源和运行检查通过，不表示回答正确或适合生产。

---

## 候选架构：服务链与 NPU 边界

本页的端口和互斥关系来自 Case9；下面的仓库图示用于对照同一仓库中已经落地的 ONNX → OM → NPU 部署边界。Marp 不把 Mermaid 代码自动绘制成图，因此不把代码块冒充流程图。

![仓库中的 NPU 部署架构图](../experiment/img8/case8_system_arch.png)

<div class="source">图示参考：`src/experiment/img8/case8_system_arch.png`；Case9 架构正文：`src/experiment/case9.md`；实现：`samples/case9/`</div>

浏览器没有模型管理接口；Profile 切换只能在板端执行 `case9-modelctl`。网关密钥只存在板端环境，不会发送给浏览器。

---

<!-- _class: visual -->

## 仓库部署图例：ONNX → OM → NPU

![ONNX 到 OM 的转换与 NPU 推理边界](../experiment/img8/case8_model_conversion.png)

<div class="source">图源：<a href="https://github.com/zhouxzh/Ascend310/blob/main/src/experiment/img8/case8_model_conversion.png">src/experiment/img8/case8_model_conversion.png</a>（案例8）；本周 Case9 架构正文：<a href="https://github.com/zhouxzh/Ascend310/blob/main/src/experiment/case9.md">src/experiment/case9.md</a>；实现：<a href="https://github.com/zhouxzh/Ascend310/tree/main/samples/case9">samples/case9/</a></div>

---

<!-- _class: compact -->

## 案例9源码地图：网关、服务与 runtime

```text
samples/case9/
├── app.py                         Bearer/OpenAI gateway
├── mindspore_chat_service.py     candidate worker service
├── acl_om_service.py             loopback ACL/OM service
├── tinyllama_acl_runtime.py      ACL runtime + contract checks
└── text_chat_app.py               browser text UI
```

<div class="source">源码：<a href="https://github.com/zhouxzh/Ascend310/tree/main/samples/case9">samples/case9/</a>；架构与门禁：<a href="https://github.com/zhouxzh/Ascend310/blob/main/src/experiment/case9.md">src/experiment/case9.md</a></div>

---

## 端口与互斥边界

| 用途 | 服务 | 网关 | 页面 |
|---|---|---|---|
| MindSpore 候选 | `127.0.0.1:8090` | `127.0.0.1:7867` | `0.0.0.0:7868` |
| 正式 Qwen2.5 基线 | `127.0.0.1:8080` | `127.0.0.1:7861` | `0.0.0.0:7865` |
| Qwen2.5 ACL 候选 | `127.0.0.1:8084` | `127.0.0.1:7867` | `0.0.0.0:7868` |

MindSpore 候选链与 Qwen2.5 ACL 候选链互斥，不能并行占用 `7867/7868`。正式 `8080 -> 7861 -> 7865` 保持不变，直到候选 Profile 完整门禁和独立复核都通过。

---

## 分层职责

- 页面层：`text_chat_app.py` 返回内嵌 HTML；`local_app.py` 是独立的板端本地服务
- 网关层：`app.py` 负责鉴权、限流、请求体上限、公共模型名和流式转发
- 服务层：`mindspore_chat_service.py`、`acl_om_service.py` 只监听 loopback，单请求串行
- 运行层：MindSpore/MindNLP Profile 适配或原生 ACL/OM runtime
- 硬件层：Ascend NPU；两条路线都不允许自动回退 CPU、云端或其他推理框架

每一层只做自己该做的事：网关不加载 tokenizer，服务做权威 token/context 检查，runtime 做 NPU 执行。

---

## Qwen2.5 静态 KV 契约：`contract-v2.json`

- 模型 ID：`qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`
- 静态序列长度：`1024`
- 词表大小：`151936`
- KV cache 布局：`split`，shape `[1, 2, 1024, 64]`
- 输入顺序：`input_ids`、`attention_mask`、`position_ids`，后接 24 层共 48 个 `past_key_values.*.key/value`
- 输出：`logits [1, 1, 151936]`，后接 48 个 `present.*.key/value`
- 特殊 token：`eos_token_id=151645`，`pad_token_id=151643`

契约还记录源 ONNX SHA-256。哈希只证明工件完整性，不证明目标板上的 ATC、ACL 或中文质量门通过。

---

## 静态 KV 执行方式

- batch 固定为 1，一次推理处理一个 token
- 服务在生成循环中维护并更新 `past_key_values`
- 每次生成前检查 `prompt_tokens + max_tokens <= 1024`
- 只支持贪婪解码：`temperature=0`、`top_p=1`
- runtime 会核对 OM 实际 descriptor，拒绝输入顺序、shape、dtype 或 byte size 不匹配的 OM

这种静态 KV 图不依赖 Transformers，也不导入 Torch、ONNX Runtime、vLLM 或 MindIE。

---

## Tokenizer 层：无 Torch 边界

`qwen25_tokenizer.py`、`acl_om_tokenizer.py`、`tinyllama_tokenizer.py` 都延迟导入 Rust `tokenizers`：

```python
# acl_om_tokenizer.py 中的 Qwen 模板拼装
rendered.append(
    "<|im_start|>" + role + "\n" + content + "<|im_end|>\n"
)
rendered.append("<|im_start|>assistant\n")
```

Qwen2.5 的 `pad_token_id` 从 `tokenizer_config.json` 和 `tokenizer.json` 解析，不做硬编码数字假设；必须存在 `<|im_start|>`、`<|im_end|>` 和可解析的 pad token。

---

## TinyLlama tokenizer 边界

`tinyllama_tokenizer.py` 使用 Llama 特殊 token：

- `unk_token_id`：`<unk>`
- `bos_token_id`：`<s>`
- `eos_token_id`：`</s>`
- pad 优先取配置，否则回退 `<pad>` 或 `<unk>`
- chat 格式为 `<|role|>\n...`

该文件同样禁止安装 Transformers 或 Torch。TinyLlama 当前保持 `blocked`，CLI 不会因为 tokenizer 可用就允许启动。

---

## MindSpore Profile 约束

`mindspore_chat_providers.py` 固定这些生成约束：

- context：`1024`
- 默认 `max_tokens`：`32`
- 最大 `max_tokens`：`80`
- `temperature=0`、`top_p=1`
- `do_sample=False`、`num_beams=1`
- 显式构造 attention mask
- 生成失败后 fail-closed，并可按配置触发 worker watchdog

必须使用 `CASE9_DEVICE_TARGET=Ascend`；CPU fallback 被显式禁用。启动时会用 `npu-smi` 校验可见 SoC，Profile 的 `board_soc` 必须匹配。

---

## MindSpore 服务边界

`mindspore_chat_service.py` 使用 Python 标准库实现，只监听 `127.0.0.1:8090`：

```python
MODEL_ID = "case9-active"
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32
MAX_MESSAGE_CHARACTERS = 24_000
```

服务保持单请求串行，并在生成前调用 provider 的 `count_tokens` 做权威 token 预算检查。直接启动被拒绝：

```python
parser.error(
    "direct start is disabled; use scripts/run_mindspore_chat_service.sh "
    "after its preflight"
)
```

---

## MindSpore API 契约

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `messages` 只接受 `system`、`user`、`assistant`
- batch 为 1，单进程单请求串行
- 请求体上限 `256 KiB`
- 支持普通 JSON 和 OpenAI SSE
- SSE 只发送前缀差量
- 内部模型名固定为 `case9-active`
- 公共网关模型名固定为 `case9-rag`

服务不允许客户端指定 Profile、权重路径、后端或任意 Python 表达式。

---

## 网关契约：`app.py`

网关不加载 tokenizer，只做边界保护：

```python
_MINDSPORE_ACTIVE_UPSTREAM_MODEL = "case9-active"
_MINDSPORE_ACTIVE_MAX_TOKENS = 80
_MINDSPORE_ACTIVE_MAX_INPUT_CHARACTERS = 4000
```

网关为 `/v1/*` 提供 Bearer 鉴权、请求体上限、peer 限流、并发上限、请求 ID 和 JSON/SSE 转发；固定上下文模型只接受贪婪 `temperature=0`、`top_p=1`。候选链 `RAG_ENABLED=false`，不注入本地知识库。

---

## ACL/OM 服务细节

`acl_om_service.py` 提供 loopback-only 的 OpenAI 兼容服务：

- 单线程 HTTPServer，天然串行
- 只接受 `127.0.0.1`、`localhost`、`::1`
- `/health`、`/v1/models`、`/v1/chat/completions`
- 请求体、角色、消息长度、`max_tokens`、贪婪参数均有硬边界
- 客户端断开时调用 runtime `cancel()`
- 正式 Qwen2.5 静态 KV 路线由独立 launcher 和 `serve_qwen25_kv_acl.py` 启动

ACL、OM、PyACL 和 NPU 检查只能在板端执行，不能在控制机模拟。

---

## 文字 UI 与本地服务

`text_chat_app.py` 是当前文字验收页面：

- `/`：内嵌 HTML
- `/api/config`：模型、Profile、字符限制
- `/api/history`：服务端会话历史
- `/api/chat`：JSON 或 SSE 聊天
- `/api/clear`：清空会话
- SSE 事件：`start`、`delta`、`done`、`error`

页面本身未启用浏览器鉴权，只在可信实验网络使用。`local_app.py` 是独立的板端本地服务（默认 `0.0.0.0:7862`），音频/PTT 路径当前暂停，本周不启动、不验收。

---

## 第2课时：控制机本地检查

控制机 `sci-agent` 只能做纯 Python、ONNX 和前端检查，不能运行 CANN、ACL、ATC、OM 或 `npu-smi`：

```powershell
$python = 'C:\Users\zhoux\anaconda3\envs\sci-agent\python.exe'
& $python -m py_compile qwen25_kv_acl_runtime.py qwen25_kv_acl_service.py scripts\serve_qwen25_kv_acl.py
& $python -m py_compile case9_model_profiles.py mindspore_chat_service.py mindspore_chat_providers.py
& $python -m pytest -q
```

这条命令来自 `samples/case9/README.md`。语法检查和单元测试通过不等于板端 NPU 推理通过。

---

## 第2课时：前端构建

```powershell
Set-Location frontend
npm ci
npm test
npm run build
Set-Location ..

git diff --check
```

前端构建只在控制机执行。当前 MindSpore 候选文字 UI 由 `text_chat_app.py` 返回内嵌 HTML；板端不需要 Node.js。React 构建产物 `frontend/dist` 服务于 `local_app.py` 的本地 UI，本周文字验收不强制依赖它。

---

## 第2课时：板端环境准备

**板端步骤**：ACL、OM、NPU 与聊天服务启动只能在昇腾板执行。

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
# PYTHONNOUSERSITE=1 只用于负向诊断，不能作为服务启动环境。
unset PYTHONNOUSERSITE
cd ~/case9-mindspore-chat
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

8T 当前地址是 `192.168.1.90`，20T 当前地址是 `192.168.1.95`。两块板的环境、CANN、Python、MindSpore、MindNLP 和驱动版本必须分别快照，不能把一块板的结论复制给另一块板。

---

## 第2课时：MindSpore 状态与工件门禁

**板端步骤**：

```bash
bash scripts/case9-modelctl.sh list
bash scripts/case9-modelctl.sh status
```

启动前可先核验 Profile 工件：

```bash
python scripts/verify_mindspore_profile_artifacts.py \
  --profile qwen1.5-0.5b-mindspore \
  --root "$PWD" \
  --output reports/mindspore-chat/qwen1.5-0.5b-mindspore/artifact-verify.json
```

返回 `passed` 才能记录 `artifact_verified`。`blocked`、`not-run` Profile 会被 CLI 拒绝，不能用参数绕过。

---

## 第2课时：MindSpore 候选切换

**板端步骤**：MindSpore Profile 使用板端现有 `base`，必须先记录完整包清单、MindSpore/MindNLP 版本、CANN 和 `npu-smi` 快照。

```bash
CASE9_ALLOW_EXPERIMENTAL=1 bash scripts/case9-modelctl.sh switch qwen1.5-0.5b-mindspore
```

`CASE9_ALLOW_EXPERIMENTAL=1` 只用于明确标记的 `experimental_dirty_base` 实验，不能绕过 `blocked`/`not-run`、环境或质量门禁。切换失败时 CLI 只回滚到上一个已验证 Profile；回滚也失败则保持候选链 fail-closed。

---

## 第2课时：MindSpore 候选网关与文字页

**板端步骤**：Profile 服务健康后，在另一个板端 shell 启动候选链。

```bash
export GATEWAY_API_KEY="$(openssl rand -hex 24)"
export UPSTREAM_BASE_URL=http://127.0.0.1:8090/v1
export UPSTREAM_MODEL=case9-active
export RAG_ENABLED=false
export MAX_CONCURRENT_REQUESTS=1
export PUBLIC_MODEL_ID=case9-rag
bash scripts/run_mindspore_chat_gateway.sh
bash scripts/run_mindspore_chat_text.sh
```

网关固定转发到 `http://127.0.0.1:8090/v1`，对外模型名是 `case9-rag`，并关闭 RAG 注入。此过程不改写 `.env`，不触碰正式端口，也不自动切换任何后端。

---

## 第2课时：健康与进程检查

**板端步骤**：MindSpore 服务只监听 loopback，先查服务本身：

```bash
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:8090/v1/models
bash scripts/case9-modelctl.sh status
```

`/health` 应包含 Profile、revision、环境指纹、NPU 型号、worker PID、busy、缓存清理和 admission 状态。浏览器只读显示活动 Profile；切换只能通过板端 CLI 完成。

---

## 第2课时：Qwen2.5 ACL 候选环境与门禁

**板端步骤**：ACL、OM、NPU。

```bash
cd ~/case9-qwen25-kv1024
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
export CASE9_QWEN25_KV_ROOT="$PWD"
export CASE9_QWEN25_KV_BOARD_ID="192.168.1.90"  # 报告采集时为同一块板的 192.168.8.178
export CASE9_QWEN25_KV_SOC_VERSION="Ascend310B4"
export CASE9_QWEN25_KV_OUTPUT_ROOT="$PWD/reports/$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

bash scripts/provision_qwen25_kv102_board.sh check
bash scripts/provision_qwen25_kv102_board.sh inspect
bash scripts/provision_qwen25_kv102_board.sh smoke
```

20T 使用独立的 `~/case9-qwen25-kv1024-20t`，并应改为 `Ascend310B1`、B1 OM/contract/lock。

---

## 第2课时：Qwen2.5 ACL 候选服务与最小 API

**板端步骤**：ACL、OM、NPU，只在 smoke 通过后启动。

```bash
export QWEN25_ROOT="$PWD"
export QWEN25_KV_OM="$PWD/artifacts/qwen25-static-kv-1024-v2.om"
export QWEN25_KV_CONTRACT="$PWD/contracts/qwen25-static-kv-1024-v2-om-contract.json"
export QWEN25_KV_TOKENIZER="$PWD/artifacts/tokenizer.json"
export QWEN25_KV_TOKENIZER_CONFIG="$PWD/artifacts/tokenizer_config.json"
export QWEN25_KV_LOCK="$QWEN25_KV_OM.lock.json"
export QWEN25_KV_TOKENIZER_LOCK="$QWEN25_KV_TOKENIZER.lock.json"
export QWEN25_KV_MAX_TOKENS=80
bash scripts/run_qwen25_kv_acl_service.sh
```

另开 shell 检查：

```bash
curl -fsS http://127.0.0.1:8084/health
curl -fsS http://127.0.0.1:8084/v1/models
```

---

## 第2课时：Qwen2.5 候选网关与文字页

**板端步骤**：候选 ACL、JSON/SSE、长输出和资源门通过后，才可启动网关和文字页。

```bash
export GATEWAY_API_KEY="$(openssl rand -hex 24)"
bash scripts/run_qwen25_kv102_gateway.sh
bash scripts/run_qwen25_kv102_text_chat.sh
```

Qwen2.5 ACL 候选链与 MindSpore 候选链使用相同 `7867/7868`，二者互斥。正式 `8080 -> 7861 -> 7865` 保持不变。

---

## 第3课时：浏览器聊天交互

按实际运行板卡访问：

- 8T：`http://192.168.1.90:7868/`
- 20T：`http://192.168.1.95:7868/`

页面无浏览器鉴权，只能在可信实验网络使用。发送一条中文问题，观察 `start -> delta -> done` 的流式事件；页面显示活动 Profile，模型切换时服务端会话会被清空，避免旧模型上下文发给新 worker。

---

## 第3课时：API/SSE 验证

可用 curl 直接验证候选网关：

```bash
curl -fsS \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"case9-rag","messages":[{"role":"user","content":"你好"}],"stream":true,"max_tokens":32,"temperature":0,"top_p":1}' \
  http://127.0.0.1:7867/v1/chat/completions
```

服务只返回前缀差量，并在末尾发送 `[DONE]`。`finish_reason=length` 表示达到 `max_tokens` 上限，文字 UI 不会把截断回复当作成功完成的对话轮次。

---

## 第3课时：证据与日志保存

每次批次使用唯一 UTC 目录，保存：

- 完整命令和实际 PID
- 服务日志、请求和原始响应
- 环境指纹、模型哈希、lock/contract
- `npu-smi` before/during/after 快照
- `case9-modelctl status`、监听端口和 worker 命令行

`Health: Alarm` 可作为诊断记录，但不能单独用来伪造通过结论。性能数字必须带 SoC、环境、warmup、循环数、百分位方法和报告路径。

---

## 第3课时：失败处理与 fail-closed

- 只停止本批次明确记录且命令行匹配的 worker PID
- 保留失败日志、哈希和报告，不删除系统 CANN、conda 缓存、其他模型或正式服务
- 维持 `blocked`、`not-run` 或具体失败状态，不自动切换 CPU、云端、Torch、vLLM、MindIE 或其他模型
- 切换失败先回滚上一个已验证 Profile；回滚失败则候选链不可用
- 出现 `DRV_LPM_FAULT`、设备重置、worker 非正常退出或 NPU 内存持续增长时，先保存 `dmesg -T`、`npu-smi info`、`modelctl status`、监听端口和 PID，再停止扩大测试
- 一次受控恢复不能写成模型稳定性通过

相同模型名或 IP 别名不能自动继承正式状态。

---

## 第3课时：模型切换与会话边界

`case9-modelctl switch` 会先停止旧 worker，再启动新 worker，轮询健康状态并记录活动状态。文字 UI 按活动 Profile generation 命名会话；状态变化后立即丢弃旧会话。

客户端不能指定 Profile、权重路径、后端或任意 Python 表达式。网关只暴露稳定的 `case9-rag`，服务内部固定为 `case9-active`，浏览器看不到模型路径。

---

## 第3课时：结课演示流程

1. 演示案例架构：UI、网关、服务、runtime、NPU 的关系
2. 演示控制机前端构建和测试
3. 在板端演示环境激活、Profile 状态和候选切换
4. 启动网关与文字页，完成一次中文聊天交互
5. 检查 `/health`、`/v1/models`、SSE 响应和运行日志
6. 说明失败规则：`blocked` 不可激活、不切换 CPU、只停本批次 PID

---

## 课堂任务

1. 阅读 `src/experiment/case9.md` 和 `samples/case9/README.md`，画出候选链分层图
2. 在控制机完成 Python 语法检查和前端 `npm ci`、`npm test`、`npm run build`
3. 在教师指定的板卡上执行环境快照、Profile `list/status` 和 Qwen1.5 实验切换
4. 启动 MindSpore 候选网关与文字页，完成一次中文聊天交互
5. 保存请求、响应、PID、健康状态和日志，整理结课演示材料

---

## 交付物

- `linux/week08/chat-demo.md`
- `linux/week08/final-report.md`

报告必须包含：使用的板卡地址与 SoC、环境激活命令、候选 Profile、端口关系、API 契约、一次完整请求/响应、服务 PID、日志路径、失败或未完成门禁，以及是否使用了 `CASE9_ALLOW_EXPERIMENTAL=1`。

---

## 验收标准

- 前端构建和测试通过
- 板端聊天服务能启动并完成一次中文交互
- 能说明模型、API、UI 和运行日志的关系
- 能复现主要命令：环境激活、Profile 切换、网关/UI 启动、健康检查
- 能说明 `case9-rag`、`case9-active`、端口和 Profile 状态
- 遵守失败规则：不切换 CPU、不启动 `blocked` Profile、只停止明确记录的 worker PID
- 结课演示和最终报告包含可复核的命令、证据路径和未完成边界
