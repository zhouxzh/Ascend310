---
name: ascend310b-runtime-debug
description: Ascend 310B1/310B4 的 CANN 运行时错误分层排查。用于 import acl、aclrt/ACLNN 返回码、Tiling、Kernel 查找、设备初始化、plog 和环境变量问题；错误码只作为线索，最终结论必须结合 310B 板端输出。
---

# Ascend 310B 运行时错误排查

本 skill 参考 CANNBot `ascendc-runtime-debug` 的分层和错误码思路，但平台范围、探针和停止条件已改为 310B；不替代 CANNBot 原技能。

## 先确认

1. 目标板确实是 310B1 或 310B4；记录 8T/20T。
2. `ascend_probe` 的 `aclImport`、`aclImportSourced`、`deviceNodes` 和 CANN 版本已采集。
3. 保存完整 stderr、返回码、命令、输入 shape/dtype、模型/OM 哈希和 Python/C++ 进程来源。
4. 区分裸 SSH 失败和 source CANN 后失败；两者不是同一结论。

## 错误路径

```text
失败
├─ device / context / stream
│  └─ 设备节点、驱动、npu-smi、aclInit/aclrtSetDevice
├─ import acl
│  └─ source 环境、Python 解释器、CANN/驱动配套
├─ 参数错误
│  └─ shape/dtype/layout/nullptr/对齐
├─ Tiling
│  └─ TilingKey、TilingFunc、shape 边界、workspace
├─ Kernel 查找
│  └─ 算子安装、OPP、版本和 SoC 分支
├─ 运行状态
│  └─ plog、错误码、同步、内存越界
└─ 业务结果
   └─ 数值和输出合同，而不是运行时崩溃
```

## 310B 特有限制

- CANN/驱动/设备版本以板端为准；不根据文件名猜版本。
- 8T 与 20T 不共享错误结论，分别保留证据。
- 不直接套用 910/950 的 Tiling、Cube、RegBase、SIMT 或 HCCL 参数。
- `Health: Alarm` 是诊断字段，不是单独的错误码；仍要找到具体失败。
- 当前默认探针不提供固件版本，不能用固件缺失解释所有运行时问题。

## 采集命令

使用 `ascend_board_exec` 执行只读命令，先 `run: false` 查看等价调用；需要修改环境或运行程序时，使用 `ascend_board_run` 并显式授权。

建议保留：

```text
npu-smi 信息、设备节点、驱动模块
import acl 的完整异常
CANN/驱动/Python 版本
plog 或 stderr 原文
模型、OM、输入 shape/dtype 和命令
```

## 停止条件

- 设备不可见或驱动未加载：停止加载模型。
- 同一进程 source 后仍无法 import：先修环境。
- 没有完整原始错误和输入合同：不要写“算子不支持”。
- 需要 reset、升级或修改设备状态：停止并交给用户明确授权处理。

## 报告

```text
层: environment / device / runtime / tiling / kernel / business
证据: exit code、原始日志、命令、版本、8T/20T
结论: observed-fail / blocked / untested
下一步: 只写一个可验证动作
```
