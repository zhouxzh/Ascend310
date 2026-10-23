---
name: ascend310b-env-check
description: Ascend 310B1/310B4 的 CANN 与统一案例环境检查。用于运行 npu-smi、确认设备节点、检查 set_env.sh、验证 conda ascend 环境（Python 3.9.25、user-site 隔离、关键包和 PyACL）、区分裸 SSH 与 source CANN 的 import acl，并在环境门失败时停止后续 ATC、ACL、摄像头或服务测试。
---

# Ascend 310B CANN 与统一环境检查

## 适用范围

只检查 Ascend 310B1/310B4 的目标板。本地控制器可以运行静态检查，但不能替代板端 `npu-smi`、`acl`、`atc`、DVPP 或摄像头验证。

本 skill 参考 CANNBot `ascendc-env-check` 的环境门思想，按照本插件探针输出和 310B 的实际 SSH 权限重新定义检查项。

## 统一案例环境

所有案例统一使用 conda 环境 `ascend`：

- Python 固定为 `3.9.25`，ABI 应为 `cpython-39-aarch64-linux-gnu`。
- 进程必须设置 `PYTHONNOUSERSITE=1`，不得依赖 `~/.local`。
- 案例不得回退到 `base`；`base` 只用于系统诊断或显式排障。
- CANN、NPU driver 和固件由开发板系统提供，不复制进 conda 环境。
- `ascend` 必须在 source CANN 后可以导入 PyACL 和 `aclruntime`，并成功设置 MindSpore Ascend context。
- 统一关键包必须覆盖所有案例；全案例通过前不删除旧环境，通过后再生成锁文件并清理。
- CANN 8.0 官方安装文档支持 Python 3.7.x 至 3.10.x。本项目结合已验证的 MindSpore/MindNLP 组合选择 Python 3.9.25；不能把未经官方矩阵覆盖的本机 Python 3.11 测试扩大成教程兼容结论。

## 顺序

1. 调用 `ascend_probe`，不要先手工执行一串未记录的 shell。
2. 检查目标身份：`scope.applicable === true`，并确认 8T/20T。
3. 检查设备：`npu-smi`、`deviceNodes`、`kernelModules`。
4. 检查 CANN：`set_env.sh`、Toolkit latest、`cannVersion`。
5. 检查统一环境：`ascendEnvPresent`、`ascendEnvVersion`、`ascendEnvAbi`、`ascendEnvPolicyIsolated`、`ascendEnvAclImport`、`ascendEnvAclruntime`、`ascendEnvMindsporeContext`、`ascendEnvPackages`。
6. 检查 Python/ACL：解释器路径、`pythonVersion`、`aclImport`、`aclImportSourced`，并区分系统探测结果与 `ascend` 环境结果。
7. 检查 ATC：`atcOnPath`，并在需要转换时记录实际版本。
8. 只有环境门通过后，才进入 ATC、OM、DVPP 或服务测试。
9. 缺少板端系统工具时，先确认包名和影响，再用 `ascend_apt_install` 的 `dryRun`/显式 `allowStateChange` 安装；不要用任意 root shell 代替。

公版默认密码未修改时，`ascend_apt_install` 可以使用 profile 的 root/sudo 凭据通过 sudo 安装受控 apt 包。凭据只走本机配置，不应出现在命令、日志或 skill 文件里。

## 失败处理

| 现象 | 先查 | 停止条件 |
| --- | --- | --- |
| `ascendEnvPresent=false` | conda env list、解释器路径 | 不启动案例，不回退 base |
| Python 不是 3.9.25 | `ascendEnvVersion`、ABI | 不锁定环境，不继续全案例验收 |
| `ascendEnvIsolated=false` | `PYTHONNOUSERSITE`、`site.ENABLE_USER_SITE` | 不把 user-site 依赖写成案例依赖 |
| `ascendEnvAclImport` 不是 ok | CANN source、Python ABI、PyACL 包 | 不执行 NPU 模型测试 |
| `ascendEnvAclruntime` 不是 ok | CANN source、`aclruntime` 包 | 不执行 case5 OM 推理 |
| `ascendEnvMindsporeContext` 不是 ok | MindSpore/CANN ABI、Ascend 插件动态库 | 不执行 MindSpore 模型；不得用 find_spec 冒充可运行 |
| `ascendEnvPackages` 缺包 | 对应案例依赖清单 | 不用 base/user-site 掩盖缺包 |
| `npu-smi` 不可用 | PATH、驱动、设备节点 | 设备不可见时不跑模型 |
| 裸 SSH `import acl` 失败 | 是否 source CANN、Python 路径 | 不算模型失败，先修启动环境 |
| source 后仍失败 | CANN/Python/驱动配套、包来源 | 不启动服务，不伪造 CPU fallback |
| `atc` 不在 PATH | source 脚本、Toolkit 路径 | 不做转换结论 |
| 空闲设备有少量 HBM | `npu-smi` 进程占用 | 不误判为设备被业务占用 |
| 摄像头设备不存在 | `/dev/video*`、V4L2、权限 | 不继续写业务逻辑 |
| 缺少 `v4l-utils`/`alsa-utils` 等系统工具 | 包名、apt 索引、默认 sudo 密码 | 先用 `ascend_apt_install` dry-run；未显式授权不安装 |

## 证据要求

```text
hostname / MAC:
SoC / tier:
CANN / driver:
系统 Python:
ascend 环境存在:
ascend Python / ABI:
PyTHONNOUSERSITE / policy user-site:
ascend 裸解释器 user-site:
ascend PyACL import:
ascend aclruntime import:
ascend MindSpore context:
ascend 关键包:
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
- 统一 `ascend` 环境缺失、版本错误或依赖 user-site。
- 设备节点或 CANN 运行时不可用。
- 同一进程无法复现 `import acl`。
- 需要执行写操作才能继续检查，但用户没有明确授权。
