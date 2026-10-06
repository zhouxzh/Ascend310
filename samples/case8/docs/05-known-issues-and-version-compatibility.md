# case8 已知问题与版本兼容性

## 📌 结论摘要

当前证据支持以下范围结论：

- `20241128` 系统上，CANN 7.0 的独立 H.264 VENC 冒烟测试通过；该系统升级
  到 CANN 8.0 后，case8 WebRTC 已验证正常。
- `20250925` 系统上，CANN 8.0 和 CANN 9.0 的独立 H.264 VENC 都在通道创
  建阶段失败。
- 新系统失败发生在 DVPP 保护内存的 IOMMU 映射，不依赖 case8 的摄像头、
  WebRTC 或 OM 推理。
- 当前没有供应商提供的已验证 VENC/SMMU 修复固件，因此不能把问题写成
  已修复，也不能只归因于 CANN 版本。

## 🧪 环境对照

| 系统 | CANN | 驱动 | 实际 Flash 固件 | 独立 VENC | 证据 |
| --- | --- | --- | --- | --- | --- |
| `20241128` | 7.0 | 23.0.0 | B309 | `observed-pass` | 通道创建、H.264 关键帧和释放成功 |
| `20241128` | 8.0 | 未单独记录完整矩阵 | B309 | `observed-pass` | case8 WebRTC 用户复测正常 |
| `20250925` | 8.0 | 25.2.0 | B309 | `observed-fail` | `507018`、IOMMU 映射失败 |
| `20250925` | 9.0 | 25.2.0 | B309 | `observed-fail` | 与 CANN 8.0 相同底层错误 |

这里的“通过”只针对实际测试过的组合；旧系统的通过不能证明所有驱动、固件
和 CANN 组合都受支持。

## ✅ 旧系统 CANN 7.0 结果

板卡为 Ascend 310B4 / 8T，系统日期为 `20241128`，驱动为 `23.0.0`，CANN
为 `7.0.0`，实际芯片 Flash 为 `7.3.3.0.b309`。独立 ACL VENC 测试输出：

```text
VENC H.264 Baseline 通道已创建  640x480@30fps
H.264 Baseline 已编码关键帧: 14,971 字节
资源已释放。
process exit: 0
```

状态：`observed-pass`。同一次对照测试没有出现 `iommu_map failed -34`、
`h264e_create_chn alloc encoder node buffer failed` 或 `507018`。

## ❌ 新系统 CANN 8.0/9.0 结果

系统日期为 `20250925`，驱动为 `25.2.0`，实际芯片 Flash 为 B309。CANN 8.0
和 CANN 9.0 均在独立最小 H.264 测试中返回：

```text
acl.init=0
acl.rt.set_device=0
acl.rt.create_context=0
venc_create_channel=507018 hex=0x7bc8a
```

同一时间窗口的内核和 DVPP 日志为：

```text
iommu_map failed -34
media_prot_mem_malloc ... ret:-34
h264e_create_chn alloc encoder node buffer failed
venc_create_chn_by_type Error 0xa008800c chn type 96
```

这是 `observed-fail`。失败在 VENC 通道创建阶段，尚未进入视频帧编码；官方
最小 VENC 示例也复现同样错误，因此不是 WebRTC 页面、摄像头输入或 OM 推
理的首要原因。

## 🧭 结论边界

`iommu_map -34` 表示当前组合在 DVPP 保护内存映射阶段无法分配/映射编码器
节点缓冲区。结合旧系统通过、新系统失败，以及 CANN 8.0/9.0 的相同结果，
当前最合理的范围判断是：Orange Pi BSP/内核 SMMU、Ascend 驱动、CANN 运行
时和 B309 固件之间的组合兼容问题。这个判断标记为 `inferred`，不是供应商
确认的单一根因。

当前尚未执行固件刷写、驱动替换、CANN 重装、重启验证或 IOMMU 修改；这些
操作不能写成已验证的修复方案。

## 🛑 case8 的失败策略

程序默认请求 CANN VENC。若 ACL 不可用、VENC 通道创建失败或单帧编码失败：

- `/offer` 或 `/stats` 返回/记录明确的编码错误；
- 前端日志显示 VENC 错误并停止视频连接；
- 程序不会自动切换为 CPU `libx264`。

CPU `libx264` 只有在网页中明确选择，或启动时使用
`--no-hardware-encode` 才会启用。CPU 路径持续运行只能说明 CPU 编码可用，
不能作为 CANN VENC 的通过证据。

## 🔧 排查顺序

1. 记录系统日期、`npu-smi info`、驱动、固件、CANN 和 `/proc/cmdline`。
2. 先运行独立 ACL VENC 冒烟，不启动 WebRTC、摄像头或 OM。
3. 查看 `iommu`、`h264e`、`venc`、`dvpp` 和 `aicpu` 同一时间窗口的日志。
4. 若独立 VENC 通过，再检查 case8 的摄像头、NV12 和 WebRTC 参数。
5. 若独立 VENC 失败，保留 fail-closed 行为，不用 CPU 结果掩盖硬编问题。

后续供应商提供新固件或驱动时，只需在上面的环境表增加一行，并按相同的
独立 VENC、OM、摄像头和 WebRTC 顺序复测。
