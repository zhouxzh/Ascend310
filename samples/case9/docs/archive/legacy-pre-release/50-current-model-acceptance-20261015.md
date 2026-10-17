# 20T 当前模型验收记录（2026-10-15）

本记录只描述在 `192.168.1.101` 实际执行的 20T 板卡结果。该板实测为
`Ascend310B1 / 20T`，CANN `8.0.0`，Python `3.9.2`。`npu-smi` 显示的
`Health: Alarm` 作为诊断信息保留，不作为单独失败条件。

## 判定口径

“可以使用”必须同时满足：模型工件锁校验、目标 SoC、ACL 或 MindSpore
上下文、实际 NPU 生成、JSON/SSE 接口、长输出、连续请求和协议边界测试。
机器门通过不等于已经获得生产准入；共享 `base` 环境仍标记为
`experimental_dirty_base`，中文质量还需要人工签字。

## 已验收模型

| 模型 | 后端 | 机器门 | 中文输出 | 性能结论 | 当前结论 |
| --- | --- | --- | --- | --- | --- |
| Qwen1.5-0.5B-Chat | MindSpore 2.4.10 + MindNLP 0.4.1 | 通过（全部门） | 通过机器探测，输出连贯 | 首 token p50 `655.5 ms`；总延迟 p50 `1295.4 ms`；约 `1.544 token/s` | 20T 上当前最适合文字聊天，实验性 |
| Qwen2.5-0.5B 静态 KV 1024 FP32 OM | 原生 ACL/PyACL | 通过（全部门） | UTF-8 和中文探测通过 | 首事件 p50 `4727.8 ms`；总延迟 p50 `4741.3 ms`；约 `0.422 token/s` | 可运行但明显偏慢，实验性 |

### Qwen1.5 MindSpore

- 来源：`Qwen/Qwen1.5-0.5B-Chat`，revision
  `4d14e384a4b037942bb3f3016665157c8bcb70ea`。
- 运行上下文：`device_target=Ascend`，`npu-smi=Ascend310B1`，不是 CPU
  fallback；健康接口给出 `placement_status=context_verified`。
- `max_tokens=8/16/32/64` 均返回合法 UTF-8；64 上限请求实际生成 55 token
  后以 `stop` 结束。
- 10 轮稳定性请求 `10/10` 成功；JSON、SSE、超上下文、错误参数、超大请求和
  客户端中断后的健康检查均通过。
- 30 次性能测量（预热 2 次，短中文问候，greedy）：
  - 首 token/首事件 p50 `655.5 ms`，p95 `708.5 ms`；
  - 总延迟 p50 `1295.4 ms`，p95 `1368.5 ms`；
  - token/s 中位数 `1.544`，范围约 `1.434-1.612`。
- 仍是共享 `base` 环境，已有 `torch`、`torch_npu`、`torchaudio`；适配器本身
  不导入这些包。该限制使它不能直接标记为生产准入。

原始报告：
`/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/qwen1.5-20t-current-20261015/acceptance.json`

### Qwen2.5 静态 KV OM + ACL

- 模型：`qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`。
- OM：`1,266,009,438` bytes，SHA-256
  `6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609`。
- 目标和实测 SoC 均为 `Ascend310B1`；descriptor、48 个 cache 张量、tokenizer
  锁和运行时 contract 全部匹配。
- 8/16/32/64 token 长度测试、10 轮稳定性、30 次性能、JSON/SSE 和协议错误
  测试全部通过；长中文回答没有乱码或重复 delta。
- 长输出性能明显较慢：短 prompt 的首事件 p50 `4727.8 ms`，总延迟 p50
  `4741.3 ms`，速度约 `0.422 token/s`。
- 设备证据：运行中 `AICore` 约 `75-80%`，设备内存约 `9.0 GB`；服务结束后
  设备内存回落到约 `7.3 GB`。

原始报告：
`/home/HwHiAiUser/case9-om-20t-20260914/reports/qwen25-b1-native-acceptance/qwen25-b1-current-20261015/acceptance.json`

## 当前可用性结论

1. **首选文字聊天：Qwen1.5 MindSpore。** 它在本次 20T 实测中生成连贯中文，
   速度约为静态 KV ACL 的 3.7 倍，适合作为当前候选聊天 worker。
2. **Qwen2.5 OM 不是不可用。** PyACL/ACL 链路已经被实测证明能在 NPU 上执行，
   不是 CPU 或云端 fallback；主要问题是当前静态图和逐 token 执行路径的延迟，
   因此适合功能验证，不适合作为当前流畅交互入口。
3. 两者都不能直接写成生产准入：MindSpore 使用 dirty-base，OM 路线虽然机器门
   通过但速度不足；正式端口 `8080 -> 7861 -> 7865` 本次没有切换。
4. TinyLlama 的历史结果只说明英文/兼容性实验，中文质量未通过；DeepSeek、
   Qwen3、MiniCPM3 等没有在本次 20T 验收中形成完整机器门，因此不能列为“可用”。

## 运行后状态

本次 Qwen1.5 MindSpore worker、Qwen2.5 ACL candidate worker 和验收进程均已
停止；没有修改正式服务、系统 CANN、驱动、OPP 或 conda 包。全部报告保留在板端
原路径，后续如需复现应使用同一模型 revision、工件锁、SoC 和启动参数。
