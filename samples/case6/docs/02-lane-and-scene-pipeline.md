# 车道线与场景分类流水线

## 车道线检测

`LaneDetector.detect(image_bgr)` 的实际步骤：

1. BGR 转灰度；
2. `GaussianBlur`，kernel 来自 `config.GAUSSIAN_KERNEL`；
3. `Canny(50, 150)`；
4. ROI 掩码：保留图像下方 `ROI_TOP=0.6` 到 `ROI_BOTTOM=1.0` 的区域；
5. `HoughLinesP`：rho 2、theta 1°、阈值 50、最小长度 40、最大间隙 100；
6. 按斜率分成左右车道，过滤 `|slope|<0.4` 和垂直线；
7. 用线段长度加权，以 `x = slope*y + intercept` 拟合；
8. 将左右车道外推到图像底部和 ROI 顶部。

当前 `_apply_roi()` 实际生成的是**全宽矩形 ROI**，不是注释中写的梯形。文档和后续实现必须按代码事实写，不能把矩形 ROI 描述成透视梯形。

返回字段：

```text
left_lane / right_lane       (x1,y1,x2,y2) 或 None
left_slope / right_slope    平均斜率或 None
num_lines_found             过滤前 Hough 线段数
```

`draw_overlay()` 先画左右线段，再与原图按 `LANE_OVERLAY_ALPHA=0.4` 混合；左右都检测到后填充多边形。填充并不代表车道几何精度已经验证。

## 场景分类

`SceneClassifier.preprocess()`：

```text
resize 224×224
BGR → RGB
uint8 → float32 / 255
ImageNet mean/std
HWC → CHW → NCHW
```

类别来自 `config.SCENE_CLASSES`：

```text
highway / urban / intersection / parking / tunnel
```

`classify()` 对 logits 做数值稳定的 Softmax，返回 Top-1 和 Top-3。`app.py` 显示 Top-3、置信度、建议、总耗时和后端。

## 证据边界

- Hough 线段数和绿色叠加只证明 CV 管线执行。
- ResNet18 的 Top-1 只有在有训练权重和固定验证集时才有质量意义。
- 置信度不是准确率；不能用一张图或页面结果证明模型性能。
- 运行耗时包含车道线检测、图像绘制、分类和页面组装，不等于 NPU 单算子延迟。
