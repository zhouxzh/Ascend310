# 量化ONNX筛选

## 范围

本记录回答以下问题：公共量化Qwen ONNX文件能否在不进行本地量化的前提下下载，并直接送入Ascend 310B上的ATC和PyACL。

对于迄今筛选过的每个候选，答案都是否定的。PyACL加载OM，而不是ONNX。ONNX文件必须先通过目标CANN版本的ATC解析器。制品完整性、图域、算子支持、形状冻结、OM生成、PyACL执行、数值一致性、质量和性能是彼此独立的门。

这些结果未执行本地AMCT量化。

## 候选锁定

[`online-quantized-qwen-candidates.json`](../configs/online-quantized-qwen-candidates.json)按仓库、完整修订、字节数、SHA-256、许可证状态、系列和筛选优先级固定12个唯一公共候选。等价文件记为别名，而不是下载两次。

静态筛选工具是[`screen-online-quantized-onnx.py`](../scripts/verify/screen-online-quantized-onnx.py)。它会计算所选图的哈希，在不加载外部权重的情况下检查ONNX模型，记录域/算子/形状，并将候选标记为：

```text
untested
artifact-mismatch
blocked-domain
blocked-operator
atc-eligible
```

`atc-eligible`仅表示静态图没有已知阻塞项。它不代表通过ATC、OM、PyACL、数值或质量门。

## 候选系列

| 系列 | 唯一公共文件 | 筛选结果 |
| --- | --- | --- |
| Qwen1.5 0.5B Chat | `model_quantized`、`model_uint8`、`model_q4`、`model_q4f16` | 全部阻塞 |
| Qwen2.5 0.5B Instruct GQA | `model_uint8`（也以`model_quantized`发布）、`model_q4`、`model_q4f16` | 全部阻塞 |
| Qwen2.5 0.5B Instruct MHA | `model_uint8`（也以`model_quantized`发布）、`model_q4`、`model_q4f16` | 全部阻塞 |
| Qwen2.5 0.5B ORT int4 | Hazem block-32图和外部数据 | 在下载权重前阻塞 |
| Qwen2.5 0.5B AMD int4 | AMD Ryzen AI图和外部数据 | 在下载权重前阻塞 |

精确字节数和哈希仍以候选锁为准。

## 先前的Int8证据

两个较早的公共int8候选已在两块板上下载：

| 候选 | 修订 | 图 |
| --- | --- | --- |
| `onnx-community/Qwen2.5-0.5B-Instruct` int8 | `22942cb7d7ba4cc81bb4673549ca4d4614469b5e` | `onnx/model_int8.onnx`，512,096,557字节 |
| `Ringoacid/Qwen2.5-0.5B-onnx-attention-int8` | `8b6afbb2b94c52523779b7dba6f5e7ac8d24ef9f` | 图3,254,466字节，另加外部数据494,222,848字节 |

两块板都在ATC转换阶段拒绝了这两个图，原因是`MatMulInteger`、`DynamicQuantizeLinear`和`DequantizeLinear`算子不受支持。两者均未生成OM。

## 算子指纹

公共文件是真正的量化图，但它们面向的是ONNX Runtime执行提供程序，而不是CANN：

- Qwen1.5 `quantized`和`uint8`包含168个`MatMulInteger`、96个`DynamicQuantizeLinear`和两个`DequantizeLinear`节点。
- Qwen1.5 `q4`和`q4f16`包含168个`com.microsoft.MatMulNBits`节点。
- Qwen2.5 GQA `uint8`包含120个`MatMulInteger`、96个`DynamicQuantizeLinear`、24个`com.microsoft.GroupQueryAttention`和48个`com.microsoft.SkipSimplifiedLayerNormalization`节点。
- Qwen2.5 GQA `q4`和`q4f16`包含120个`MatMulNBits`、24个`GroupQueryAttention`和48个`SkipSimplifiedLayerNormalization`节点。
- Qwen2.5 MHA `uint8`包含168个`MatMulInteger`、96个`DynamicQuantizeLinear`、24个`com.microsoft.MultiHeadAttention`、48个`com.microsoft.RotaryEmbedding`和48个`SkipSimplifiedLayerNormalization`节点。
- Qwen2.5 MHA `q4`和`q4f16`以168个`MatMulNBits`节点替换`MatMulInteger`，同时保留供应商注意力与归一化算子。
- Hazem和AMD int4图包含121个`MatMulNBits`、24个`GroupQueryAttention`以及其他`com.microsoft`节点。其图文件仅约175-192 KB，但外部权重约为818-862 MB。静态算子门在下载这些权重前停止了测试。

CANN 8.0的ATC解析器未为已测试的310B目标注册这些算子/域组合。

## ATC结果

以下具有代表性的唯一算子系列在静态单令牌解码形状下提交给ATC：

| 开发板 | 候选 | ATC结果 | 停止层 | OM |
| --- | --- | --- | --- | --- |
| Ascend310B4 / 8T | Qwen1.5 uint8 | exit 255 | `MatMulInteger` / 动态量化解析器 | no |
| Ascend310B4 / 8T | Qwen1.5 q4 | exit 255 | 两个domain-version条目；`MatMulNBits`图 | no |
| Ascend310B4 / 8T | Qwen2.5 GQA uint8 | exit 255 | 两个domain-version条目；自定义GQA图 | no |
| Ascend310B1 / 20T | Qwen1.5 uint8 | exit 255 | `MatMulInteger` / 动态量化解析器 | no |
| Ascend310B1 / 20T | Qwen1.5 q4f16 | exit 255 | 两个domain-version条目；`MatMulNBits`图 | no |
| Ascend310B1 / 20T | Qwen2.5 MHA uint8 | exit 255 | 两个domain-version条目；自定义MHA图 | no |

代表性错误：

```text
No parser is registered for Op [...], optype [ai.onnx::14::MatMulInteger]
No parser is registered for Op [...], optype [ai.onnx::14::DynamicQuantizeLinear]
No parser is registered for Op [...], optype [ai.onnx::14::DequantizeLinear]
E16005: The model has [2] [--domain_version] fields, but only one is allowed.
```

这些是转换/解析器失败，不是内存失败。ATC在OM分配或NPU执行前拒绝该图。开发板服务的NPU内存不参与此判定。

其余唯一文件在静态算子/域门处停止，因为它们复用了已经复现的失败系列。它们未被分配重复的ATC运行。

## 高效分布式筛选

初始矩阵在两块板上重复了代表性ATC测试。结果稳定后，流程被收窄：

1. 将不同的新候选分配给8T和20T。
2. 第一块板执行静态图筛选和ATC。
3. 如果ATC未生成OM，不在第二块板上重复该候选。
4. 如果ATC生成了OM，则在第二块板上独立转换并校验同一源ONNX。
5. 只有生成OM的候选才进入PyACL描述符、冒烟、连续生成、数值和性能门。

这减少了重复的解析器失败，同时没有合并两块板的证据集。

## PyACL与生成

本次工作中没有候选生成OM。因此：

```text
PyACL descriptor: not reached
PyACL load/execute: not reached
single-token generation: not reached
16-token continuous generation: not reached
accuracy: not reached
performance: not reached
```

即使已生成OM，单令牌ONNX冒烟也不足以满足聊天资格。该图仍需要正确的KV反馈、tokenizer行为、停止处理和连续生成。

## 支持方向

语言模型只有在满足以下条件时才是PyACL候选：

1. 图具有固定契约，或可由ATC冻结到目标形状。
2. 目标CANN解析器接受每个算子和域。
3. KV缓存布局与更新语义明确。
4. ATC为正确的SoC生成非空OM。
5. OM描述符与运行时契约匹配。
6. PyACL加载、执行并正确反馈缓存输出。
7. 固定golden支持数值比较。
8. 质量和性能分别测量。

当前公共ORT量化格式不满足前两个门。未来可行路线是ATC原生量化导出，或使用仅含已注册算子的标准静态图。安装自定义OPP或更改CANN不在本次工作范围内。

## 决策

- 全部12个新锁定的公共候选均在静态图检查阶段阻塞。
- 六个代表性唯一候选提交给ATC；六个均以255退出且未生成OM。
- 较早的两个int8候选也在两块板上ATC失败。
- 内存耗尽不是这些失败的原因。
- 本次工作中没有任何原始量化公共Qwen ONNX到达PyACL。
- 公共ONNX可用性不是昇腾兼容性的证据。
- 正式FP32静态KV发布保持不变。

后续图手术实验成功将Qwen1.5 `q4f16`反量化为标准FP16，为B4和B1完成转换，并通过PyACL执行。该派生模型具有匹配的FP16数值行为，但没有INT4执行或可测量速度优势。参见[Q4F16 ONNX迁移与解码速率](12-q4f16-onnx-migration-and-decode-rate.md)。
