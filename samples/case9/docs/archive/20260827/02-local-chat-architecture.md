# 本地中文聊天架构与操作边界

## 服务边界

本地聊天服务是 `local_app.py`，与现有的受鉴权 `app.py` 网关独立运行：

```text
Windows 浏览器
  -> local_app.py :7862
  -> PulseAudio: C922 麦克风 / USB 喇叭
  -> sherpa-onnx: Zipformer 中文 ASR / Huayan VITS TTS
  -> case9 gateway :7861
  -> no-Torch ACL/OM LLM :8081（当前 `.90` 隔离验证；最终迁移才用 :8080）
```

浏览器只发送 WebSocket 控制消息和接收文本状态，绝不调用
`getUserMedia`、`MediaRecorder` 或浏览器音频播放 API。录音和合成 PCM 只在
开发板进程内存和管道中传递，不会写入 WAV、日志或数据库。

`local_app.py` 默认监听 `0.0.0.0:7862`，不需要浏览器 API Key。这是用户选择的
实验网络模式，不是生产配置：同一局域网内能够访问该端口的主机可以要求开发板采集
麦克风或播放喇叭。网页、`/health` 和启动日志都必须显示这一风险提示。

网关令牌不下发给浏览器。`scripts/run_local_chat.sh` 从板端 `.env` 或
`.env.local` 读取 `GATEWAY_API_KEY`，仅用于 `local_app.py -> 127.0.0.1:7861`
的内部 HTTP 请求。

## WebSocket 协议

路径为 `/api/ws`。所有消息都是 JSON；协议不包含音频字节。

| 方向 | 类型 | 关键字段 | 说明 |
| --- | --- | --- | --- |
| 浏览器 -> 服务 | `hello` | 可选 `session_id` | 创建或恢复进程内会话 |
| 浏览器 -> 服务 | `text` | `text` | 发送文本消息 |
| 浏览器 -> 服务 | `ptt_start` | 无 | 开始板端录音 |
| 浏览器 -> 服务 | `ptt_stop` | 无 | 停止录音并识别 |
| 浏览器 -> 服务 | `clear` | 无 | 丢弃当前会话 |
| 服务 -> 浏览器 | `ready` | `session_id`, `warning` | 包含实验网络警告和设备名称 |
| 服务 -> 浏览器 | `state` | `state` | `recording`、`recognizing`、`generating`、`playing`、`idle` |
| 服务 -> 浏览器 | `transcript` | `text` | 仅显示当前轮识别文本，不写入文件 |
| 服务 -> 浏览器 | `delta` | `text` | 网关 SSE 增量文本 |
| 服务 -> 浏览器 | `done` | `text`, `latency_ms` | 一轮完成及非持久化时延统计 |
| 服务 -> 浏览器 | `error` | `code`, `message` | 不泄漏令牌或内部异常详情 |

服务端一个进程同一时刻只允许一个音频操作。断开 WebSocket、超时或异常时必须发送
中断信号终止 `parec` 并释放其设备。PTT 采样固定为 PulseAudio 转换后的 16 kHz、
单声道、S16 little-endian，最大 30 秒；超过该值自动结束。播放采用 22.05 kHz、
单声道、S16 PCM 直接交给 `paplay`。

每个浏览器会话仅保存在 `ConversationStore` 内存中：`text_chat_app.py` 当前最多 4
条消息、700 字符，`local_app.py` 的音频/WebSocket 路径也最多 4 条消息、700 字符；
两者都受网关聚合上限 768 字符和 TinyLlama 固定上下文约束。清空、断开连接或进程
重启后都不保留内容。早期 20 条/12,000 字符的统一默认值只属于历史草案，不适用于
当前 TinyLlama 路径。

## 板端环境与模型

所有本地语音依赖放在独立 conda 环境 `case9-local-chat`（Python 3.9），不能修改
`base` 或 shell 启动文件。运行时设置 `PYTHONNOUSERSITE=1`，防止从 `~/.local` 或
base 环境隐式导入包。

ACL/OM LLM 使用另一个名称固定为 `case9-acl-om` 的 Python 3.9 环境；两个环境不共享
用户 site-packages，也不把语音依赖或历史 XiaoZhi 环境当作 LLM 运行时。

模型来源、不可变 revision、预期大小和已知 SHA-256 记录在
[`local_model_manifest.json`](../../../local_model_manifest.json)。模型、压缩包、锁定清单、
ONNX/OM 工件、ACL 构建产物和验收报告均留在
`$HOME/case9-local-chat`，不提交到 Git。

| 功能 | 固定工件 | 运行位置 | 许可/证据限制 |
| --- | --- | --- | --- |
| ASR | `sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23`（默认 int8 encoder/decoder/joiner） | sherpa-onnx CPU | aarch64 Python 3.9 基线 |
| TTS | `vits-piper-zh_CN-huayan-medium` | sherpa-onnx VITS CPU | 仅私有实验；模型卡数据集许可为 `Unknown`，不得作商业承诺 |
| LLM | TinyLlama ManualReset `tiny-llama.om` | 预编译 OM -> 原生 ACL | 固定社区工件；本板文本/API 实验通过，中文质量未接纳 |

先运行下列显式步骤，脚本不会修改系统启动项，也不会自动回退到 CPU 或云端 LLM：

```bash
export CASE9_DIR="${CASE9_DIR:-$HOME/case9-review-20260822}"
if [[ -d "$CASE9_DIR/src/scripts" ]]; then
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR/src}"
  export CASE9_TINYLLAMA_HOME="${CASE9_TINYLLAMA_HOME:-$CASE9_DIR}"
else
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR}"
fi
cd "$CASE9_SOURCE_DIR"
bash scripts/provision_local_chat_board.sh --install-runtime
bash scripts/check_local_audio_board.sh

# 当前 LLM 门禁必须按顺序执行；这些命令不安装 Torch/inference framework。
bash scripts/provision_tinyllama_board.sh create-env
bash scripts/provision_tinyllama_board.sh install-runtime
bash scripts/provision_tinyllama_board.sh check
bash scripts/provision_tinyllama_board.sh download
bash scripts/provision_tinyllama_board.sh inspect
bash scripts/provision_tinyllama_board.sh smoke
```

语音 `--download-speech` 对缺少发布方 SHA-256 的 GitHub 压缩包，首次受控 TLS 下载后
会将尺寸与 SHA-256 写入板端 `manifest.lock.json`；后续使用必须匹配该锁定值。ACL/OM
下载使用 `local_model_manifest.json` 中固定的 Hugging Face revision、字节数和 SHA-256。
首次下载应保留锁定文件和命令输出作为工件完整性证据。旧 Qwen GGUF 与 llama.cpp 目录
只保留为历史失败证据，不得作为当前 LLM 启动输入。

TinyLlama/ACL/OM 下载脚本本轮只接受 manifest 中的固定 URL，不接受操作员传入的
镜像 URL；网络不可达时应停止并记录失败，不能改写 URL 或跳过 SHA-256。语音脚本的
受控传输覆盖规则只适用于语音压缩包，不能套用到 ACL/OM 模型。

## 历史 Qwen ACL/OM 资源与约束

以下 Qwen ONNX 记录仅用于解释此前的阻断，不是当前启动路径。旧脚本
`provision_acl_om_board.sh` 的 `install-runtime` 默认拒绝执行；除非有单独批准并显式设置
`CASE9_ALLOW_LEGACY_QWEN_ACL_OM=1`，不得运行它。

首轮候选是 `onnx-community/Qwen1.5-0.5B-Chat-ONNX` 的
`onnx/model_fp16.onnx`，revision 为
`6d413dd9a252749e0760902c93331e3e4e65b73c`，预期 `928,499,243` bytes，SHA-256 为
`1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262`。同一 revision
的 `tokenizer.json` 为 `11,418,266` bytes，SHA-256 为
`bcfe42da0a4497e8b2b172c1f9f4ec423a46dc12907f4349c55025f670422ba9`。完整字段和下载
地址在 manifest 中维护。

该 ONNX 是通用 Transformers.js 导出，不是预生成 Ascend OM。`inspect` 必须先检查
opset、所有输入输出、动态维度、past-key/value 布局、外部数据引用和量化算子；只有
契约报告通过后才能在板端运行 `atc --soc_version=Ascend310B4`。不安装自定义 OPP，
不升级系统 CANN，不把通用 ONNX 的 Transformers.js/ONNX Runtime 通过结果当作 ACL
结果。

历史 Qwen ACL 服务计划固定只监听 `127.0.0.1:8080`，模型名为
`qwen1.5-0.5b-chat-acl-om`，batch 固定为 1、上下文上限 2048、首轮 `max_tokens`
上限 128、greedy 解码。它只使用原生 `acl`、NumPy 和 tokenizer 文件，不引入
`transformers`、`torch`、`torch_npu`、`torchaudio` 或 `mindtorch`。单进程串行执行，
异常、取消和断开时释放 ACL model/context/stream/device 资源。

## 启动顺序

当前替换板的 Qwen2.5 文字链路应使用完整复现包中的
`src-board/provision_qwen25_kv102_board.sh` 和 `scripts/run_repro_chain.sh`；
具体路径、哈希、环境前置条件和候选/正式端口边界见
[`20-qwen25-kv1024-reproducibility-bundle.md`](20-qwen25-kv1024-reproducibility-bundle.md)。
下面的 TinyLlama/`.90` 命令仅保留为历史实验记录，不是当前替换板的启动入口。

先在另一个终端启动已通过 TinyLlama ACL/OM 门禁的本地 LLM；历史 `.90` 只绑定 loopback
的隔离端口：

```bash
export CASE9_DIR="${CASE9_DIR:-$HOME/case9-review-20260822}"
if [[ -d "$CASE9_DIR/src/scripts" ]]; then
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR/src}"
  export CASE9_TINYLLAMA_HOME="${CASE9_TINYLLAMA_HOME:-$CASE9_DIR}"
else
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR}"
fi
cd "$CASE9_SOURCE_DIR"
bash scripts/run_tinyllama_acl_service.sh --port 8081
```

检查服务启动日志、`/v1/models` 和 `npu-smi`，再从相同 `.env` 启动受鉴权网关：

```bash
bash scripts/run_xiaozhi_gateway.sh --host 127.0.0.1 --port 7861
```

最后启动本地浏览器服务：

```bash
bash scripts/run_local_chat.sh --host 0.0.0.0 --port 7862
```

当前替换板 `.178` 的文字验证地址为 `http://192.168.8.178:7863/`；音频页面仍规划为
`http://192.168.8.178:7862/`。历史 `.90` 地址和结果仅作为归档证据。启动任一 ACL/OM 环节失败时停止本地 LLM
验收；严禁将该失败伪装为 CPU、云端、Torch 或另一模型的成功。网关通过后才恢复文本聊天，
再进入 ASR/TTS 音频测试。

## 小智隔离

`xiaozhi-esp32-server` 是第二阶段的独立 Python 3.10 服务。它与本地浏览器服务不
应同时做板端音频设备测试。固定源码、OpenAI provider 覆盖配置和设备不可用时的验收
边界见 [`04-xiaozhi-phase2-plan.md`](04-xiaozhi-phase2-plan.md)。
