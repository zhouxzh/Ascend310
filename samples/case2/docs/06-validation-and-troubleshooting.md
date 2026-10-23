# 案例 2 验证与问题记录

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 尚无统一带日期的板端报告 |
| 开发板 | 未记录 |
| SoC / 档位 | 8T/20T 必须分开 |
| CANN / 驱动 | 未记录 |
| Python / 框架 | `requirements.txt`；ONNX Runtime/PyACL 按 backend |
| 模型 / revision | ONNX/OM 文件名、大小、SHA-256 必须记录 |
| 输入与命令 | 见下方命令；原始输出需另存 |

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
