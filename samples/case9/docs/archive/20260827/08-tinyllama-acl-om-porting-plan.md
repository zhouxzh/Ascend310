# TinyLlama ACL/OM 移植计划

## 目标和边界

本文件定义在 `192.168.8.178`（`Ascend310B4 / 8T`）上验证 TinyLlama 的实施路径。
首轮只验收无 Torch 的文本生成，不部署 XiaoZhi；麦克风采集、ASR 和独立音频质量不作为
验收条件。`local_app` 的文本回归可能按既有实现触发一次 TTS 播放，但这不计入音频门。

```text
TinyLlama prebuilt OM
  -> native ACL + NumPy + tokenizer.json
  -> 127.0.0.1:8080 (final loopback service; 8081 was the isolation port)
  -> case9 gateway:7861
  -> local text chat
```

硬门通过后，服务已改用 `127.0.0.1:8080`，并接入现有网关的
`UPSTREAM_BASE_URL`/`UPSTREAM_MODEL`。板端不得安装 `torch`、`torch_npu`、`torchaudio`、
`mindtorch`、vLLM、MindIE 或未经批准的自定义 OPP。

## 候选工件和证据边界

候选来自 [Tiny-Llama ManualReset](https://gitee.com/wan-zutao/tiny-llama-manual-reset)，
固定源码 revision 为 `114a158718411d8b0a252806ca14144c01a7e3db`。清单位置是
[`local_model_manifest.json`](../../../local_model_manifest.json)。

| 工件 | 固定信息 | 当前判断 |
| --- | --- | --- |
| `tiny-llama.om` | OBS URL；`1,493,077,371` bytes；SHA-256 `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967` | 工件、descriptor、ACL execute 和 NPU 生成已在目标板实测；中文质量未接纳 |
| `tokenizer.zip` | OBS URL；`709,459` bytes；SHA-256 `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` | 可做内容完整性校验；模型结构仍以 OM descriptor 为准 |
| `tiny-llama.onnx` | OBS URL；`1,487,421,772` bytes | 仅授权的 ATC/自定义 OPP 分支使用，默认不下载 |

OBS 的 multipart ETag 不是 SHA-256。下载脚本使用 `.part` 文件、字节数和 SHA-256
校验，并把验证的 OM SHA-256 写入板端锁文件；模型文件和锁文件不提交 Git。当前目标板
已经完成 OM descriptor、真实 `acl.mdl.execute`、8080 JSON/SSE 和 7861 网关协议门；
连续 10 轮观察发现 NPU 内存/HugePages 有残余增长，中文探测出现英文回退和 U+FFFD，
所以只能把它作为实验性英文/协议候选，不能称为中文聊天模型。

上游方案包含 SmoothQuant INT8 和 `MatMulInteger` 自定义算子。类似 B4/CANN8 图在
[昇腾论坛案例](https://www.hiascend.com/dev/forum/thread-0278197953923430371-1-1.html)
中曾在 ATC 阶段失败；因此默认先验证预编译 OM，失败后停止，不自动重建。

## 板端目录和脚本

脚本只在板端执行，默认使用以下目录：

```text
~/case9-tinyllama/
├── artifacts/tiny-llama.om
├── artifacts/tokenizer.zip
├── artifacts/tokenizer/{tokenizer.json,tokenizer.model,...}
├── reports/tinyllama-acl-contract.json
├── reports/*-tinyllama-*.log
└── artifacts/tinyllama-artifacts.lock.json
```

新增入口：

- `scripts/provision_tinyllama_board.sh`：环境、下载、描述符、smoke 和可选 ATC 门禁；
- `scripts/run_tinyllama_acl_service.sh`：只启动已通过门禁的 loopback 服务；
- `tinyllama_acl_contract.py`、`tinyllama_acl_runtime.py`、`tinyllama_tokenizer.py` 和
  `tinyllama_acl_service.py`：原生 ACL 运行时及 OpenAI 兼容接口。
- `requirements-tinyllama-acl-om.txt`：仅 NumPy/tokenizers 的哈希锁定安装清单；
  `tests/test_tinyllama_acl.py` 合并覆盖 contract、runtime、service 测试（没有再拆出重复的
  `test_tinyllama_contract.py`/`test_tinyllama_service.py` 文件）。

脚本不会编辑 shell 启动文件、删除 conda 环境、修改系统 CANN/OPP，或同步模型到 Git。

## 实施顺序

### 1. 环境冻结

在同一个 shell 中显式执行 CANN 和环境激活：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
```

先运行：

```bash
bash scripts/provision_tinyllama_board.sh check
```

检查必须记录 `uname -m`、`npu-smi info`、`310B4`、CANN/驱动版本、`acl` 导入、可用磁盘、
Python 版本和禁止模块。`Health: Alarm` 单独记录，不作为唯一阻断条件。环境缺失时，
只有人工确认后才运行 `create-env` 和 `install-runtime`；安装清单固定为
`requirements-tinyllama-acl-om.txt` 中的 NumPy 与 tokenizers 两个 wheel，并使用
`--force-reinstall --no-deps --require-hashes`。`requirements-acl-om.txt` 仅保留给已禁用的
历史 Qwen 路径。`transformers`、
vLLM、MindIE、Torch 系列和 ONNX Runtime 均为硬禁止模块。若历史环境已预先存在
`onnx`、`protobuf`、`typing_extensions`、`sentencepiece` 或 `mindspore`，脚本只记录
`preexisting_not_used`，不删除它们，也不把该环境标成“洁净重建”；TinyLlama 运行时不会导入
这些包。

### 2. 工件下载

```bash
bash scripts/provision_tinyllama_board.sh download
```

该命令只下载 OM 和 tokenizer；不会执行 pip、ATC 或服务。脚本必须拒绝：

- LFS pointer 或字节数不符的文件；
- stale `.part` 文件；
- 清单 URL、文件名或 revision 不一致；
- tokenizer ZIP 路径穿越；
- OM SHA-256 与板端锁文件不一致。

需要检查源图时，必须另行运行 `download-onnx`，并在报告中说明它不属于默认运行路径。

### 3. OM descriptor 和契约

```bash
bash scripts/provision_tinyllama_board.sh inspect
```

`inspect` 使用 `acl.mdl.load_from_file` 和 model descriptor，生成
`reports/tinyllama-acl-contract.json`。预期输入仅作为审计参考，实际顺序、dtype、rank、
shape 和 byte size 必须来自 descriptor：

```text
input_ids          int64    [1, 1]
attention_mask     int64    [1, 1025]
position_ids       int64    [1, 1]
past_key_values    float16  [22, 2, 1, 4, 1024, 64]
```

契约 schema v1 至少包含 `model`、`acl_om`、`source_artifact`、`source_revision`；
`model.model_id` 固定为 `tiny-llama-1.1b-acl-om`，`model.family` 固定为 `tinyllama`；
模型维度字段使用 runtime 的 `num_layers`、`num_kv_heads` 和 `head_dim` 命名，
`execution_mode` 固定为 `kv_cache_token`。输出 logits 的索引和 KV 输出索引必须显式
记录，禁止照搬上游固定索引。

任何输入数量、dtype、shape、KV 布局、词表范围或输出语义不符，都停止在 descriptor 门，
不执行 ACL 生成。

当前方案审计得到的输出候选为 `/Cast:0`（logits，`float32 [1,1,32000]`）、
`/model/Reshape_1:0`（单步 KV，`float16 [22,2,1,4,1,64]`）和
`/model/Reshape_2:0`（attention scores，`float16 [22,1,32,1,1025]`）。这些名称和
shape 仍必须在目标板通过 ACL descriptor 重读并写入 contract；attention scores 只作为
辅助输出，不得被误当作 logits 或 KV。

### 4. 无 Torch ACL runtime

运行时使用单进程、单模型实例、同步 `acl.mdl.execute` 和串行请求。每个请求从全零 KV
Cache 开始，初版固定 `batch=1`、context 上限 `1024`、greedy、`max_tokens<=32`。
所有 NumPy 数组必须 contiguous，ACL 错误必须抛出；不能回退 CPU 或云端。

每步处理一个 token：读取最后位置 logits、选择最大 token、更新 descriptor 指定的 KV
输出，直到 EOS 或上下文上限。请求结束、超时或异常时按以下顺序释放：

```text
同步 stream -> 销毁 dataset/data buffer -> 释放 device/host buffer
-> unload model -> destroy descriptor/context/stream -> acl.finalize
```

tokenizer 从 ZIP 解出的 `tokenizer.json` 加载；启动前用 manifest 同时校验
`tokenizer.json`、`tokenizer.model`、`special_tokens_map.json` 和 `tokenizer_config.json` 的
字节数/SHA-256，且显式 config 只能指向同一目录的 manifest-bound 文件。ZIP 中的 `config.json`
和模型索引不用于推导网络结构。特殊 token ID 必须从 tokenizer 文件读取，不能使用上游硬编码值。

### 5. ACL smoke 和服务

```bash
bash scripts/provision_tinyllama_board.sh smoke
bash scripts/run_tinyllama_acl_service.sh --port 8080
```

smoke 必须保存推理前后 `npu-smi`、输出 token/text、耗时和进程退出码。服务只监听
`127.0.0.1`，最终端口为 `8080`（隔离验证曾使用 `8081`），提供：

```text
GET  /v1/models
POST /v1/chat/completions
```

支持普通 JSON 和 SSE；请求不允许指定 OM 路径、后端或 API key。所有硬门已确认后，网关环境变量为：

```dotenv
UPSTREAM_BASE_URL=http://127.0.0.1:8080/v1
UPSTREAM_MODEL=tiny-llama-1.1b-acl-om
```

切换端口前先确认现有 Qwen 服务已停止且 TinyLlama 的所有硬门通过。网关 token 仍只放在
板端，浏览器和音频服务不接触该 token。网关转发到 TinyLlama 时移除 penalties、`stop`、
`user` 和消息 `name` 等首轮未实现字段；非 greedy 参数或 `max_tokens>32` 直接返回 400。

生产式启动只使用 `run_tinyllama_acl_service.sh`，因为它还会校验 tokenizer ZIP 的固定摘要；
服务本身在启动时继续校验 manifest-bound 的四个解出文件、OM 和 contract。直接调用 Python
入口不会绕过这些解出文件校验，但不应作为板端启动方式。

每次 `acl.mdl.execute` 和整次逐 token 请求共享不超过 50 秒的 best-effort 预算，避免长
prompt 无限占用串行服务。Python `SIGALRM` 在 C 扩展阻塞时不是强制 wall-clock 杀进程机制；
超时后必须检查进程、健康状态和 NPU 资源。板端 smoke 额外使用
`timeout --kill-after=5s`，防止诊断进程在 TERM 无效时残留。

### 6. 可选 ATC/自定义 OPP 分支

预编译 OM 失败时默认状态为 `blocked`。只有单独批准后才运行：

```bash
bash scripts/provision_tinyllama_board.sh download-onnx
CASE9_TINYLLAMA_ALLOW_ATC=1 \
CASE9_TINYLLAMA_ONNX_CONTRACT=admitted \
ASCEND_CUSTOM_OPP_PATH="$HOME/case9-tinyllama/custom-opp" \
bash scripts/provision_tinyllama_board.sh convert
```

`custom-opp` 必须先由审核过的构建流程创建并放在该隔离目录中；脚本不会自动创建或填充
它。ONNX 导出和 OPP 构建必须在隔离构建环境完成；开发板不得安装 Torch/TorchNPU、覆盖系统
OPP 或运行 `apt-get`。ATC 命令固定目标 `Ascend310B4`，完整日志、OM 大小和 SHA-256
必须留存。任何自定义算子、静态 shape、内存或 CANN 兼容性错误都直接记录为阻断。

## 验收门和回滚

| 门 | 证据 | 失败动作 |
| --- | --- | --- |
| G0 工件 | URL/revision、字节数、SHA-256、板端锁 | 隔离未校验文件，停止 |
| G1 环境 | B4、CANN、ACL、无硬禁止包、磁盘；历史额外包单独标记污染 | 不安装替代后端 |
| G2 descriptor | 完整输入输出契约 JSON | 不执行推理 |
| G3 ACL smoke | 有限 logits、合法 token、前后 NPU 快照 | 标记 OM/runtime 失败 |
| G4 API | JSON、SSE、loopback `/v1/models` | 不接网关 |
| G5 网关 | 7861 鉴权转发 | 不恢复音频/XiaoZhi |
| G6 稳定性 | 连续 10 轮、FD/RSS/NPU 观察 | 保留实验性状态 |
| G7 数值一致性 | 外部控制机 ONNX CPU top-k/余弦参考（当前未执行） | 不提升为正式模型 |
| G8 中文质量 | 中文探测集单独人工评分 | 不把英文结果冒充中文能力 |

回滚只停止已识别 TinyLlama PID，并处理 `~/case9-tinyllama`；不删除共享 conda 缓存、
系统 CANN、其他模型或网关文件。完整模板见
[`docs/09-tinyllama-acl-om-validation-record.md`](09-tinyllama-acl-om-validation-record.md)。
