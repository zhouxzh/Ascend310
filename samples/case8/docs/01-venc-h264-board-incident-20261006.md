# Orange Pi 8T CANN VENC H.264 板端故障记录

## 记录范围

本记录基线为 2026-10-06，在 `ascend8t`（`192.168.1.100`，主机名
`orangepiaipro`）上以 root 只读核对系统、固件、驱动、CANN、SMMU、内存和
VENC 失败证据。记录重点是 CANN VENC 本身；不修改 CPU fallback。

用户提供的系统历史为：系统镜像时间戳 `20250925`，原有 CANN 8.0，之后将
CANN 升级到 9.0；没有升级 Ascend 驱动和板端固件。

## 当前硬件与软件基线

| 项目 | 当前值 | 证据状态 |
| --- | --- | --- |
| 板卡 | Orange Pi Ai Pro 8T；Ascend 310B4；`8T_1.6GHz` | observed-pass |
| 主机/架构 | `orangepiaipro`；Ubuntu 22.04.3 LTS；aarch64 | observed-pass |
| 内核 | `5.10.0+ #32 SMP Thu Sep 25 17:54:23 CST 2025` | observed-pass |
| 系统时间戳 | `20250925`（用户提供） | documented |
| CANN 当前路径 | `/usr/local/Ascend/cann-9.0.0`，版本 `9.0.0`，内部版本 `V100R001C10SPC001B250` | observed-pass |
| CANN 旧目录 | `/usr/local/Ascend/ascend-toolkit/8.0.0` 仍存在，但当前软链接和环境使用 CANN 9.0.0 | observed-pass |
| Ascend 驱动 | `25.2.0`，`V100R001C21SPC008B208` | observed-pass |
| 实际芯片固件 | `7.3.3.0.b309`；`npu-smi` 显示 `Firmware Version: 7.3.3.0.b309` | observed-pass |
| 驱动固件兼容声明 | `compatible_version_fw=[7.0.0,7.7.99]` | observed-pass |
| 健康状态 | `Health: Alarm`；具体 RAS 见下文 | observed-fail |

驱动和固件没有随 CANN 8.0 到 9.0 的升级而更换；当前 CANN 9 与驱动
25.2.0 的安装元数据显示为一个可初始化的组合，但这不等于 VENC 已通过。

## 可获得的 Orange Pi 固件包

用户目前能获得三份固件说明：

| 包 | 目标 | 与本故障的关系 |
| --- | --- | --- |
| `7.3.T3.0.B309`，8T，20240428 | CPU 1.6 GHz，不切换到 12T | 与当前芯片实际 Flash 版本一致 |
| `7.3.T10.0.B528`，12T，20240605 | CPU 1.6 GHz + AI 12T | 不适用于当前 8T 档位 |
| `7.3.T3.0.B309`，8T，20241022 | 三星 16 GB 内存只识别 8 GB 时使用 | 当前系统已经识别完整 16 GB，不是已验证的 VENC 修复包 |

对两份 8T B309 包做了只读内容比对：`HBOOT1_a`、`HBOOT1_b`、`HBOOT2_UEFI`、
`TrustedFirmware`、`hilink25_fw`、`microwatt`、`sysBaseConfig` 和
`userBaseConfig` 的 SHA-256 相同，只有 `lpddr_mcu.bin` 不同。这与第三份包
用于内存识别修复的说明一致。

板端当前实际内存为：

```text
MemTotal:       15984680 kB
NPU Total Capacity: 16384 MB
```

因此目前没有证据表明需要为了 VENC 刷第三份内存修复包。板上的
`/usr/local/Ascend/firmware/version.info` 为 `7.3.T7.0.B507`，而芯片实际
Flash 为 `7.3.3.0.b309`；这是磁盘固件目录与芯片已刷固件的版本漂移，不能单独
推导为固件损坏。

截至本记录，没有供应商提供的明确 DVPP/SMMU/VENC 修复固件。没有执行任何
固件刷写、重启或 IOMMU 修改。

## 内存与 SMMU 观测

```text
/proc/cmdline: ... enable_ascend_share_pool ... movable_node cma=256M ...
CmaTotal:     786432 kB
CmaFree:      717240 kB
ulimit -l:     1998084 kB
```

设备树包含约 512 MiB 的 `hmi_cma_highmem` 共享 DMA 池，加上内核参数的
256 MiB CMA；测试时 CMA 仍有约 700 MiB 空闲。内存耗尽不是当前 VENC 失败的
充分解释。

设备树和 sysfs 确认 6 个 `arm-smmu-v3` 实例，DVPP 使用独立 SMMU，设备树
`iommus` 的 stream ID 为 `0x1f`。当前内核没有启用 `CONFIG_IOMMU_DEBUGFS`，
所以不能从 debugfs 读取更深层的 IOVA/OAS/IAS 运行时状态。

## VENC 失败证据

重启后的独立最小 H.264 ACL 测试，不依赖 WebRTC、摄像头或 OM 推理：

```text
acl.init=0
acl.rt.set_device=0
acl.rt.create_context=0
set_entype_h264_baseline=0
set_pic_format_nv12=0
venc_create_channel=507018 hex=0x7bc8a
```

内核在同一时刻报告：

```text
arm_lpae_map -> arm_smmu_map -> iommu_map
[HiDvpp] dvpp_prot_mem_map ... iommu_map failed -34
media_prot_mem_malloc ... ret:-34
h264e_create_chn alloc encoder node buffer failed
venc_create_chn_by_type Error 0xa008800c chn type 96
```

官方 `venc_image` 示例也出现相同的 `507018` 和 DVPP 映射失败。故障发生前，
系统已经出现：

```text
MATA RAS error_code: 0x50e
Health Alarm: 0x80f18003
DRV_LPM_FAULT: 0x80e3a203
```

这些是 `observed-fail` 的板端平台证据。后续 AICPU/TS 异常属于 VENC 创建失败
后的连锁现象，不作为首个根因。

## 判定与边界

- `observed-fail`：当前板端 CANN VENC H.264 通道无法创建，错误点在 DVPP 保护内存的 IOMMU 映射。
- `observed-fail`：官方最小 VENC 路径同样失败，排除 case8 WebRTC、摄像头、OM 推理和通道参数作为首要原因。
- `inferred`：问题位于 Orange Pi BSP/内核 SMMU 配置、Ascend 驱动与 B309 固件组合，或 B309 固件自身的 DVPP 映射缺陷。
- `untested`：没有供应商修复包可用于验证“刷写后是否恢复 VENC”；不能把三份现有包中的任意一个写成修复方案。

## 历史文档检索结果

在本次记录创建前，`samples/case8/docs/` 不存在，因此没有发现过去的案例 8
VENC H.264 故障文档。仓库 Markdown 中找到的案例 8 README 和 chapter5 WebRTC
文档只描述正常的 CANN VENC/CPU 编码路径，没有记录本次的
`507018`、`0xa008800c`、`iommu_map -34` 或 `h264e_create_chn` 失败。

本文件是该故障的第一份案例 8 专题记录。后续新增板端证据时，应继续区分
`observed-pass`、`observed-fail`、`documented`、`inferred` 和 `untested`，不把
CPU 编码路径的通过结果当作 CANN VENC 通过。
