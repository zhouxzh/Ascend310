# 已发布模型下载与 Ascend OM 部署

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

`tools/convert_onnx_to_om.sh` 用于在 Ascend 310B 开发板上调用 ATC，把 ONNX
模型转换为 OM，并分析本次 ATC 输出和 CANN 日志中是否存在不支持算子、解析器
未注册或 kernel 选择失败等问题。ATC 属于设备端工具，不要在普通开发电脑上运行。

## 获取已发布模型

Piano-DDSP、DDSP-VST 和 MIDI-DDSP 的 ONNX/OM 均从
`zhouxzh/piano-ddsp-ascend310` 的已发布 release 下载。不要在 case3 重新执行
TensorFlow checkpoint 或 TFLite 导出。下载器必须使用固定 `--revision`，先下载和校验
`SHA256SUMS`，再断点下载每个资产：

```bash
# 当前锁定的 Piano-DDSP release。
python tools/download_model_release.py

# 其他模型族：数值取目标发布的 release 清单，不使用 branch 名。
python tools/download_model_release.py \
  --revision <immutable-release> --release-dir <published-directory> \
  --target-dir models/<family> --manifest-sha256 <sha256-of-SHA256SUMS>
```

下载报告会记录解析后的 commit SHA。下载失败、manifest SHA256 不符或任何资产哈希不符时
必须停止，不得用未校验文件继续 ATC。现有 `.tflite`、旧 ONNX、模型、报告和上游素材不会
由下载器或部署脚本删除；当前运行和部署流程只依赖已校验的发布资产。

## DDSP-VST Effect Feature OM

Effect 使用独立的音频特征模型，运行时路径为
`models/om/ddsp_vst_feature_mixed_float16.om`，SHA256 为
`a1973830eca98111642dcb331e0a1a163f7a664d871e6d15f40fdc70f9b98db4`。其张量契约固定为：

```text
audio float32[1024]
f0_scaled, pw_scaled, f0_hz, pw_db float32[1]
sample_rate=16000, hop_size=320
```

来源是 `magenta/ddsp-vst` 提交
`f2996e97f9469f3956a6b8e9d2d9b50b6555e1e9` 的
`extract_features_micro.tflite`，源 SHA256 为
`98ade06772f9ca6ac64789fd843f14aeddf709084bbc76f060761b141837d0a2`。
TFLite 到静态 ONNX 的一次性转换在开发电脑的个人
`convert-tensorflow-to-onnx` Skill 和 `sci-agent` 环境中完成，不属于 case3 运行依赖；
开发板只接收验证后的 ONNX 并使用已有 ATC 生成 OM，不安装 TensorFlow、ONNX Runtime
或其他转换包。

板端 CANN 8.0.0、`Ascend310B4` 的 1,000 帧实测中，`mixed_float16` 的 Feature p95 为
`10.207 ms`，与 Violin Control 合计 p95 为 `11.321 ms`；`f0_hz` 最大绝对误差为
`0.141 Hz`，`pw_db` 为 `5.73e-6 dB`。`force_fp16` 虽满足延迟要求，但产生约 `30 dB`
的 `pw_db` 最大误差，因此已拒绝。运行时 manifest 只登记通过的 mixed OM。

本地 `models/ddsp_vst_effect/release-v1.0.0/` 已包含完整 SHA256 清单、许可证、ATC 日志和
两种精度报告。当前 Hugging Face 上传仍等待本机完成认证；在上传成功并取得不可变 revision
前，不得将该本地目录写成已发布 release，也不得用分支名代替固定 revision。

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
错误阶段和日志位置见 [测试故障排查记录](11-troubleshooting.md#20t-本机-atc-转换失败)。

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
