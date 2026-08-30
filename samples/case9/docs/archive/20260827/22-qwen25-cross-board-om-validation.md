# Qwen2.5 同一 OM 跨板验证记录

## 验证目的

本记录回答一个具体问题：同一份 Qwen2.5 静态 KV OM，是否能在
`Ascend310B4/8T` 和 `Ascend310B1/20T` 两块板上加载、执行并提供相同的
OpenAI 兼容接口。测试不覆盖正式网关、文字页面、音频或 XiaoZhi。

测试批次为 2026-08-27，使用相同的 tokenizer、运行时代码、contract、提示词和
请求参数。所有 OM 都放在独立的 `cross-om-test` 目录，未覆盖原模型或正式端口。

## 为什么两个 OM 字节不同

两块板使用同一 ONNX 和同一静态 KV contract，但分别用不同的 ATC 目标生成了 OM：

| 工件 | ATC 目标 | bytes | SHA-256 |
| --- | --- | ---: | --- |
| B4 OM | `Ascend310B4` | `1,266,010,586` | `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| B1 OM | `Ascend310B1` | `1,266,009,438` | `6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609` |

OM 不只是 ONNX 权重的压缩副本，还包含面向目标 SoC 的算子实现、内存规划和
执行元数据。因此 ATC 目标不同而产生不同字节是正常的；字节不同不表示模型
语义或 tokenizer 不同。本次使用的 ONNX SHA-256 为
`b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e`，两边相同。

## 测试协议

| 项目 | 固定值 |
| --- | --- |
| 8T 板 | `192.168.8.178`，`Ascend310B4`，CANN `8.0.0` |
| 20T 板 | `192.168.8.210`，`Ascend310B1`，CANN `8.0.0` |
| prompt | `你好，请用一句话介绍你自己。` |
| 解码 | `max_tokens=2`、`temperature=0`、`top_p=1`、batch 1 |
| 统计 | 1 次预热 + 5 次测量，lower-index p50/p95 |
| API | loopback `127.0.0.1:8084`，JSON 或 SSE |
| runtime | 同一静态 KV ACL runtime；同步 execute；单进程串行 |

比较的不是理论 TOPS，而是端到端 HTTP 请求耗时，包含 prompt 编码、ACL 执行、
logits 读取、greedy 解码和响应序列化。

## 同一 B4 OM 的双板结果

同一 SHA 为 `f6650e52...40eb8` 的 B4 OM 在两块板上均完成 descriptor 校验、
ACL smoke、JSON 和 SSE 请求。

| 板卡 | JSON p50 (ms) | JSON p95 (ms) | SSE 总耗时 p50 (ms) | SSE 首事件 p50 (ms) | 成功率 |
| --- | ---: | ---: | ---: | ---: | --- |
| 8T/B4 | `10375.744` | `10377.640` | `10344.559` | `10212.736` | JSON/SSE `5/5` |
| 20T/B1 | `7678.424` | `7737.832` | `7709.688` | `7584.653` | JSON/SSE `5/5` |

在这一组相同 OM、相同协议的对照中，20T 相对 8T：

- JSON p50 快 `1.351x`，耗时下降 `25.996%`；p95 快 `1.341x`，下降 `25.437%`；
- SSE 总耗时 p50 快 `1.342x`，下降 `25.471%`；首事件 p50 快 `1.347x`，下降 `25.733%`。

两边每次响应的文本均为 `我是Q`，completion 为 2 token，`finish_reason=length`。
因此这组数据证明的是同一 OM 的实测执行和性能差异，不证明长文本中文质量。

## 同一 B1 OM 的对称结果

为排除“只有 B4 OM 可互用”的偶然性，又将同一 SHA 为
`6bca884f...98609` 的 B1 OM 在两块板上运行：

| 板卡 | JSON p50 (ms) | JSON p95 (ms) | SSE 总耗时 p50 (ms) | SSE 首事件 p50 (ms) | 成功率 |
| --- | ---: | ---: | ---: | ---: | --- |
| 8T/B4 | `10285.786` | `10303.100` | `10302.362` | `10170.613` | JSON/SSE `5/5` |
| 20T/B1 | `7679.848` | `7696.983` | `7697.568` | `7576.490` | JSON/SSE `5/5` |

同一 B1 OM 的 JSON p50 在 20T 比 8T 快约 `25.34%`，与同一 B4 OM 的结果方向
一致。两组都只有 5 次测量，不能替代长期基准。

## 交叉加载 smoke

除了性能对照，还做了反向单 token smoke：

| 组合 | 结果 |
| --- | --- |
| B4 OM -> 20T/B1 | ACL load、descriptor 和 smoke 通过，返回 `你好！` |
| B1 OM -> 8T/B4 | ACL load、descriptor 和 smoke 通过，返回 `你好！` |

这说明这两个具体 OM 在当前驱动、CANN 和 runtime 组合下具有实测兼容性。
它不是“任意 310B OM 都可跨板复用”的保证，也不替代按目标 SoC 重新 ATC 的
provenance。CANN 的正式转换流程仍应把 `--soc_version` 与部署芯片绑定；跨 SoC
复用必须保留为显式兼容性例外，并继续做完整的长输出和稳定性验收。
这一约束也与 [CANN 8.0 的 `--soc_version` 说明](https://www.hiascend.com/document/detail/en/canncommercial/800/devaids/atc/atlasatcparam_16_0036.html)
一致：转换值应对应模型运行阶段使用的 AI 处理器型号。

## 证据位置

本地归档根目录：

```text
repro/qwen25-kv1024-20260825/reports/cross-om/20260827T080000Z/
```

其中 `board8t/` 和 `board20t/` 分别保存 OM、contract、lock、JSON/SSE 原始报告、
before/during/after `npu-smi` 快照、反向 smoke 日志和服务停止复核。两个板端原始
目录分别为：

```text
/home/HwHiAiUser/case9-qwen25-kv1024-20260825/artifacts/cross-om-test/
/home/HwHiAiUser/case9-qwen25-kv1024-20260827-20t/artifacts/cross-om-test/
```

测试结束后两个候选 PID 均已停止，`8084`、`8080`、`7861` 和 `7865` 均无监听。

## 当前判定和未完成门

| 状态 | 判定 |
| --- | --- |
| 同一 B4 OM 双板 ACL/API smoke | passed experimentally |
| 同一 B1 OM 双板 ACL/API smoke | passed experimentally |
| 同一 OM JSON/SSE 固定协议测速 | passed experimentally |
| 正式 20T 生产模型 | not admitted |
| 长输出连续性 | not run；本批次仅 2 token |
| 20T 中文 10 条质量集 | not run |
| 10 轮 RSS/FD/NPU 稳定性 | not run |
| 正式 `8080 -> 7861 -> 7865` | unchanged |
| 干净 Python 环境 | not met；20T base 仍可发现预置 Torch 系列包 |

所以，准确表述是：**这两份具体 OM 在本次环境中已实测可以互相加载并完成短请求**；
这不等于任意 OM 的跨 SoC 正式支持。正式 20T 部署应优先保留 B1 专用 OM；若选择
同一 B4 OM，必须在配置、lock 和文档中明确记录跨 SoC 兼容性例外，不能静默覆盖
B1 工件。
