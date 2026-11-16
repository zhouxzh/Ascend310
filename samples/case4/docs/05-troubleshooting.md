# 案例 4 测试问题记录

> 本文件是案例 4 的唯一测试问题台账。原始板端报告、日志和真实掌纹资产保留在受控设备目录，不进入源码仓库。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | 2026-09-11 |
| 开发板 | Ascend 310B4 / 8T |
| SoC / 档位 | Ascend 310B4 / 8T |
| CANN / 驱动 | 正确 conda/CANN 环境；具体版本见板端报告 |
| Python / 框架 | 板端 conda 环境；具体版本见板端报告 |
| 模型 / revision | 六个 embedding 候选；revision 和 OM hash 见候选报告 |
| 代码提交 | 未记录 |
| 输入与命令 | `python -m tools.offline.candidate_campaign ...`；候选报告契约 |
| 原始证据 | 板端私有目录 `/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/candidates-corrected/` |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CASE4-ISS-20260911-01` | `open` | `blocker` | 2026-09-11 | 六个 CompNet 候选出现 `err_ret=-512` / driver cleanup | 未恢复；候选保持 `acl_failed` |
| `CASE4-ISS-20261023-01` | `resolved` | `major` | 2026-10-23 | aarch64 上 V4L2 格式偏移错误，真实 UGREEN 摄像头被自动发现过滤 | 修复后板端 camera tests `10 passed`，三帧采集和 HTTP 预览通过 |
| `CASE4-ISS-20261106-01` | `mitigated` | `major` | 2026-11-06 | 8T可见光与20T红外未隔离，校准/模板/摄像头准入和注册并发存在合同缺口 | 代码、隔离环境、离线调试包和静态门通过；NPU服务/API人工验收待板端受控启动 |
| `CASE4-ISS-20261116-01` | `open` | `major` | 2026-11-16 | C922场景中用户确认的左掌会在部分姿态被`plus-m-320`判为右掌 | 8T受限调试帧记录显示模型右类分数确实高于左类；根因尚待训练标签和输入姿态对照确认 |

## CASE4-ISS-20260911-01：六个 CompNet 候选的 ACL 运行时阻断

### 状态与影响

- 状态：`open`
- 级别：`blocker`
- 影响对象：`ACL / device / candidate-campaign`
- 影响范围：六个具备实际 OM 的 embedding 候选；生产默认 CCNet 不受影响
- 是否阻塞发布：阻塞五个 CompNet 的生产准入；不改变 CCNet 的发布边界

### 预期行为

六个候选分别完成 ACL load/run/close，输出维度、有限性和资源释放通过，并进入后续人工功能测试。

### 实际症状

六个候选完成 10 次独立 ACL load/run/close 后，独立诊断 trace 均出现同一类 `err_ret=-512` 和 driver cleanup 事件，最终状态为 `acl_failed`。这不是模型质量或识别准确率结论。

### 复现步骤

1. 在 Ascend 310B4 / 8T 板端激活正确的 conda/CANN 环境。
2. 按候选 manifest 准备对应 OM。
3. 对单个候选执行 `candidate_campaign` 的 load/run/close 和诊断 trace。
4. 读取候选报告中的 ACL、生命周期和最终状态文件。

### 原始证据

- 摘要：[候选模型审计](03-model-candidate-audit.md)
- 板端目录：`/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910/reports/candidates-corrected/`
- 证据摘要：[candidate-campaign-20260910](evidence/candidate-campaign-20260910.md)
- 完整日志、真实图像和模板不进入 Git。

### 判定层级

`device / ACL runtime / driver cleanup`。失败发生在运行时生命周期，不是输入合同、分类精度或模型权重解析。

### 根因状态

- 根因结论：`hypothesis`
- 结论说明：多个独立候选出现相同运行时清理事件，指向共享的 ACL/驱动/设备路径；现有材料尚不足以证明单一根因。

### 临时处理与修复

- 临时处理：候选保持 `manual_test_pending` / `acl_failed`，生产 registry 继续只包含 CCNet。
- 代码/配置修复：无。
- 未采用方案：不重写网络来掩盖失败，不把 ACL smoke 写成质量通过，不升级冻结版本。

### 回归验证

| 检查 | 修复前 | 修复后 | 证据 |
| --- | --- | --- | --- |
| 单候选 ACL load/run/close | 10 次基础调用通过 | 诊断仍出现 `-512` | [候选模型审计](03-model-candidate-audit.md) |
| 六候选诊断 trace | 出现 `err_ret=-512` | 未恢复 | 板端私有报告 |
| 生产 CCNet | 可用 | 保持默认 | [发布清单](04-release-checklist.md) |

回归结论：`open`。需要新的设备/驱动诊断证据，不能在当前版本内宣称修复。

### 剩余边界

尚未完成根因定位、驱动/CANN 组合对照、长时间稳定性、识别质量、触摸屏人工测试和真实摄像头闭环。

### 关联文档

- [候选模型审计](03-model-candidate-audit.md)
- [发布与人工测试清单](04-release-checklist.md)
- [设备部署与功能验收](02-device-deployment-and-acceptance-report.md)

## CASE4-ISS-20261023-01：aarch64 V4L2 格式偏移导致真实摄像头被过滤

### 状态与影响

- 状态：`resolved`
- 级别：`major`
- 影响对象：`V4L2 自动发现 / USB camera preview`
- 硬件：`0c45:2283 UGREEN Camera 1080P`
- 环境：Ascend 310B4 / 8T，CANN 8.0 / driver 25.2，Python 3.9.25 `ascend`

### 实际症状

内核 `v4l2-ctl` 和 OpenCV `CameraCapture` 均能打开摄像头并抓取
1280x720 MJPG，但 `list_v4l2_devices()` 返回空列表，camera smoke 将真实
设备误判为“不是 V4L2 设备”。

### 根因

`struct v4l2_format` 的 `type` 字段占 4 字节，但格式 union 在 aarch64 上按
8 字节对齐。原代码从偏移 4 读取 width/height，实际得到 `(0, 1280)`，
因此 `width > 0` 检查失败。

修复为：

```python
_V4L2_FORMAT_WIDTH_OFFSET = 8
```

### 回归验证

| 检查 | 结果 |
| --- | --- |
| `tests/board/test_camera_capture.py` | `10 passed` |
| V4L2 原始抓帧 | 1280x720 MJPG，3 帧成功 |
| CameraCapture | 3 帧 RGB，平均约 315 ms |
| 正式 API open/frame/close | 通过 |
| HTTP 预览 | MJPG 输入，960x540 JPEG，12,483 bytes |

### 剩余边界

本次证明了相机采集和预览链路；NPU 掌纹模型、识别质量、模板质量和真实
掌纹业务闭环仍需要准入模型资产后单独验收。

## CASE4-ISS-20261106-01：跨光谱合同、隔离环境与服务生命周期缺口

### 状态与影响

- 状态：`mitigated`
- 级别：`major`
- 影响对象：可见光/红外摄像头合同、模板和校准隔离、摄像头预约、注册取消、评测取消、上传图像边界与ACL清理。
- 硬件范围：Ascend310B4 / 8T可见光与Ascend310B1 / 20T红外分别记录，不合并识别质量或性能结论。

### 实际症状

旧实现允许任意`/dev/videoN`进入同一可见光模板和阈值路径；模板没有按光谱和物理摄像头隔离。校准生成器未写入物理摄像头、ROI配置和光谱，已批准校准无法在运行时命中。自动注册、识别和预览之间还存在摄像头预约竞态，ACL构造失败后的清理没有可靠重试。

### 修复

- 摄像头发现结果增加模态、光谱和准入合同；API只接受最近一次V4L2发现清单中的设备。
- 模板搜索和写入绑定模态、光谱、物理摄像头ID和ROI配置；可见光与红外不共享模板。
- 校准报告写入并校验物理摄像头ID、ROI配置、光谱、模型合同和OM哈希。
- 注册提交/取消、摄像头预约和评测取消增加受控状态转换；上传图片增加解码后尺寸和像素数上限。
- ACL runner构造失败时重试资源清理。
- 两块板统一使用`/home/HwHiAiUser/Documents/Ascend310`作为部署、资产、工作区和归档根；DSH任务路径被收紧到受控工作区。旧`ascend`环境保留为`ascend-legacy-20261106`，新的隔离`ascend`环境在两板通过Python3.9.25、`PYTHONNOUSERSITE=1`、OpenCV、PyACL、`aclruntime`和MindSpore Ascend上下文导入门。

### 回归验证

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| Case4 Python编译与目标单元测试 | 通过；板端依赖缺失项跳过 | 代码/测试门，不代表NPU推理 |
| Case4前端测试、构建与资产校验 | 通过 | UI静态资产门 |
| 8T、20T隔离`ascend`环境导入门 | 通过 | 环境门，不代表模型加载 |
| 8T可见光与20T红外摄像头V4L2采集 | 通过 | 采集门，不代表识别质量 |
| NPU服务、API、模板闭环与人工页面 | 待执行 | 需要每板固定来源模型资产与受控服务启动 |

### 剩余边界

本轮为明确标记的离线调试包，未使用固定Git commit和固定Hugging Face revision重建模型资产，不能作为发布复现证据。两块板的Case4服务尚未完成受控启动；模型加载、数值一致性、跨光谱模板隔离、阈值校准、任务质量、性能和人工页面验收继续保持`untested`。

## CASE4-ISS-20261116-01：左掌在C922连续帧中被判为右掌

- 状态：`open`；影响层级为`task-quality / camera`，不视为ATC、ACL加载或前端标签映射故障。
- 环境：Ascend310B4 / 8T、CANN8.0.0、驱动25.2.0、Python3.9.25、C922`/dev/video2`、`plus-m-320`的`allow_mix_precision` OM。服务是`offline-debug-20261116-handed`离线调试发行，未作为公开复现或生产准入。
- 现象：用户确认左掌掌心在C922画面中时，检测标签在连续帧间会变化；一段帧显示左掌，更多帧只显示右掌，另有少量帧同时给出左右两类框。
- 已确认：后处理固定把输出通道0映射到`left_palm`、通道1映射到`right_palm`，没有水平翻转，也不按画面左右位置重命名。两类独立阈值和类别内NMS，因此同一目标可同时出现两类框。调试模式保存的JSON证明这不是显示层替换：用户确认左掌的一组连续有效帧中，右类最高分约`0.49–0.78`且有7–13个候选超过`0.35`，左类最高分约`0.13–0.32`且没有候选超过阈值；另有少量帧左类最高分约`0.50–0.67`、右类低于阈值，显示正确左掌。
- 根因状态：`hypothesis`。该现象与模型在C922姿态、距离、光照或掌心旋转下的类别边界不足一致；也需继续核对训练标签的解剖学左右约定与实际镜像/朝向。当前数据不支持把原因写成CANN或8T数值错误，也不支持固定交换左右标签。
- 已采取处理：增加默认关闭的`PALMPRINT_DETECTOR_DEBUG_CAPTURE`。开启后仅将检测请求的原图、带框图和JSON元数据写入板端发行目录的`reports/debug/detector/`；JSON记录左右类最高分、阈值候选数和前三候选框。默认每秒最多1帧、每次服务最多120帧，且不写入模板库或Git。本次受限调试会话上限为60帧。注册保留严格掌侧核对：检测标签与人工选择不一致时拒绝帧，不创建或更新模板；检测标签仍不用于全图检索的模板过滤。
- 未采用方案：不因为个别左掌帧把所有`right_palm`映射为左掌；不降低总阈值来把低置信左掌框强行显示；不把同一位置两类框按固定类别覆盖；不允许检测标签与人工选择不一致的帧进入注册模板。
- 后续验证：分别采集用户确认的左掌和右掌，在掌心正对、旋转、距离、拇指可见性和不同光照下比较分数分布。若训练标签约定与部署预览约定冲突，应在训练/导出合同层修复；若仅特定姿态错误，应补充该姿态样本并重新训练。完成后由操作者清理板端调试图像。
- 原始证据：仅保留在8T发行目录的`reports/debug/detector/`，不复制真实掌纹图像或原始JSON到源码仓库。部署和调试模式说明见[左右掌检测器接入工作台](17-handed-detector-workbench-20261116.md)。

## CASE4-ISS-20261115-01：实时检测不触发与笔记本画面受挤压

- 状态：`mitigated`；最终效果等待人工确认。
- 环境：Ascend310B4 / 8T，CANN8.0.0、驱动25.2.0，`ascend`环境Python3.9.25，C922摄像头；本地未提交源码的人工调试部署，检测器来源提交及哈希见`detector_manifest.json`。
- 首次发现：2026-11-15。症状为预览请求持续返回200，但页面没有检测框；笔记本分辨率下画面区域过窄。
- 根因状态：`confirmed`。检测依赖全画面背景变化及稳定触发，不能保证持续调用；识别结果中的静态预览优先覆盖实时帧。左右固定最小宽度及画面高度伸缩使笔记本显示受挤压。新增连续检测还暴露线程切换后的`host_to_device failed: 107002`，对应ACL context恢复晚于输入拷贝。
- 修复：增加不归档图像的摄像头`/detections`接口及串行轮询；显示同一次检测的画面和坐标。ACL context在输入拷贝之前恢复。画面按自然宽高比居中`contain`显示，笔记本采用上下布局。
- 复现及回归：服务从`deployments/case4/8t/manual-camera-20261115`启动，日志为该发行目录的`reports/system/service.log`。连续4次`POST /api/cameras/%2Fdev%2Fvideo0/detections`返回200，检测约39毫秒，采样画面返回0个框；接口回归6项、前端15项通过，前端构建通过。合成画面在1366×768、1920×1080、390×844下比例与框显示正确，无横向溢出。
- 证据边界：合成框验证只证明绘制和布局；当前采样0框不证明真实手掌定位通过。真实单掌、多掌及笔记本画面最终效果由用户人工检查。不调低模型阈值，不保存真实摄像头截图。

## CASE4-ISS-20261115-02：同一手掌的嵌套框可能在IoU抑制后保留

- 状态：`mitigated`；用户观察两只手显示三个框，最终效果等待人工复核。
- 环境与来源：Ascend310B4 / 8T、CANN8.0.0、驱动25.2.0、Python3.9.25、C922。人工调试发行目录为`deployments/case4/8t/manual-camera-20261115`，源码是当前未提交调试版本，模型来源提交及哈希见`detector_manifest.json`。
- 根因状态：配置不生效为`confirmed`；用户三框场景的具体原因仍为`hypothesis`。IoU公式及索引更新正确，DFL和priors未发现确定性错误；近乎同中心的大、小嵌套框即使包含率很高，IoU仍可能小于0.60而同时保留。现场抽样为0或1个框，未复现三框，也未保存真实摄像头图像。
- 实际修复：将清单中的分数、IoU阈值传入解码，分数0.35和IoU0.60保持不变；增加可选的包含抑制，要求交集占较小框面积至少0.90、面积比至少0.25、两个轴的中心距离均不超过较小框对应尺寸的0.25。保留高分框，排序同分时稳定；过滤非有限和零面积框。源码清单的`nms_containment_threshold`默认是`null`（关闭）；本次`.100`人工调试实例显式设为`0.90`便于现场对比，不能视为生产默认。将该配置设为`null`或删除后重启即可关闭包含抑制。
- 未采用方案：不限制最多两个框，不凭两只手场景降低分数阈值或随意合并所有相交框。
- 回归：板端`python -m unittest tests.unit.test_multi_palm -v`的12项测试通过，覆盖同框、位移重复框、低IoU嵌套框、相邻框、偏心包含框、三个独立手掌框、无效框和清单参数传递。更新后连续3次检测HTTP 200，首轮初始化约1946毫秒，后续约40毫秒；画面采样0框，不构成真实手掌去重通过证据。
- 剩余边界：真实前后重叠、近乎同中心的两只手仍可能被该包含启发式误抑制，用户需要对单掌、双掌及部分遮挡进行人工复核。模型框级误检与NMS不能仅靠计数相同区分。服务日志留在对应发行目录的`reports/system/service.log`，不将原始图像或运行报告提交Git。

## CASE4-ISS-20261115-04：左右掌NanoDet五精度转换及输出偏差

- 状态：`open`；首次发现为2026-11-15。详细数据和文件身份见[五精度记录](14-handed-nanodet-five-precision-20261115.md)。
- 环境：Ascend310B4 / 8T，CANN8.0.0、驱动25.2.0、Python3.9.25、NumPy1.22.4，目标板MAC为`c0:74:2b:fd:dd:7a`。模型为ace1的seed43左右掌双类别ONNX，输入`data:float32[1,3,320,320]`，SHA-256为`35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991`；本次是未提交调试程序与离线训练导出的实验，不是公开源码复现。
- 预期：五种模式分别转换并在相同输入、解码和计时协议下对照ONNX参考，记录成功、失败及最大/平均误差，不把ATC成功当成数值或任务质量通过。
- 实际症状：首轮五模式均返回255，日志EC0010缺少`decorator`；补齐编译依赖后，未设置图/算子编译并发限制的第一份编译期间板端SSH/HTTP失联并随后重启，尚无OOM或内核证据。设置`TE_PARALLEL_COMPILER=1`与`MAX_COMPILE_CORE_NUMBER=1`后四种模式转换成功；`must_keep_origin_dtype`仍返回255/EZ3002，对应MaxPoolV3的DT_FLOAT输入不满足当前内核dtype支持。
- 判定层级：首次依赖失败为`test-harness / conversion`，根因为`confirmed`；板端失联为`device / OS`、根因`unknown`；保留dtype模式失败为`conversion / operator-contract`、根因`confirmed`。四种成功OM均可ACL执行和清理，但对20个同源参考张量的`allclose(atol=0.02,rtol=0.02)`均为0/20，属于`numerical`观察，其算子根因为`unknown`。
- 已采取处理：按现有板端编译环境补齐依赖；明确限制两项ATC编译并发后串行重跑；保留首轮日志与新日志。运行时比较补充CANN source与PyACL路径。未修改精度容差，不复制另一模式结果填补失败项，不自动替换服务。
- 原始证据：板端`workspaces/case4/handed-seed43-five-20261115/logs/`、`logs/single-worker/`及`reports/`保存退出码、日志和逐样本结果；该目录位于`/home/HwHiAiUser/Documents/Ascend310/`。仓库只保留不含图片/张量的结构化JSON、CSV及中文汇总。
- 最小复现：在该目录加载`ascend`和CANN，设置两项编译并发为1后执行`bash convert-five.sh`；对已成功OM执行`bash run-benchmark.sh <mode>`。转换失败时保留原始退出码，不参与数值或速度排名。
- 回归：四种原始成功模式均完成20样本比较、30次预热及5轮×100次计时，清理成功；最大绝对误差约3.665至3.667，平均绝对误差约0.09865至0.09874，详细分组与框统计见关联记录。后续1600张完整test集上，新增`force_fp32 + --op_select_implmode=high_precision_for_all`对照：新OM转换成功且日志不含默认FP32路径的tiling告警，但预测SHA、raw误差、mAP和左右掌计数与默认FP32逐项相同，P50略慢。三样本中间层探针进一步显示：骨干各出口仅有小误差，FPN的`reduce_layers.2`输出MAE约`2.68e-5`，第一次`Resize(half_pixel, linear)`输出立即到约`7.11e-3`，随top-down融合扩大到40×40端点约`2.94e-2`。该结果将首个显著边界定位到FPN第一次Resize之后；独立Resize OM比较需在目标板空闲后执行，不能提前归因于单算子。该结果排除“默认高性能实现优先级单独造成差异”的假设，但仍需独立验证Resize单算子与共享图融合差异，并以真实摄像头单掌、双掌和左右手注册操作进行人工复核。

## CASE4-ISS-20261115-05：双类别NanoDet完整test评测的环境与脚本故障

- 状态：`mitigated`；完整结果见[CUDA与8T完整test记录](15-handed-nanodet-fulltest-8t-20261115.md)。
- 环境：ACE2 RTX 5090 D运行ONNX Runtime 1.23.2 / CUDAExecutionProvider，8T为Ascend310B4、CANN8.0.0、驱动25.2.0、Python3.9.25。测试模型与标注哈希分别为`35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991`和`5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a`。
- 症状：ACE2首次ONNX创建会话时找不到`libcublasLt.so.12`，ORT自动仅保留CPU provider；评测器按设计停止，没有回退运行。ACE1到ACE2首次同步保持了ACE1数据盘的符号链接，导致ACE2的1600张test图全部失效。8T首次FP32评测进入ACL后，进度日志引用了流式改造前的`reference`变量，报告为`NameError`。
- 根因状态：均为`confirmed`。ACE2新隔离环境未包含CUDA运行时库；ACE1 test图源是指向`/data2`的符号链接；8T脚本从整包读取改为流式读取时没有同时替换进度消息变量。
- 修复：ACE2启动器显式使用现有torch环境的CUDA12库目录，ORT profile确认494,709个CUDA节点、零CPU节点。重新用`rsync -L`传入图像实际JPEG，并核对1600/1600图像和每图SHA。8T将reference pack改为流式校验，修正变量后从FP32完整重跑；四种成功OM均处理1600张并完成ACL清理。
- 回归：ACE2 CUDA ONNX和8T四份成功OM全量报告均为`completed`，所有8T输出有限。首次失败日志保留在受控ACE2/8T工作目录；不把初次失败归为模型精度模式失败。20T按照用户明确要求未执行。
- 剩余边界：test标注来自MediaPipe自动生成，尚非独立人工真值；CUDA/8T结果不能外推到20T、摄像头或生产注册流程。

## CASE4-ISS-20261115-03：页面高度溢出与注册摄像头会话未交接

- 状态：`mitigated`；页面效果及最终注册质量等待人工检查。
- 环境：上述`.100`的Ascend310B4 / 8T、C922、人工调试发行目录和模型资产不变。
- 症状及根因：用户反馈视频需要整页滚动、文字和页头过大，切换注册页后不可用。布局固定大字号、大页头与纵向最小高度导致可用画面空间不足；注册页开始前没有预览，离页未取消注册，取消失败后仍清空本地状态，可能留下独占摄像头的后台会话。
- 实际修改：900像素及以上宽度使用紧凑桌面工作台，视频和主要按钮固定在视口内；长结果列表局部滚动，视频居中且保持完整比例。注册前提供预览，开始采集前关闭预览会话，启动失败清理已创建会话，离页取消未结束会话，取消失败时保留本地状态。注册状态请求改为串行。
- 回归：前端16项测试和构建通过。浏览器合成画面核对1366×768、1920×1080、1024×768的四个页面，没有根页面和工作页面溢出，视频位于可见视口。实际浏览器操作注册开始两次均为HTTP 200并进入倒计时；显式取消和离页取消各一次均返回200，切回实时页后检测恢复200。未执行五个合格掌纹样本的保存和身份质量验收。
- 原始证据：板端日志位于该发行目录的`reports/system/service.log`；本地合成布局检查位于仓库临时目录`tmp/package-staging/case4-camera-20261115`。没有保存真实摄像头截图，未创建人物模板；最后以用户实际屏幕和操作判定。
