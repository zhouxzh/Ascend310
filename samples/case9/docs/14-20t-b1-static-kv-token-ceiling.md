# 20T B1静态KV Token上限

## 测试范围

本记录在当前`Ascend310B1 / 20T`板上测量`qwen2.5-0.5b-instruct-static-kv-1024-b1-offline` PyACL候选。该候选是经操作员授权的`offline-experimental`候选，不是公开Hugging Face复现，也不是8T正式ACL发布。

实验在`127.0.0.1:8086`上启动独立的回环ACL worker。它不使用UI、网关或supervisor端口`7865`、`7861`和`7863`。无论实验通过还是失败，runner在退出时都会终止这个隔离worker。

| 项目 | 实测值 |
| --- | --- |
| 板卡 | Ascend310B1 / 20T |
| CANN/驱动 | 8.0.0 / 25.2.0 |
| Python环境 | `ascend`，Python 3.9.25，`PYTHONNOUSERSITE=1` |
| 模型 | Qwen2.5 0.5B fixed-KV，1024 Token上下文 |
| OM | 1,266,009,438字节，`6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609` |
| OM契约 | 51个输入/49个输出；controller `fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498`；runtime `3ff15ccf682e85e32f521417aa7204347ff5ab4fb0608edd18f3c9a639af3c21` |
| ACL实例请求上限 | 80 Token |
| 候选来源 | 离线实验性；ACL加载前重新核验当前板上的产物和契约哈希 |

## 渐进请求上限

隔离runner使用同一个中文提示逐一请求表中列出的每个`max_tokens`值。每个请求都返回HTTP 200。每个响应都在生成5个完成Token后自然停止，因此这一系列测试确定的是可接受的请求上限，而不是已观测到80个Token的完成结果。

| 请求的最大Token数 | HTTP | 完成Token数 | 结束原因 | 耗时（秒） | 请求后健康状态 |
| --- | --- | --- | --- | --- | --- |
| 8 | 200 | 5 | `stop` | 11.371 | ready and healthy |
| 16 | 200 | 5 | `stop` | 11.125 | ready and healthy |
| 24 | 200 | 5 | `stop` | 11.141 | ready and healthy |
| 32 | 200 | 5 | `stop` | 11.110 | ready and healthy |
| 40 | 200 | 5 | `stop` | 11.141 | ready and healthy |
| 48 | 200 | 5 | `stop` | 11.189 | ready and healthy |
| 56 | 200 | 5 | `stop` | 11.098 | ready and healthy |
| 64 | 200 | 5 | `stop` | 11.142 | ready and healthy |
| 72 | 200 | 5 | `stop` | 11.139 | ready and healthy |
| 80 | 200 | 5 | `stop` | 11.101 | ready and healthy |

第一份报告只保留在板端：

```text
run/reports/20t-b1-static-kv-token-ceiling-20261030T190000Z/results.json
```

## 续写探测

第二个隔离请求设置`max_tokens=80`，并要求模型输出以空格分隔的整数，不使用句号，也不提前停止。模型返回HTTP 200，在22.713秒内生成18个完成Token，随后返回`finish_reason=stop`。请求后的ACL健康状态仍为ready and healthy；其watchdog和restart标志均为false。

```text
run/reports/20t-b1-static-kv-continuation-20261030T191000Z/results.json
```

第三次隔离的80 Token续写运行禁用了运行时正常的句子边界停止。它在22.793秒内生成了相同的18个完成Token，并返回`finish_reason=stop`；worker保持健康，隔离端口已释放。这排除了正常的16 Token句子停止策略是18 Token结果成因的可能。对于这个提示，模型输出的是EOS/自然停止。

```text
run/reports/20t-b1-static-kv-no-sentence-stop-20261030T193000Z/results.json
```

## 强制解码容量

隔离B1候选worker新增了一种仅用于容量测试的模式。它不是HTTP字段，也不提供给UI、网关或正式B4发布使用。该模式在贪心argmax前屏蔽契约EOS Token，禁用句子停止，保留固定的1024 Token上下文检查，为每个请求创建新的48张量KV缓存，并保留有界且故障关闭的watchdog。报告要求`completion_tokens == requested_max_tokens`、`finish_reason=length`、boot ID不变，且请求后worker健康。

| 请求Token数 | 输入Token数 | 实际完成Token数 | 结束原因 | 耗时（秒） | NPU内存前/后 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 80 | 55 | 80 | `length` | 188.726 | 6386 / 6428 MB | observed-pass |
| 128 | 55 | 128 | `length` | 465.970 | 6396 / 6505 MB | observed-pass |
| 256 | 55 | 256 | `length` | 1829.767 | 6401 / 6436 MB | observed-pass |
| 512 | N/A | N/A | 重试中观察到超过3,869秒 | 运行中RAM已用3327 MB | 初始6442/取消时6516 | 操作员在64:29后取消；无最终结果 |

80 Token和128 Token的报告记录boot ID `9e9d7a30-2a46-4db4-9bdc-aa245cf0a3f9`；后续256 Token运行记录boot ID `d5d68c8a-3103-4867-9ad6-ee5da1da31c9`。每份报告在自身请求前后的boot ID相同。worker健康状态保持ready and healthy；watchdog、restart-required和runtime-error字段均无异常。每次完成的运行结束后，隔离worker和端口8086都已释放。

```text
run/reports/20t-b1-force-continue-80-20261031T171000Z/results.json
run/reports/20t-b1-force-continue-128-20261031T172000Z/results.json
run/reports/20t-b1-force-continue-256-20261031T180000Z/results.json
```

每完成一个Token的实测延迟从80 Token级别的2.36秒增至128 Token级别的3.64秒，并在256 Token级别达到7.15秒。256 Token请求耗时1829.767秒（约30.5分钟），表明解码延迟具有明显的非线性。第一次512 Token运行在全新的隔离worker上启动，并配置10800秒诊断watchdog。它在24分钟时仍保持健康，但操作员随后在读取最终报告前重启了20T板。该报告仍是一条没有结果行的中断记录。重启后，一次重试以PID 3506启动，boot ID为`a6d79c5a-d9db-4a48-9d20-3faa7dbcdd9b`；初始健康检查确认了force模式、`max_tokens=512`和B1 SoC。重试在64分29秒后仍保持健康，但尚未返回结果。随后将其取消，因为预计还需数小时的运行时间已不再能提供有用的产品证据。取消前，按`MemTotal - MemAvailable`定义计算的系统RAM已用量为3327 MB，NPU内存为6516 MB。TERM释放了端口8086；其余runner/worker PID被终止，之后不再有实验进程残留，NPU记账降至4699 MB。

```text
run/reports/20t-b1-force-continue-512-20261031T183000Z/results.json
run/reports/20t-b1-force-continue-512-retry-20261031T203500Z/results.json
```

## 结果解读

正常的UI/服务请求准入上限是**80 Token**：隔离worker接受了从8到80的每个受测请求，并在每次请求后保持健康。

在正常解码下，实测最大实际完成量为**续写提示的18 Token**，因为模型输出了EOS。在隔离的强制解码模式下，候选实际完成了**256 Token**并返回`finish_reason=length`。因此，当前观测到的强制运行时上限为256 Token。这是运行时容量结果，不是正常语言模型行为或质量结果。512级别当前正在运行；768级别和上下文剩余部分仍未测试。

浏览器候选默认使用32 Token，目前提供32、64和80作为**实验性请求预算**。隔离的8--80扫描以及两次80 Token续写探测均完成，未发生worker健康故障。此前一次64 Token UI请求被网关记录为HTTP 200，此后板卡失联并随后重启；没有重启前内核证据，因此因果关系仍未知。在重复的UI路径测试活动记录NPU内存和无重启完成结果之前，不要把64或80描述为无重启稳定性保证。

## 复现命令

在核验B1离线候选资产、并确认端口8086未使用后，从当前20T代码检出目录运行：

```bash
cd /home/HwHiAiUser/case9-20t-max-token-7c60983/samples/case9
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
export ASCEND_EDGE_CHAT_ACL_CANDIDATE_DATA_DIR=/home/HwHiAiUser/Documents/ascend-edge-chat-acl-b1
bash scripts/verify/run-20t-b1-static-kv-token-ceiling.sh
```

要重复续写探测，请指定一个级别和提示：

```bash
bash scripts/verify/run-20t-b1-static-kv-token-ceiling.sh \
  --levels 80 \
  --prompt '请从1开始连续输出整数，使用空格分隔，不要解释，不要句号，不要提前结束。'
```

要复现强制128 Token运行时容量门：

```bash
ASCEND_EDGE_CHAT_TOKEN_CEILING_FORCE_CONTINUE=1 \
ASCEND_EDGE_CHAT_TOKEN_CEILING_WORKER_MAX_TOKENS=128 \
ASCEND_EDGE_CHAT_TOKEN_CEILING_EXECUTION_TIMEOUT_SECONDS=480 \
bash scripts/verify/run-20t-b1-static-kv-token-ceiling.sh \
  --levels 128 \
  --request-timeout 510 \
  --prompt '请从1开始连续输出整数，使用空格分隔，不要解释，不要句号，不要提前结束。'
```

runner是一条诊断路径。它不改变浏览器选择的后端或浏览器Token限制。在上一份报告确认实际完成、`length`、boot ID稳定且请求后状态健康之前，不要提高到下一个级别。

## 剩余验收门槛

- 在解码路径优化前，不要恢复512/768/上下文剩余部分的运行。当前观测到的强制运行时上限仍是256 Token；512在超过一小时仍无最终响应后被取消。
- 查明此前64 Token UI请求后观察到的板卡重启原因；该原因仍为`unknown`。
- 将B1候选以固定commit上传到Hugging Face，并从公开源码下载锁重新运行本实验。当前产物来源仍属于离线实验，因为上传尝试期间controller和20T都无法访问Hugging Face。

## PyACL候选冒烟测试

板卡重启后，同一个B1候选在端口8084上的全新隔离worker中加载。产物锁、Ascend310B1目标、48个拆分FP32 KV缓存和worker健康状态全部通过。一次正常短请求返回3个自然完成Token和`finish_reason=stop`。

强制连续请求随后完成了每个受测级别：

| 请求Token数 | 实际Token数 | 结束原因 | 耗时（秒） |
| ---: | ---: | --- | ---: |
| 8 | 8 | `length` | 14.082 |
| 32 | 32 | `length` | 42.944 |
| 64 | 64 | `length` | 128.115 |
| 80 | 80 | `length` | 188.848 |

冒烟测试后worker已停止；端口8084已释放，NPU记账恢复到4719 / 23673 MB。这验证了现有B1 PyACL候选，但没有验证其他MindSpore检查点可作为PyACL模型使用。每个额外PyACL模型都需要自己的B1 OM、输入/输出契约、锁、ACL加载和数值冒烟测试。

## 全上下文FP16 B1 OM实验

另一个B1专用Qwen2.5-0.5B OM采用静态2048 Token全上下文契约，在统一`ascend`环境中通过端口8091加载。其3个int64输入和622329856字节FP16 logits输出与已记录契约一致；8 Token回环API请求在59.358秒内返回HTTP 200。

但是，响应包含重复的无关英文Token和一个U+FFFD替换字符：

```text
 Holden Holden Holden 纲纲 radiation�
```

这表明该OM能够在Ascend310B1上执行，但不能表明它是可用的聊天模型。它仍为`observed-degraded`，未获准加入浏览器模型列表。冒烟测试后服务已停止；端口8091已释放，系统可用RAM恢复到约22.9 GiB。

相关问题：[20T B1候选容量与重启观察](09-troubleshooting.md#case9-iss-20261030-01)。
