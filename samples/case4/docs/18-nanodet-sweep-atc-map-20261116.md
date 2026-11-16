# 左右掌NanoDet批量ATC与mAP记录

本记录覆盖ACE1导出的16个左右掌NanoDet sweep模型、ACE2 CUDA ONNX参考，以及Ascend310B4 / 8T和Ascend310B1 / 20T的独立`allow_mix_precision`转换。两个算力档位的结果不合并排名。

## 模型与数据合同

- 训练来源：ACE1 `artifacts/runs/`中的16个`trained`运行；导出输入、输出SHA-256和模型合同保存在本地忽略目录`models/onnx/nanodet-all-20261116/`中的`manifest.json`和`contracts.json`，不作为Git二进制资产提交。
- 本地ONNX目录：`samples/case4/models/onnx/nanodet-all-20261116/`，共16个sweep模型；另有3个历史参考ONNX，不计入本轮16模型mAP排名。
- test集：1600张图像、1601个标注，annotation SHA-256为`5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a`。
- 类别映射固定为`1=left_palm`、`2=right_palm`；分数为图内sigmoid输出；DFL方向为left、top、right、bottom。
- Plus模型使用4个stride和`center_offset=0.0`；legacy模型使用3个stride和`center_offset=0.5`。416输入的四stride prior数为3598，使用`ceil(416/64)=7`。
- 预处理为BGR直接拉伸到各自静态输入尺寸，均值`[103.53,116.28,123.675]`、标准差`[57.375,57.12,58.395]`。

## 转换与测试工具

- ATC批量转换：[run_nanodet_sweep_atc.py](../tools/board/run_nanodet_sweep_atc.py)
- OM全量test：[run_nanodet_sweep_om.py](../tools/board/run_nanodet_sweep_om.py)
- ONNX CUDA参考：[run_nanodet_sweep_onnx.py](../tools/offline/run_nanodet_sweep_onnx.py)
- COCO mAP：[evaluate_nanodet_sweep_predictions.py](../tools/offline/evaluate_nanodet_sweep_predictions.py)
- ONNX/OM对照：[compare_nanodet_sweep_map.py](../tools/offline/compare_nanodet_sweep_map.py)

原有seed43脚本硬编码`320x320`、`[1,2125,34]`、`reg_max=7`和零中心偏移，不能直接测试416、512、640或legacy模型。本轮新增脚本按每个模型合同选择静态输入、stride、`reg_max`和中心偏移，并校验1600/1601数据规模、annotation SHA、ONNX/OM SHA及ATC SoC/精度。

## ACE2 ONNX参考

环境：`palm-nanodet-seed43-eval`，Python 3.11.18、ONNX 1.20.1、ONNX Runtime 1.23.2、NumPy 2.4.1、OpenCV 4.13.0，CUDA provider为主提供者，pycocotools可用。

完整结果保存在`reports/nanodet-sweep-20261116/ace2-onnx-cuda-corrected/`。当前排序如下。

| 模型 | mAP50-95 | AP50 | AP75 |
| --- | ---: | ---: | ---: |
| plus-m15-416 | 0.681661 | 0.987180 | 0.839554 |
| plus-m-416 | 0.680440 | 0.985121 | 0.831007 |
| plus-m-320 | 0.676573 | 0.985659 | 0.821992 |
| plus-m15-320 | 0.675853 | 0.985777 | 0.823004 |
| plus-convnext-640 | 0.670560 | 0.986293 | 0.824159 |
| legacy-efficientlite1-416 | 0.659945 | 0.984070 | 0.810474 |
| legacy-repvgg-a0-416 | 0.650304 | 0.986311 | 0.795945 |
| legacy-efficientlite2-512 | 0.648697 | 0.985662 | 0.788206 |
| legacy-g-416 | 0.648400 | 0.979910 | 0.788810 |
| legacy-efficientlite0-320 | 0.646537 | 0.980837 | 0.770648 |
| legacy-m-320 | 0.642332 | 0.979573 | 0.769561 |
| legacy-m15-416 | 0.641759 | 0.983174 | 0.785813 |
| legacy-m-416 | 0.641447 | 0.982176 | 0.770392 |
| legacy-m15-320 | 0.641190 | 0.984461 | 0.765250 |
| legacy-t-320 | 0.639918 | 0.983267 | 0.779512 |
| legacy-m05-320 | 0.635217 | 0.978920 | 0.770961 |

这组结果是ONNX Runtime CUDA在同一1600张test图上的参考，不代表8T或20T OM指标。

## Ascend310B OM结果

两台板均使用CANN 8.0.0、驱动25.2.0、Python 3.9.25和`allow_mix_precision`，16个模型全部完成ATC，16个OM全部完成1600张test推理、ACL清理和COCO mAP计算。8T的OM SHA与20T不同；同一模型的COCO predictions SHA-256逐项相同。

| 模型 | ONNX mAP | 8T mAP | 20T mAP | 8T Δ | 20T Δ | 8T P50 ms | 20T P50 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| plus-m15-416 | 0.681661 | 0.582934 | 0.582934 | -0.098727 | -0.098727 | 25.011 | 16.523 |
| plus-m15-320 | 0.675853 | 0.577053 | 0.577053 | -0.098800 | -0.098800 | 12.168 | 8.923 |
| plus-m-320 | 0.676573 | 0.568153 | 0.568153 | -0.108420 | -0.108420 | 8.842 | 7.106 |
| plus-m-416 | 0.680440 | 0.560876 | 0.560876 | -0.119563 | -0.119563 | 17.956 | 13.600 |
| plus-convnext-640 | 0.670560 | 0.532954 | 0.532954 | -0.137607 | -0.137607 | 166.615 | 110.548 |
| legacy-efficientlite2-512 | 0.648697 | 0.498539 | 0.498539 | -0.150158 | -0.150158 | 45.850 | 68.343 |
| legacy-efficientlite1-416 | 0.659945 | 0.466066 | 0.466066 | -0.193879 | -0.193879 | 25.935 | 28.957 |
| legacy-repvgg-a0-416 | 0.650304 | 0.456151 | 0.456151 | -0.194153 | -0.194153 | 20.033 | 13.721 |
| legacy-t-320 | 0.639918 | 0.454032 | 0.454032 | -0.185886 | -0.185886 | 11.772 | 7.718 |
| legacy-efficientlite0-320 | 0.646537 | 0.452639 | 0.452639 | -0.193898 | -0.193898 | 12.026 | 9.771 |
| legacy-m15-320 | 0.641190 | 0.423637 | 0.423637 | -0.217553 | -0.217553 | 11.293 | 8.515 |
| legacy-m15-416 | 0.641759 | 0.421828 | 0.421828 | -0.219931 | -0.219931 | 23.341 | 15.671 |
| legacy-m-416 | 0.641447 | 0.405967 | 0.405967 | -0.235480 | -0.235480 | 16.719 | 13.982 |
| legacy-m05-320 | 0.635217 | 0.404841 | 0.404841 | -0.230376 | -0.230376 | 6.415 | 4.883 |
| legacy-m-320 | 0.642332 | 0.392647 | 0.392647 | -0.249686 | -0.249686 | 8.217 | 6.384 |
| legacy-g-416 | 0.648400 | 0.340921 | 0.340921 | -0.307479 | -0.307479 | 11.903 | 9.879 |

精度最高的是`plus-m15-416`；若以当前板的单模型推理时延和精度折中，`plus-m15-320`更快且只低约0.0059 mAP。`plus-m-320`在8T和20T上分别约8.84毫秒和7.11毫秒，适合更严格的延迟预算。上述P50只覆盖H2D、ACL execute、同步和D2H，不含图像解码、预处理、DFL/NMS和HTTP。

完整报告保存在本地忽略目录`samples/case4/reports/nanodet-sweep-20261116/`，其中`final-summary.json`、`final-summary.csv`和`final-summary.md`是跨档位汇总；`ace2-onnx-cuda-corrected/`、`board-8t/`和`board-20t/`分别保存各阶段原始报告和predictions。

## 结果解释

ONNX到OM的mAP下降在Plus模型约为0.099至0.138，legacy模型约为0.150至0.307。虽然8T和20T的OM哈希不同，但16个模型的predictions SHA-256逐项一致，说明本次两个SoC档位的框级任务输出相同；这不把两者的时延混为一项。转换后精度下降仍需结合原始输出误差、框定位和真实摄像头人工检查，不能仅凭ATC成功或HTTP推理成功判定模型可用。

## 人工判定边界

自动脚本只验证数据合同、推理执行、输出有限性、预测文件和COCO指标。模型框质量、左右掌注册是否可用、摄像头端到端效果仍由用户人工检查。原始运行目录保存在对应板端的`Documents/Ascend310/workspaces/case4/`下。
