# MindSpore 验收超时与 Watchdog 因果复盘

## 结论

`2026-10-16` 之前的 Qwen2 和 Qwen2.5 长输出失败中，30 秒 HTTP 客户端超时是已确认的
干扰因素。客户端先关闭连接，服务随后按 fail-closed 规则销毁 worker；TBE
`task_distribute main process disappeared` 是该清理后的子进程日志，不能单独证明模型
或 NPU 内存崩溃。

这项结论适用于旧的 `30 s` 验收命令和对应的 run ID，不外推至其他模型、其他
MindSpore/CANN 版本或独立的 `Alloc failed` 记录。

## 已实施修正

| 层 | 旧设置 | 新设置 | 目的 |
| --- | ---: | ---: | --- |
| 候选服务 provider watchdog | 隐式/与测试无明确关系 | `CASE9_GENERATION_TIMEOUT=300` 秒 | 不可中断生成由 worker 自己终止并标记不健康 |
| 验收 HTTP 客户端 | `30` 秒 | `330` 秒 | 保证客户端不先于 300 秒 worker watchdog 断开 |
| 运行脚本 | 未传递 watchdog 值 | 显式传递并限制为 1--300 秒 | 保留启动命令中的时间预算证据 |
| 验收脚本 | 默认 `30` 秒 | 默认 `330` 秒，允许 0.001--600 秒 | 区分客户端错误与服务端超时 |

单元测试覆盖两条边界：64-token 的慢响应不会被客户端提前取消；超过 300 秒的不可中断
生成仍须终止 worker 并 fail-closed。

## 重新解释历史记录

| Profile | 历史结论 | 修正后的证据状态 |
| --- | --- | --- |
| Qwen2-0.5B | 长输出后 TBE 子进程退出 | `timeout-confounded`；已在 330 秒客户端复测完成长输出与稳定性，另有 UTF-8 质量失败 |
| Qwen2-1.5B | 首次调用错误地把 `artifacts/` 当作模型根目录，未进入 NPU | 更正根目录后在 300 秒/330 秒链路完成长输出、稳定性和性能；中文加法探测为 `12 加 30 �`，因此按文本完整性阻断 |
| Qwen2.5-0.5B | 首次 JSON 后 TBE 子进程退出 | `timeout-confounded`；复测完成所有机器协议门和机器质量检查 |
| Qwen2.5-1.5B | 32-token 超时后 TBE 子进程退出 | `timeout-confounded`；另有一次高占用条件下的真实 `Alloc failed`，低占用复测完成生成 |

`Alloc failed, size:481482240` 属于不同证据：它发生在 Qwen2.5-1.5B 首次运行时，运行前
已有大量未知 NPU 内存占用。低占用重测成功，因此只能说明该次高占用组合无法分配所需
连续块，不能写成模型恒定内存不足。

## 证据边界

- `observed-pass`：修正后的 run 已记录真实 Ascend context、JSON/SSE、长输出、稳定性和性能。
- `observed-fail`：Qwen2-0.5B、Qwen2-1.5B、Qwen2.5-1.5B 的中文探测各出现一次 `U+FFFD`，因此不能激活为聊天候选。
- `untested`：8T 回归尚未执行，不能从 20T 推断结果。

原始 JSON、日志和环境快照只保留在板端或 Git 忽略的 `repro/` 中；其路径由
`configs/chat_model_profiles.json` 的板卡验证条目索引。
