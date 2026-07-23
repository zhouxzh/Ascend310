# MIDI-DDSP OM 精度与速度实测

本文记录 2026-07-22 在 `ascend8t2` 上对 MIDI-DDSP FP16 和
`mixed_float16` OM 的精度与速度验证。模型转换过程、兼容性修正和首次失败日志见
[`model-export.md`](model-export.md)，本文只讨论已经成功生成的 4 个 OM。

## 测试边界

板端环境：

- SoC：Ascend 310B4
- NPU 驱动 / `npu-smi`：25.2.0
- CANN：8.0.0，软件包版本 `7.6.0.1.220`
- Python：Anaconda `base`，3.9.2
- `npu-smi`：`Health: Alarm`，但设备可见，4 个 OM 均成功加载和推理
- 板端没有安装、升级或删除任何软件

测试对象：

| 组件 | 静态输入 | 输出 | FP16 OM | Mixed OM |
| :--- | :--- | :--- | ---: | ---: |
| Expression | 32 个音符或休止符 | 6 维表情控制量 | 7,160,916 B | 7,989,478 B |
| Synthesis parameters | 64 帧，250 Hz | f0、幅度、60 维谐波分布、65 维噪声幅度 | 19,618,179 B | 20,293,976 B |

混合精度 Expression OM 比 FP16 大 11.57%，Synthesis OM 大 3.44%。Synthesis
模型一次处理 64 帧，也就是 256 ms。它只生成神经网络控制参数，不包含 DDSP
振荡器、滤波噪声和混响，所以本文的延迟不是完整音频合成链路延迟。

本次测试使用的 OM 哈希：

| OM | SHA256 |
| :--- | :--- |
| `midi_ddsp_expression_notes32_force_fp16.om` | `79269c3ac5faba9876f7d6bd7b1e933b639ac2e3231c16ae202f8280795f4d4c` |
| `midi_ddsp_expression_notes32_mixed_float16.om` | `101bef14694fa0e98dcb3e42aed7ede423fbf2a4e44e0cbb067004ca996d3f22` |
| `midi_ddsp_synthesis_params_frames64_force_fp16.om` | `117a58b90716791f0566d68f02f73ed4c996bcf1e740fc830b95d1495ae84478` |
| `midi_ddsp_synthesis_params_frames64_mixed_float16.om` | `adb45467ebcd3ba95e08552fe50b0d1c69bec21a8385399b7a7741d59e205650` |

参考文件：

| 参考 | SHA256 |
| :--- | :--- |
| `midi_ddsp_expression_notes32_reference.npz` | `6a691cf3d0569fc2a3e2959d8688cf7656447824f977e5888301c9fbc6e3765a` |
| `midi_ddsp_synthesis_params_frames64_reference.npz` | `48a42fb8a43bc65641c031e97b093ad5d30e6018e1393a347065d9debdd991ef` |

## 测试方法

精度测试对每个 OM 使用同一份 NPZ 输入运行 5 次，同时与 NPZ 中保存的
TensorFlow 和 ONNX 输出比较。NRMSE 定义为 `RMSE / reference RMS`，并记录余弦
相似度、最大绝对误差、p99 误差和有限值检查。重复性 NRMSE 是第 2 至第 5 次
输出相对第 1 次输出的中位数。

速度采用两个口径：

1. 纯 NPU：`ais_bench --batchsize 1 --warmup_count 100 --loop 1000`，使用
   `NPU_compute_time`。
2. 端到端：通过 `InferSession.infer(..., mode="static")` 测量 Python、ACL、输入输出
   搬运和 NPU 的总墙钟时间。先 warmup 20 次，再循环 100 次，重复 5 轮，以每轮
   平均延迟的中位数为主指标。

每组测试均检查 OM 输入顺序、输入输出形状以及 NaN/Inf。4 个 OM 的精度阶段和
`ais_bench` 阶段退出码全部为 0，所有输出均为有限值。

## 速度结果

下表中 delta 按 `(mixed / FP16 - 1) * 100%` 计算，正值表示混合精度更慢。

| 组件 | 精度 | NPU mean | NPU median | NPU p99 | 端到端 median | 端到端 p95 |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| Expression | FP16 | 6.120812 ms | 6.119000 ms | 6.165010 ms | 6.467977 ms | 6.480559 ms |
| Expression | Mixed | 6.944766 ms | 6.943000 ms | 6.992010 ms | 7.289697 ms | 7.311170 ms |
| Synthesis | FP16 | 32.486012 ms | 32.474998 ms | 32.814139 ms | 33.305390 ms | 33.338195 ms |
| Synthesis | Mixed | 33.501995 ms | 33.522499 ms | 33.777010 ms | 34.306163 ms | 34.339490 ms |

| 组件 | Mixed NPU median delta | Mixed 端到端 median delta |
| :--- | ---: | ---: |
| Expression | +13.47% | +12.70% |
| Synthesis | +3.23% | +3.00% |

本次环境下，两个混合精度 OM 都没有速度优势。FP16 Synthesis 的纯 NPU 时间约
32.47 ms，Mixed 约 33.52 ms；相对 256 ms 的输入块，两者的神经参数生成分别约
为 7.88 倍和 7.64 倍实时速度。该比例不能代表加入 DDSP 合成器和音频输出后的
最终实时余量。

## 精度结果

以下均为 5 次运行的中位 NRMSE。`TF-ONNX` 是导出阶段两个参考后端之间的基线；
`Repeat` 是 OM 自身重复运行波动。

| 输出 | TF-ONNX | FP16-TF | Mixed-TF | FP16-ONNX | Mixed-ONNX | FP16 Repeat | Mixed Repeat |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Expression controls | 0.000053% | 0.143742% | 0.139534% | 0.143737% | 0.139532% | 0.000000% | 0.000000% |
| f0 (Hz) | 2.211792% | 1.729790% | 1.732303% | 1.464234% | 1.461195% | 1.964419% | 1.940160% |
| Amplitudes | 0.153075% | 0.132942% | 0.137620% | 0.143750% | 0.135882% | 0.144473% | 0.154122% |
| Harmonic distribution | 0.501567% | 0.362276% | 0.353726% | 0.386337% | 0.379034% | 0.428962% | 0.433339% |
| Noise magnitudes | 1.155007% | 0.880345% | 0.826939% | 0.874345% | 0.858772% | 1.035461% | 1.025281% |

OM 对 TensorFlow 的余弦相似度和最大绝对误差如下：

| 输出 | FP16 cosine | Mixed cosine | FP16 max abs | Mixed max abs |
| :--- | ---: | ---: | ---: | ---: |
| Expression controls | 0.999999114 | 0.999999207 | 0.001817 | 0.001843 |
| f0 (Hz) | 0.999882440 | 0.999883254 | 20.817352 | 20.925659 |
| Amplitudes | 0.999999155 | 0.999999058 | 0.016450 | 0.016450 |
| Harmonic distribution | 0.999993438 | 0.999993752 | 0.064704 | 0.068713 |
| Noise magnitudes | 0.999961481 | 0.999965898 | 0.046494 | 0.049912 |

### 结果解释

Expression 模型是确定性的：两种 OM 的重复运行误差均为 0，FP16 与 Mixed 对
TensorFlow 的 NRMSE 分别为 0.143742% 和 0.139534%。Mixed 低 0.004208 个百分
点，但差距很小，不能据此认定它在实际音乐质量上更好。

Synthesis 模型包含 `Multinomial`/`OneHot` 随机采样。TensorFlow 与 ONNX 参考
自身已有 0.153% 至 2.212% NRMSE，OM 重复运行也有 0.144% 至 1.964% NRMSE。
这与 OM 对参考的误差处于同一量级，因此表中的 Synthesis 逐点差异主要受采样
随机性影响，不能完整归因于 FP16 或混合精度。要做严格的精度模式比较，应另外
导出确定性采样版本，例如将随机类别采样改为 argmax；或者增加样本数并比较输出
分布，而不是比较单次随机序列。

综合本次固定输入实测：

- 4 个 OM 都能在这组驱动/CANN 不匹配环境中加载和推理，没有 NaN/Inf。
- FP16 在两个网络上都更快，Mixed 慢 3.00% 至 13.47%。
- 确定性 Expression 网络中，两种精度对 TensorFlow 的 NRMSE 都约为 0.14%。
- 随机 Synthesis 网络没有足够证据判断哪种精度更准确。
- 这是单个固定参考输入的数值回归，不等同于完整数据集或听感质量评估。

## 复现和结果文件

板端从仓库根目录执行：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source ~/Ascend/latest/set_env.sh 2>/dev/null || \
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
TE_PARALLEL_COMPILER=1 OMP_NUM_THREADS=1 \
  bash tools/benchmark_midi_ddsp_ascend.sh
```

本次原始结果已经回收到本地：

```text
reports/ascend8t2/midi_ddsp/benchmark/status.tsv
reports/ascend8t2/midi_ddsp/benchmark/run_environment.txt
reports/ascend8t2/midi_ddsp/benchmark/precision/    # 精度 JSON 和端到端日志
reports/ascend8t2/midi_ddsp/benchmark/outputs/      # 5 次 OM 原始输出
reports/ascend8t2/midi_ddsp/benchmark/ais_bench/    # 纯 NPU 原始日志
reports/ascend8t2/midi_ddsp/benchmark/summary.json
reports/ascend8t2/midi_ddsp/benchmark/summary.csv
reports/ascend8t2/midi_ddsp/benchmark/summary.md
```

相关工具：

- `tools/compare_midi_ddsp_om.py`：输入契约、精度、重复性和端到端计时
- `tools/benchmark_midi_ddsp_ascend.sh`：板端 4 个 OM 批处理
- `tools/summarize_midi_ddsp_benchmark.py`：JSON/CSV/Markdown 汇总
- `tests/test_midi_ddsp_benchmark.py`：指标和日志解析单元测试
