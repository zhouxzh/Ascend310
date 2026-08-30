# DeepSeek 20T 板端验证记录（2026-08-30）

本记录对应 20T 开发板的隔离实验，不改变 Case9 正式入口、模型注册表或候选服务。
旧记录中的 `192.168.8.210` 已不可达；本次通过 SSH 别名 `ascend20t` 连接到同一块板的
实际 WLAN 地址 `192.168.1.95`。板卡为 `Ascend310B1 / 20T`。
固定 revision 只写入本实验的 `service-test/chat_model_profiles.json` 和本地复现清单；正式
`configs/chat_model_profiles.json` 继续保留 blocked 占位，不允许 `case9-modelctl` 激活本批次。

## 1. 环境和边界

| 项目 | 实测值 |
| --- | --- |
| CANN | 8.0.0（`/usr/local/Ascend/ascend-toolkit/latest`） |
| Python | 3.9.2（`/usr/local/miniconda3/bin/python`） |
| MindSpore / MindNLP | 2.4.10 / 0.4.1（用户 site） |
| NumPy / tokenizers | 1.22.4 / 0.19.1 |
| NPU 工具 | `npu-smi 25.2.0`，`Health: Alarm`（仅诊断记录） |
| 内存 | 23 GiB，总可用约 21 GiB（采集时） |
| 禁止包状态 | `torch`、`torch_npu`、`torchaudio` 为原有 dirty-base 包；本轮未安装、升级或删除任何包。适配代码未导入这些包。 |

原始环境快照位于 [`environment/`](../repro/deepseek-r1-20t-20260830/environment/)。

## 2. 工件和运行副本

来源为 Modelers 的固定 revision：
`MindSpore-Lab/DeepSeek-R1-Distill-Qwen-1.5B-FP16`，commit
`0a28897fe71fdd30de350b667ae588601a85990f`。权重从 8T 已校验副本经控制机中转到
20T；20T 端使用 `.part -> 长度 -> SHA-256 -> 原子改名`，完整权重为
`3,554,214,416` bytes，SHA-256 为
`706e1bfd7cb0680fbf73df6a2506766e447246e4291d7054c8b395dc3583419c`。

原始工件和逐文件哈希见 [`artifacts/model/`](../repro/deepseek-r1-20t-20260830/artifacts/model/)、
[`SHA256SUMS.txt`](../repro/deepseek-r1-20t-20260830/SHA256SUMS.txt) 和
[`bundle-manifest.json`](../repro/deepseek-r1-20t-20260830/bundle-manifest.json)。
原始配置没有被覆盖；为避免 `max_position_embeddings=131072` 引起超大分配，隔离运行副本
`runtime-model-context1024` 仅做两处改动：

```text
ms_dtype: mindspore.float16 -> float16
max_position_embeddings: 131072 -> 1024
```

运行副本配置 SHA-256：
`9e7c36ca50be71385be6ce736a512d1b88a44631d197356ba9053d48e63f28e6`。

## 3. NPU 加载和生成

在 `base` 环境显式加载 CANN，`device_target=Ascend`，使用本地 tokenizer 和
`AutoModelForCausalLM`。单 token 与 4-token 同步生成均返回 0；4-token smoke 输出
`嗯，用户让我`。模型加载约 `36.196 s`，首次单 token 生成 `20.874 s`（包含图初始化），
同一进程随后 4-token 为 `8.065 s`。原始日志：
[`fp16-context1024-smoke-20t.log`](../repro/deepseek-r1-20t-20260830/reports/board20t/fp16-context1024-smoke-20t.log)。

`npu-smi` 快照显示执行前设备内存 `7033/23673 MB`，执行后
`7153/23673 MB`；无残留模型 Python 进程。快照见
[`npu-before-context1024-smoke.txt`](../repro/deepseek-r1-20t-20260830/reports/board20t/npu-before-context1024-smoke.txt)
和 [`npu-after-context1024-smoke.txt`](../repro/deepseek-r1-20t-20260830/reports/board20t/npu-after-context1024-smoke.txt)。

## 4. 20T 与 8T 可比性能

协议完全相同：同一中文 prompt、FP16、context 1024、显式 `int64` attention mask、
同一模型和 tokenizer、2 次预热后测 5 次 4-token 生成。

| 指标 | 8T / Ascend310B4 (`192.168.1.90`) | 20T / Ascend310B1 (`192.168.1.95`) |
| --- | ---: | ---: |
| 模型加载 | 34.860 s | 35.443 s |
| 稳态 p50 | 10.154 s | 7.829 s |
| 稳态 p95 | 10.919 s | 7.835 s |
| 吞吐 | 0.388 token/s | 0.511 token/s |
| 2-token 稳定性 | 10/10 | 10/10 |

按该短批次，20T 的 p50 延迟约低 22.9%，吞吐约高 31.8%。这是同一时间窗口的
实验性对照；随后独立临时 API 服务完成了完整 2+30 SSE campaign（见下一节）。两种 SoC
的证据仍分别保存，不能合并排名。
8T 原始报告在 `~/case9-deepseek-8t-experiment/reports/`，20T 报告在
`~/case9-deepseek-20t-experiment/reports/`。

## 5. 临时 OpenAI API 服务验证

在不修改正式注册表和网关的前提下，使用 `service-test` 临时 Profile 启动
`127.0.0.1:8090`。服务启动前环境预检和五个模型文件哈希均通过，随后执行完整机器 API
批次：`/health`、`/v1/models`、JSON、SSE、8/16/32/64-token 长输出、10 轮稳定性、6 类
结构化错误、超上下文、超大请求和客户端中断。9 个机器门全部通过；质量探测在此前的
独立报告中单独记录，未因 API 通过而提升中文质量。

2 次预热 + 30 次 SSE 测量结果：总耗时 p50/p95 `2489.698/2537.096 ms`，首事件
p50/p95 `2483.582/2530.960 ms`，吞吐 p50 `0.805 token/s`。原始目录为
[`deepseek-api-full-20t-20260830T051523Z/`](../repro/deepseek-r1-20t-20260830/reports/board20t/api/deepseek-api-full-20t-20260830T051523Z/)，
服务日志、环境预检和 artifact verification 同目录的 `../` 层级保存。

### 5.1 重新开放后的复核批次

用户重新开放 20T 后，在同一隔离 `service-test` 目录重新启动服务并执行同样的机器门，
没有安装、升级或删除任何 Python 包。批次为
[`deepseek-reopen-20t-20260830T0536Z`](../repro/deepseek-r1-20t-20260830/reports/board20t/api/reopen-20260830T0536Z/)。
`health`、`/v1/models`、JSON、SSE、8/16/32/64 长输出、10 轮稳定性、错误边界和协议边界
均通过，机器门仍为 `9/9`。2 次预热 + 30 次 SSE 的总耗时 p50/p95 为
`2484.751/2557.242 ms`，首事件 p50/p95 为 `2478.495/2551.004 ms`，吞吐 p50
为 `0.805 token/s`。该批次的完整 `acceptance.json`、`performance.json`、服务日志及
停机前后 `npu-smi` 快照已纳入复现包清单；服务随后按已核验的进程组停止，8090 端口
释放，正式 `8080 -> 7861 -> 7865` 未改变。停机前设备内存为 `20573/23673 MB`
（HugePages `2086/2086`），停机后回落为 `7427/23673 MB`（HugePages `15/15`）；
`Health: Alarm` 仍仅作为诊断记录。

## 6. 稳定性和中文输出观察

- 20T 10 轮 `max_new_tokens=2`：10/10 成功，均为合法 UTF-8，无进程崩溃。
- 10 条中文探测（每条 16 token）：均非空且无 `U+FFFD`，但都在 `<think>` 推理开头
  截断，不能作为人工质量通过。
- 两条 64-token greedy 长输出中，自我介绍完成了回答；算术题没有可靠输出 `5`。
- 固定 seed 的官方采样参数（`temperature=0.1, top_p=0.9, repetition_penalty=1.2`）
  仍未使算术题稳定回答 `5`，且自我介绍出现重复。

完整响应见 [`chinese-probe-20t.json`](../repro/deepseek-r1-20t-20260830/reports/board20t/chinese-probe-20t.json)、
[`long-output-64-20t.json`](../repro/deepseek-r1-20t-20260830/reports/board20t/long-output-64-20t.json)
和 [`sampling-64-20t.json`](../repro/deepseek-r1-20t-20260830/reports/board20t/sampling-64-20t.json)。
因此本次通过了工件、环境、MindSpore/Ascend 加载、短生成、短稳定性、临时 OpenAI
JSON/SSE 契约和性能对照；`quality_reviewed`、正式网关/UI 和正式准入仍为
`not-run` 或 `blocked`。

## 7. 回滚和后续边界

临时 `service-test` 已启动并在证据采集后停止；没有启动候选网关或浏览器服务，也没有修改
`8080 -> 7861 -> 7865`。失败时只需停止本实验产生的进程并保留
`~/case9-deepseek-20t-experiment/`；不要删除
共享 conda 缓存、系统 CANN 或其他模型。DeepSeek Profile 仍保持 blocked，直到固定工件、
正式候选 API/网关契约、人工中文质量门和 dirty-base 准入审批另行完成并获批准；不能因为 20T NPU 推理成功就自动接入小智或
正式 Case9 链路。

参考：[MindSpore Orange Pi 在线推理目录](https://www.mindspore.cn/tutorials/zh-CN/master/orange_pi/model_infer.html)、
[DeepSeek-R1-Distill-Qwen-1.5B 模型卡](https://huggingface.co/MindSpore-Lab/DeepSeek-R1-Distill-Qwen-1.5B)。
