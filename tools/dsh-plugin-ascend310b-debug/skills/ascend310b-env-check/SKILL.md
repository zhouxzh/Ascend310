---
name: ascend310b-env-check
description: Ascend 310B1/310B4 的 CANN 开发环境检查。用于运行 npu-smi、确认设备节点、检查 set_env.sh、区分裸 SSH 与 source CANN 的 import acl、记录 Python/CANN/driver 版本，并在环境门失败时停止后续 ATC、ACL、摄像头或服务测试。
---

# Ascend 310B CANN 环境检查

## 适用范围

只检查 Ascend 310B1/310B4 的目标板。本地控制器可以运行静态检查，但不能替代板端 `npu-smi`、`acl`、`atc`、DVPP 或摄像头验证。

本 skill 参考 CANNBot `ascendc-env-check` 的环境门思想，按照本插件探针输出和 310B 的实际 SSH 权限重新定义检查项。

## 顺序

1. 调用 `ascend_probe`，不要先手工执行一串未记录的 shell。
2. 检查目标身份：`scope.applicable === true`，并确认 8T/20T。
3. 检查设备：`npu-smi`、`deviceNodes`、`kernelModules`。
4. 检查 CANN：`set_env.sh`、Toolkit latest、`cannVersion`。
5. 检查 Python/ACL：解释器路径、`pythonVersion`、`aclImport`、`aclImportSourced`。
6. 检查 ATC：`atcOnPath`，并在需要转换时记录实际版本。
7. 只有环境门通过后，才进入 ATC、OM、DVPP 或服务测试。

## 失败处理

| 现象 | 先查 | 停止条件 |
| --- | --- | --- |
| `npu-smi` 不可用 | PATH、驱动、设备节点 | 设备不可见时不跑模型 |
| 裸 SSH `import acl` 失败 | 是否 source CANN、Python 路径 | 不算模型失败，先修启动环境 |
| source 后仍失败 | CANN/Python/驱动配套、包来源 | 不启动服务，不伪造 CPU fallback |
| `atc` 不在 PATH | source 脚本、Toolkit 路径 | 不做转换结论 |
| 空闲设备有少量 HBM | `npu-smi` 进程占用 | 不误判为设备被业务占用 |
| 摄像头设备不存在 | `/dev/video*`、V4L2、权限 | 不继续写业务逻辑 |

## 证据要求

```text
hostname / MAC:
SoC / tier:
CANN / driver:
Python:
set_env.sh:
import acl 裸 SSH:
import acl source 后:
ATC:
设备节点:
探测时间:
```

当前 310B 默认探测不采集固件版本；不要用驱动版本代替固件版本，也不要把它作为环境门的阻塞条件。

## 停止条件

- `scope.applicable` 不是 `true`。
- 设备节点或 CANN 运行时不可用。
- 同一进程无法复现 `import acl`。
- 需要执行写操作才能继续检查，但用户没有明确授权。
