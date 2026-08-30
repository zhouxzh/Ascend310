# Qwen2.5 静态 KV 1024 移植计划

## 文档边界

本文定义 Qwen2.5-0.5B 静态 KV decode 候选的集成边界；实测结果以
[`18-qwen25-static-kv-1024-validation-record.md`](18-qwen25-static-kv-1024-validation-record.md)
为准。
审计日期为 2026-08-23，目标板为 `192.168.1.90`（Ascend310B4/8T，CANN
`8.0.0`）。实施结果已记录在 [`18-qwen25-static-kv-1024-validation-record.md`](18-qwen25-static-kv-1024-validation-record.md)：
板端隔离候选的 ONNX/ATC/OM/ACL/NPU/API/UI 门已通过，中文探测为 8/10；同协议性能
对照改善 48.79%/48.70%，正式入口已受控提升。本文中的预期 shape 和端口仍不能替代验证记录中的实测
descriptor、哈希和原始报告。

旧的 `qwen25-static-1024-last.onnx` 是独立的短上下文 last-logits 候选，已有清单和
`docs/16` 记录，不属于本文的静态 KV 模型。两者不能共用文件名、OM、运行时契约、
端口或测试结论。

## 候选身份

| 字段 | 当前值 |
| --- | --- |
| 网关上游模型 ID | `qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om` |
| ONNX 候选 ID | `qwen2.5-0.5b-instruct-static-kv-1024-fp32` |
| 模型来源 | `Qwen/Qwen2.5-0.5B-Instruct`；ModelScope 传输 revision `13448952dbdab7a1627d0680ecd207535d889a23` |
| 权重源 | `model.safetensors`，988,097,824 bytes，SHA-256 `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| 目标设备 | `Ascend310B4` / `8T` |
| CANN | `8.0.0`（板端 G0 实测） |
| ONNX 文件 | `qwen25-static-kv-1024-v2.onnx`，`1261082122` bytes，SHA-256 `b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e`（控制机和板端均验证） |
| OM 文件 | `qwen25-static-kv-1024-v2.om`，`1,266,010,586` bytes，SHA-256 `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| 当前状态 | `artifact/ATC/descriptor/ACL/NPU/API/UI/formal-promotion-passed; Chinese 8/10` |

清单中的 ONNX `expected_bytes` 和 `sha256` 已由控制机和板端完整单文件检查写入；OM
字段来自板端 ATC 实际输出和锁定记录。不能用模型权重、短上下文 last-logits 图或
OBS/ModelScope ETag 填充 OM 字段。

## 预期静态契约

这是导出和检查阶段必须证明的契约，不是默认信任的运行时常量：

| 张量 | dtype | shape | 语义 |
| --- | --- | --- | --- |
| `input_ids` | `int64` | `[1, 1]` | 当前 decode token |
| `attention_mask` | `int64` | `[1, 1024]` | 固定 cache window；当前位置及其之前为 1 |
| `position_ids` | `int64` | `[1, 1]` | 当前真实位置 |
| `past_key_values.0.key/value` ... `.23.key/value` | `float32` | `[1, 2, 1024, 64]`（每个张量） | 24 层、每层独立 key/value 的 split StaticCache |
| `logits` | `float32` | `[1, 1, 151936]` | 当前 token 的 logits |
| `present.0.key/value` ... `.23.key/value` | `float32` | `[1, 1, 2, 64]`（每个张量） | 当前 token 的增量 cache；按同一 layer/key/value 顺序更新 |

模型参数假设为 24 层、2 个 KV heads、head dimension 64、词表 151,936。输入输出
顺序、名称、byte size、输出是否为完整 cache，必须由 ONNX 图和 OM descriptor 同时
确认；不能按上游项目或数组索引猜测。若实际图使用 FP16、动态维度、全序列 logits、
packed cache、错误 cache 位置或其他输出布局，本文候选即阻断，不能悄悄改写运行时契约。

## 隔离目录和端口

候选批次预留以下板端目录和 loopback 端口：

```text
~/case9-qwen25-kv1024/
├── artifacts/
├── contracts/
├── logs/
├── reports/
└── run/

ACL 服务：  127.0.0.1:8084
候选网关： 127.0.0.1:7867
候选文字 UI：0.0.0.0:7868
```

这些端口用于候选验证；门禁通过后，已将同一工件提升到正式 `8080`、`7861`、`7865`。
候选 PID、旧 `8082/8083/7864/7866` 工件和日志均保留，回滚时只停止经过命令行核对的
正式 PID。所有进程都必须用可识别的命令行和 PID 启动。

## 网关适配边界

网关代码已识别该精确上游 ID，并提供保守的固定上下文适配；这不表示上游已经可用。

* RAG 片段注入被跳过，以免检索文本消耗固定 1024 窗口；公共模型名仍为
  `case9-rag`，密钥只留在板端。
* `max_tokens` 上限为 80；`temperature` 只能为 `0`，`top_p` 只能为 `1`。
  生成至少 16 个 token 后，ACL runtime 在第一个完整句末（中文 `。！？；` 或对应
  ASCII 标点）结束，避免 310B4 为后续重复句子继续逐 token 执行；Qwen 的 EOS 仍优先。
  这是针对 310B4 静态图未及时产生 `im_end` 的边界策略，不是隐式截断或 CPU fallback。
* 网关在没有 tokenizer 的情况下将所有消息字符总量限制为 768；服务端仍必须按
  tokenizer 做真实 token/context 校验，不能把字符数当 token 数。
* `frequency_penalty`、`presence_penalty`、`stop`、`user` 和消息 `name` 不转发，
  因为候选 ACL runtime 尚未实现这些参数。
* `config.py` 对该上游默认单并发；显式 `MAX_CONCURRENT_REQUESTS` 仍由部署配置
  决定，但首轮不允许并发推理。

正式链路的超时预算为：ACL 单请求 `240 s`、网关上游/流 `270 s`、文字界面
`300 s`。外层预算必须大于 ACL 预算，否则网关会在模型仍在执行时先返回超时。

本轮板端复核（2026-08-23）先记录了旧阈值下的候选句末停止边界：候选 ACL `8084` PID
`61313` 对 `你是谁？`、`max_tokens=80` 返回 `prompt_tokens=32`、
`completion_tokens=76`、`finish_reason=stop`，文本后缀为 `回答。`；候选网关
`7867` PID `61990` 省略 `max_tokens` 时得到同样的 76 token/`stop` 结果。候选
SSE 在显式 `max_tokens=32` 时返回完整首句、`finish_reason=stop` 和 `[DONE]`。
正式 ACL/网关/UI 当前分别为 `8080` PID `65088`、`7861` PID `62247`、`7865` PID
`64080`；正式 ACL 显式 `max_tokens=32` 和省略上限的请求均返回 22 token、
`finish_reason=stop`，以 `千问。` 结束；同一请求经正式网关连续转发并发送终止
`finish_reason=stop` 与 `[DONE]`。正式 UI 的 `/api/chat` 也连续发送增量并以完整
`done` 文本收尾。正式 ACL 请求后的健康状态为 `ready=true`、
`device_cache_update=true`、`restart_required=false`。旧 `max_tokens=32` 与 runtime
`96 s` 配置只属于历史修复批次；旧批次中没有完整 finish 证据的 64/80 token 运行不标为
句末停止结果。

2026-08-24 因受控命令中断而恢复过一次正式链路；当前 ACL/Gateway/UI PID 为
`4255/4726/4833`，端口仍为 `8080/7861/7865`。恢复后 ACL `/health` 报告
`max_tokens=80`、`sentence_stop_min_tokens=16`、`device_cache_update=true`，
真实 NPU 请求再次返回 22-token 完整首句，网关和 UI 的终止事件均完整。

正式板端进程当前通过进程环境固定为：
`UPSTREAM_BASE_URL=http://127.0.0.1:8080/v1`、
`UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`、
`RAG_ENABLED=false`、`MAX_CONCURRENT_REQUESTS=1`。仓库 `.env.example` 仍不写入密钥，
候选启动器仍保持隔离，避免普通开发启动误覆盖正式部署。

## 执行顺序

板端恢复后，所有命令在同一 shell 中执行，不从 `base` 或系统 Python 推理：

```bash
cd ~/case9-qwen25-kv1024/src
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
bash scripts/provision_qwen25_kv102_board.sh check
bash scripts/provision_qwen25_kv102_board.sh inspect
bash scripts/provision_qwen25_kv102_board.sh convert
bash scripts/provision_qwen25_kv102_board.sh smoke
```

通过 smoke 后，候选服务可以用两个隔离启动器验证；它们只读取当前 shell 的
`GATEWAY_API_KEY`，不修改正式 `.env`，也不会自动切换 `7861` 或 `7865`：

```bash
bash scripts/run_qwen25_kv_acl_service.sh
bash scripts/run_qwen25_kv102_gateway.sh
bash scripts/run_qwen25_kv102_text_chat.sh
```

对应链路为 `7868 -> 7867 -> 8084`。任何门失败时只停止这三个候选 PID，保留
`~/case9-qwen25-kv1024/logs` 和报告。

`inspect` 前必须把控制机生成的单文件 ONNX、`contracts/qwen25-static-kv-1024-fp32-contract.json`、
`tokenizer.json` 和 `tokenizer_config.json` 放入候选目录，并先完成 bytes/SHA-256 校验；
任一文件缺失或解释器门失败都应停止，不安装替代框架。

### G0：环境冻结

在板端同一 shell 中显式执行 CANN 环境和专用 conda 环境，记录芯片、驱动、CANN、
Python、ACL、磁盘、内存、HugePages、`npu-smi` 和禁止包扫描。板端不得安装
`torch`、`torch_npu`、`torchaudio`、`transformers`、`onnxruntime`、MindSpore、
MindTorch、vLLM、MindIE 或其他替代推理框架；不能修改系统 CANN/OPP。

### G1：控制机导出和完整性

只在 Windows `sci-agent` 环境执行导出，固定 batch=1、静态 1024、FP32 和 tokenizer
revision。下载/生成使用 `.part -> Content-Length -> SHA-256 -> 原子改名`，并生成
ONNX checker、opset、initializer、动态维度和算子白名单报告。external-data、LFS
指针、缺失 sidecar 或大小/hash 不一致，立即停止。

### G2：ATC 和 OM descriptor

把已验证的单文件 ONNX 复制到候选目录后，用当前 CANN 的
`atc --soc_version=Ascend310B4` 转换，保存完整命令、退出码和日志。OM 生成后重新
计算 bytes/SHA-256，再用原生 ACL 读取每一个 descriptor。契约任何一项不符都不执行
推理，不尝试自动 reshape、dtype 转换或输出索引猜测。

### G3：ACL 单 token 和 cache 更新

运行时每个请求创建全零 FP32 cache，初版只使用同步 `acl.mdl.execute`、单进程、单
请求串行。验证首 token logits 的有限性、合法词表 ID、present cache 的 shape/dtype
和下一步输入；验证 EOS、reset、达到 1024 的边界以及异常时的资源释放。禁止跨请求
复用设备缓冲区。

### G4：API 和隔离网关

先验证 `8084` 的 `/health`、`/v1/models`、JSON completion 和 SSE completion，再启动
`7867` 验证 Bearer 鉴权、RAG 跳过、输入/解码限制和上游模型 ID。最后才可启动
`7868` 的文字 UI。任何接口门失败都停止候选，不恢复音频、ASR/TTS 或 XiaoZhi。

### G5：数值、中文和稳定性

控制机 ONNX CPU 参考只用于 token/top-k/余弦对照，板端不装 Torch。板端需完成至少
单 token 对照、中文探测集、连续 10 轮 RSS/FD/NPU 内存观察，并将原始报告路径写入
独立验证文档。NPU 推理成功不等于中文质量通过；中文能力不满足时停止该路线，不
自动替换成另一个模型。

## 门禁和回滚

只有 `artifact_verified`、`contract_verified`、`atc_passed`、`om_descriptor_verified`、
`acl_smoke_passed`、`npu_generation_passed`、`api_passed` 和 `isolated_gateway_passed`
全部有原始证据，才可提出正式网关切换；本批次另完成了中文 8/10、10 轮观察和同协议
性能门，因此已记录正式提升。中文质量仍单独报告，不等同于通用中文能力保证。

失败时只停止识别出的候选或正式 PID；不删除共享 conda 缓存、系统 CANN、
其他模型、旧报告或正式网关文件。不得用 CPU、云端、Torch、MindSpore 或旧
last-logits OM 填补失败门。`Health: Alarm` 只记录为诊断字段，不能单独作为通过或
失败依据。

## 关联记录

* 候选身份和空 hash 字段：[`local_model_manifest.json`](../../../local_model_manifest.json)。
* 已完成的 full-context Qwen2.5 证据：[`docs/15`](15-qwen25-static-onnx-validation-record.md)。
* 独立 last-logits 和短上下文候选：[`docs/16`](16-qwen25-optimization-research-and-last-logits-validation.md)。
* 跨候选门禁索引：[`docs/12`](12-case9-evidence-index.md)。
