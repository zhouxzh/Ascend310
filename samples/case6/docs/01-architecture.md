# 系统架构

案例 6 把道路图像分成两条互补的感知路径：

```text
道路图像
  ├─ OpenCV 车道线检测（CPU）
  └─ ResNet18 场景分类（优先 NPU，失败后 CPU）
        ↓
Gradio 页面：标注图 + 分类结果 + 后端状态
```

## 组件职责

| 组件 | 文件 | 职责 |
| --- | --- | --- |
| Web 入口 | `app.py` | 接收图像、调用感知、渲染结果 |
| 车道线检测 | `lane_detector.py` | 灰度、模糊、Canny、ROI、Hough、拟合和叠加 |
| 场景分类 | `scene_classifier.py` | ResNet18 预处理、NPU/CPU 推理和 Softmax |
| 配置 | `config.py` | 模型、数据、类别、阈值和 NPU device id |
| 模型准备 | `prepare_models.py` | 保存 PTH、导出 ONNX、调用 ATC |
| 安装 | `setup.sh` | 安装系统/Python 依赖并准备模型 |

## 数据流

1. Gradio 把上传图像转换成 BGR NumPy 数组。
2. `LaneDetector.detect()` 返回左右车道线段、斜率和 Hough 线段数。
3. `LaneDetector.draw_overlay()` 在图像副本上绘制绿色叠加。
4. `SceneClassifier.classify()` 把图像缩放到 `224×224`，按 ImageNet 均值和标准差归一化，执行 NPU 或 CPU 推理。
5. `app.py` 将 Top-3 类别、置信度、建议、耗时和后端写回页面。

## 边界

- 车道线检测是经典 CV，不经过 NPU；不要把它的结果说成 NPU 推理。
- 场景分类只有在 `use_npu=true` 且 OM 成功加载时才是 NPU 证据。
- CPU 回退可保持页面可用，但不构成 Ascend 310B 通过证据。
- 页面绑定 `0.0.0.0`；`--share` 会创建公网入口，默认没有认证。只在受控环境使用。
