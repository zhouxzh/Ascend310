---
name: ascend310b-npu-arch
description: Ascend 310B1/310B4 的架构与能力核对流程。用于确认 8T/20T 档位、DAV_3002 架构线索、设备能力、条件编译和平台差异；禁止把 Ascend 910/950 的 NpuArch、Cube/RegBase/SIMT、tiling 或性能参数直接套到 310B。
---

# Ascend 310B NPU 架构核对

## 适用范围

只用于 Ascend 310B1（20T）和 Ascend 310B4（8T）。310P、910、950 及其他 Ascend 型号使用各自规范。

本 skill 参考 CANNBot `npu-arch` 的“运行时读取架构、不要硬编码”思想，但只保留 310B 可验证的部分。外部仓库不是 310B 专用；任何参数必须回到当前板卡和当前 CANN 验证。

## 工作流

1. 先用 `ascend_probe` 采集 SoC、chip、tier、CANN、驱动、设备节点和 `import acl`。
2. 以板端返回的 `board.soc` 和 `board.tier` 建立身份：
   - `Ascend310B1` → 20T 证据集；
   - `Ascend310B4` → 8T 证据集。
3. 在程序运行时优先用平台接口读取实际架构和能力，例如 `PlatformAscendC` 的 NpuArch、SocVersion、核数和内存信息；不要仅凭芯片字符串写死条件分支。
4. 需要确认 DAV_3002 的某一个能力时，记录查询接口、返回值、CANN 版本和原始日志，再决定是否启用条件编译。
5. 遇到 910/950 文档中的 Cube、RegBase、SIMT、FP8、HCCL 或跨代迁移参数时，先标记为“不适用于 310B”，不要复制。

## 310B 判断规则

- 8T 与 20T 是两个独立证据集，不合并性能、吞吐或精度结果。
- 架构映射是“线索”，不是兼容性证明；最终以板端运行结果为准。
- 不把商品名、主机名、IP 或旧截图当作芯片身份。
- 不因为一个 API 在 910/950 可用，就推断它在 310B 可用。
- 不因为一个算子失败就写“310B 不支持”；先区分环境、编译、tiling、加载和算子实现层。

## 停止条件

- `scope.applicable` 不是 `true`：停止 310B 分支。
- 档位无法确认：停止写 8T/20T 结论。
- 架构接口返回失败或与探针矛盾：先补证据，不继续调性能。
- 当前 CANN 文档和板端行为冲突：记录冲突，按实测结果和停止条件处理。

## 输出

```text
SoC / tier:
CANN / driver:
架构读取方式:
NpuArch / SocVersion:
设备节点:
结论证据: observed-pass / observed-fail / untested
```
