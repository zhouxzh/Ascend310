# 案例 2 验证与问题记录

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-10-23 |
| 开发板 | Orange Pi `.249`，Ascend310B4 / 8T |
| SoC / 档位 | Ascend310B4 / 8T |
| CANN / 驱动 | CANN 8.0 / driver 25.2.0 |
| Python / 框架 | conda `ascend` Python 3.9.25；PyACL |
| 模型 / revision | `ssd320_mobilenetv2_050.om`，6,061,476 bytes，SHA-256 `d795943d5a8ea2b59afa91dc97140a5eaf199bee1f7970f1f59738a879818b1d` |
| 输入与命令 | `demo/vtest.avi`；检测与跟踪均使用 `--device npu --no-display` |

## 板端实测结果

```text
ssd320_mobilenetv2_050.om
input:  320x320
output: (1,4,3234), (1,81,3234)
video:  768x576@10, FFMPEG, MJPG
```

- `detection_app.py` 完整处理 vtest，输出 `output/case2-ssd.mp4`，17,118,578 bytes。
- `tracking_app.py` 完整处理 vtest，输出 `output/case2-tracking.mp4`，18,376,244 bytes。
- 两个进程退出码均为 0，没有 NPU/decoder/视频写入错误。

该结果是单模型、单视频文件 smoke，不是 mAP、MOTA/IDF1 或实时摄像头
性能结论。其余 14 个 SSD 候选模型尚未逐一做板端验收。

## 本地检查

```bash
cd samples/case2
python -m py_compile scripts/detection_app.py scripts/tracking_app.py scripts/download_models.py scripts/convert_onnx_to_om.py
python scripts/detection_app.py --device cpu --source demo/vtest.avi --no-display
python scripts/detection_app.py --device cpu --source demo/vtest.avi --save output/check.mp4
```

本地没有 `onnxruntime`、`acl`、摄像头或 CANN 时，记录为本地限制，不能当作板端失败。

## 板端验收门

1. 记录 `npu-smi`、SoC、CANN、驱动和模型文件。
2. 确认目标 SoC 对应的 OM 已生成。
3. NPU 加载和单帧推理成功。
4. 输出 boxes/scores/classes 与 decoder prior 数匹配。
5. 视频文件路径完成检测和跟踪。
6. 真实摄像头完成首帧、分辨率/FPS 和退出释放。
7. 保存视频可打开，轨迹 ID/类别显示正确。
8. 记录每阶段耗时，但不能把单次 FPS 当作性能结论。

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| 暂无 | - | - | - | 现有文档没有带日期的原始失败证据 | - |

## 已知边界

- `DeepSORT` 不使用外观 embedding，只是 Kalman + IoU/中心距关联。
- ONNX/OM 输入尺寸由文件名推断；改名是契约变化。
- ATC 默认 SoC 是 `Ascend310B4`；20T 必须显式指定 `Ascend310B1`。
- 下载器没有内置 SHA-256 校验，发布资产需要外部 manifest 核验。
- CPU 检测和 NPU 检测的证据分开记录；CPU 跟踪结果不能替代 NPU 结果。
- 当前没有跟踪 MOTA/IDF1 或检测 mAP 数据集报告。

出现真实失败时按 `CASE2-ISS-YYYYMMDD-NN` 补充复现、原始输出、判定层级和回归结果，完整模板见 [troubleshooting-template.md](../../../tools/dsh-plugin-ascend310b-writing/skills/ascend310b-writing-docs/references/troubleshooting-template.md)。
