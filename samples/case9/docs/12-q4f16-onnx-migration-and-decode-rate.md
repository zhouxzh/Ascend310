# Q4F16 ONNX迁移与解码速率

## 范围

本记录检验公开的Qwen1.5 q4f16 ONNX计算图能否从ORT的`MatMulNBits`算子迁移为CANN 8.0 ATC可接受的标准ONNX计算图。随后，以同一Hugging Face revision的官方FP16计算图为基准，测量PyACL解码吞吐率。

所得模型不是INT4 NPU模型。它将4位权重具体化为FP16常量，因此不保留量化存储和INT4计算带来的收益。

## 源产物

迁移源和未量化基线来自：

```text
repository = onnx-community/Qwen1.5-0.5B-Chat-ONNX
revision   = 6d413dd9a252749e0760902c93331e3e4e65b73c
```

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| q4f16源ONNX | 485,369,358 | `320e83b2f9e996dd77ba933e820c8f6c78b5d60b5a7fa8ed62f8453143eb11cf` |
| 迁移后FP16 ONNX | 928,515,726 | `9707358bef80ab1d83567581fbbc352eb5e672dc6e21f1277b297016ca99d090` |
| 官方FP16 ONNX | 928,499,243 | `1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262` |
| 规范化官方FP16 ONNX | 928,499,258 | `22102190a6241d91ec8e89bebc0c9aeb3443c9feb907187d3090e4935145b4b4` |

迁移后的计算图在8T和20T上分别独立重新生成。两块板生成了相同的派生ONNX SHA-256。

## 计算图迁移

源计算图包含168个`com.microsoft.MatMulNBits`节点，不包含其他自定义算子。每个q4权重使用以下参数：

```text
bits        = 4
block_size  = 32
zero point  = 8
scale dtype = float16
```

打包布局通过与ORT输出比对完成验证，而非猜测：

- 低半字节对应K的偶数位置；
- 高半字节对应紧随其后的K奇数位置；
- scale顺序为`[N, K_blocks]`；
- 反量化后的逻辑形状为`[K, N]`；
- 对称反量化为`(q - 8) * scale`。

迁移工具执行以下步骤：

1. 验证由初始化器承载的`MatMulNBits`输入；
2. 解包并反量化全部168个权重；
3. 将每个节点替换为标准`MatMul`和FP16初始化器；
4. 移除`com.microsoft` opset域；
5. 将ONNX Runtime隐式嵌入的`Transpose perm`显式化，因为ATC要求该属性。

官方FP16基线也包含相同的隐式`Transpose`。它在不改变权重的情况下单独完成了规范化。

## CPU数值门

两个计算图均在以下条件下使用ONNX Runtime 1.19.2 CPU运行：

```text
input token = 1
past length = 0
KV heads    = 16
head size   = 64
```

| 指标 | 结果 |
| --- | ---: |
| logits余弦相似度 | 0.9999999403953552 |
| logits最大绝对差 | 0.0004115104675292969 |
| logits平均绝对差 | 0.00008606024493928999 |
| Top-1原始/迁移后 | 13 / 13 |
| Top-5交集 | 5/5 |
| present-KV最大绝对差 | 0.00390625 |
| present-KV最大平均绝对差 | 0.00006198883056640625 |

迁移结果与原始ORT q4f16计算图在数值上一致。

## ATC结果

所有转换均采用单Token解码形状：

```text
input_ids            1,1
attention_mask       1,2
position_ids         1,1
each past key/value  1,16,1,64
```

| 目标 | 模型 | ATC | OM字节数 | OM SHA-256 |
| --- | --- | --- | ---: | --- |
| Ascend310B4 / 8T | 迁移后FP16 | pass | 1,243,905,055 | `2fa9dda39dd76a19a25f7502b8def11cdb34b60643a8bdf9d02662304c3833f8` |
| Ascend310B4 / 8T | 官方FP16 | pass | 1,243,811,569 | `95096a0527299f91ec8d17621effef274b8273899b5fb3a8aaad1b1ae1ed42fe` |
| Ascend310B1 / 20T | 迁移后FP16 | pass | 1,243,905,043 | `eba0c0756395df04175b8a606f06b6b095d48c27fb26a9fe6373b0104527ddb1` |
| Ascend310B1 / 20T | 官方FP16 | pass | 1,243,811,503 | `8dcf9110c262a3429b4a436c4e48bf7af3a637146e55e5145c8b432eb2917d4d` |

PyACL加载了全部4个OM，输入为51个、输出为49个。48个KV输入采用float32形状`[1,16,1,64]`。对于输入Token 1，两个模型都返回了词表Top-1 Token 13。

## 短上下文解码速率

协议：

- 采用固定单Token形状，包含一个历史Token；
- 每个模型在每块板上运行两轮独立测试；
- 每轮包含两次预热和20次计时迭代；
- `end-to-end`包含主机输入复制、同步ACL执行、logits复制以及全部present-KV输出复制；
- `execute-only`仅对驻留缓冲区中的`acl.mdl.execute`计时，不包含输出D2H传输。

| 板卡 | 计时模式 | q4-derived FP16 | 官方FP16 | q4-derived差值 |
| --- | --- | ---: | ---: | ---: |
| 8T | end-to-end | 5.9512 token/s | 5.9515 token/s | -0.0054% |
| 20T | end-to-end | 10.2303 token/s | 10.2346 token/s | -0.0417% |
| 8T | execute-only | 5.9953 token/s | 5.9951 token/s | +0.0024% |
| 20T | execute-only | 10.3919 token/s | 10.3955 token/s | -0.0347% |

这些差异处于测量噪声量级。

## 长上下文解码速率

另外使用以下形状编译了B4 OM：

```text
attention_mask       1,1024
each past key/value  1,16,1023,64
```

| 模型 | OM字节数 | OM SHA-256 |
| --- | ---: | --- |
| q4-derived FP16 | 1,243,915,086 | `efa9820b594e0b4c93b2eca721f18d3b358708f6b1bf1c2e98adbfa2c72b5d9e` |
| 官方FP16 | 1,243,821,602 | `45ffc6824fc6f33394be6f7fe662124318eb756f6766c5e3c3f1302b30f4cb7a` |

| 计时模式 | q4-derived FP16 | 官方FP16 | q4-derived差值 |
| --- | ---: | ---: | ---: |
| execute-only，30次运行 | 2.5724 token/s | 2.5612 token/s | +0.44% |
| end-to-end，10次运行 | 2.2372 token/s | 2.2572 token/s | -0.89% |

`execute-only`与`end-to-end`的变化方向相反，说明这些低于1%的差异属于噪声，而不是保留下来的量化加速收益。

## 结果解读

迁移在技术上成功：

```text
public q4f16 ONNX
-> dequantized standard FP16 ONNX
-> B4 and B1 ATC
-> PyACL execution
-> matching Top-1 token
```

但所得的OM是FP16。它与官方FP16模型速率相同，因为两个计算图执行的都是相同的标准FP16 MatMul工作负载。q4-derived模型没有在Ascend上执行`MatMulNBits`，因此无法获得INT4内存带宽或计算方面的收益。

迁移还将ONNX文件从463 MB扩展到886 MB，约为原来的1.91倍。OM大小与FP16实际上相同。

## 限制

- 这些测量是固定形状的解码微基准，不是包含HTTP、tokenizer、采样或客户端开销的生产聊天服务。
- 动态增长KV计算图若要支持完整的多Token服务，需要为每个历史长度分别生成OM，或重写为静态缓存计算图。
- 长上下文对比仅在B4上测量。B1只在短解码形状上完成了验证和测量。
- CPU ONNX Runtime仅用作数值基准。NPU性能来自PyACL OM执行。
- 原始INT4 OM在两个310B目标上仍不存在。

## 决策

- q4f16到FP16的计算图迁移：`observed-pass`。
- CPU数值等价性：`observed-pass`。
- B4和B1 ATC：`observed-pass`。
- PyACL加载与单Token执行：`observed-pass`。
- NPU上的INT4执行：`not achieved`。
- 迁移后的量化加速收益：`none observed`。

正确的工程结论是：计算图改写使公开模型能够在PyACL上运行，但同时移除了量化执行路径。要获得可部署的INT4路线，仍需使用ATC原生量化导出、CANN量化工具链，或为自定义量化算子设计静态缓存计算图。
