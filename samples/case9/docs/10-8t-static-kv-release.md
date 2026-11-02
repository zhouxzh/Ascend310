# 可选Ascend310B4静态KV后端

本文记录Ascend310B4/8T单独锁定的PyACL/静态KV后端。在Ascend310B4/8T和Ascend310B1/20T上，默认Case9聊天程序均使用共享MindSpore工作进程和Qwen1.5-0.5B模型。静态KV是一种替代推理实现，具有自己的ONNX转OM制品、tokenizer、张量契约、序列化KV缓存执行、输出限制和验收证据；其结果不会替代或继承MindSpore证据。

## 发布标识

| 项目 | 固定值 |
| --- | --- |
| 目标 | Ascend310B4 / 8T |
| CANN | `8.0.0` |
| Python | `3.9.25`、`cpython-39-aarch64-linux-gnu` |
| 运行时 | 原生PyACL，无CPU或云端回退 |
| 公共模型制品仓库 | `zhouxzh/ascend310b-llm-om-zoo` |
| 制品修订 | `0f516070e1c0566e3c652d43c488cd3cfbad89a3` |
| 基础模型 | `Qwen/Qwen2.5-0.5B-Instruct` |
| 基础模型修订 | `7ae557604adf67be50417f59c2c2f167def9a775` |
| 许可证 | Apache-2.0 |
| 上下文 | 1024个令牌 |
| 默认输出预算 | 64个令牌 |
| 浏览器输出选项 | 64或80个令牌 |
| 最大输出预算 | 80个令牌 |
| 公共模型名称 | `ascend-edge-chat` |
| 内部ACL模型ID | `qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om` |

不可变字节数、SHA-256值和源路径位于[`release-8t-static-kv.lock.json`](../configs/release-8t-static-kv.lock.json)。所有公共制品通过校验后，服务会在数据目录内派生schema-2运行时锁。

## 必需制品

发布包下载九个文件：

- 静态KV ONNX图；
- tokenizer及tokenizer配置；
- 控制器/导出契约；
- B4 `CANN 8.0` OM；
- B4描述符契约；
- 公共tokenizer锁和schema-1 OM来源锁；
- 上游Apache-2.0许可证。

下载器不接受调用方提供的任何URL或目标路径。它只使用`https://hf-mirror.com`，禁止路径穿越和符号链接，通过`.part`文件写入，并在重命名前校验字节数和SHA-256。

## 安装依赖

所有进程均使用统一的`ascend`环境。将哈希锁定的8T依赖闭包安装到经审查的本地wheelhouse：

```bash
cd /opt/ascend-edge-chat
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
python -m pip install --no-deps .

export ASCEND_EDGE_CHAT_WHEELHOUSE=/opt/ascend-edge-chat-wheelhouse
export ASCEND_EDGE_CHAT_REQUIREMENTS_FILE=/opt/ascend-edge-chat/configs/release-8t-service-requirements.txt
bash scripts/verify/install-service-dependencies.sh
```

不要从`base`或`~/.local`运行案例程序。CANN和NPU驱动仍是开发板自有的系统组件。

## 配置

```bash
cp configs/release-8t.env.example .env
chmod 600 .env
```

只替换占位网关令牌。重要的固定值如下：

```text
ASCEND_EDGE_CHAT_BACKEND=acl-static-kv
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8084/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om
ASCEND_EDGE_CHAT_UI_WORKER_MODE=external
ASCEND_EDGE_CHAT_UI_WORKER_HEALTH_URL=http://127.0.0.1:8084/health
```

网关会拒绝指向`8090`的ACL后端、指向`8084`的MindSpore后端，或内部模型ID不匹配的情况。浏览器仍只能看到`ascend-edge-chat`。

## 获取并校验

```bash
export ASCEND_EDGE_CHAT_ENV_FILE=/opt/ascend-edge-chat/.env
bash scripts/import/download-static-release.sh
bash scripts/verify/release-8t-preflight.sh
```

`release-8t-preflight.sh`在任何服务启动前执行以下门：

1. 激活`ascend`并加载CANN；
2. 要求Python `3.9.25`并禁用用户site导入；
3. 导入PyACL、FastAPI、HTTPX、Pydantic和Uvicorn，然后要求经审查的wheelhouse依赖闭包提供`numpy==1.26.4`和`tokenizers==0.19.1`；
4. 从`npu-smi`识别`Ascend310B4`；
5. 校验全部九个公共制品并派生运行时锁；
6. 校验网关和UI后端契约。

## 可选独立启动

历史独立ACL验收仍可用其单独环境复现，但它不是默认的Case9应用路径。仅在明确测试独立PyACL模式时运行前台启动器：

```bash
bash scripts/run/run-static-kv-standalone.sh
```

它会启动：

```text
ACL static-KV :8084
Gateway       :7861
UI            :7865
```

仅在可信局域网上打开`http://<board-ip>:7865/`。UI没有浏览器登录。网关密钥保留在服务器侧。`Ctrl+C`通过有界的TERM/KILL清理停止全部三个进程。

仅当ACL健康响应满足以下条件时，UI才报告就绪：

```text
ready=true
healthy=true
model=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om
backend=acl_om
sequence_length=1024
descriptor_validated=true
```

该后端禁用模型切换。

## API冒烟测试

```bash
source .env
curl -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  http://127.0.0.1:7861/v1/models

curl -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"ascend-edge-chat","messages":[{"role":"user","content":"你好"}],"max_tokens":4}' \
  http://127.0.0.1:7861/v1/chat/completions
```

响应必须使用`model=ascend-edge-chat`；内部ACL模型ID不得越过网关边界。

## 机器验收

从公共源码提交的干净检出运行：

```bash
ASCEND_EDGE_CHAT_MODEL_SOURCE=hf \
  bash scripts/verify/run-release-8t-acceptance.sh
```

报告包括：

- 源码仓库和当前公共`main`提交；
- 干净的Git状态；
- 公共HF仓库和固定修订；
- 每个已下载制品的字节数和SHA-256；
- 统一环境和`npu-smi`输出；
- 网关身份验证和模型名称边界；
- JSON和SSE补全；
- 描述符和KV缓存锁证据；
- 中文探针、长度检查、稳定性请求和实测延迟。

静态图每次ACL调用执行一个令牌。其延迟预计远高于20T MindSpore路径，必须单独记录。

在源码提交`c11c96eb625403a2e160792c6428f2eaa341018b`上的历史公共验收为该独立B4静态KV后端返回了`machine_status=passed`。它校验了九个HF制品、网关/UI、JSON/SSE、输出预算8/16/32/64、稳定性请求和中文机器探针。这些测量仅适用于PyACL静态KV图，不得当作MindSpore结果进行比较。中文语义人工审查仍待完成。

一个单独的输出预算探测使用`max_tokens=80`询问固定问题`请简单介绍一下昇腾310B`。它使用38个提示令牌，返回41个补全令牌和54个字符，并以`finish_reason=stop`结束。这表明配置上限能够生成完整段落，而不只是名义限制。它不能证明语义质量：小模型重复了句子的一部分。新的64令牌默认值不会截断这次实测的41令牌响应，但更长回答仍可能以`finish_reason=length`结束。

240秒执行看门狗是应用层的失败关闭预算，不是B4内存规格。使用位于`max_tokens=1024`和`execution_timeout=3600`的临时测试工作进程时，128和256个补全令牌均完成，同时保持`watchdog_triggered=false`；256个令牌耗时约1984秒，NPU使用量接近`5.9 GB`。发布版本仍限制为80，因为按当前产品看门狗，80个令牌已经耗时约225秒。对于55个令牌的提示，1024令牌上下文上限为969个输出令牌，但该完整生成尚未完成，也未作为发布限制宣传。

## 失败处理

| 失败 | 停止条件 |
| --- | --- |
| 源码树不干净或不在公共`main`上 | 不得声称完成公共发布复现 |
| HF制品字节/SHA不匹配 | 不得下载、转换、加载或提供服务 |
| `npu-smi`未报告Ascend310B4 | 停止；不得使用B1/20T OM |
| 直接使用schema-1上游OM锁 | 停止；使用派生的schema-2运行时锁 |
| 描述符不是51个输入/49个输出 | 在ACL执行前停止 |
| UI外部健康状态降级 | 不发送聊天请求；先修复ACL工作进程 |
| 任何模型切换尝试 | 拒绝；外部ACL工作进程固定 |
| 模型来源为本地或未提交 | 标记为`blocked`，而不是`observed-pass` |

## 限制

该可选后端不声称具备连续生产可用性、同时多模型加载、语音支持、检索或人工批准的中文回答质量。其静态KV吞吐量必须与共享MindSpore后端分开报告，任一板的测量结果都不得转移到另一SoC。
