# 左右掌检测器接入工作台

## 范围

本记录对应2026-11-16把已转换的左右掌NanoDet接入掌纹工作台。目标板是有C922的Ascend310B4 / 8T，当前地址为`192.168.1.108`。同一块板当时仍在批量转换扫表ONNX，程序先在本地改完，等ATC彻底结束后再部署。20T没有使用，现有服务模型在ATC结束前没有切换。

## 模型合同

工作台检测器改为双类别合同，不再使用旧单类别`[1,2125,33]`：

```text
模型：plus-m-320
类别：left_palm、right_palm
输入：float32[1,3,320,320] BGR直接缩放并归一化
输出：float32[1,2125,34]，前2通道为已sigmoid的左右掌分数，后32通道为4组8-bin DFL
分数阈值：0.35
类别内NMS IoU：0.60
OM路径：models/om/plus-m-320_8t_allow_mix_precision.om
OM字节数：6327161
OM SHA-256：e9b021d882ff571f42956e7243027748ede48ea37340a0910879e36644a4a21d
精度模式：allow_mix_precision
```

该OM来自摄像头8T板上`workspaces/case4/nanodet-sweep-20261116/atc-allow-mix/om/plus-m-320.om`。它与seed43参考ONNX哈希不同，不能把先前seed43完整test集的mAP直接写成这个OM的摄像头准确率。

## 程序改动

检测器按类别独立NMS，返回`palm_side=left|right`。实时识别、摄像头预览和注册画面都会显示左掌/右掌框。全图识别不以检测掌侧过滤模板；但注册是写入身份模板的路径，画面中仍只能有一只手，且检测掌侧必须与人工选择一致。两者不一致时拒绝该帧、不裁剪ROI、不保存样本，直到检测结果与选择一致。

本轮是明确标记的离线调试包，不是GitHub固定commit加Hugging Face固定revision的发布复现。真实摄像头定位、左右掌核对和注册质量仍需人工检查。

## 调试帧归档

检测接口默认不保存实时C922画面。为调查左右掌误判，可在启动服务前显式设置`PALMPRINT_DETECTOR_DEBUG_CAPTURE=1`。调试模式仅影响`/api/cameras/{device}/detections`，每个服务进程新建一个私有目录，保存完整原图JPEG、带左右掌框和分数的标注JPEG、同名JSON检测结果。JSON还记录左掌和右掌各自最高分、超过阈值的候选数量及前三个候选框，因此即使最终没有框，也能分析分数是否低于阈值。图片保存在发行目录的`reports/debug/detector/`下，不进入模板库、数据集、Git或正式测试材料。

默认每秒最多保存1帧、每个服务进程最多120帧，分别可由`PALMPRINT_DETECTOR_DEBUG_CAPTURE_MIN_INTERVAL_SECONDS`和`PALMPRINT_DETECTOR_DEBUG_CAPTURE_MAX_FRAMES`调整。达到上限或间隔未到时，接口仍完成检测并返回`debug_capture.saved=false`，不会中断NPU推理。`GET /api/health`中的`detector_debug_capture`字段显示是否开启、已保存数量和当前运行目录标识。

调试启动示例：

```bash
cd /home/HwHiAiUser/Documents/Ascend310/deployments/case4/8t/offline-debug-20261116-handed
PALMPRINT_DETECTOR_DEBUG_CAPTURE=1 \
PALMPRINT_DETECTOR_DEBUG_CAPTURE_MAX_FRAMES=60 \
PALMPRINT_DETECTOR_DEBUG_CAPTURE_MIN_INTERVAL_SECONDS=1.0 \
python -m palmprint_workbench.api --host 0.0.0.0 --port 7861
```

结束左右掌排查后，停止这个调试服务并以默认环境重新启动，不设置该变量。调试图像包含生物特征，确认问题和记录结论后应由操作者清理对应调试目录。

一次用户确认左掌的C922连续采样中，既有正确左掌帧，也有持续只显示右掌的帧。右掌帧的右类最高分约为`0.49–0.78`，左类最高分约为`0.13–0.32`且未超过`0.35`阈值；正确左掌帧的左类最高分约为`0.50–0.67`，右类低于阈值。这说明误判发生在模型类别分数本身，不能以固定交换类别、降低阈值或页面标注修复。原始图像和JSON只留在8T受限目录，详细台账见[测试问题记录](05-troubleshooting.md)。

## 板端部署

ATC扫表完成后，源码部署到`/home/HwHiAiUser/Documents/Ascend310/deployments/case4/8t/offline-debug-20261116-handed/`，模型资产在`/home/HwHiAiUser/Documents/Ascend310/assets/case4/models/8t/palm-handed-20261116/`。旧发行目录未覆盖。服务从该目录监听`0.0.0.0:7861`，访问地址为[http://192.168.1.108:7861/](http://192.168.1.108:7861/)。

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| ATC扫表 | `observed-pass`：16个模型，无失败；`plus-m-320`已有OM被跳过 | 板端`atc-bulk.log` |
| 检测OM完整性 | `observed-pass`：6,327,161字节，SHA-256`e9b021d882ff571f42956e7243027748ede48ea37340a0910879e36644a4a21d` | 新资产目录 |
| CCNet OM完整性 | `observed-pass`：与registry一致 | 从上一发行资产复制，未改字节 |
| HTTP健康与前端 | `observed-pass`：`GET /api/health`、`GET /api/bootstrap`、`GET /`均为200；C922登记为`/dev/video2` | 服务日志 |
| 摄像头检测 | `observed-pass`：`POST /api/cameras/%2Fdev%2Fvideo2/detections`三次200；首次约1693毫秒，后续约23毫秒；当前画面0框 | 链路和ACL加载门，不是真实左右掌定位 |
| 注册掌侧核对 | `observed-pass`：8T板端测试确认注册帧的检测掌侧必须与人工选择一致；不一致则拒绝且不保存样本。全图检索仍不使用类别过滤模板 | 注册数据完整性门，不是身份识别质量 |
| 运行回归 | `observed-pass`：8T板端13项多掌检测/API测试通过；服务重启后C922检测接口返回200并加载左右掌OM | API、ACL加载和代码合同门，不是真实左右掌质量 |
| 真实左右掌画面 | `observed-fail`：用户确认左掌的连续帧出现右掌类别高分 | 归因仍为模型类别分数不稳定，需补充训练标签与姿态对照 |

服务日志在该发行目录的`reports/system/service.log`。没有保存真实摄像头截图，也没有写入人物模板。最终左右掌框、注册核对和识别质量由你在页面上人工确认。

## 当前人工测试结论

用户已在当前8T / C922工作台场景完成人工测试，确认本次测试路径无问题，并要求直接发布。该结论是当前设备、摄像头、页面对话和测试动作下的`user-reported`结果，不是全人群、全姿态、全光照的准确率结论，也不替代EER、Rank-1、长期稳定性或左右掌边缘姿态评价。
