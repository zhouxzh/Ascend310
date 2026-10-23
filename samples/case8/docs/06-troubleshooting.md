# 案例 8 测试问题记录

> 本文件是案例 8 的唯一测试问题台账。完整版本对照见 [已知问题与版本兼容性](05-known-issues-and-version-compatibility.md)。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2025-09-25 |
| 开发板 | Orange Pi Ascend 310B4 / 8T |
| SoC / 档位 | Ascend 310B4 / 8T |
| CANN / 驱动 | CANN 8.0 / 9.0；驱动 25.2.0 |
| Python / 框架 | 见板端 VENC 测试环境 |
| 模型 / revision | VENC 独立最小测试与 case8 WebRTC 程序 |
| 代码提交 | 未记录 |
| 输入与命令 | 独立 ACL H.264 VENC 最小测试；case8 WebRTC |
| 原始证据 | 板端日志；当前文档保留关键错误摘要 |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CASE8-ISS-20250925-01` | `open` | `blocker` | 2025-09-25 | CANN 8.0/9.0 的 H.264 VENC 在通道创建阶段失败 | 旧 CANN 7.0 通过；新系统未修复 |

## CASE8-ISS-20250925-01：DVPP VENC IOMMU/通道创建失败

### 状态与影响

- 状态：`open`
- 级别：`blocker`
- 影响对象：`CANN VENC / DVPP / driver / SMMU`
- 影响范围：20250925 Orange Pi 系统、CANN 8.0/9.0、驱动 25.2.0、B309 固件组合
- 是否阻塞发布：阻塞 CANN VENC 路径；不阻塞明确选择 CPU `libx264` 的降级演示

### 预期行为

独立最小 H.264 VENC 测试完成通道创建、关键帧编码和资源释放。

### 实际症状

CANN 8.0/9.0 均在通道创建阶段返回 `507018`，尚未进入实际视频帧编码：

```text
acl.init=0
acl.rt.set_device=0
acl.rt.create_context=0
venc_create_channel=507018 hex=0x7bc8a
iommu_map failed -34
media_prot_mem_malloc ... ret:-34
h264e_create_chn alloc encoder node buffer failed
venc_create_chn_by_type Error 0xa008800c chn type 96
```

### 复现步骤

1. 在对应 Orange Pi 系统记录系统日期、驱动、固件、CANN 和 `/proc/cmdline`。
2. 运行独立 ACL VENC 冒烟，不启动 WebRTC、摄像头或 OM 推理。
3. 观察 VENC 通道创建、DVPP 保护内存映射和内核日志。

### 判定层级与根因

`driver/DVPP/VENC/SMMU` 组合层。旧 CANN 7.0 通过、新 CANN 8.0/9.0 失败，官方最小 VENC 示例也能复现，因此不是 WebRTC 页面、摄像头或 OM 推理的首要原因。

根因状态：`hypothesis`。当前范围判断指向 Orange Pi BSP/内核 SMMU、Ascend 驱动、CANN 运行时与 B309 固件组合；没有供应商确认的单一根因。

### 临时处理与修复

- 临时处理：失败时明确报错并停止连接，不自动切换 CPU。
- 未采用方案：没有刷固件、替换驱动、重装 CANN、修改 IOMMU 或宣称 CPU `libx264` 是 NPU VENC 的修复。
- 回归：官方 Atlas 310B4 的 CANN 9.2.0-beta.2 组合在正确用户组和重新插拔摄像头后通过，但不能据此认定 Orange Pi 系统已修复。

### 证据与关联

- [已知问题与版本兼容性](05-known-issues-and-version-compatibility.md)
- [部署与测试](04-deployment-and-testing.md)
