# Qwen2.5 优化案例检索与 last-logits 验证

## 记录范围

本文记录 2026-08-23 在 `192.168.1.90`（Ascend310B4/8T、CANN 8.0.0）上对 Qwen2.5
静态全上下文图进行的第一轮低风险优化，以及对 GitHub/Hugging Face 类似方案的核查。
板端没有安装 Torch、TorchNPU、Torchaudio、Transformers、ONNX Runtime、MindSpore、
vLLM、MindIE 或自定义 OPP。现有 full-context 服务保持在 `127.0.0.1:8082`，优化候选
单独运行在 `127.0.0.1:8083`。

## 结论

这轮优化已通过本地 ONNX 对照、Ascend310B4 ATC、OM descriptor、真实 ACL/NPU 执行、
JSON 和 SSE 接口。它把公开 logits 从 `[1,2048,151936]` 收窄为 `[1,1,151936]`，
可将每步设备到主机的 logits 回传从 `622,329,856` bytes 降到 `303,872` bytes，且
单 token 结果与旧图一致。

它不是完整的解码加速：LM head 仍对固定 2048 个位置计算，OM 大小几乎不变，实测首 token
仍约 12 秒。因此当前状态是 `last_logits_acl_verified_experimental`，不能宣称已经解决
慢响应。下一轮高收益方向是固定 KV cache 的 `decode S=1` 图，并需重新完成 ATC、ACL 和
数值门禁。

下一轮已单独冻结为 Qwen2.5 静态 KV 1024 FP32 候选，模型 ID 为
`qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`，预留 ACL 8084、网关 7867 和
文字 UI 7868。该候选已有控制机 ONNX bytes/SHA-256 和静态契约记录，但没有板端可核验的 OM bytes、ATC、ACL
或 API 证据；完整边界见 [`docs/17`](17-qwen25-static-kv-1024-porting-plan.md)。
它不能与本文的 `qwen25-static-1024-last.onnx` 或 2048 last-logits OM 合并，也不
授权切换现有正式端口。

控制机还准备了两个未上板的短上下文 last-logits 候选，用于下一轮 ATC 前的选择：

| sequence length | ONNX bytes | SHA-256 | 控制机 CPU/ORT 36-token 参考 |
| ---: | ---: | --- | ---: |
| 1024 | 1,263,023,352 | `bbd85ce8af9ef332e4135678de3b72a9e134b4c4655f5281e9b95f0cee7ce70b` | 约 4.6 s |
| 512 | 1,261,442,290 | `8966c00e87c20ddb549fc7ca99e32d006b85f3f45b23d1f288e7fe26e829e586` | 约 2.0 s |

路径为 `C:\Users\zhoux\case9-qwen25-build\onnx-short\`。同一输入下两者与 2048
候选的 logits max diff 为 0、top-5 一致；这只是 CPU/ORT 参考，尚未证明板端 NPU 收益。
若进入板端，优先 1024 以保留更多对话上下文，512 只作为低延迟实验；两者都必须新建
独立 OM 和端口，不能覆盖 2048 证据。

## 当前实现

优化器 `tools/optimize_qwen25_last_logits_onnx.py` 不改权重，只改图的公开输出：

```text
full_logits [1,S,V]
      -> ReduceSum(attention_mask)
      -> Sub(1)
      -> Gather(axis=1)
last_logits [1,1,V]
```

运行时在 `last_logits_static` 契约下读取 `output[0,0,:]`；旧的
`full_context_static` 契约仍读取 `output[0,length-1,:]`。mask 必须是非空的左对齐二值前缀，
因此不会隐式截断或改变 prompt。

本地控制机使用 `C:\Users\zhoux\anaconda3\envs\sci-agent` 做 ONNX 图处理和 CPU 对照；
开发板只使用原生 ACL、NumPy 和既有 tokenizer。优化后的 ONNX 已在板端由原始板端 ONNX
直接生成，避免再次传输 1.2 GB 文件。

## 工件身份

| 工件 | 路径/来源 | bytes | SHA-256 |
| --- | --- | ---: | --- |
| 原始 full-context ONNX | `qwen25-static-2048.onnx` | 1,269,330,329 | `9887d67ad36179ef8d451a1226adc35011ad7093e65b1b49cf2ab1888163c43f` |
| 优化 last-logits ONNX | `optimized-last-logits/qwen25-static-2048-last.onnx` | 1,269,331,200 | `7d443d424127305368e78bc6755df41875bd0a029582ccaa432f26e3428dc5c4` |
| 原始 full-context OM | `qwen25-static-2048.om` | 1,407,111,161 | `dc17b153ee1e76b3d31e971617d1cfdc7c56f366226212a61377a2c85e1d92b8` |
| 优化 last-logits OM | `optimized-last-logits/qwen25-static-2048-last-retry2.om` | 1,407,130,895 | `ae196fd8ba4ccb7c721f8fcb9d0ff4d9b5b72bfcdf9a77309c6fe51d5103c460` |
| 优化契约 | `contracts/qwen25-static-2048-last-contract.json` | 2,802 | `b21609e3e9ddc05f6d6a930de0b8d268ed30b83c1cff6d9b5bb283d27d441726` |

候选服务使用的源码 SHA-256 与控制机一致：`qwen25_acl_contract.py`
`916d0a82bf3a72759ed2e1253b4710105939bd9a8b05d940952ca215db75b293`、
`qwen25_acl_runtime.py` `aae514293a0cf84b4c05a8b4a55f37c9269ae2e5784817ad360d282cadeb642f`、
`qwen25_acl_service.py` `8a5a9587e3ebcdf0e0ab29e5977fc646e7b0bdda05a8603a5d7f0fd3133b47da`、
`scripts/serve_qwen25_acl.py` `1a32219f105e0b257ce20aa2051a8c325292e4a656ba28d44c5298542e15c7d3`。

重启时曾误将独立 inspector 的分析报告作为服务契约；服务因缺少 `acl_om` 主字段立即
fail-closed，未执行推理。随后改用兼容的 full-context inspector 重新生成上述契约，加入
byte size 后 descriptor 和 8083 健康检查再次通过。失败日志仍保留在同一板端服务日志中。

两张图均为 opset 17、三个 `int64 [1,2048]` 输入。优化 OM 的 descriptor 为：

```text
input_ids       int64    [1,2048]       16,384 bytes
attention_mask  int64    [1,2048]       16,384 bytes
position_ids    int64    [1,2048]       16,384 bytes
last_logits     float16  [1,1,151936]   303,872 bytes
```

ATC 使用的固定命令为：

```bash
atc --framework=5 \
  --model=/home/HwHiAiUser/case9-qwen25/artifacts/optimized-last-logits/qwen25-static-2048-last.onnx \
  --output=/home/HwHiAiUser/case9-qwen25/artifacts/optimized-last-logits/qwen25-static-2048-last-retry2 \
  --input_format=ND \
  --input_shape="input_ids:1,2048;attention_mask:1,2048;position_ids:1,2048" \
  --soc_version=Ascend310B4 \
  --precision_mode=must_keep_origin_dtype
```

日志为 `~/case9-qwen25/logs/optimized-last-logits/atc-qwen25-static-2048-last-retry2.log`。
首次重试只因隔离环境隐藏了 ATC 自带的 `decorator` 而失败；重试显式使用系统已有的
`/usr/local/miniconda3/lib/python3.9/site-packages`，没有安装或升级任何包。

## 板端实测

优化服务 PID 为 `26870`，监听 `127.0.0.1:8083`；旧服务 PID 为 `27022`，监听
`127.0.0.1:8082`。两者的 `/health` 都报告 `backend=acl_om` 和
`descriptor_validated=true`，优化服务额外报告 `execution_mode=last_logits_static`。

在相同 greedy 请求下，两个服务产生完全相同的 token 文本：

| 请求 | full-context 8082 | last-logits 8083 |
| --- | ---: | ---: |
| 中文 prompt，`max_tokens=1` | 12,244 ms | 11,984 ms |
| 中文 prompt，`max_tokens=2` | 22,823 ms | 21,437 ms |
| 结果 token | `我是` / `我是由` | `我是` / `我是由` |

这些是单次受控 wall-clock 结果，不是 p50/p95 基准；约 6% 的差异不能归因成稳定收益。
优化服务 SSE 也返回了角色 chunk、`你好`、`！`、结束原因和 `[DONE]`。原始响应和健康
报告保存在：

```text
~/case9-qwen25/reports/optimized-last-logits/health-8083.json
~/case9-qwen25/reports/optimized-last-logits/api-8083-json-1token.txt
~/case9-qwen25/reports/optimized-last-logits/api-8083-sse-2token.txt
~/case9-qwen25/reports/optimized-last-logits/final-byte-contract-json-1token.txt
```

请求期间的 `npu-smi` 采样保存在
`~/case9-qwen25/logs/optimized-last-logits/npu-during-optimized.log`，AICore 观测到
53%、74%、84% 和 100% 的峰值；这与服务日志的 `acl.mdl.execute_async` 路径相符，证明
本候选确实执行了 NPU。采样同时仍显示已知的 `Health: Alarm`，该诊断字段不单独作为门禁。

## GitHub/Hugging Face 类似案例

| 来源 | 可复用机制 | 对当前板的边界 |
| --- | --- | --- |
| [Tlntin/qwen-ascend-llm](https://github.com/Tlntin/qwen-ascend-llm)（[导出源码](https://raw.githubusercontent.com/Tlntin/qwen-ascend-llm/main/export/export_onnx.py)） | 单 token 输入加合并 KV cache；`max_prefill_length=1` 关闭动态 shape；通过 ATC 生成 OM。 | README 明确只在 310B1 测试；导出依赖 `torch_npu`，并使用混合精度/自定义算子路径。没有当前 B4/CANN8 的 OM、ACL 和性能证据，不能直接安装或照搬。 |
| [yinghuo302/ascend-llm](https://github.com/yinghuo302/ascend-llm/blob/main/readme.md) | 固定最大 KV cache、`attention_mask` 屏蔽无效位置，decode 输入固定为一个 token；示例包含 TinyLlama/Llama2 的静态 shape。 | 示例是 310B1、CANN 7.x 体系，且需要图修改和自定义 `MatMulInteger` OPP。只能借鉴 descriptor，不得覆盖本机 OPP。 |
| [Microsoft ONNX Runtime GenAI builder](https://github.com/microsoft/onnxruntime-genai/blob/main/src/python/py/models/README.md#prune-language-modeling-head) | `prune_lm_head=true` 只投影最终 hidden state，公开 logits 为 `[batch,1,vocab]`；这是本轮 last-logits 优化最接近的通用先例。 | 这是 ORT builder 功能，不是 CANN/ATC 保证。若只是图末端 Gather，主要减少 D2H；要减少 NPU 计算，必须在 LM head 前截取 hidden state。 |
| [ONNX Runtime Mobius static cache](https://raw.githubusercontent.com/onnxruntime/mobius/main/examples/static_cache_generation.py) | 预分配固定 KV buffer，用 `write_indices` 原位更新，每步一 token 并只取最后 logits。 | 依赖 ORT runtime/EP，没有 CANN backend；只能重写为标准 ONNX 算子后再做 ATC/ACL 验证。 |
| [onnx-community/Qwen2.5-0.5B-Instruct](https://huggingface.co/onnx-community/Qwen2.5-0.5B-Instruct/tree/main/onnx) | 提供 fp16、int8、q4 等通用 ONNX 工件，便于比较量化体积。 | 目标是 Transformers.js/通用 ONNX；页面没有 Ascend310B4 静态 KV 或 OM 证据，不能作为板端可用模型。 |
| [Ringoacid/Qwen2.5-0.5B-onnx-attention-int8](https://huggingface.co/Ringoacid/Qwen2.5-0.5B-onnx-attention-int8) | 合并 prefill/decode、past/present KV 和 int8 attention 的参考契约。 | 面向 ORT WebGPU，输入/缓存仍是动态 shape，且输出全序列 logits；没有 Ascend/ATC 证据。 |

截至本记录，没有找到官方且已在 Ascend310B4/CANN8 验证的 Qwen2/Qwen2.5 静态-KV OM。
社区案例都必须重新走 ONNX 图检查、ATC、OM descriptor、ACL smoke、NPU 数值和 API 门禁。

## 后续优化顺序

1. 在外部 `sci-agent` 导出 `sequence_length=512` 或 `1024` 的短上下文 full-context 图，
   先测固定 shape 对首 token 的收益；服务明确拒绝超长 prompt，不隐式截断。
2. 重新导出 `decode S=1 + fixed KV cache` 图，优先 `max_prefill_length=1`、batch=1、
   FP16、标准 ONNX 算子；每步只返回 last logits 和新 KV。这个方向才可能把每 token
   计算从 O(context) 降到接近 O(1)。
3. FP16 正确性通过后再评估 W8/W8X8。出现 `MatMulInteger`、`AscendQuant`、ORT
   `com.microsoft` 节点或自定义 OPP 时，转为隔离阻断分支，不在板端安装 Torch 或替代框架。
4. 通过独立候选的 10 轮 p50/p95、RSS/FD/NPU 内存和中文探测集后，才考虑网关切换；当前
   8083 不替换正式网关、音频或 XiaoZhi。

## 门禁状态

| 门 | 状态 | 证据 |
| --- | --- | --- |
| 本地 optimizer/contract tests | `passed` | Qwen 专项 15/15，`py_compile` 和 `git diff --check` 通过 |
| ONNX checker/静态 last-logits contract | `passed` | 优化图 SHA 和独立 inspector 报告 |
| Ascend310B4 ATC/OM | `passed` | retry2 日志、OM SHA/bytes |
| ACL descriptor | `passed` | 8083 `/health` 和启动日志 |
| 真实 NPU greedy | `passed-limited` | 1/2 token 结果、AICore 运行采样 |
| JSON/SSE | `passed-isolated` | 8083 原始报告 |
| 稳定性、中文质量、正式网关、音频、小智 | `not-run` | 保持后续阶段 |
