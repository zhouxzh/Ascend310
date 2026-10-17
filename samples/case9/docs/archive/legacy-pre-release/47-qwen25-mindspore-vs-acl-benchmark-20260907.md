# Qwen2.5 MindSpore 与静态 KV ACL 对照测试

本记录比较同一块 Ascend310B4 / 8T 开发板上同一 Qwen2.5-0.5B 模型的两条
推理路径：完整 MindSpore + MindNLP，以及静态 KV ONNX 转 OM + 原生 ACL。两条
测试严格串行，测试前均确认没有 Case9 服务运行；每个程序只拥有自己创建的
worker/service 进程，超时或异常时只终止自己的进程组。

## 测试口径

| 项目 | 固定值 |
| --- | --- |
| 主机 | `orangepiaipro`，`192.168.1.90` |
| NPU | `Ascend310B4 / 8T` |
| CANN/驱动 | `8.0.0` / `25.2.0` |
| 模型 | Qwen2.5-0.5B，同一 revision `7ae557604adf67be50417f59c2c2f167def9a775` |
| prompt | `你是谁？` |
| 解码 | batch 1、greedy、`temperature=0`、`top_p=1`、`max_tokens=2` |
| 采样 | 预热 2 次，正式 5 次 |
| 统计 | 请求总耗时；生成 token 数固定为 2；不含控制机网络耗时 |

MindSpore 使用现有 `base` 的 MindSpore 2.4.10/MindNLP 0.4.1，并显式设置
`CASE9_REQUIRE_PLACEMENT_EVIDENCE=0`，因此这是 context-only 诊断性能，不是
模型准入证据。ACL 使用已验证的静态 KV OM，服务监听临时端口 `127.0.0.1:8084`。

## 两个独立测试程序

```text
scripts/benchmark_qwen25_mindspore.py
scripts/benchmark_qwen25_acl.py
```

MindSpore 程序的父进程不导入 MindSpore，而是启动隔离 worker，通过事件文件
传递 ready/result；加载或生成超时后父进程可杀掉整个 worker 进程组。ACL 程序
在启动前拒绝已响应的端口，启动自己的 ACL 服务，完成请求后只终止自己创建的
服务进程组。两者都在报告中保存 `npu-smi` 前后快照和子进程退出码。

## 实测结果

### MindSpore + MindNLP

报告：板端
`/home/HwHiAiUser/case9-mindspore-chat/reports/qwen25-ms-compare-20260907.json`。

| 指标 | 结果 |
| --- | ---: |
| 模型加载 | `53.7565 s` |
| 正式请求数 | `5` |
| 请求耗时 p50 | `1.1236 s` |
| 请求耗时 p95 | `1.1779 s` |
| token/s p50 | `1.7799` |
| 返回文本 | `我是Q`（每次 2 token，`finish_reason=length`） |
| worker 退出 | `0` |
| NPU 内存 | `3763 MB` 前；`5980 MB` 后 |

第一次生成包含额外编译/缓存开销（`14.6960 s`）；后续正式样本为
`1.0967–1.1899 s`。因此加载时间和稳态生成时间必须分开报告。

### 静态 KV OM + ACL

报告：板端
`/home/HwHiAiUser/case9-qwen25-kv1024/reports/qwen25-acl-compare-20260907.json`。

| 指标 | 结果 |
| --- | ---: |
| 服务启动至 health | 记录在 `startup_seconds` |
| 正式请求数 | `5` |
| 请求耗时 p50 | `9.2618 s` |
| 请求耗时 p95 | `9.3069 s` |
| token/s p50 | `0.2159` |
| 返回文本 | `我是Q`（每次 2 token，`finish_reason=length`） |
| 服务退出 | `-15`（父程序主动 SIGTERM，属于预期清理） |
| NPU 内存 | `2280 MB` 前；`3756 MB` 后 |

ACL 服务的 `/health` 同时确认了静态 KV descriptor、48 个 cache tensor、
`[1,2,1024,64]` cache shape 和 `descriptor_validated=true`。`device_cache_update`
是惰性能力，启动时尚未执行模型，不能据此判断；本次首个 execute 后的 trace
记录为 D2D 成功。

## 解释与边界

在本次同 prompt、同输出长度、同一块板的稳态对照中，MindSpore 路径约
`1.78 token/s`，ACL 静态 KV 路径约 `0.216 token/s`；前者约快 8.2 倍。这个
结果与过去“MindSpore 更快”的观察一致，但不能直接解释为 MindSpore 一定更
适合正式服务，原因包括：

- 两条路径的图结构和 cache 实现不同；当前 ACL `/health` 显示
  `device_cache_update=false`，每步仍有既定的 cache 传输开销；
- MindSpore 结果使用 context-only placement 诊断，严格 placement gate 仍未
  通过；
- 只测了 2 个输出 token，不能外推到 32/64 token 长输出；
- 没有把服务启动、浏览器、网关或网络开销混入请求耗时；
- NPU 健康字段为 `Alarm`，只作为诊断记录，不作为单独失败原因。

因此本报告是**同模型性能实验**，不是生产准入或中文质量结论。正式选择仍应
同时满足 NPU placement、稳定性、长输出、中文质量和完整 API 门禁。

## 为什么当前 ACL 慢很多

为避免把猜测当成结论，本次给静态 KV backend 加了 opt-in trace（由
`CASE9_ACL_TRACE_FILE` 启用），原始 JSONL 只保留在板端：

```text
~/case9-qwen25-kv1024/reports/qwen25-acl-trace-m1-20260907.trace.jsonl
~/case9-qwen25-kv1024/reports/qwen25-acl-trace-m2-20260907.trace.jsonl
~/case9-qwen25-kv1024/reports/qwen25-acl-opt-m1-20260907.trace.jsonl
~/case9-qwen25-kv1024/reports/qwen25-acl-opt-m2-20260907.trace.jsonl
```

`m1` 为 32 个 prompt token + 1 个输出 token，共 32 次 execute；`m2` 为 32 + 2，
共 33 次 execute。3 次正式请求的典型 trace 数据如下：

| 阶段 | m1 | m2 | 结论 |
| --- | ---: | ---: | --- |
| ACL execute 次数 | 32 | 33 | prompt 被逐 token 执行 |
| execute 累计 | 6.85--6.86 s | 7.09 s | 主要 NPU 计算成本 |
| 初始全零 KV H2D | 25,165,824 B，9--11 ms | 同左 | 每请求一次 |
| 后续 Cache H2D/D2H | 0 B / 0 B | 0 B / 0 B | D2D 实际成功 |
| 基础输入 H2D | 262,656--270,864 B，3.5--4.0 ms | 同量级 | 每步 3 个输入 |
| logits D2H | 19,447,808 B，约 13 ms | 20,055,552 B，约 13--14 ms | 607,744 B/step |
| D2D Cache 更新 | 32/32 成功 | 33/33 成功 | 约 19--20 ms 总计 |
| HTTP 请求 p50 | 8.923 s | 9.256 s | 含 Python 生成循环 |

优化后单请求的 NPU 内存快照为 `6007 -> 6008 MB`（m1）和 `6013 -> 6015 MB`
（m2），服务退出码均为父进程预期的 `-15`，没有观察到本次请求造成的持续
NPU 内存增长。该结果只是短测，不能替代 10 轮稳定性门。

因此，“每步把 25 MiB KV 从 host 搬回设备”是 fallback 路径的理论风险，
不是本次复测的实际原因。当前请求总耗时比 `execute` 累计还多约 2.1 s；这部分
不在 ACL memcpy 计数中。补充 trace 显示 `decode_select_seconds` 只有约
`11.2 ms/请求`，`set_context_seconds` 约 `0.4 ms/请求`；真正的固定 CPU 热点
是请求开始时的 tokenizer 词表反复查询（优化前 `encode_messages=1.868 s`），
另有约 `0.15 s` 的 host cache 数组创建。

更准确的模型是：

```text
ACL 当前实现 = 32 次 token-by-token prompt execute
             + 同步 execute
             + 每步 CPU logits 选择/文本处理

MindSpore    = 一次 prompt prefill
             + 框架内部 cache decode
             + FP16 路径
```

这不是 OM 的理论性能上限。优化优先级是：

1. 增加 `[1,prompt_len]` 的 prefill 图或少量静态 gear，直接消除约 6.9 s 的重复主体计算；
2. 保持并持续验证 D2D cache，避免退回约 25 MiB/step 的 host 路径；
3. 通过设备端 top-1/top-k 或更小 logits 输出降低 CPU 选择开销；当前 logits D2H
   传输本身只有约 13 ms/请求，不能单独解释 2 s；
4. 在数值验证通过后评估 FP16 KV，降低内存和带宽；
5. 最后再评估 `execute_async`，异步化不能替代减少图执行次数。

所以 PyACL 本身没有被本次数据证明为“每次调用都慢到秒级”：单次同步 execute
约 214--230 ms；主要问题是调用次数和 host 生成循环。8.2 倍只代表当前实现的
端到端差异，不是 MindSpore 与 OM 的理论性能排名。

### 已完成的低风险优化

在 `qwen25_kv_tokenizer.py` 中缓存了不会变化的词表大小。此前 `_encode` 对
每个 token 都调用 Rust tokenizer 的 `get_vocab_size()`，这个板端调用的累计开销
是确定的 CPU 热点。优化前后用同一 prompt、同一 OM、同一 `max_tokens` 各做一次
诊断请求：

| 指标 | 优化前 `m1d` | 优化后 `opt-m1` | 变化 |
| --- | ---: | ---: | ---: |
| HTTP 请求 | 9.101 s | 7.157 s | -21.4% |
| encode_messages | 1.868 s | 0.0013 s | -99.9% |
| NPU execute | 6.867 s | 6.877 s | 基本不变 |
| execute 次数 | 32 | 32 | 未改变 |

优化后的 `max_tokens=2` 请求为 `7.385 s`，33 次 execute，execute 累计
`7.103 s`；从一个输出 token增加到两个，边际约 `0.228 s`，与单次 execute
`214--230 ms` 一致。由此可见，当前最值得做的不是继续微调 PyACL memcpy，
而是减少模型图执行次数。

### 当前未完成但有明确收益的优化

1. 导出接受 `[1,prompt_len]` 的 prefill OM（或少量静态长度 gear），把 32 次
   prompt execute 降为 1 次；这是预计最大的收益项。
2. 保留 D2D cache 路径，并在长输出/10 轮测试中持续记录 `d2d_successes`；
   一旦退回 host fallback，必须单独报告其 25 MiB/step 上界。
3. 评估将 logits 的 argmax/top-k 放到设备图内；当前 argmax 实测只有约 11 ms，
   所以它不是首要瓶颈，但可以减少 Python 边界开销。
4. 在数值一致性通过后评估 FP16 KV；这主要减少内存/带宽，不会消除 execute 次数。
5. 最后评估异步 ACL。同步 execute 目前每步约 0.22 s，异步化只有在 prefill
   图和 cache 路径优化后才值得测量。

## 复现命令

先确认两个服务均未运行，然后在板端分别执行，禁止并行：

```bash
# 路径一：完整 MindSpore（使用 base 的已验证 user-site）
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd ~/case9-mindspore-chat
python scripts/benchmark_qwen25_mindspore.py \
  --root ~/case9-mindspore-chat \
  --output ~/case9-mindspore-chat/reports/qwen25-ms-compare-<UTC>.json \
  --prompt '你是谁？' --max-tokens 2 --warmup 2 --loops 5

# 路径二：静态 KV ACL（程序会自行启动并清理 8084）
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python ~/case9-qwen25-kv1024/src/scripts/benchmark_qwen25_acl.py \
  --root ~/case9-qwen25-kv1024 \
  --launcher src/scripts/run_qwen25_kv_acl_service.sh --port 8084 \
  --output ~/case9-qwen25-kv1024/reports/qwen25-acl-compare-<UTC>.json \
  --prompt '你是谁？' --max-tokens 2 --warmup 2 --loops 5
```

模型、OM、tokenizer、日志和报告仍只保留在板端或 Git 忽略目录。重新测试时
必须生成新的时间戳报告，不能覆盖本记录的原始证据。
