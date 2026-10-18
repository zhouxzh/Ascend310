---
name: ascend310b-crash-debug
description: Ascend 310B1/310B4 的崩溃、卡死、进程退出、设备异常和内存问题排查。用于 ACL/ACLNN 进程崩溃、exit 139、卡在 stream 同步、设备丢失、显存/内存泄漏或内核模块异常；结合 310B 设备节点、CANN 日志和原始进程状态，不把 910/950 的 crash 工具结论直接移植。
---

# Ascend 310B 崩溃与稳定性排查

本 skill 参考 CANNBot `ascendc-crash-debug` 的稳定性排查路径，但平台范围、探针和停止条件已改为 310B；不替代 CANNBot 原技能。

## 范围

只处理 310B1/310B4。先记录 8T/20T、CANN/驱动、设备节点、进程命令和精确退出码；这些事实决定后续路径。

## 最小证据

```text
命令和 cwd:
target 8T/20T:
CANN / driver:
npu-smi:
设备节点 / 内核模块:
exit code / signal:
stderr / plog:
输入 shape/dtype/模型哈希:
是否可复现:
```

没有原始日志和可复现输入时，不写“硬件坏了”或“算子不支持”。

## 分层排查

### 1. 进程退出 / signal

- 保存完整 stderr、退出码和运行时间。
- 检查是否是可执行文件、Python 解释器、动态库或环境变量问题。
- 用最小可执行输入重跑，不要先跑完整模型。

### 2. 卡死 / 同步

- 检查是否为 stream/event 未同步、队列空转、buffer 未释放或设备等待。
- 记录最后一条成功日志和同步调用。
- 不通过无限延长 timeout 掩盖明确卡住的位置。

### 3. 内存 / 越界

- 核对输入输出字节数、对齐、Buffer 容量和生命周期。
- 检查重复释放、重复使用、未释放和越界写入。
- 对 310B 设备内存以 `npu-smi` 和实际进程状态为依据，不用桌面 GPU 经验猜测。

### 4. 设备异常

- 检查设备节点、驱动模块和 `npu-smi` 是否仍可见。
- 只有用户明确授权时才允许 reset、重启服务或修改设备状态。
- 设备异常与模型业务异常分开报告。

## 常用只读检查

通过 `ascend_board_exec`：

```text
npu-smi 信息
ps / 进程命令
dmesg 中相关设备错误（按权限）
/var/log 下相关 CANN 日志（按权限）
```

通过 `ascend_board_run` 运行最小复现程序，必须显式 `allowStateChange: true` 并限制工作目录。

## 停止条件

- 设备已经从系统消失：停止重跑模型，保留状态并交给用户处理。
- 需要 reset、升级、删除或修改系统文件：停止，要求用户明确操作。
- 没有可复现输入、日志或版本：只能写 `blocked` / `untested`。
- 错误发生在 device 层时，不要继续做数值或性能判断。
