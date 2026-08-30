# ACL/OM 本地 LLM 部署计划

## 目标与边界

目标是在 Ascend 310B4 / 8T 开发板上，用固定 Qwen1.5-0.5B ONNX 生成 OM，再由原生
ACL 服务提供 OpenAI 兼容文本接口。该服务只绑定 `127.0.0.1:8080`，由现有 case9
网关 `127.0.0.1:7861` 调用；浏览器和 XiaoZhi 都不能直接访问 ACL 服务。

本计划不安装 Torch、TorchNPU、Torchaudio、MindTorch、vLLM、MindIE 或自定义 OPP，
不升级系统 CANN，不修改 conda 启动文件。模型和报告只保留在板端
`$HOME/case9-local-chat`，不提交 Git。

## 固定资源与目录

`local_model_manifest.json` 是唯一的下载元数据来源。首轮资源为：

```text
artifacts/acl-om/qwen1.5-0.5b-chat-model_fp16.onnx
artifacts/acl-om/tokenizer.json
artifacts/acl-om/om/qwen1.5-0.5b-chat-acl-om.om
reports/acl-om/
```

模型与 tokenizer 的 revision、大小和 SHA-256 必须与 manifest 完全一致。本轮默认不
下载没有固定 SHA-256 的辅助配置；后续需要时，必须先把固定 revision、大小和发布方
SHA-256 加入 manifest。不能把未校验的 LFS pointer 当作模型或配置。

模型卡许可记录为 `tongyi-qianwen-research`。这是实验工件的许可审计字段，不等于
商业授权；任何再分发或产品化都必须单独审核原始许可。

## 分阶段命令

所有命令在板端执行，且使用同一 shell 激活 CANN 和 `case9-acl-om` Python 3.9 环境。
`scripts/provision_acl_om_board.sh` 是保留的历史 Qwen 路径，当前默认已禁用；它只会在
显式设置 `CASE9_ALLOW_LEGACY_QWEN_ACL_OM=1` 时继续。当前首选是
`scripts/provision_tinyllama_board.sh`，它只会创建名称固定的隔离 Python 3.9 环境，并只从
哈希锁定的 `requirements-tinyllama-acl-om.txt` 安装 NumPy/tokenizers：

```bash
export CASE9_DIR="${CASE9_DIR:-$HOME/case9-review-20260822}"
if [[ -d "$CASE9_DIR/src/scripts" ]]; then
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR/src}"
  export CASE9_TINYLLAMA_HOME="${CASE9_TINYLLAMA_HOME:-$CASE9_DIR}"
else
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR}"
fi
cd "$CASE9_SOURCE_DIR"
bash scripts/provision_tinyllama_board.sh create-env
bash scripts/provision_tinyllama_board.sh install-runtime
bash scripts/provision_tinyllama_board.sh check
bash scripts/provision_tinyllama_board.sh download
bash scripts/provision_tinyllama_board.sh inspect
bash scripts/provision_tinyllama_board.sh smoke
bash scripts/provision_tinyllama_board.sh serve
```

旧 Qwen `create-env/install-runtime/download/inspect/convert/smoke/serve` 命令只作为历史
证据保留；不得在没有单独批准的情况下执行旧脚本的 `install-runtime`。

### `check`

必须记录 `uname -m`、Python 可执行路径和版本、CANN/ATC 版本、`npu-smi info`、
`import acl` 结果、可用磁盘，以及以下模块均不存在：
`torch`、`torch_npu`、`torchaudio`、`mindtorch`、`torchvision`、`xformers`。

检查只允许 `Ascend310B4`，并拒绝设置 `ASCEND_CUSTOM_OPP_PATH`。如果当前 CANN 环境
不能导入 ACL 或 `npu-smi` 无法识别 310B4，后续命令必须停止。

### `create-env` 与 `install-runtime`

TinyLlama `create-env` 只创建 `case9-acl-om` Python 3.9 conda 环境，拒绝与已有非 Python
3.9 环境混用；它不修改 `base` 或 shell 启动文件。TinyLlama `install-runtime` 仅在这个
已隔离环境中执行 `pip --force-reinstall --no-deps --require-hashes --only-binary=:all:`，
并只读取 TinyLlama 专用锁定文件中的 NumPy 和 Rust `tokenizers` 两个 aarch64 wheel；
旧的 `requirements-acl-om.txt` 保留给显式批准的历史 Qwen 脚本。启动前后检查
Torch/inference-framework 禁止模块；历史环境里预先存在的 ONNX/ABI 或 MindSpore 包不被
该脚本使用，也不会由该脚本删除。

这两个命令都不允许任意 PyPI 包名、requirements URL、Torch 系列包或依赖解析。若
某个 wheel、哈希、Python ABI、ACL 导入或禁止模块检查失败，环境门失败并停止后续步骤。

### `download`

脚本下载固定 revision 的 ONNX 和 tokenizer 到临时 `.part` 文件，依次检查 LFS pointer、
字节数和 SHA-256 后原子改名。旧的 GGUF 文件不参与本轮下载或启动。网络镜像不能改变
仓库、revision、文件名或哈希；仓库、revision、实际路径、字节数和 SHA 写入板端锁定记录。
本轮因 `huggingface.co` 在板端连接超时，另行用 ModelScope 对象存储取得同一内容做契约
审计；该手工传输只在整文件 SHA-256 与 manifest 完全一致后保留，未改写脚本的 canonical
HF URL，也不构成新的候选来源。

### `inspect`

`scripts/inspect_qwen_onnx.py` 只生成 JSON，不执行推理。报告至少包括：文件大小和
SHA-256、ONNX IR/opset、所有输入输出名称/类型/形状、动态维度、initializer/external
data、past-key/value 结构、算子计数、量化节点和操作集审计。生成的 contract 同时绑定
ONNX bytes、SHA-256、固定 revision 和 ATC 输入顺序；`convert`、`smoke`、`serve` 会重新
核对当前文件，拒绝 stale 或手工替换的 contract。

本项目首轮只接受静态 batch 1、长度 2048、`input_ids`/`attention_mask`/`position_ids`
输入和 `logits` 输出的已审核契约。只接受审计范围内的标准 ONNX opset/operator；通用
Transformers.js merged decoder、动态 KV、vendor domain 或不明量化算子不得直接送 ATC；
检查失败是阻断，不自动修改图。

### `convert`

只有 contract JSON 明确标记 `supported_autoregressive_qwen_layout=true` 才运行：

```bash
atc --model=<verified-onnx> --framework=5 \
  --output=<board-local-prefix> --input_format=ND \
  --input_shape='input_ids:1,2048;attention_mask:1,2048;position_ids:1,2048' \
  --soc_version=Ascend310B4 --output_type=FP16
```

报告必须保留完整命令、CANN 环境、ATC stdout/stderr、退出码、OM 大小和 SHA-256。脚本
同时写入 `om/om.lock.json`，绑定 OM 路径、字节数、SHA-256 和当前 contract SHA；`smoke`
与 `serve` 启动前会重新校验该锁，禁止替换 OM 或 contract 后直接启动。禁止覆盖已有 OM；
不同转换尝试使用明确的新前缀。ATC 成功但 OM 不存在或大小为零仍算失败。

### `smoke`

启动前后各采集一次 `npu-smi info`。`acl_om_service.py smoke` 使用中文 `你好`，
只做 batch 1、greedy、短输出，超时默认 300 秒。必须确认：ACL device/context/stream
成功创建，模型可加载，输入内存和输出内存释放，tokenizer 解码可得到文本，且没有
CPU/云端/Torch 回退。运行时使用 `execute_async` + `synchronize_stream`，并在板端
主线程对单次 ACL 调用施加 300 秒 deadline；失败时保留前后 NPU 快照和完整去敏日志。

## ACL 服务接口

入口为 `acl_om_service.py`，核心模块为 `acl_om_contract.py`、`acl_om_runtime.py`、
`acl_om_tokenizer.py`。推荐由 `provision_acl_om_board.sh serve` 启动；启动参数显式
给出 contract、OM 和 tokenizer，不从浏览器或请求体读取路径。`tokenizer_config.json`
不是本轮固定下载项，tokenizer 内含 Qwen 特殊 token 定义：

```bash
python acl_om_service.py serve \
  --contract <contract.json> \
  --om <qwen1.5-0.5b-chat-acl-om.om> \
  --tokenizer <tokenizer.json> \
  --host 127.0.0.1 --port 8080
```

固定模型名为 `qwen1.5-0.5b-chat-acl-om`。实现要求：

- `GET /v1/models` 返回模型名、服务状态和已加载 OM 的摘要，不泄漏绝对路径或密钥；
- `POST /v1/chat/completions` 接收 `messages`、`stream`、`max_tokens` 和 `temperature`，
  强制 batch 1、context 2048、首轮 `max_tokens<=128`、greedy；
- `stream=false` 返回 OpenAI 风格 JSON；`stream=true` 返回
  `text/event-stream`，每个片段位于 `choices[0].delta.content`，以 `data: [DONE]`
  结束；
- 不接受请求指定模型路径、OM、provider、API key 或任意 Python 表达式；
- 单进程串行执行，取消、断开、超时和异常都先确认 stream 已同步，再释放 ACL 资源；
  同步失败时保留 dataset/buffer 句柄、标记服务需重启，不在设备可能仍运行时 free；
- 只依赖 native `acl`、NumPy 和 tokenizer 文件，不导入 Transformers 或 Torch。

## 网关接入顺序

先验证 ACL 服务的 `/v1/models`、JSON completion 和 SSE completion，再启动现有网关：

```dotenv
UPSTREAM_BASE_URL=http://127.0.0.1:8080/v1
UPSTREAM_MODEL=qwen1.5-0.5b-chat-acl-om
UPSTREAM_API_KEY=
```

网关的 Bearer token 仍只存在板端 `.env`，不会发送到浏览器。网关通过后，先做文本
聊天，再恢复 ASR/TTS；音频故障不能掩盖 LLM 故障。XiaoZhi 仍保持暂停。

## 硬门槛与停止条件

| 门 | 必须证据 | 失败动作 |
| --- | --- | --- |
| 环境 | 310B4、CANN、ACL、无 Torch 模块 | 停止，不安装依赖 |
| 工件 | 完整 ONNX/tokenizer、固定 revision/bytes/SHA | 删除未校验临时文件或隔离，重新审计 |
| 契约 | JSON 检查报告明确 admitted | 不运行 ATC |
| ATC | 非零/零退出码、完整日志、OM hash | 标记转换失败，不伪造 OM |
| ACL | model/context/stream/device 创建与释放 | 标记运行时失败，不 CPU 回退 |
| NPU | 中文 smoke 前后 `npu-smi` 和输出 | 标记真实推理失败 |
| API | JSON、SSE、网关转发 | 不恢复网页音频或 XiaoZhi |

## 后续清理边界

本地 LLM 证据写完后，可对早期 XiaoZhi 隔离目录执行一次路径核对和人工确认后的
删除：只允许 `$HOME/case9-xiaozhi`、`case9-xiaozhi` 环境及该案例专属归档，不删除共享
`~/.cache/pip` 或其他项目文件。该清理不属于 LLM 通过条件，也不启动 XiaoZhi。
