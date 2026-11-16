# 19 - PicoDet XS左右掌ONNX与310B部署测试

本文记录ACE1训练完成的`picodet-xs-320`和`picodet-xs-416`从最佳checkpoint导出ONNX，并在ACE2、Ascend310B4/8T和Ascend310B1/20T上进行的独立测试。ONNX导出、ATC转换、ACL加载、原始输出数值和任务mAP是不同证据门，不能互相替代。

## 模型与数据合同

| 模型 | 输入 | 分类输出先验数 | DFL输出 | ONNX SHA-256 |
| --- | --- | ---: | --- | --- |
| `picodet-xs-320-seed-42` | `image float32[1,3,320,320]` | 2125（1600+400+100+25） | 四级，每级32通道 | `bb4b72ba38856db6f79a1e9a239057f55ef903c2128763c341fe9bd7df5026da` |
| `picodet-xs-416-seed-42` | `image float32[1,3,416,416]` | 3598（2704+676+169+49） | 四级，每级32通道 | `c7e7403205ad680677f739a51147dd23c4c2161316a8cc5e0ec9a3fe2b24f7be` |

导出提交为ACE1 `953d80979d6db4eee244ddc32a8ef8cedd1479d9`。Paddle和ONNX对固定随机输入的最大绝对误差分别为`2.146e-6`和`4.231e-6`，两个ONNX均通过checker。

固定test集包含1600张图像和1601条标注，标注SHA-256为`5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a`。预处理为BGR解码后转RGB、直接缩放到方形、OpenCV cubic插值，再按ImageNet均值和标准差归一化；不能复用NanoDet的BGR均值方差或线性插值。

PicoDet输出不能按NanoDet单张量解码：四个分类层和四个DFL层按stride `[8,16,32,64]`解码，`reg_max=7`，anchor中心偏移0.5，分类分数已在图中执行对齐sigmoid，不能再次sigmoid。训练时的后处理为score阈值0.025、NMS IoU 0.6、每类top-k 1000、全图最多100个检测。

## ACE2 ONNX参考

ACE2使用CUDAExecutionProvider完成两份ONNX的1600张test推理。独立ORT profile显示两份图各317个Node事件，全部归属于CUDAExecutionProvider，没有CPU节点回退。

| 模型 | mAP50-95 | AP50 | AP75 | P50 | P95 | Predictions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `picodet-xs-320-seed-42` | 0.643638 | 0.981137 | 0.777211 | 1.146 ms | 1.158 ms | 38145 |
| `picodet-xs-416-seed-42` | 0.657918 | 0.982066 | 0.802280 | 1.215 ms | 1.238 ms | 31041 |

ACE1开发集上的ONNX与Paddle最佳checkpoint对照分别为`0.645762`对`0.645857`和`0.657062`对`0.657069`，差值约`-0.000095`和`-0.000008`，支持解码合同。

## 8T OM结果

8T的Ascend310B4、CANN 8.0.0、驱动25.2.0环境完成两份`allow_mix_precision` ATC。OM均已通过ACL加载，实际描述为1输入、8个float32输出，层级shape与ONNX合同一致。

| 模型 | OM mAP | ONNX mAP | ΔmAP | AP50 | AP75 | P50 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `picodet-xs-320-seed-42` | 0.637463 | 0.643638 | -0.006175 | 0.980334 | 0.768263 | 7.647 ms | 7.768 ms |
| `picodet-xs-416-seed-42` | 0.657978 | 0.657918 | +0.000059 | 0.982434 | 0.801253 | 13.329 ms | 13.494 ms |

8T的OM文件、ATC日志和数值诊断已备份到本地被Git忽略的`models/om/picodet-xs-20261116/board-8t/`目录。两个OM的SHA-256分别为：

- `picodet-xs-320-seed-42.om`：`240a028444c31971360d2c8bb6d22fcfaaa273710989cacb3adbff4a71f23b6f`
- `picodet-xs-416-seed-42.om`：`59cce59918ddad55d3ca52e278c01a057cccb93aadd6d2fb20fd073b2efcf17d`

需要注意，16张固定输入的CUDA与OM原始八输出没有通过`atol/rtol=0.05`门限：XS-320最大绝对误差0.4875，XS-416为0.6952。任务mAP仍接近ONNX参考，说明框级结果对部分logit差异不敏感，但不能把该OM记录为逐张量数值完全一致。该状态保留在`om-manifest.json`和对比JSON中，部署前仍需要真实图像人工检查。

## 20T OM结果

20T使用Ascend310B1、CANN 8.0.0、驱动25.2.0和`allow_mix_precision`。两份ATC分别约640秒和654秒完成。OM为1输入、8个float32输出，层级shape与ONNX合同一致。

| 模型 | OM mAP | ONNX mAP | ΔmAP | AP50 | AP75 | P50 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `picodet-xs-320-seed-42` | 0.637463 | 0.643638 | -0.006175 | 0.980334 | 0.768263 | 6.206 ms | 6.881 ms |
| `picodet-xs-416-seed-42` | 0.657978 | 0.657918 | +0.000059 | 0.982434 | 0.801253 | 11.341 ms | 12.190 ms |

20T与8T的1600张COCO predictions SHA逐项一致，但OM文件SHA不同。20T的原始八输出数值门与8T一样未通过`atol/rtol=0.05`门限，XS-416误差主要集中在stride 8的DFL输出；任务mAP仍与ONNX参考接近。

20T OM本地备份位于`models/om/picodet-xs-20261116/board-20t/`，SHA-256分别为：

- `picodet-xs-320-seed-42.om`：`9c01508a04f4929a0814032b055e03b877a05c5f1aefb6e3500fb364475fc846`
- `picodet-xs-416-seed-42.om`：`69600bb32437934f704a8429f9e5ba407194dfb1e08782a19c7c997dac6f6dfc`

## 部署选择

仅从当前test集和受控计时边界看，`picodet-xs-416-seed-42`是精度优先候选，8T/20T mAP均为0.657978；`picodet-xs-320-seed-42`是低延迟候选，20T P50约6.21毫秒。二者都只完成了自动任务指标，仍需真实摄像头、左右掌方向、反手姿态、遮挡、过曝和服务重启的人工检查。原始八输出数值门失败也必须保留在准入结论中。

## 证据路径

- ONNX与导出元数据：`models/onnx/picodet-v001/`
- 8T和20T OM本地备份：`models/om/picodet-xs-20261116/board-8t/`、`board-20t/`
- ACE2 CUDA与开发集证据：`reports/runs/picodet-xs-20261116/ace2-onnx-cuda/`、`ace1-dev-parity/`
- 8T全量报告与对比：`reports/runs/picodet-xs-20261116/board-8t-om/`、`comparison-8t/`
- 20T全量报告与对比：`reports/runs/picodet-xs-20261116/board-20t-om/`、`comparison-20t/`
- 三端最终汇总：`reports/runs/picodet-xs-20261116/final-summary.json`、`final-summary.csv`、`final-summary.md`

模型、预测和原始报告目录均被Git忽略。发布包只应保留本文、工具源码、manifest结构和哈希说明。
