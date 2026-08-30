# 文字聊天测试界面

## 用途

`text_chat_app.py` 是一个不依赖音频、ASR、TTS、Torch 或 Ascend Python 包的
FastAPI 文字测试页面。它只验证下面这条链路：

```text
浏览器 -> text_chat_app :7863 -> case9 网关 :7861 -> TinyLlama ACL/OM :8081（当前隔离验证）
```

网关 API Key 由服务端环境变量读取，绝不会下发到浏览器。页面本身没有鉴权，
默认绑定 `0.0.0.0`，因此只能在可信实验局域网使用。它和带麦克风/喇叭的
`local_app.py :7862` 是两个独立进程，不应同时把它们绑定到同一个端口。

## 启动

在开发板上先确认网关和 TinyLlama 服务已经运行，然后使用已经存在的 Python
环境启动，不安装任何新推理框架：

```bash
export CASE9_DIR="${CASE9_DIR:-$HOME/case9-review-20260822}"
if [[ -d "$CASE9_DIR/src/scripts" ]]; then
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR/src}"
  export CASE9_TINYLLAMA_HOME="${CASE9_TINYLLAMA_HOME:-$CASE9_DIR}"
else
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR}"
fi
cd "$CASE9_SOURCE_DIR"
export TEXT_CHAT_GATEWAY_API_KEY="$GATEWAY_API_KEY"
bash scripts/run_text_chat.sh --host 0.0.0.0 --port 7863
```

也可以先加载 `.env` 和 `.env.local`，脚本会自动读取这两个文件。默认浏览器地址：

```text
http://192.168.8.178:7863/
```

脚本在激活 conda 前读取这两个文件，因此可以在 `.env.local` 中设置
`TEXT_CHAT_CONDA_ENV` 或 `CONDA_PROFILE`。启动前会扫描并拒绝
`torch`、`torch_npu`、`torchaudio`、`mindtorch`、`mindspore`、`transformers`、
`vllm` 等禁止推理包；它不会自动安装或替换任何依赖。

`GET /health`、`GET /api/config` 和 `GET /api/history` 可用于检查服务状态；
`POST /api/chat` 接收 `{"message":"...","stream":true}` 并返回浏览器使用的
SSE；设置 `stream:false` 时返回聚合 JSON。`POST /api/clear` 清除当前浏览器的
内存会话。请求体默认限制为 65,536 字节，读取超时为 15 秒；可通过
`TEXT_CHAT_MAX_BODY_BYTES`、`TEXT_CHAT_BODY_TIMEOUT_SECONDS` 和
`TEXT_CHAT_WRITE_TIMEOUT_SECONDS` 调整，但服务端仍会拒绝超过代码定义上限的值。
`/health` 只表示页面进程存活；网关/NPU 是否可用必须以一次实际 `/api/chat` 请求为准。

## 会话和边界

会话通过 HttpOnly cookie 标识，服务端最多保留 128 个会话；为适配 TinyLlama OM 的
1024-token 上下文，默认每个会话最多 4 条消息、700 个字符。网关对 TinyLlama 的
聚合输入硬上限为 768 字符，页面保留少量模板和配置余量。WebSocket 单帧默认限制 64 KiB，超限帧以 1009 关闭。重启或清空后内容丢失；浏览器 cookie 过期后不会再带回旧会话，服务端
会在达到 128 个会话上限时淘汰最旧条目。内容不写入文件、数据库或日志。单进程只允许
一个生成请求，并且同时最多读取 8 个请求体，以匹配板端 TinyLlama ACL runtime 的
串行约束。SSE 每次写入也有独立超时；客户端停止读取时服务会中断该响应并释放
推理槽位。该端口仍未启用认证或完整 DDoS 防护，只适合可信实验网络。

此页面通过网关的 SSE 进行文本验证，不代表中文质量、音频闭环或 XiaoZhi 设备协议
已经通过。TinyLlama 当前中文可理解性仍标为未通过，详见
[`09-tinyllama-acl-om-validation-record.md`](09-tinyllama-acl-om-validation-record.md)。
页面固定调用公开模型 `case9-rag` 和 loopback 网关路径 `/v1`；网关密钥必须是至少
24 个 ASCII 字符的服务端 token。TinyLlama 上游路径在网关中明确跳过 RAG 注入，以免
未经 tokenizer 预算的知识库片段耗尽 OM 上下文；其他上游模型仍可使用网关的 RAG。

## 本地检查

在控制机不需要 CANN 或模型文件即可运行纯 Python 测试：

```bash
python -m py_compile text_chat_app.py
python -m unittest tests.test_text_chat_app -v
```

## 板端实测记录

当前替换板地址为 `192.168.8.178`；历史 `.90` 的复核记录见
`docs/11-code-review-optimization-and-board-192-168-1-90.md`，历史页面结果不能替代当前板验收。
2026-08-21 的 `.178` 页面记录属于早期部署批次；本次替换板新证据见
`docs/20-qwen25-kv1024-reproducibility-bundle.md`。
历史一轮验证时间为 `2026-08-21T12:25:19Z`：

| 检查 | 结果 |
| --- | --- |
| 页面/健康接口 | `GET /`、`/health`、`/api/config` 返回 200；响应带 `Cache-Control: no-store` |
| 服务进程 | `text_chat_app.py --host 0.0.0.0 --port 7863`，当前 PID `36389` |
| 下游进程 | 历史板 PID `35118`/`35906`；历史三端口分别为 7863、7861、8080 |
| 实际文本请求 | `POST /api/chat` 返回 SSE，观察到 `start`、单个完整 `delta` 和 `done`，耗时约 26.5 秒；无重复片段或 `U+FFFD` |
| 禁止包扫描 | `torch`、`torch_npu`、`torchaudio`、`mindtorch`、`transformers`、`vllm` 均未发现 |
| 输出质量 | NPU/API 链路通过；TinyLlama 中文质量仍未通过，不能宣称中文聊天可用 |

原始板端证据保留在：`/tmp/case9-text-chat.log`、
`/tmp/case9-text-chat-smoke-final-20260821T1225Z.sse`；此次请求退出码为 `0`。
TinyLlama runtime/service 的流式修复源文件分别为
`~/case9-tinyllama/source/tinyllama_acl_runtime.py` 和
`~/case9-tinyllama/source/tinyllama_acl_service.py`，把完整 tokenizer 解码作为一个
稳定 SSE 增量发送；这验证了协议语义，不等同于中文质量门通过。

板端运行源文件 SHA-256：`text_chat_app.py`
`77f2016ab94f366050b01fa3aeeaf7a1bd7069726432d244da9384c6312f38dd`，
`local_session.py` `4438f9af439929b9aed91539d5170ab4cdf267fc69cc7cdc11324053f7d02965`，
`upstream.py` `cbb9fd55ff64f82868682267b069d7a9f45ae448b8002d8375d0be8dc773f4e9`。
