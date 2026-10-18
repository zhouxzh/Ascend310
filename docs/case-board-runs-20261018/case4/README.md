# case4 8T 摄像头复测（2026-10-18）

## 结论

8T 的摄像头预览失败不是模型或 API 问题，而是**设备编号漂移**导致默认设备指向了不能出帧的 ISP 虚拟节点。

- 2026-10-17 记录时：`/dev/video0` 是 ISP 节点，USB 摄像头在 `/dev/video2`。
- 2026-10-18 本次复测：USB 摄像头在 `/dev/video0`、`/dev/video1`；ISP `vin-media_mainpath` 在 `/dev/video2`、`/dev/video3`。

设备编号会随上电/插拔顺序变化，`PALMPRINT_CAMERA_DEVICE` 默认值固定为 `/dev/video0`，因此在编号反向的那次运行里必然打不开。

## 板端枚举（v4l2-ctl --list-devices）

```text
vin-media_mainpath (platform:media:isp-vir0):
	/dev/video2
	/dev/video3

USB Composite Device: USB Camer (usb-xhci-hcd.3.auto-1):
	/dev/video0
	/dev/video1
```

## 两个节点的能力差异

| 节点 | 驱动 | 类型 | 格式 | 结果 |
| --- | --- | --- | --- | --- |
| `/dev/video0` | `uvcvideo` | USB Composite Device | MJPG 1920×1080 @25fps | 可取帧 |
| `/dev/video2` | `vin-media` | `vin-media_mainpath`（ISP 虚拟节点） | `0/0`、像素格式为空、planes 0 | 不能出帧 |

## 取帧实测

```text
video0: http=200 bytes=50348 time=0.454564s
  JPEG 1280x720, components 3
video2: http=503 bytes=130
  {"detail":"摄像头错误: /dev/video2: Camera /dev/video2 returned no frame; /dev/video3: Camera /dev/video3 returned no frame"}
```

## 识别实测（/dev/video0，resolution=1280x720）

```json
{"outcome":"no_templates","accepted":false,
 "quality":{"sharpness":141.66,"brightness":143.22,"contrast":52.85,"roi_area_ratio":0.455625},
 "roi_pipeline_id":"fixed_guide_v1",
 "timing":{"ROI / quality ms":29.70,"preprocess ms":3.30,"model ms":45.85,"search ms":0.90,"total ms":3854.04}}
```

- 摄像头取流、固定引导框 ROI、质量评估、CCNet NPU 推理与检索都实际执行。
- 模板库为 0，因此 `outcome=no_templates`，不能记录为身份匹配成功。
- `npu-smi info` 推理后显存占用 `3302 / 15610 MB`（运行前 `2039 MB`），与 NPU 侧模型加载一致。

## 服务启动命令（本次实际使用）

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Ascend310/cases/case4
PALMPRINT_PROFILE=manual_test PALMPRINT_CAMERA_DEVICE=/dev/video0 \
  python -m palmprint_workbench.api --host 0.0.0.0 --port 7860
```

`/api/health` 返回：`status=ok`、`runtime_importable=true`、`model_ready=true`、`admitted_model_ids=["ccnet"]`、ONNX 与 OM 的字节数与 SHA-256 均匹配。

## 证据分级

- `observed-pass`：USB 摄像头枚举、取帧（1280×720）、健康检查、模型加载、ROI 与 NPU 推理计时。
- `observed-fail`：`/dev/video2`、`/dev/video3`（ISP 节点）取帧返回 503 `returned no frame`。
- `untested`：模板注册、身份匹配、EER/Rank-1 等识别指标、长时间稳定性、硬件触发与补光。

## 隐私

前端截图为真实摄像头画面，按仓库规则**不提交**，保存在本机
`C:\Users\zhoux\Ascend310-local-evidence\case4\board-8t-video0-retry.png`。

## 已修复

设备发现现在会在 `VIDIOC_QUERYCAP` 之后追加一次 `VIDIOC_G_FMT`，要求宽度与高度都大于 0。`vin-media_mainpath` 报 `0×0`，因此不再进入候选列表；USB 节点报 1280×720，正常保留。修改位于 `samples/case4/palmprint_workbench/runtime/camera.py`，并新增了针对该分支的单元测试。