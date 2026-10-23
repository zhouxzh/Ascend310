# 检测流水线

## 模型与输入

`utils.preprocessing.resolve_model_path()` 根据文件名发现模型：

```text
CPU: ssd300_<backbone>.onnx / ssd320_<backbone>.onnx
NPU: ssd300_<backbone>.om   / ssd320_<backbone>.om
```

模型输入由文件名决定：

- `resnet` 或 `ssd300` → `300×300`，8732 个 prior；
- `mobilenet` 或 `ssd320` → `320×320`，3234 个 prior。

这是文件名契约，不是从 ONNX meta 自动推断。改名或传错 `--model` 会改变解码器选择。

## 预处理

`preprocess_frame()`：

```text
BGR → resize → RGB
0..255 → 0..1
HWC → CHW
ImageNet mean/std
NCHW float32
```

输入 backend 前会变为连续 NumPy 数组。

## 推理与解码

`DetectionBackend.infer_with_profile()` 把耗时分成：

```text
preprocess / inference / decode
```

`decode_detections()` 先尝试识别 SSD boxes/scores 输出：

1. 形状匹配 300/320 prior 时使用 `SSDDecoder`；
2. 支持 detection_boxes/scores/classes/num_detections 命名输出；
3. 支持 `[N,6]` 或 `[N,7]` 的通用检测张量；
4. 其他输出在 NPU strict 模式下直接报错。

解码后按类别执行 NMS，再按 `score-threshold`、`max-detections` 和可选 `allowed_class_ids` 过滤。坐标按原帧宽高缩放并裁剪。

## 命令

```bash
cd samples/case2
python scripts/detection_app.py --device cpu --source demo/vtest.avi --no-display
python scripts/detection_app.py --device npu --source 0 --save output/tracking.mp4
python scripts/detection_app.py --device npu --list-models
```

## 输出与限制

- 检测框、类别和分数由模型输出与后处理共同决定。
- `--score-threshold` 和 `--nms-threshold` 是显示/筛选参数，不是模型精度。
- 单帧绘制成功不等于 COCO mAP、检测率或跨场景质量。
- `Device: NPU` 只应在 NPU backend 真正初始化后出现。
