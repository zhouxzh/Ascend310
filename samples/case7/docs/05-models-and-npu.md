# 模型流水线与 NPU 准入

_说明三个生产模型的准备、ATC 转换、ACL 校验和准入边界。_

---

本文只描述当前模型流水线和板端准入要求，不回填历史板卡转换数字。

## 生产模型

| model_id | 用途 | 输出 | 运行时约束 |
| --- | --- | --- | --- |
| `mobileclip_s0__npu__mixed_fp16` | 英文及通用语义检索 | 512 维 | 图像、文本空间独立 |
| `chinese_clip_rn50__npu__mixed_fp16` | 中文语义检索 | 1024 维 | 图像、文本空间独立 |
| `resnet50_feature__npu__mixed_fp16` | 经典相似图 | 2048 维 | 不与 CLIP 向量混合 |

模型二进制、ONNX、checkpoint、tokenizer 和测试报告默认不进入 Git。`models/registry.json` 只允许已经完成准入的模型。

## 下载和导出

Hugging Face 下载使用镜像：

```bash
export HF_ENDPOINT=https://hf-mirror.com
python prepare_models.py download --model all --hf-endpoint https://hf-mirror.com
python prepare_models.py export --model all
python prepare_models.py check --model all
```

证书异常时只能在明确隔离的下载操作中使用不安全模式，并在报告中记录；生产服务不允许忽略证书或自动下载模型。

## ATC 转换

ATC 只在真实开发板执行。以 `Ascend310B4` 为例，转换参数必须与输入合同和实际芯片匹配：

```bash
export MAX_COMPILE_CORE_NUMBER=1
export MULTI_THREAD_COMPILE=0
export TBE_PARALLEL_COMPILER=0
export TE_PARALLEL_COMPILER=1
export ASCENDC_PAR_COMPILE_JOB=0
export TILINGKEY_PAR_COMPILE=0
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export CMAKE_BUILD_PARALLEL_LEVEL=1
export MAKEFLAGS=-j1

python prepare_models.py convert \
  --model mobileclip_s0__npu__mixed_fp16 \
  --soc-version Ascend310B4
```

转换要求：

- 使用固定 batch=1；
- 使用 `allow_fp32_to_fp16`；
- 对精度敏感算子使用明确的 `keep_dtype` 配置；
- 使用 `high_precision_for_all`；
- 禁用图并行和编译缓存；
- 单个组件串行转换，不能使用 `--model all` 并行编译；
- 不增加 swap 或持久化编译缓存。

`--soc-version` 必须对应实际运行处理器。不同 310B 计算等级的 OM 不预设通用，跨板运行必须单独验证。

## ACL 准入门

准入前检查：

1. ONNX 文件大小和 SHA-256；
2. 输入名称、shape、dtype 和字节数；
3. 输出维度、dtype 和有限值；
4. OM 文件大小和 SHA-256；
5. ACL 初始化、模型加载和串行执行；
6. OM 与 ONNX 归一化向量的余弦相似度不低于 `0.995`；
7. COCO-CN 检索质量和性能证据独立记录。

```bash
python prepare_models.py validate --model all --admit
```

服务端只加载已注册且 hash 匹配的 OM。ACL 初始化失败、模型缺失或准入失败时请求直接报错，不切换 CPU/PyTorch。

## COCO-CN 测试

COCO-CN 是 Case7 唯一公开测试图库。固定测试清单、中文/英文查询、Recall@1/3/5 和性能协议由板端测试脚本生成报告。测试资产放在受管数据目录，不把真实个人照片混入公开测试集。

## 证据边界

以下结论必须分开记录：

| 证据 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| ONNX 静态检查 | 输入输出合同正确 | NPU 可执行 |
| ATC 成功 | 指定 CANN/SoC 生成 OM | ACL 数值正确 |
| ACL 数值 | OM 与参考输出一致 | 搜索质量和端到端性能 |
| Recall | 测试查询的检索质量 | 其他数据集表现 |
| P50/P95 | 指定板卡和协议的延迟 | 另一块板卡的性能 |

任何未取得当前板端报告支持的数字都标为“待验证”。
