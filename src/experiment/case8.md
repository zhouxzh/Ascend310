# 案例 8：实时手势识别

## 项目简介

本案例在昇腾 310B 上实现实时手势识别。系统从 USB 摄像头读取视频，使用
HaGRID YOLOv10 模型在 NPU 上完成目标检测，把检测框和标签绘制到原始画面，
再使用 H.264/WebRTC 将结果发送到浏览器。

案例代码位于 `samples/case8`。其中 `hagrid_yolo` 保存模型预处理、推理后端
和后处理，`webrtc_app` 保存摄像头、DVPP 和 CANN VENC 适配，`scripts` 保存
模型转换、OM 推理和 WebRTC 服务入口，`web` 保存浏览器页面。

完整的工程说明位于 `samples/case8/docs/`：

- `docs/README.md`：工程文档入口；
- `docs/01-project-design.md`：项目设计；
- `docs/02-program-analysis.md`：程序解析；
- `docs/03-model-dataset-and-conversion.md`：模型、数据集与转换；
- `docs/04-deployment-and-testing.md`：部署与测试；
- `docs/05-known-issues-and-version-compatibility.md`：问题与版本兼容性。

## 学习目标

完成本案例后，你应能够：

- 说明 HaGRID 手势检测任务和 YOLOv10 输出含义；
- 将 PyTorch 权重导出为 ONNX，并使用 ATC 转换为 Ascend OM；
- 使用 PyACL 加载 OM，完成摄像头帧的预处理、推理和后处理；
- 解释 NV12、CANN VENC、CPU `libx264` 和 WebRTC 的关系；
- 在开发板上完成 OM 冒烟、摄像头测试和远程 WebRTC 验收。

## 系统流程

```mermaid
flowchart LR
    accTitle: 实时手势识别流程
    accDescr: 摄像头图像经过预处理和 OM 推理，检测结果绘制回原图后编码为 H.264，通过 WebRTC 发送到浏览器。
    cam[USB 摄像头] --> pre[预处理]
    pre --> om[ACL 加载 OM]
    om --> post[后处理与绘制]
    post --> enc[CANN VENC 或显式选择的 CPU 编码]
    enc --> rtc[WebRTC]
    rtc --> browser[浏览器]
```

原始画面可以是 1280x720 等采集尺寸，但模型输入固定为静态
`1x3x640x640`。程序使用 letterbox 保持宽高比，再转成 RGB、NCHW 和
归一化张量。推理输出经过置信度过滤、坐标恢复和 NMS 后绘制回原图。

## 模型与数据集

本案例使用 HaGRID 提供的 YOLOv10 手势检测权重，不在开发板上训练模型。
导出脚本同时生成 ONNX、标签文件和元数据；ATC 在目标 Ascend 310B 上生成
OM。模型输入尺寸、输入名称和类别顺序以元数据及 OM 描述为准，不能在网页
中动态修改模型输入尺寸。

模型导出示例：

```bash
cd samples/case8
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models --imgsz 640 --batch 1 --opset 13 --device cpu
```

开发板转换示例：

```bash
cd /home/HwHiAiUser/Documents/case8
source /usr/local/Ascend/ascend-toolkit/set_env.sh
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
```

## OM 推理

模型转换成功后，先执行不接摄像头的 benchmark：

```bash
python scripts/infer_om_camera.py \
  --benchmark-runs 20 --warmup-runs 5 --print-model-info
```

再执行摄像头推理：

```bash
python scripts/infer_om_camera.py \
  --source /dev/video0 --camera-width 1280 --camera-height 720 \
  --camera-fps 30 --max-frames 60 --no-window
```

benchmark 只验证 ACL 能够加载和执行 OM；摄像头测试还会验证采集、预处理、
后处理和资源释放。两者都通过后再进入 WebRTC 测试。

## WebRTC 推流

启动服务：

```bash
python scripts/webrtc_om_app.py
```

浏览器访问开发板打印的局域网地址，默认是 `http://192.168.1.100:8080`。
服务通过 `/models` 列出 OM，通过 `/offer` 完成 SDP 协商，通过 `/stats` 提供
采集、推理和编码状态。

网页的编码器选项有两个：

- `CANN VENC`：推理仍在 NPU，H.264 使用 Ascend VENC；通道创建或编码失败
  时明确报错并停止，不自动切换 CPU。
- `CPU libx264`：仅在用户明确选择时使用 CPU 编码，OM 推理仍在 NPU。

因此 CPU 编码可以作为明确的对照路径，但不能作为 CANN VENC 失败后的隐藏
回退。

## 验收顺序

1. 检查 CANN 环境和 `import acl`。
2. 运行 OM benchmark。
3. 运行有限帧摄像头推理。
4. 检查摄像头实际 MJPG/FourCC 和帧率。
5. 启动 WebRTC，选择 CANN VENC，确认浏览器持续收到 H.264。
6. 若 VENC 失败，保存错误码和设备日志，不用 CPU 结果替代硬件验收。

香橙派 8T 的系统版本差异会影响 VENC。当前已验证的旧系统、新系统和
`iommu_map failed -34` 证据集中记录在
`samples/case8/docs/05-known-issues-and-version-compatibility.md`。
