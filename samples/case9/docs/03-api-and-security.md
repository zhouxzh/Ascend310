# API与安全

## 网络边界

| 服务 | 绑定地址 | 用途 | 身份验证 |
| --- | --- | --- | --- |
| 文本UI | `0.0.0.0:7865` | 在可信局域网中进行浏览器聊天 | 无 |
| 网关 | `127.0.0.1:7861` | 公共OpenAI兼容契约 | Bearer令牌 |
| MindSpore工作进程 | `127.0.0.1:8090` | 内部20T推理 | 无法通过网络访问 |
| ACL static-KV工作进程 | `127.0.0.1:8084` | 内部8T推理 | 无法通过网络访问；固定模型 |

UI有意不进行身份验证。这是部署约束，而不是访问控制特性：同一可达网络中的主机可以提交消息、读取经过清理的语音状态，并在语音模式启用时通过UI代理请求允许列表内的板端音频模式/设备变更。语音代理自身的HTTP监听器仍仅限回环地址，任意设备路径或命令都会被拒绝。不要对UI做端口转发，不要将其置于公共反向代理之后，也不要在不可信网络上使用它。

## 网关身份验证

在`.env`中设置服务器端密钥：

对于20T MindSpore后端：

```dotenv
ASCEND_EDGE_CHAT_GATEWAY_API_KEY=replace-with-a-random-long-token
ASCEND_EDGE_CHAT_BACKEND=mindspore
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8090/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=ascend-edge-chat-worker
```

对于固定8T ACL后端：

```dotenv
ASCEND_EDGE_CHAT_GATEWAY_API_KEY=replace-with-a-random-long-token
ASCEND_EDGE_CHAT_BACKEND=acl-static-kv
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8084/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om
```

网关只读取`ASCEND_EDGE_CHAT_`变量。它会拒绝端口`8090`上的ACL后端、端口`8084`上的MindSpore后端，以及任何不匹配的内部模型ID。旧式无前缀变量不会配置此发布版本。密钥必须是由至少24个字符组成的ASCII令牌。UI只从其服务器端进程环境读取密钥，绝不会在HTML、JSON或浏览器JavaScript中返回该密钥。

## OpenAI兼容端点

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
```

`GET /v1/models`和`POST /v1/chat/completions`需要：

```http
Authorization: Bearer <ASCEND_EDGE_CHAT_GATEWAY_API_KEY>
```

非流式请求示例：

```bash
curl http://127.0.0.1:7861/v1/chat/completions \
  -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "ascend-edge-chat",
    "messages": [{"role":"user","content":"你好，请介绍自己。"}],
    "temperature": 0,
    "top_p": 1,
    "max_tokens": 32
  }'
```

此发布版本接受`system`、`user`和`assistant`角色。它只接受贪心解码（`temperature=0`、`top_p=1`），一次处理一个请求，并使用1024令牌的上下文。20T MindSpore路径默认输出128个令牌，上限为256个令牌，具体取决于所选配置档。8T ACL UI只提供64或80个输出令牌，默认值为64，上限为80；较低的API值仅用于诊断。它会拒绝后端/模型/端口交叉接线、非回环上游、任意路径和任意表达式。8T UI禁用模型切换。

对于`stream:true`，网关会发出标准`data:` SSE数据块和一个终止`data: [DONE]`。在将结果发送给客户端之前，它会把内部工作进程模型标识符改写为`ascend-edge-chat`。

## 资源与失败限制

- 请求体：最大256 KiB；
- 浏览器消息历史：服务器内存中最多保留四条消息和4,000个字符；
- 网关和工作进程：仅一个在途请求；
- MindSpore工作进程看门狗：默认300秒，最高可配置为600秒；
- ACL执行看门狗：最长240秒，这是应用层的失败关闭策略，而不是由硬件推导出的令牌限制；
- 浏览器写入超时：30秒；
- 工作进程错误或超时：失败关闭，不回退到CPU、云端或其他模型。

会话内存仅存在于进程本地。清除浏览器会话、重启UI或停止UI都会将其丢弃。服务不会创建消息数据库。
