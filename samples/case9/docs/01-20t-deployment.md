# 20T部署

## 前置条件

仅在已验证的20T硬件档上使用此流程。该流程不安装驱动、固件、CANN工具包、OPP包、MindSpore或MindNLP。本流程必须使用仓库统一的Conda环境`ascend`；不得回退到`base`或用户站点包。

```bash
cd /opt/ascend-edge-chat
export ASCEND_EDGE_CHAT_CONDA_ENV='ascend'
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate "$ASCEND_EDGE_CHAT_CONDA_ENV"
python -m pip install --no-deps .
export ASCEND_EDGE_CHAT_DATA_DIR=/opt/ascend-edge-chat-data
cp configs/release.env.example .env
chmod 600 .env
```

编辑`.env`，并将`ASCEND_EDGE_CHAT_GATEWAY_API_KEY`替换为一个至少24个字符的随机ASCII令牌。不要将令牌放入浏览器、客户端脚本、提交或shell历史记录捕获中。

网关、工作进程和UI启动脚本在`./.env`可读时会自动加载该文件。设置`ASCEND_EDGE_CHAT_ENV_FILE`可使用其他私有环境文件。该文件在激活conda/CANN之前加载，并且必须保持`0600`权限模式；文件中的显式赋值优先于同名shell变量，因此启动服务前请先审查该文件。浏览器UI没有身份认证：可达的局域网客户端可以提交聊天，并且在启用语音模式时可读取经过脱敏的语音状态或请求允许列表内的板端音频控制。请将端口`7865`保持在可信局域网内。

## 服务依赖闭包

工作进程必须在没有用户站点包的情况下运行。如果预检报告缺少FastAPI、Pydantic、Uvicorn或dotenv，请在已连接网络的机器或板卡上将已审查的wheel下载到普通目录，对照`configs/release-service-requirements.txt`核验，然后仅从该本地wheelhouse安装：

```bash
export ASCEND_EDGE_CHAT_WHEELHOUSE=/opt/ascend-edge-chat-wheelhouse
bash scripts/verify/install-service-dependencies.sh
```

安装器使用`--no-index --no-deps --require-hashes`；它既不会升级CANN/MindSpore，也不会安装模型或其他推理框架。请将这次服务层安装记录到部署证据中。

## 环境检查

```bash
bash scripts/verify/release-preflight.sh
```

除非活动进程使用统一的`ascend`环境，且Python版本为`3.9.25`、ABI为`cpython-39-aarch64-linux-gnu`、设置了`PYTHONNOUSERSITE=1`，并具备PyACL以及锁定版本的MindSpore/MindNLP和CANN，否则预检会故障关闭。随后，它会记录活动导入来源、显示`npu-smi info`、校验每个锁定的模型产物并解析网关配置。如果预检失败，请在此停止。导入成功不等于生成结果；后续步骤将执行API验证。

## 模型安装

通过已配置的Hugging Face镜像在线获取：

```bash
bash scripts/import/download-model.sh
export ASCEND_EDGE_CHAT_MODEL_SOURCE=hf
```

使用仅包含锁定文件名的离线源目录：

```bash
bash scripts/import/import-model.sh /mnt/offline/qwen1.5-0.5b
export ASCEND_EDGE_CHAT_MODEL_SOURCE=offline-bundle
```

两种方式都会写入隐藏的`.part`文件，检查声明的字节数和SHA-256，然后原子重命名。不完整或不匹配的文件会被删除；已验证的现有文件会保留。离线来源还必须包含`offline-bundle.manifest.json`，其中声明`offline-bundle`、仓库、修订、许可证以及每个产物的字节数/SHA-256。离线来源信息仍标记为`offline-bundle`；它不能作为在线Hugging Face下载的证据。

## 启动与停止

在部署目录中打开三个板端终端：

```bash
# 终端1
bash scripts/run/start-worker.sh

# 终端2
bash scripts/run/start-gateway.sh

# 终端3
bash scripts/run/start-ui.sh
```

三条命令都故意在前台运行。请在其所属终端中使用`Ctrl+C`停止已识别的进程。不要终止无关的NPU进程，也不要使用宽泛的进程名匹配。

工作进程和网关仅绑定环回地址。UI绑定`0.0.0.0:7865`以允许可信局域网访问。教程UI有意不设浏览器登录：可达的局域网客户端可以提交聊天，并且在启用语音功能时可读取经过脱敏的语音状态或请求允许列表内的板端音频模式/设备变更。请使用防火墙或VLAN限制对端口`7865`的访问。

## 健康检查

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:7861/health
curl -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  http://127.0.0.1:7861/v1/models
curl -N -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:7861/v1/chat/completions \
  -d '{"model":"ascend-edge-chat","stream":true,"messages":[{"role":"user","content":"请用一句中文介绍你自己。"}],"temperature":0,"top_p":1,"max_tokens":16}'
```

然后浏览到`http://<board-ip>:7865/`并提交一条简短提示。UI健康载荷包含`worker.reachable`、`worker.ready`和`worker.healthy`；仅`:7865`或`:7861`的HTTP可达并不代表模型就绪。模型一次运行一个请求；第二个请求会收到有界的忙碌响应，而不是无界排队工作。加载模型前，工作进程还会检查特定档位的`npu-smi` HBM统计。该预检会在已知高占用板卡上提前失败；通过预检只是加载前置条件，并不保证分配器能容纳每个配置档。

## 2026-10-18服务验证

已运行的20T服务通过浏览器访问路径，以活动教学配置档`qwen2-0.5b-instruct-mindspore`完成验证。UI的`/api/config`和`/api/chat`返回HTTP 200，网关仅接受配置的Bearer令牌和公共模型，服务完成了JSON、SSE、8/16/32/64个token的长度检查、十次短稳定性请求和十次中文探测。带日期的原始验收报告保留在板端，作为被Git忽略的部署证据。

首次无间隔运行触发了`HTTP 429 Too many request bodies are being read`。这是由测试客户端突发请求造成的准入边界观测，不是NPU错误。在复现验收运行时，请在请求之间加入延迟，并等待流式清理完成。32/64预算标签表示tokenizer token数，而不是中文字符数。人工中文语义审查仍处于pending状态。

## 输出token与超时验证

发布配置档`qwen1.5-0.5b-chat`默认输出128个token，上限为256个token。在`2026-10-25`，一个包含171个提示token的重复模式探测生成了全部256个输出token，耗时`143.984 s`，返回`finish_reason=length`，且工作进程保持健康。

一个临时测试检出副本调高了配置档和网关上限，以测试1024-token上下文边界。它以NPU峰值`14,444 / 23,673 MB`在`507.982 s`内生成了850个token，随后在`483.612 s`内生成了853个token，且`total_tokens=1024`。后者是该提示的精确上限：

```text
171 prompt tokens + 853 completion tokens = 1024 context tokens
```

临时测试随后停止，已跟踪文件也已恢复。这些较大数值是容量证据，不是发布默认值。

MindSpore使用独立的默认300秒看门狗，最长可配置到600秒。它独立于8T ACL运行时的240秒限制。不要把8T static-KV超时或OM限制复制到20T部署中。

## 回滚

本次发布不会自动替换先前服务。要回滚，请仅停止三个前台发布进程，保留模型文件和日志用于诊断，并从先前已验证部署自己的目录启动该部署。不要删除共享Conda缓存、CANN安装目录或未知的板端工作负载。
