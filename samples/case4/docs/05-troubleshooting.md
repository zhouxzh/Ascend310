# 案例 4 测试问题记录

> 本文件是案例 4 的唯一测试问题台账。原始板端报告、日志和真实掌纹资产保留在受控设备目录，不进入源码仓库。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-09-11 |
| 开发板 | Ascend 310B4 / 8T |
| SoC / 档位 | Ascend 310B4 / 8T |
| CANN / 驱动 | 正确 conda/CANN 环境；具体版本见板端报告 |
| Python / 框架 | 板端 conda 环境；具体版本见板端报告 |
| 模型 / revision | 六个 embedding 候选；revision 和 OM hash 见候选报告 |
| 代码提交 | 未记录 |
| 输入与命令 | `python -m tools.offline.candidate_campaign ...`；候选报告契约 |
| 原始证据 | 板端私有目录 `/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/candidates-corrected/` |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CASE4-ISS-20260911-01` | `open` | `blocker` | 2026-09-11 | 六个 CompNet 候选出现 `err_ret=-512` / driver cleanup | 未恢复；候选保持 `acl_failed` |
| `CASE4-ISS-20261023-01` | `resolved` | `major` | 2026-10-23 | aarch64 上 V4L2 格式偏移错误，真实 UGREEN 摄像头被自动发现过滤 | 修复后板端 camera tests `10 passed`，三帧采集和 HTTP 预览通过 |

## CASE4-ISS-20260911-01：六个 CompNet 候选的 ACL 运行时阻断

### 状态与影响

- 状态：`open`
- 级别：`blocker`
- 影响对象：`ACL / device / candidate-campaign`
- 影响范围：六个具备实际 OM 的 embedding 候选；生产默认 CCNet 不受影响
- 是否阻塞发布：阻塞五个 CompNet 的生产准入；不改变 CCNet 的发布边界

### 预期行为

六个候选分别完成 ACL load/run/close，输出维度、有限性和资源释放通过，并进入后续人工功能测试。

### 实际症状

六个候选完成 10 次独立 ACL load/run/close 后，独立诊断 trace 均出现同一类 `err_ret=-512` 和 driver cleanup 事件，最终状态为 `acl_failed`。这不是模型质量或识别准确率结论。

### 复现步骤

1. 在 Ascend 310B4 / 8T 板端激活正确的 conda/CANN 环境。
2. 按候选 manifest 准备对应 OM。
3. 对单个候选执行 `candidate_campaign` 的 load/run/close 和诊断 trace。
4. 读取候选报告中的 ACL、生命周期和最终状态文件。

### 原始证据

- 摘要：[候选模型审计](03-model-candidate-audit.md)
- 板端目录：`/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/candidates-corrected/`
- 证据摘要：[candidate-campaign-20260910](evidence/candidate-campaign-20260910.md)
- 完整日志、真实图像和模板不进入 Git。

### 判定层级

`device / ACL runtime / driver cleanup`。失败发生在运行时生命周期，不是输入合同、分类精度或模型权重解析。

### 根因状态

- 根因结论：`hypothesis`
- 结论说明：多个独立候选出现相同运行时清理事件，指向共享的 ACL/驱动/设备路径；现有材料尚不足以证明单一根因。

### 临时处理与修复

- 临时处理：候选保持 `manual_test_pending` / `acl_failed`，生产 registry 继续只包含 CCNet。
- 代码/配置修复：无。
- 未采用方案：不重写网络来掩盖失败，不把 ACL smoke 写成质量通过，不升级冻结版本。

### 回归验证

| 检查 | 修复前 | 修复后 | 证据 |
| --- | --- | --- | --- |
| 单候选 ACL load/run/close | 10 次基础调用通过 | 诊断仍出现 `-512` | [候选模型审计](03-model-candidate-audit.md) |
| 六候选诊断 trace | 出现 `err_ret=-512` | 未恢复 | 板端私有报告 |
| 生产 CCNet | 可用 | 保持默认 | [发布清单](04-release-checklist.md) |

回归结论：`open`。需要新的设备/驱动诊断证据，不能在当前版本内宣称修复。

### 剩余边界

尚未完成根因定位、驱动/CANN 组合对照、长时间稳定性、识别质量、触摸屏人工测试和真实摄像头闭环。

### 关联文档

- [候选模型审计](03-model-candidate-audit.md)
- [发布与人工测试清单](04-release-checklist.md)
- [设备部署与功能验收](02-device-deployment-and-acceptance-report.md)

## CASE4-ISS-20261023-01：aarch64 V4L2 格式偏移导致真实摄像头被过滤

### 状态与影响

- 状态：`resolved`
- 级别：`major`
- 影响对象：`V4L2 自动发现 / USB camera preview`
- 硬件：`0c45:2283 UGREEN Camera 1080P`
- 环境：Ascend 310B4 / 8T，CANN 8.0 / driver 25.2，Python 3.9.25 `ascend`

### 实际症状

内核 `v4l2-ctl` 和 OpenCV `CameraCapture` 均能打开摄像头并抓取
1280x720 MJPG，但 `list_v4l2_devices()` 返回空列表，camera smoke 将真实
设备误判为“不是 V4L2 设备”。

### 根因

`struct v4l2_format` 的 `type` 字段占 4 字节，但格式 union 在 aarch64 上按
8 字节对齐。原代码从偏移 4 读取 width/height，实际得到 `(0, 1280)`，
因此 `width > 0` 检查失败。

修复为：

```python
_V4L2_FORMAT_WIDTH_OFFSET = 8
```

### 回归验证

| 检查 | 结果 |
| --- | --- |
| `tests/board/test_camera_capture.py` | `10 passed` |
| V4L2 原始抓帧 | 1280x720 MJPG，3 帧成功 |
| CameraCapture | 3 帧 RGB，平均约 315 ms |
| 正式 API open/frame/close | 通过 |
| HTTP 预览 | MJPG 输入，960x540 JPEG，12,483 bytes |

### 剩余边界

本次证明了相机采集和预览链路；NPU 掌纹模型、识别质量、模板质量和真实
掌纹业务闭环仍需要准入模型资产后单独验收。
