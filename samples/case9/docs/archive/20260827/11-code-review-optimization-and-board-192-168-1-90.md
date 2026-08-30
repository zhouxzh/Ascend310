# 代码审核优化与 192.168.1.90 板端验证

## 验证范围

本记录对应 2026-08-22 的一次代码审核、修复和板端复核。目标板为
`192.168.1.90`，主机名 `orangepiaipro`，设备为 `Ascend310B4 / 8T`。本次覆盖
网关、文字页面和无 Torch TinyLlama ACL/OM 文本路径；不安装或启动 XiaoZhi、音频、
ASR/TTS、Torch、`torch_npu`、`torchaudio`、MindSpore、Transformers、vLLM 或
ONNX Runtime。板端用户级 `~/.local/lib/python3.9/site-packages` 原先已有
`mindspore==2.4.10`、`onnxruntime==1.19.2` 和 `sentencepiece`；本次没有删除它们，专用服务通过
`PYTHONNOUSERSITE=1` 隔离并在启动时报告该污染。

## 已实施的优化

### 网关和配置

- `/v1` 写请求在读取 chunked body 前统一完成鉴权和对端限流；请求体读取增加独立的
  非排队容量上限，避免慢请求占满内存。
- SSE 解析在收到真正的 `data: [DONE]` 后立即终止；超时、字节上限或上游提前 EOF
  只发送错误事件，不伪造成功终止标记。解析完整事件后才检查预算，合法 DONE 后同
  一网络块的尾部数据会被丢弃。
- JSON/SSE 响应统一使用 `Cache-Control: no-store, private`；非流式上游响应也有
  字节上限。
- 网关 SSE 对每次下游 ASGI 写入设置 30 秒超时；响应头尚未写出、慢读客户端、断开或
  写超时都会关闭上游流并幂等释放单并发槽位，避免一个连接永久占住 NPU 请求。
- 鉴权头先拒绝非 ASCII 字节，再执行常量时间比较；畸形 `Authorization` 统一返回 401，
  不让 Python `hmac.compare_digest` 的 `TypeError` 变成 500。
- `UPSTREAM_BASE_URL` 默认只允许 loopback，并校验主机、端口、凭据、查询和片段；
  非 loopback 必须显式设置 `UPSTREAM_ALLOWED_HOSTS`。
- 空 `RAG_DOCUMENTS_DIR`、非法端口和 TinyLlama 的超预算输入被拒绝；TinyLlama
  网关上限与 ACL 服务统一为 `max_tokens <= 8`，并默认单并发。

### ACL/OM 运行时

- TinyLlama 和旧 ACL 运行时都按 CANN Python 文档释放资源：
  `acl.rt.free -> acl.destroy_data_buffer -> acl.mdl.destroy_dataset`。每个分配记录
  所属 dataset；清理失败时保留无法证明已释放的句柄并要求进程重启。
- 生成结果显式区分 EOS (`stop`) 与长度上限 (`length`)；请求在发送 SSE 响应头前完成
  runtime、tokenizer 和固定上下文预算预检。
- 当时的 TinyLlama/旧 ACL 路线默认生成上限收紧到 8 token；请求仍为 batch 1、greedy、固定 1024 上下文。当前 Qwen2.5 StaticCache 正式服务已单独改为 32 token，见 [`18-qwen25-static-kv-1024-validation-record.md`](18-qwen25-static-kv-1024-validation-record.md)。
- TinyLlama SSE/JSON 写回设置 30 秒客户端写超时；BrokenPipe、连接重置或超时会标记
  连接关闭并调用 runtime cancel，避免慢读客户端长期占用单线程服务。
- ATC 分支要求 ONNX 文件 SHA、静态契约报告 SHA、输入 shape 和板端锁文件全部绑定，
  并在临时目录生成 OM 后原子移动。
- 修正 provisioning 脚本把带 `--hash` 的 PEP 508 requirement 当作单个 pip 参数的
  问题；现在通过临时 requirements 文件安装，并保留 `--no-deps`、`--require-hashes`
  和 binary-only 约束。

### 本地文字聊天和 UI

- 会话使用完整 user/assistant turn 淘汰；生成失败或客户端断开时不提交半个 turn。
- WebSocket 发送失败会取消 LLM/TTS/录音任务并释放资源；HTTP/SSE、健康接口和静态页
  禁止缓存。
- 前端错误/断线时清理残留 assistant stream，处理链路标签改为 `TinyLlama ACL/OM`。
- 针对板端 Python 3.9，在应用工厂中延迟创建 `asyncio.Semaphore`/`Lock`，避免在
  uvicorn/TestClient 建立事件循环前触发 `RuntimeError`。
- 音频服务的启动脚本现在 fail-closed 检查 FastAPI/httpx/sherpa/uvicorn、完整禁止包
  集合和 user-site 隔离；音频本轮仍未启动或验收。
- `local_app` 在每个上游 delta 到达时检查 700 字符上限，超限前不会继续累积或送入
  TTS，也不会提交半个会话 turn；loopback 网关 URL 同时校验 `/v1` 路径和端口。
- `text_chat_app` 对流式回复使用整数累计字符数，避免每个 delta 重算整个回复；
  `LocalSettings` 的直接构造也执行 loopback、端口、TinyLlama 上限和超时校验，只有
  注入测试 LLM 时才允许省略网关 token。
- 音频设置固定采集 `16 kHz`、单声道 S16 和播放 `22.05 kHz`；启动时拒绝不匹配的
  采样率，TTS 返回错误采样率或 `paplay` 长时间无响应时也 fail-closed，避免把
  设备协商结果误当作模型契约或长期占用音频锁。
- 残余边界：TinyLlama 的原生 HTTP handler 在首个 token 写出前无法从同步 ACL 生成循环
  看到客户端断开；当前单次执行仍受 50 秒硬截止限制，属于有界延迟风险，不宣称已完成
  首 token 前取消优化。

## 控制机验证

已执行：

```text
板端 Python py_compile: 通过
Frontend npm test: 4 passed
Frontend npm run build: 通过
Shell bash -n: 通过
git diff --check: 通过
```

控制机没有可用的 Python 解释器（WindowsApps stub），因此没有把控制机语法检查写成通过；
FastAPI/httpx 测试和 `py_compile` 在板端专用环境执行。控制机不运行 CANN、ACL、ATC 或
OM 推理。

## 192.168.1.90 环境证据

SSH 使用 `HwHiAiUser@192.168.1.90` 和 `BatchMode=yes` 成功。只读和专用环境结果：

| 项目 | 实测 |
| --- | --- |
| 主机/架构 | `orangepiaipro`，Linux 5.10，`aarch64` |
| NPU | `Ascend310B4`，8T；`npu-smi 25.2.0` |
| 健康栏 | `Alarm`；仅作诊断记录，不单独阻断 |
| CANN | `/usr/local/Ascend/ascend-toolkit/latest`，运行版本 `8.0.0` |
| Tiny 运行环境 | `case9-acl-om`，Python `3.9.16`，NumPy `1.26.4`，tokenizers `0.19.1` |
| 文字 UI 环境 | `case9-local-chat`，Python `3.9.16`，FastAPI/httpx/uvicorn 纯 Web 依赖 |
| 专用环境扫描 | `PYTHONNOUSERSITE=1` 后两个专用环境均无 Torch、NPU 推理框架或音频推理包 |
| 用户 site 边界 | 既有 `mindspore==2.4.10`、`onnxruntime==1.19.2`、`sentencepiece`；未删除，专用服务显式隔离 |
| 资源 | 根分区约 182 GB 可用，内存约 15 GiB |

板上既有 `base` 环境仍包含 `torch==2.1.0`、`torch_npu==2.1.0.post2` 和
`torchaudio==2.1.0`；它没有用于 TinyLlama 运行时。网关协议回归曾在该污染环境执行，
因此该回归不能替代无 Torch 推理证据。

## 板端执行结果

源码、工件和日志位于隔离目录：
`/home/HwHiAiUser/case9-review-20260822`。没有覆盖系统 CANN 或既有工程。

| 门 | 实测命令/证据 | 状态 |
| --- | --- | --- |
| G0 工件 | OM `1493077371` bytes，SHA-256 `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967`；tokenizer ZIP `709459` bytes，SHA-256 `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` | 通过 |
| G1 环境 | CANN 8.0、ACL、Python 3.9、NumPy/tokenizers；运行时禁止包为空，用户 site 另有既有 MindSpore/ONNX Runtime/sentencepiece 且被隔离 | 通过（隔离条件） |
| G2 descriptor | `reports/tinyllama-acl-contract.json`；4 个静态输入和 3 个输出与 OM descriptor 一致 | 通过 |
| G3/G4 ACL smoke | `reports/20260822T111328Z-tinyllama-smoke.log`；中文 prompt、8 token greedy，`finish_reason=length` | 通过 |
| G7 TinyLlama API | loopback `127.0.0.1:8081` 的 `/v1/models`、普通 JSON completion、SSE completion | 通过 |
| G7 网关转发 | `127.0.0.1:7861` health/models、Bearer 鉴权 JSON/SSE 转发 | 通过（当前 `case9-local-chat`；早期污染 base 结果仅作历史记录） |
| G7 文字页面 | `0.0.0.0:7863` health/config/static、SSE `/api/chat`、history/clear | 通过（`case9-local-chat`） |
| Web/协议回归 | `case9-local-chat` + `PYTHONNOUSERSITE=1`：显式运行 gateway/config/local_app/local_session/text_chat/retrieval/audio/ACL 协议与静态测试；`20260822T2103Z-web-tests.log`，`104` 通过、`1` 跳过 | 通过（含新网关写超时/鉴权边界回归；不是额外 NPU 质量证据） |
| Tiny ACL 回归 | `case9-acl-om` + CANN：`tests.test_tinyllama_acl`，`20260822T2050Z-tinyllama-tests.log`，`26` 通过；日志中的 ACL cleanup traceback 是故障注入测试的预期输出 | 通过（运行时/契约回归） |

ACL smoke 的输出以英文为主（示例为 `Sure, I'd be`），这证明执行链路，不证明中文
能力。`npu-smi` smoke 前后均报告 `Ascend310B4`、`Health: Alarm`，设备内存约从
5728 MB 变为 5738 MB；这是诊断快照，不足以推出长期稳定性。

测试必须按职责分环境执行：`case9-local-chat` 没有 NumPy/ACL，`case9-acl-om`
没有 FastAPI/httpx。部署目录还保留了早期同步产生的根级测试副本；不要使用
`unittest discover -s .` 作为总数证明。此前缺少同步文件的失败日志仍保留在
`reports/20260822T1941Z-web-tests.log` 和 `reports/20260822T1944Z-web-tests.log`，不纳入通过统计。

报告文件名中的 `Z` 是本轮报告编号沿用的标记，不应当作 UTC 时间戳；板端实际时间和
文件修改时间以报告正文的 `date -Is`、`stat` 为准。最新 `2103Z` 报告正文记录了板端
`py_compile` 和 `Ran 104 tests ... OK (skipped=1)`。TinyLlama 专用环境另行运行
`20260822T2050Z-tinyllama-tests.log`，结果为 `Ran 26 tests ... OK`；其中 ACL cleanup
故障注入 traceback 是预期输出。

代码同步后的旧实时冒烟报告为 `reports/20260822T2040Z-live-http-smoke.log`、
`reports/20260822T2050Z-live-post-restart.log` 和
`reports/20260822T2055Z-live-post-fix.log`；最后一次鉴权加固重启后的完整实时证据为
`reports/20260822T2105Z-live-final.log`：Tiny `/health` ready、网关鉴权
`/v1/models`、非 ASCII 鉴权 401、文字 `/health`/`/api/config`、JSON/SSE 请求均通过；
`/api/config` 未泄露网关 token。中文探测实际返回 `我是�����`，而英文请求返回 `Sure, I'd be`，
所以仍只证明协议和 NPU 执行链路，不证明中文质量。

## 当前判定和边界

当前 `.90` 状态为：

```text
artifact_verified
descriptor_verified
acl_smoke_passed
api_passed
```

TinyLlama 服务本轮留在隔离端口 `127.0.0.1:8081`，没有切换 `8080`，避免覆盖其他
服务。网关和文字页面分别验证了 `7861`、`7863`；页面明确是未鉴权实验服务，同网段
主机可以提交文字。若要对外提供服务，必须先限制防火墙来源并替换测试 token。

当前进程为 TinyLlama ACL `127.0.0.1:8081`（PID `14411`）、网关
`127.0.0.1:7861`（PID `18926`）和文字页面 `0.0.0.0:7863`（PID `15739`）。页面
访问地址为 `http://192.168.1.90:7863/`；`8080` 没有被占用或切换。

尚未通过或未执行：中文质量探测、连续 10 轮资源稳定性、音频 ASR/TTS、真实小智设备
协议和 XiaoZhi 服务端部署。不得用本次文本 API 结果代替这些验收。失败日志、哈希和
NPU 快照必须保留在板端；不自动切换 CPU、云端、Torch 或其他模型。

资源释放顺序依据
[CANN 8.0 AscendCL Python 文档](https://www.hiascend.com/document/detail/en/canncommercial/800/appdevg/aclpythondevg/aclpythondevg_0032.html)。
