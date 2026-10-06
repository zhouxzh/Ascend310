# CANN 8.0 新系统 VENC H.264 复测

## 测试范围

本次测试针对用户重新安装的香橙派官方系统。系统日期标识仍为 `20250925`，
IP 为 `192.168.1.100`，root 和 `HwHiAiUser` 均可免密 SSH。用户未对系统做
额外修改；当前 CANN 为系统自带的 8.0。测试不启动 case8 WebRTC、不接摄像头、
不加载 OM 推理，也不修改 CPU fallback。

## 测试基线

| 项目 | 实测值 | 证据状态 |
| --- | --- | --- |
| 主机 | `orangepiaipro`；Orange Pi Ai Pro 8T | observed-pass |
| 系统 | Ubuntu 22.04.3 LTS；系统启动记录 `2025-09-25 21:34`；内核 `5.10.0+ #32 SMP Thu Sep 25 17:54:23 CST 2025` | observed-pass |
| SoC | Ascend 310B4；`npu-smi` 为 8T；NVE `8T_1.6GHz` | observed-pass |
| 驱动 | `25.2.0`，`V100R001C21SPC008B208` | observed-pass |
| 芯片实际固件 | `7.3.3.0.b309`；HBOOT、ATF、HSM、HLINK、SysBase、UserBase 均为 B309 | observed-pass |
| CANN | `/usr/local/Ascend/ascend-toolkit/8.0.0`；CANN `8.0.0`；内部版本 `V100R001C20SPC001B251` | observed-pass |
| CANN runtime | `Version=7.6.0.1.220`，`runtime_acl_version=1.0`，`runtime_dvpp_version=1.0` | observed-pass |
| 内存 | `MemTotal=15984680 kB`，`CmaTotal=786432 kB`，`CmaFree=703804 kB` | observed-pass |

CANN 8.0 安装元数据的 `compatible_version` 到 `V100R001C20`，而当前驱动
内部版本为 `V100R001C21`。这是新系统原始状态下的配套风险，标记为
`documented`/`inferred`，不能把它单独写成根因；后续 CANN 9.0 在相同驱动和
固件组合下也复现了同样的 VENC 映射错误。

## 最小 H.264 测试

测试脚本为仓库 [chapter5/venc/venc_minimal.py](../../chapter5/venc/venc_minimal.py)，
这是原始 ACL VENC 最小程序；本次执行在 H.264 Baseline 通道创建阶段失败，未进入
后续编码步骤。
运行环境为 `base` conda、CANN 8.0 `set_env.sh`。

```text
test time: 2026-10-06 16:55:00 +08:00
python: /usr/local/miniconda3/bin/python
H.264 Baseline venc_create_channel 失败: 507018 (0x7bc8a)
process exit: 1
```

这是 `observed-fail`。失败发生在创建通道阶段，尚未进入摄像头、WebRTC、OM
推理或 RTP。

## 内核与设备日志

同一测试进程 `pid=4799` 的内核日志：

```text
arm_lpae_map -> arm_smmu_map -> iommu_map
[HiDvpp] dvpp_prot_mem_map ... iommu_map failed -34
[HiDvpp] media_prot_mem_malloc ... ret:-34
[HiDvpp] h264e_create_chn alloc encoder node buffer failed!
[HiDvpp] venc_create_chn_by_type Error 0xa008800c chn type 96
```

同一进程的 CANN/AICPU SLOG：

```text
HiMpiVencCreateChnEx failed, ret=a008800c
DvppCreateVencChannel ... failed, ret:5
rtStreamSynchronize ... ErrCode=507018, desc=[aicpu exception]
```

在本次 VENC 测试之前，系统已经出现：

```text
DRV_LPM_FAULT event_id=0x80E3A203
MATA RAS error_code=0x50e
APEI Generic Hardware Error, severity=recoverable
```

这说明 RAS/平台异常先于 VENC 创建失败出现；测试后的 TS/AICPU 异常是连锁
现象，不改变首个错误点。

## 与 CANN 9.0 结果的对照

此前在同一 `20250925` 系统升级到 CANN 9.0 后，独立 H.264 测试同样返回
`507018 (0x7bc8a)`，并在内核报告 `iommu_map failed -34`、
`h264e_create_chn alloc encoder node buffer failed`。
因此：

- `observed-fail`：CANN 8.0 新系统仍无法创建 H.264 VENC 通道。
- `observed-fail`：CANN 8.0 与 CANN 9.0 在相同 25.2.0 驱动/B309 固件平台上出现相同底层错误。
- `inferred`：问题主要位于 Orange Pi BSP/内核 SMMU、Ascend 驱动与 B309 固件组合，或该固件的 DVPP 映射路径；不是 CANN 9.0 升级单独引入的回归。
- `untested`：没有供应商提供的 DVPP/SMMU/VENC 修复包，尚未验证修复后的固件或驱动。

## 结论

香橙派提供的 `20250925` 新系统在未改动状态下，CANN 8.0 的 CANN VENC H.264
硬件编码仍然失败。case8 当前在 CANN 模式采用 fail-closed 策略，只有显式选择时才使用
CPU `libx264`；这能避免用 CPU 编码掩盖硬件故障，但不构成板端 VENC 的根因修复。修复目标
仍是板端 VENC 的 protected-memory IOMMU 映射链路。
