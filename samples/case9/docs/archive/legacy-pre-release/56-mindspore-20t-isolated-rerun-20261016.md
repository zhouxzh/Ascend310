# 20T 隔离 MindSpore 超时修正复测

## 环境和协议

目标为 `192.168.1.101`，`Ascend310B1 / 20T`。运行环境为
`case9-ms240-cann80`：Python `3.9.25`、MindSpore `2.4.10`、MindNLP `0.4.1`、
CANN `8.0.0` 和 `PYTHONNOUSERSITE=1`。`npu-smi` 的 Health Alarm 仅记录为诊断信息。

所有 Profile 以单一 `8090` worker 串行运行，服务 watchdog 为 300 秒，验收客户端为
330 秒。每个复测包括 Ascend context、工件哈希、JSON/SSE、`2/8/16/32/64` token、10 轮
稳定性、10 条中英文机器探测，以及 2 次预热加 30 次 `max_tokens=2` 性能测量。

## 结果

| Profile | run ID | 机器协议与稳定性 | 中文机器探测 | 首事件 p50/p95 | 总耗时 p50/p95 | token/s p50/p95 | 当前状态 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| Qwen1.5-0.5B Chat | `qwen15-timeout-control-20t-20261016` | 11/11、10/10 | 10/10；人工待审 | 671.868/723.280 ms | 1310.003/1401.746 ms | 1.531/1.642 | `experimental_isolated` |
| Qwen1.5-1.8B Chat | `qwen15-18b-20t-20261016` | 11/11、10/10 | 中英各 5/5；人工待审 | 1561.806/1599.717 ms | 2991.879/3035.690 ms | 0.670/0.680 | `experimental_isolated` |
| Qwen2-0.5B Instruct | `qwen2-05-timeout-retest-20t-20261016` | JSON/SSE/64-token/10轮通过 | 9/10；加法项为 `12 加 30 �` | 693.995/787.045 ms | 1338.346/1448.931 ms | 1.494/1.617 | `blocked`，UTF-8 失败 |
| Qwen2-1.5B Instruct | `qwen2-15b-20t-retry2-20261016` | JSON/SSE/64-token/10轮通过 | 中文 `4/5`；加法项为 `12 加 30 �`；英文 `5/5` | 1343.582/1379.287 ms | 2577.378/2628.939 ms | 0.776/0.796 | `blocked`，UTF-8 失败 |
| Qwen2.5-0.5B Instruct | `qwen25-05-timeout-retest-20t-20261016` | 11/11、10/10 | 10/10；人工待审 | 696.448/758.562 ms | 1314.523/1407.406 ms | 1.531/1.667 | `experimental_isolated` |
| Qwen2.5-1.5B Instruct | `qwen25-15-low-occupancy-retry-20t-20261016` | JSON/SSE/64-token/10轮通过 | 9/10；加法项为 `12 加 30 �` | 1428.028/1447.062 ms | 2698.279/2728.795 ms | 0.742/0.760 | `blocked`，UTF-8 失败 |

Qwen2.5-1.5B 另有 run `qwen25-15-timeout-retest-20t-20261016`。该次 worker 加载后，
未知进程已占用大量 NPU 内存，首次生成出现 `Alloc failed, size:481482240`。随后低占用
重测通过了生成门，因此此错误记录为**特定共存内存状态的 observed-fail**，而不是把 1.5B
标记为恒定无法运行。

## 准入结论

Qwen1.5-0.5B、Qwen1.5-1.8B 与 Qwen2.5-0.5B 完成技术门，但仍只允许经
`CASE9_ALLOW_EXPERIMENTAL=1` 的候选实验启动，不自动切换正式
`8080 -> 7861 -> 7865` 链路。Qwen2-0.5B、Qwen2-1.5B 和 Qwen2.5-1.5B 的
`U+FFFD` 为可见文本损坏，故不能作为聊天机器人后端激活。Qwen2-1.5B 的该次
300 秒 watchdog/330 秒客户端链路没有发生客户端误杀，其 64-token、稳定性和性能门
均通过；阻断原因只限于固定中文探测的文本完整性。8T 尚未独立复测，状态为 `untested`。

在 `2026-10-16` 对 8T 当前入口 `192.168.1.90:22` 的只读 SSH 连通性探测超时，因而本批次
没有尝试下载、加载、停止进程或运行 8T worker。这个网络状态只使 8T 回归保持 `not-run`，
不改变或反推任何 20T 技术结论。
