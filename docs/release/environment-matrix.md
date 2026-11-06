# Ascend 310B 环境兼容矩阵

本文只维护跨案例的环境基线和当前状态。具体命令、模型、日志、性能和逐项验收证据由各案例的 `samples/caseN/docs/` 持有。

## 统一运行环境

```text
conda environment: ascend
Python: 3.9.25
ABI: cpython-39-aarch64-linux-gnu
user site: disabled with PYTHONNOUSERSITE=1
```

CANN、NPU driver、固件、ALSA/PulseAudio 和系统工具由开发板提供，不复制进 Python 环境。案例执行不得回退到 `base` 或依赖 `~/.local`。

## 板级兼容组

| 档位 | CANN | NPU driver | Python | 环境锁 |
| --- | --- | --- | --- | --- |
| Ascend310B4 / 8T | 7.0.0 | 23.0.0 | 3.9.25 | [`dependencies/ascend-py39-cann70.lock`](../../dependencies/ascend-py39-cann70.lock) |
| Ascend310B1 / 20T | 8.0.0 | 25.2.0 | 3.9.25 | [`dependencies/ascend-py39-cann80.lock`](../../dependencies/ascend-py39-cann80.lock) |

两组锁文件是候选冻结记录，不是跨档位等价证明。8T 和 20T 的 CANN、驱动、固件、模型和性能结果必须分别记录，不能合并排名。

## 案例状态

下表是最近一次汇总的机器状态。各案例文档中的命令、版本和证据边界优先于本表；更换板卡或软件版本后必须重新验证。

| 案例 | 最近记录 | 边界 |
| --- | --- | --- |
| [案例 1](../../samples/case1/docs/03-board-acceptance.md) | 20T 健康检查、真实摄像头抓拍和 clockin API 通过 | 无真实授权人脸样本，不宣称身份识别准确率 |
| [案例 2](../../samples/case2/docs/06-validation-and-troubleshooting.md) | 20T SSD OM 完成单视频检测与跟踪 | 单模型、单视频协议，不代表其余候选模型 |
| [案例 3](../../samples/case3/docs/10-benchmark-results.md) | 20T 测试、Piano OM 和 WebUI API 通过 | 真实 MIDI、音频和触摸屏验收单列 |
| [案例 4](../../samples/case4/docs/02-device-deployment-and-acceptance-report.md) | 20T CCNet OM ready，识别热路径有运行记录 | 空模板库，无真实掌纹 EER/Rank-1 |
| [案例 5](../../samples/case5/docs/README.md) | 20T 统一环境单元测试通过 | 真实采集、OM 和硬件稳定性单独验证 |
| [案例 6](../../samples/case6/docs/05-validation-and-limitations.md) | CANN 7 与 CANN 8 组完成 ONNX、ATC、OM 和 NPU 分类 | 模型无训练分类头，不宣称场景精度；8T CANN 8 待复测 |
| [案例 7](../../samples/case7/docs/07-validation-and-troubleshooting.md) | 20T 测试、三类 NPU embedding 和图文搜索通过 | 使用合成照片，无真实个人照片 |
| [案例 8](../../samples/case8/docs/04-deployment-and-testing.md) | OM camera、NPU 推理和 WebRTC 降级链路有记录 | 当前 CANN 8.0/driver 25.2 VENC 返回 `507018` |
| [案例 9](../../samples/case9/docs/04-validation-and-limitations.md) | 20T release acceptance 与语音可听验收有记录 | 8T Qwen 在线加载受 HBM/runtime 初始化阻断 |

## 证据边界

- 环境可导入、模型可加载、单次推理、数值一致性、任务精度和性能是不同门槛。
- `observed-pass`、`observed-fail`、`blocked` 和 `untested` 不得混写。
- 原始板端日志和截图不进入根 `docs/`；需要保留时遵循 [文档归属规则](../governance/documentation-ownership.md)。
