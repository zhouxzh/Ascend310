# 跟踪流水线

## 实际跟踪器

`tracking/deepsort.py` 的类名是 `DeepSORT`，但当前实现是轻量跟踪器：

- 中心点卡尔曼预测；
- IOU 关联；
- 中心距归一化作为 fallback；
- 类别兼容性检查；
- 没有 ReID 外观 embedding；
- 没有深度学习特征匹配。

因此不能把轨迹 ID 稳定性写成“DeepSORT 外观重识别”能力。

## 状态

每个 `Track` 保存：

```text
track_id, bbox, score, class_id
trail, width/height smoothing
Kalman state [x,y,vx,vy]
time_since_update, hits
class_scores
```

每帧流程：

1. 对所有现有轨迹执行 `predict()`；
2. 用 IoU 建立轨迹/检测成本矩阵；
3. 只允许类别相同或类别为 `-1` 的匹配；
4. 未匹配项用归一化中心距再次关联；
5. 新检测创建新轨迹；
6. 删除 `time_since_update > max_age` 的轨迹；
7. 只返回本帧已更新且 `hits >= min_hits` 的轨迹。

## 参数

`tracking_app.py` 的默认值：

```text
--track-max-age 90
--track-min-hits 1
--track-iou-threshold 0.3
--track-center-distance-threshold 1.8
--track-size-smoothing 0.8
--track-score-smoothing 0.7
```

`--track-classes person,bus` 或 `1,6` 可限制被跟踪类别。类 id 0（background）不被接受。

## 证据边界

- 轨迹 ID 连续不等于身份识别正确。
- 遮挡、交叉、快速运动可能造成 ID switch；当前没有 MOTA/IDF1 报告。
- 跟踪输入来自检测结果；检测漏检会直接破坏轨迹。
- CPU 与 NPU 检测 backend 的差异需要分别测试；不能把 CPU 跟踪结果外推为 NPU 结果。
