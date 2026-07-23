# ONNX 转换为 Ascend OM

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

`tools/convert_onnx_to_om.sh` 用于在 Ascend 310B 开发板上调用 ATC，把 ONNX
模型转换为 OM，并分析本次 ATC 输出和 CANN 日志中是否存在不支持算子、解析器
未注册或 kernel 选择失败等问题。ATC 属于设备端工具，不要在普通开发电脑上运行。

脚本会自动激活板端 Anaconda `base` 环境并加载现有 CANN 环境，不安装额外依赖。
当前 Orange Pi AI Pro 20T 上 `npu-smi info` 报告的芯片为 `310B1`，因此默认使用：

```text
soc_version=Ascend310B1
```

## 转换小提琴模型

在开发板的 case3 目录中运行：

```bash
cd ~/Documents/case3
bash tools/convert_onnx_to_om.sh
```

默认输入模型和固定输入形状为：

```text
model=models/ddsp_vst/Violin.onnx
state:512;f0_scaled:1;pw_scaled:1
```

成功或失败后都会保留以下文件：

```text
models/om/Violin.om                 # ATC 转换成功后生成的 OM
models/om/Violin.atc.log            # 完整 ATC 标准输出和错误输出
models/om/Violin.atc.summary.txt    # 返回码、OM 状态和算子兼容性摘要
```

直接查看日志和兼容性摘要：

```bash
cat models/om/Violin.atc.log
cat models/om/Violin.atc.summary.txt
```

较早一次在 CANN 8.0.0、Ascend310B1 上的默认 FP16 实测结果为：

```text
ATC_EXIT_CODE=0
OM_UPDATED=yes
OPERATOR_COMPATIBILITY=no incompatibility pattern found
ERROR_LINES=none
```

生成的 `Violin.om` 大小约为 3.9 MB，MD5 为
`7e840a90e22beb8d9ac31571e3571e2d`。ATC 日志包含
`ATC run success`，没有发现不支持算子、ONNX 解析失败或 kernel 选择失败。

该结果是历史成功基线，不表示当前 20T 环境可以稳定重复转换。2026-07-21 重启后
使用相同脚本复测时，默认 FP16 和 `mixed_float16` 都在 TBE/CannKB 初始化阶段
失败，没有生成新 OM；旧的原生 B1 FP16 OM 仍可由 ACL 加载推理。两轮时间线、
错误阶段和日志位置见 [测试故障排查记录](troubleshooting.md#20t-本机-atc-转换失败)。

## 转换其他模型

脚本支持替换模型、输出路径、输入形状和 SOC 版本：

```bash
bash tools/convert_onnx_to_om.sh \
  --model models/ddsp_vst/Flute.onnx \
  --output models/om/Flute \
  --input-shape 'state:512;f0_scaled:1;pw_scaled:1' \
  --soc-version Ascend310B1
```

当前 DDSP-VST 音色模型具有相同的输入契约，可以逐个执行：

```bash
for model in models/ddsp_vst/*.onnx; do
  bash tools/convert_onnx_to_om.sh --model "$model"
done
```

目前只对 `Violin.onnx` 完成了板端 ATC 实测。其他音色虽然使用相同图结构，仍应
分别检查各自生成的 `.atc.summary.txt`，不能仅根据小提琴模型的结果判定全部模型
已经转换成功。

## FP16 OM 精度验证

`tools/compare_onnx_om_precision.py` 用相同输入比较 ONNX Runtime 的 FP32 输出和
Ascend 上 `force_fp16` OM 的输出。测试分为两种模式：

- `teacher-forced`：OM 每帧使用 ONNX 的状态输入，隔离单次转换误差。
- `closed-loop`：OM 将自己的 `state_out` 回灌到下一帧，用于检查状态误差是否累积。

先在本地开发环境生成确定性的 ONNX 基准：

```bash
python tools/compare_onnx_om_precision.py reference \
  --onnx models/ddsp_vst/Violin.onnx \
  --output reports/Violin_onnx_reference_1024.npz \
  --steps 1024 --seed 20260721
```

将基准文件同步到开发板后，在板端 Anaconda `base` 环境运行 OM 对比：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh

cd ~/Documents/case3
python tools/compare_onnx_om_precision.py om \
  --om models/om/Violin.om \
  --reference reports/Violin_onnx_reference_1024.npz \
  --report reports/Violin_fp16_precision_1024.json \
  --device 0
```

1024 帧相当于约 20.48 秒的 DDSP 控制序列。本次实测的归一化均方根误差
（NRMSE）如下：

| 输出 | Teacher-forced | Closed-loop | Closed-loop 余弦相似度 |
| :--- | ---: | ---: | ---: |
| 幅度 `amplitude` | 0.199% | 0.180% | 0.999998374 |
| 有效谐波幅度 `amplitude * harmonics` | 0.223% | 0.233% | 0.999997305 |
| 噪声系数 `noise_amps` | 0.332% | 0.351% | 0.999993874 |
| GRU 状态 `state_out` | 0.093% | 0.184% | 0.999998308 |

闭环状态误差没有随 1024 帧测试发散，OM 单次推理平均约为 0.805 ms。原始
`harmonics` 分布出现过 `0.452` 的孤立最大绝对误差，但该帧的总幅度只有
`6.13e-7`，接近静音。原因是对应高次谐波在 FP32 中计算为略低于 8 kHz，FP16
舍入后变为恰好 8 kHz，从而跨过 `Less(< Nyquist)` 截止条件。乘上总幅度后的
有效谐波闭环 NRMSE 只有 0.233%，因此该孤立误差不会按原始分布数值直接反映到
合成音频幅度上。

结论是：FP16 转换确实带来可测量的舍入误差，但当前 1024 帧测试没有发现明显的
精度损失问题，也没有发现 GRU 状态持续漂移。该结果足以支持当前 DDSP 控制模型
原型继续使用 FP16 OM；最终音质验收仍应使用真实 MIDI 序列合成音频，并与 ONNX
版本进行听感和波形对比。
