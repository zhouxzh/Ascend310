# 06 - 可见光自动注册与阈值校准

## 运行定位

当前工作台用于可见光、规则 ROI 的掌纹 1:N 候选检索。`person_id:left`
和 `person_id:right` 是两个独立掌纹身份；系统不自动推断左右手，也不会按掌侧
预过滤检索结果。模板命名空间保持为 `<model_id>__npu__mixed_fp16>`，并且只和
`contour_valley_v1` ROI 管线的模板比较。

## 注册流程

注册会进行时自动分配递增的人员编号，格式为 `P-000001`，显示名可选。已保留的编号不会因取消会话而复用，以避免并发或服务重启引起的身份冲突。完成一只掌后，界面可保留同一编号登记另一只掌。相同模型、人员编号和掌侧
再次注册时会原子替换该掌的 embedding；只有替换成功后才删除此前关联的采集图片。

- 自助模式：倒计时 3 秒后服务端独占摄像头，连续采集 5 张合格样本并自动保存。
- 管理员辅助模式：同样自动采集 5 张；审阅者可删除单张 ROI，系统会继续自动补采，
  到 5 张后才允许确认保存。
- 每帧都检查 `contour_valley_v1` ROI、清晰度、掌区域、位置、角度和尺度稳定性；
  约连续 400 ms 稳定才可入选，样本间至少间隔 0.8 秒。拒绝帧只计数，不保存图片。
- 等待合格手掌超过 20 秒、持续无法稳定超过 30 秒、摄像头异常或用户取消时，服务
  结束会话、释放摄像头并删除会话的入选图片和 ROI。
- 删除模板会删除对应的原图、ROI、`metadata.json` 和 `captures/index.json` 记录。

已完成的模板不能通过取消会话删除。要移除已注册掌纹，必须在“掌纹模板管理”确认删除。

## 校准前识别

未存在本机校准记录时，实时识别只返回 `candidates_only`、Top-K 候选和分数；界面不
显示“通过”或“未通过”。ROI 失败和空模板库分别返回 `roi_rejected` 与
`no_templates`。

规则 ROI 会返回裁剪四角、中心、角度和尺度。先记录实际摄像头下的 ROI 成功率、
拒绝原因、几何漂移和单帧耗时；未达到验收线前不接入学习式关键点、左右手或 ROI 网络。

## 私有可见光校准

校准集至少包含 10 人的双掌，即 20 个独立 `palm_id`。每掌至少有 5 张注册样本和
两次独立采集的 10 张探测样本。即使左右掌属于同一人，也必须作为非匹配对。

在目标开发板、已激活 conda/CANN 的 shell 中先生成仅含特征向量的私有 JSON。每项的
最小格式为：

```json
{"records":[{"palm_id":"P-001:left","role":"enroll","embedding":[0.1]}, {"palm_id":"P-001:left","role":"probe","embedding":[0.1]}]}
```

该文件不能进入 Git。随后运行：

```bash
python -m tools.offline.calibrate_visible_light \
  --embeddings /secure/private/visible-light-embeddings.json \
  --output "$PALMPRINT_ROOT/data/calibration/visible_light.json" \
  --model-id ccnet \
  --camera-resolution 1280x720 \
  --om "$PALMPRINT_ROOT/models/om/ccnet_mixed_fp16.om" \
  --approve
```

工具输出 EER、FAR/FRR、Top-1、真实/非真实分数分布和推荐阈值，并记录 OM 字节数及
SHA-256、ROI 管线版本、摄像头分辨率和私有输入清单哈希。运行时只有在模型、NPU
精度、ROI 管线、实际摄像头分辨率和 OM SHA-256 都一致时，才会使用这份记录将结果
切换为 `matched` 或 `below_threshold`。修改其中任一项后必须重新校准。

## 上线清理旧模板

部署完成并确认 `/api/templates` 的当前 namespace 后，先核对旧模板和关联采集目录，
再逐项删除旧模板。不得对 `data/` 使用宽泛删除命令。旧的未标注 ROI 管线模板不会被
新识别流程使用，必须以新流程重新注册。
