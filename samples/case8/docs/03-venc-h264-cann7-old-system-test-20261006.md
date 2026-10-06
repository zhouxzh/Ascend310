# CANN 7.0 旧系统 VENC H.264 对照测试

## 测试范围

本次测试针对香橙派提供的旧系统镜像，系统时间戳按用户提供的标识记录为
`20241128`。板端 IP 为 `192.168.1.100`，root 和 `HwHiAiUser` 均可免密 SSH。
测试不启动 case8 WebRTC、不接摄像头、不加载 OM 推理，也不修改 CPU fallback；
只运行仓库 [chapter5/venc/venc_minimal.py](../../chapter5/venc/venc_minimal.py)
完成独立 ACL VENC 烟雾测试。

## 实测软件、固件和内存基线

| 项目 | 实测值 | 证据状态 |
| --- | --- | --- |
| 主机与系统 | `orangepiaipro`；Ubuntu 22.04.3 LTS；内核 `5.10.0+ #1 SMP Tue Nov 26 15:45:36 CST 2024`；系统启动记录 `2024-11-27 21:52` | observed-pass |
| SoC 与 NVE | Ascend 310B4；`npu-smi` 显示 8T；NVE `8T_1.0GHz`；Health 为 `Alarm` | observed-pass |
| 驱动 | `23.0.0`；`Innerversion=V100R001C15SPC002B224`；`compatible_version_fw=[7.0.0,7.1.99]` | observed-pass |
| CANN | `/usr/local/Ascend/ascend-toolkit/7.0.0`；CANN `7.0.0`；内部版本 `V100R001C15SPC003B226` | observed-pass |
| CANN runtime/pyACL | runtime `Version=7.1.0.3.220`；`runtime_dvpp_version=1.0`；pyACL `Version=7.0.0` | observed-pass |
| 系统文件中的固件版本 | `/usr/local/Ascend/firmware/version.info`：`Version=7.1.T11.0.B226`，`firmware_version=1.0` | observed-pass |
| 芯片实际 Flash 固件 | `/var/davinci/driver/upgrade-tool --device_index 0 --component -1 --version`：HBOOT、HSM、HLINK、ATF、SysBase、UserBase 均为 `7.3.3.0.b309`；DDR 为 `7.5.8.0.b060` | observed-pass |
| npu-smi 固件字段 | `Firmware Version: 7.3.3.0.b309` | observed-pass |
| 内存 | `MemTotal=15984272 kB`；`CmaTotal=786432 kB`；`CmaFree=758492 kB`；`HugePages_Total=15`；`Hugetlb=30720 kB`；`ulimit -l=1998032` | observed-pass |
| 内核命令行 | 含 `enable_ascend_share_pool`、`ascend_enable_all`、`movable_node`、`cma=256M` | observed-pass |

这里必须区分两个固件版本来源：系统文件仍是 B226，而芯片 Flash 和
`npu-smi` 报告的是 B309。驱动的 `compatible_version_fw` 上限为 7.1.99，
与实际 Flash 的 7.3.3.0.b309 存在配套风险；这是 `documented`/`inferred`
的版本关系，不能单独当作失败根因。本次实测表明，在这套旧驱动/CANN 组合下，
即使实际 Flash 为 B309，VENC 仍可工作。

## 最小 H.264 测试结果

执行环境为 `base` conda、CANN 7.0 `set_env.sh`，Python 为
`/usr/local/miniconda3/bin/python`。测试时间为 `2026-10-06 17:43:57 +08:00`。

```text
VENC H.264 Baseline 通道已创建  640x480@30fps
H.264 Baseline 已编码关键帧: 14,971 字节
VENC H.265 Main 通道已创建  640x480@30fps
H.265 Main 已编码关键帧: 8,528 字节
资源已释放。
process exit: 0
```

H.264 是 `observed-pass`：通道创建、关键帧编码和资源释放均成功；H.265
同时通过，但它不是本次 H.264 结论的必要条件。

## 内核与 CANN/AICPU 证据

测试进程对应 SLOG 为 `device-app-4420`。CANN/AICPU 正常加载并解析 DVPP
VENC API：

```text
Get api DvppCreateVencChannel from so libdvpp_kernels.so success.
Get api DvppSendVencFrame from so libdvpp_kernels.so success.
Get api DvppDestroyVencChannel from so libdvpp_kernels.so success.
Mpi Dvpp event statistic: [0]
```

测试时间窗口内，内核没有出现 `iommu_map failed -34`、
`media_prot_mem_malloc ret:-34`、`h264e_create_chn alloc encoder node buffer
failed`、`venc_create_chn_by_type Error 0xa008800c` 或 `507018`。启动阶段仍可见
以下平台级记录：

```text
arm-smmu-v3 ... ias 48-bit, oas 48-bit
svm_bus svm0_dvpp0: DMA mask not set
svm_bus svm0_dvpp0: no reg, FW should install the sid
APEI Generic Hardware Error, severity: recoverable
[DRV_LPM_FAULT] ... event_id=0x10E3A203
```

这些记录是本次系统的已观察平台告警，但没有阻止本次最小 VENC 成功；不能把
它们直接等同于上一套系统中的 VENC 映射失败。

## 与 CANN 8.0/9.0 结果的对照结论

- `observed-pass`：`20241128 / CANN 7.0 / driver 23.0.0` 在实际 Flash B309
  的 310B4/8T 板上完成 H.264 硬件编码。
- `observed-fail`：此前 `20250925 / CANN 8.0 / driver 25.2.0` 和同平台的
  CANN 9.0 测试均在创建 H.264 通道时返回 `507018`，并出现
  `iommu_map failed -34` 与 `h264e_create_chn` 缓冲区分配失败。
- `inferred`：VENC 参数和 ACL 调用序列本身不是必然错误；故障更可能与
  `driver 25.2.0/C21`、CANN 8/9 运行时、BSP/SMMU 和实际 B309 固件之间的
  组合有关。旧系统的通过结果不能单独证明某一组件已被供应商正式支持。
- `untested`：没有更换驱动、固件或 CANN，也没有修改 IOMMU；因此尚未验证
  哪个单一版本变更可以稳定修复新系统。

## 结论

在同一块 310B4/8T 板上，旧 `20241128 / CANN 7.0` 系统的 CANN VENC H.264
可以正常工作。当前证据支持把问题范围收窄到新系统的软件配套组合，而不是
case8 摄像头、WebRTC、OM 推理或 H.264 通道参数的普遍错误。未执行任何固件、
驱动、CANN、重启或 IOMMU 改动。
