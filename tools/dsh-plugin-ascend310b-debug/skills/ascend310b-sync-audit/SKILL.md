---
name: ascend310b-sync-audit
description: Ascend 310B1/310B4 Ascend C 同步与流水审计。用于检查 SetFlag/WaitFlag、EnQue/DeQue、CrossCore、双缓冲、事件和 stream 同步的配对与顺序，定位卡死、竞态、输出未完成或偶发错误；不得使用 Ascend 950 专属 BufferID/Lock 语义替代 310B 的同步模型。
---

# Ascend 310B 同步审计

本 skill 参考 CANNBot `ascendc-sync-audit` 的同步审查思想，但明确排除 950 专属语义，并按 310B 的流水和证据门重写；不替代 CANNBot 原技能。

## 目标

证明数据生产者与消费者之间的同步是**成对、顺序正确、对象正确**的，而不是仅靠“程序有时能跑”判断。

## 审查清单

### Queue 与 Buffer

- 每个 `EnQue` 是否有对应 `DeQue`。
- Buffer 是否在消费者完成后才释放或重新写入。
- 双缓冲是否真的有独立的 buffer，而不是同一块内存重复排队。
- 队列深度、超时和退出路径是否覆盖异常结束。

### Flag / Event

- `SetFlag` 和 `WaitFlag` 的 flag ID、事件对象和执行顺序是否配对。
- 是否存在重复 set、缺少 wait、跨循环泄漏或提前 wait。
- 多核/AIC/AIV 场景是否按当前 310B CANN 的同步接口实现。
- 不把 950 的 BufferID/Lock/Unlock 规则套到 310B；310B 是否支持某接口以当前 CANN 文档和编译结果为准。

### Stream / Host

- host 是否在输出被读取前完成 stream 同步。
- 错误路径是否仍然同步并释放资源。
- 是否存在多个线程共享一个 stream 但无外部串行化。

## 验证

1. 用最小单块数据跑通。
2. 人为增加重复批次，观察是否偶发错误。
3. 在合法范围内增加并发或迭代次数，记录失败位置。
4. 加日志时记录 block、flag、buffer 和迭代号，避免只打印“到这里了”。
5. 用代码审查和最小复现同时确认，不凭日志顺序猜硬件行为。

## 停止条件

- 存在未配对的 set/wait 或 enque/deque。
- 运行时接口与当前 310B/CANN 不匹配。
- 设备已经卡死或消失：先保留现场并请求用户授权处理，不要继续压测。
- 无法确定 buffer 生命周期时，不评价性能或精度。
