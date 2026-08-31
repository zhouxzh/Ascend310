# 模型流水线与 NPU 准入

*三个生产模型从下载到 Ascend 310B ACL 执行的操作手册。*

---

## 🧠 生产模型

| 模型 ID | 用途 | 维度 | 输入合同 |
| --- | --- | ---: | --- |
| `mobileclip_s0__npu__mixed_fp16` | 英文/通用语义 | 512 | 图像 `1x3x256x256`，文本 `1x77` |
| `chinese_clip_rn50__npu__mixed_fp16` | 中文语义 | 1024 | 图像 `1x3x224x224`，文本 `1x52` |
| `resnet50_feature__npu__mixed_fp16` | 经典相似图 | 2048 | 图像 `1x3x224x224` |

MobileCLIP 固定提交为 `aecfb5453d022e9deff12f81a150ea8f35194baa`；Chinese-CLIP 固定提交为 `31863c707501bf1605d36842f43deb78793dbc5d`。详细原理见 [src/experiment/case7.md](../../../src/experiment/case7.md)。

当前生产注册表的 `precision_mode` 是 ATC 转换策略，不等于每个算子都使用同一种数据类型。模型的浮点
权重、图像输入和 embedding 输出按 FP32 ONNX 合同导出；文本 token 输入仍为 `int64`。`allow_fp32_to_fp16`
允许 ATC 对可安全转换的算子使用 FP16，其余算子可保留 FP32。MobileCLIP 图像组件已完成选择性精度
扫描并推广 C0：组件级策略为 `allow_fp32_to_fp16`，FP32 `keep_dtype` 白名单为空。历史
`must_keep_origin_dtype` OM 和 `atc_configs/mobileclip_s0_image_keep_dtype.cfg` 只保留为诊断与
回溯证据，不是当前生产 ATC 命令的一部分。

| 生产组件 | 当前 ATC 策略 | FP32 例外 | 状态依据 |
| --- | --- | --- | --- |
| MobileCLIP 图像 | `allow_fp32_to_fp16` | C0：零节点白名单 | `models/registry.json`、最终扫描报告 |
| MobileCLIP 文本 | `allow_fp32_to_fp16` | 无组件级清单 | ACL 报告 |
| Chinese-CLIP 图像/文本 | `allow_fp32_to_fp16` | 无组件级清单 | ACL 报告 |
| ResNet50 图像 | `allow_fp32_to_fp16` | 无组件级清单 | ACL 报告 |

### 当前板运行边界

早期运行复核曾记录操作板 `192.168.1.135` 使用 CANN `8.3.0.1.200:8.3.RC1`、20 张受管照片
和三个模型的 embedding；该段是历史快照，不代表本次跨板 campaign 的环境。当前跨板实验的
8T/310B4 与 20T/310B1 两块板均使用 CANN `7.6.0.1.220:8.0.0`，对应的版本、固件原始字段和
ACL 证据见 [docs/12-mobileclip-cross-board-compatibility.md](./12-mobileclip-cross-board-compatibility.md)。
历史 `192.168.8.180` 的 ACL、500 图检索、选择性精度扫描和性能门槛仍按下文单独标注，不能与本次
跨板结果合并或互相替代。

### 2026-08-28 新板选择性精度候选结果

新板 `192.168.1.135` 的 MobileCLIP 图像候选使用同一生产 ONNX
（SHA-256 `baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79`）和
`Ascend310B4` 串行 ATC。每次转换都设置 `allow_fp32_to_fp16`、
`high_precision_for_all`、`--op_compiler_cache_mode=disable` 和
`--ac_parallel_enable=0`，并在独立目录中输出；C0--C4 的 `keep_dtype` 文件节点数与 SHA-256
如下：

| 候选 | 节点数 | keep-dtype SHA-256 | OM 大小（bytes） | OM SHA-256 | ACL 余弦（1 个输入） | 数值门槛 |
| --- | ---: | --- | ---: | --- | ---: | --- |
| C0 | 0 | `05c5a4826c57b050f3af0d1dc22355438090fb78e1a2e3d1ca29f2606f083cee` | 34208184 | `1e770a3bb40379d768042cd8754f9d508a0f8e55c7de3db8a829041644358566` | 0.2134858668 | 未通过 |
| C1 | 1 | `ef7cf27b0983f20d1a83fff92b5f0f3844b54bf20eb06b48a33bacc543647bab` | 34227522 | `84026cae4d3299ec726f8fffebdc92c912fec61bab4e9ee885073510933ad770` | 0.3825977445 | 未通过 |
| C2 | 7 | `28eb7d4170abd0b08573504ec754b81dac4fd69bea5fee9fe89c856cef8660c6` | 34495846 | `c3d51f06b0e7a572d785a3564d4738f7a2b5b4cc7169c742253c3808f52afb70` | 0.3103438020 | 未通过 |
| C3 | 12 | `2b9c062056690710a6c4fd796208b38b0921190957a2e3900fe27f3335a6e155` | 34761415 | `a465c84720f543c42058bbd5f4f3b38f43f4c8937e01c6a5160d6857008827d8` | 0.3635685146 | 未通过 |
| C4 | 29 | `0eea4cee15631a65dc6c0d186e7ae8f47aff7d26915b53cccb66c019571ebd4e` | 40161094 | `3bc72167dd42117c0c6a2a0aa9367ca27d8ca1878f2c6e8a24a236d833c44848` | 0.6712348461 | 未通过 |

上述五份候选 ACL 报告分别位于
`shared/reports/precision_sweep/mobileclip_s0_image_precision/board-20260828-192.168.1.135/C0/`
至 `C4/`。每份报告只含一个固定输入；输出维度均为 512 且有限值检查通过，但余弦均低于
`0.995`，因此这些 OM 均未准入。候选没有执行 500 张 COCO-CN 的 Recall@1/3/5，也没有执行
20 次预热、100 次计时、3 轮的性能门；文档不填补这些缺失数字。

### 新板高精度回退和 canonical group-conv 证据

新板对隔离的 `mobileclip_s0_image_must_keep_high_precision.om` 做了完整 fixture 验证。
32 张固定 COCO-CN fixture 加 4 个压力种子（`310`--`313`）共 36 个输入全部通过，输出
512 维、无 NaN/Inf，最小/最大余弦分别为 `0.9999661446` 和 `0.9999938011`；OM SHA-256
为 `09a670455e017cbad969a8eb7383dcc8e4ac00f73b68362d62440595ae393858`，大小
`64903967` bytes。该文件属于 `must_keep_origin_dtype` 类高精度回退，而不是本节的
选择性 mixed-FP16 方案；新板 Recall 和性能仍未测量，故不改变生产注册表。

group-conv 隔离候选先通过离线等价性检查：原始 MobileCLIP 图像 ONNX 467 个节点，改写后
472 个节点；改写 ONNX SHA-256 为
`65a822d2b816b7111c0896e35740c44d39562f8fa9d25a0d8366b17625d987f5`（45565471 bytes），
36/36 fixture 等价，最小余弦 `0.9999999999999998`、最大绝对误差 `0.0`。随后在
`192.168.1.135` 以同一串行 ATC 策略生成隔离 OM，SHA-256
`440673e0338e2953b93d06f29f6131e5ef0b4ea8921338653036fcc0c94cb3cd`，大小 `34235140`
bytes；ACL 36/36 通过，余弦范围 `0.9999415278`--`0.9999895096`。该变体尚未执行
COCO-CN Recall 或性能门，也未替换 canonical OM；完整证据位于
`shared/reports/precision_sweep/mobileclip_s0_image_precision/board-20260828-192.168.1.135/group_conv/`。

### 2026-08-29 20T/310B1 隔离 ATC/ACL 对照

使用独立测试板 `192.168.1.95`（Ascend 310B1 / 20T）验证 MobileCLIP 两个组件。该板的
CANN 组件版本为 `7.6.0.1.220:8.0.0`（toolkit 目录 `8.0.0`），`npu-smi` 和驱动为
`25.2.0`；`ascendhal_version=7.35.23`、`tsfw_version=1.0`、
`Innerversion=V100R001C21SPC008B208`，板级 `Firmware Version=NA`。固定图像 ONNX
SHA-256 为 `baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79`，文本 ONNX
SHA-256 为 `e4b8323616dfd9a9972279a2be0c5a90089d6e3e49fac50c233abcd3eec67ac1`。

ATC 使用 `--soc_version=Ascend310B1`、`--precision_mode=allow_fp32_to_fp16`、
`--op_select_implmode=high_precision_for_all`、`--enable_graph_parallel=0` 和
`--op_compiler_cache_mode=disable`；文本组件的完整单线程环境（包括
`MAX_COMPILE_CORE_NUMBER=1`、`MULTI_THREAD_COMPILE=0`、`TBE_PARALLEL_COMPILER=0`、
`TE_PARALLEL_COMPILER=1`、`ASCENDC_PAR_COMPILE_JOB=0`、`TILINGKEY_PAR_COMPILE=0` 及
线程库变量）保存在 `text/report/serial_env.txt`。

| 组件 | 输入合同 | OM 大小（bytes） | OM SHA-256 | ACL 样本 | 通过 | 最低余弦 |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| 图像 | `image:1,3,256,256` FP32 | 34179368 | `096b5ced17bf5386be3478a2bb38b32365b6d2c23d3ec1355122669d2ddcd95d` | 36/36 | 通过 | 0.9999415278 |
| 文本 | `text:1,77` INT64 | 137089021 | `d9cbee64b10f7c3fcf0a77c1706d8ea9af3da3d4b922ac27249136ede95c8f77` | 20/20 | 通过 | 0.9999965429 |

图像复用既有隔离 C0 OM；文本 OM 在 `text/om/` 新生成。两者输出均为 512 维
FP32（2048 bytes），所有样本有限，归一化余弦均达到 `0.995` 门槛。文本 fixture 使用
固定 COCO-CN 20 条英文查询和锁定 tokenizer，离线参考后端为
`onnx.reference.ReferenceEvaluator`。本结果仅回答 310B1/CANN 8.0 组合的 ATC/ACL
一致性，不能与 8T/310B4 结果合并。结合同一 8T 板 `npu-smi info` 软件版本仍为
`25.2.0`、而 CANN 8.3 异常在 CANN 8.0 复验中消失，当前证据优先指向 CANN 8.3
用户空间转换栈；这仍是版本对照线索，不是唯一根因证明。

本轮不执行 `--admit`，不更新 `models/registry.json`，不替换生产 OM，不部署到 Case7
服务，也不修改 SQLite/FAISS；未执行 Recall、性能或生产准入门槛。板端聚合报告
`report/mobileclip_consistency_20t.json` 的 `production_mutation` 全部为 `false`，完整
证据已同步至 `reports/model_pipeline/board-20260829-192.168.1.95-8t20t-mobileclip/`。
36 个图像 fixture 的元数据清单 SHA-256 为
`554e561e5bfe6c238948d6fcfa089f974134a84a0e8eeeba26364494ed89c1eef`；20 个文本 fixture
清单 SHA-256 为 `1fa2fd1794061006a27e90b6e8a7f5a239ccb4d00565027d58209ce4556b260d`。
聚合报告同时记录板端验证目录和本地同步目录的映射；ACL 报告中的相对 reference 路径以
隔离验证器目录为基准，不能按报告文件所在目录直接解析。图像资源快照
`c0/report/resource_snapshot_20t.txt` 的 SHA-256 为
`543bfe9f54b0fe1197b086d81f8ca68e7763c3e3d713a7eff529c1cb06b47591`。

## 📥 下载、导出与检查

```bash
export HF_ENDPOINT=https://hf-mirror.com
python prepare_models.py download --model all --hf-endpoint https://hf-mirror.com
python prepare_models.py export --model all
python prepare_models.py check --model all
```

下载后必须记录 checkpoint、tokenizer 和 ONNX 的大小及 SHA-256。镜像证书异常时使用显式 `--insecure-hf-tls`，只影响 HF 镜像请求，不能关闭全局 TLS 校验。

静态检查确认输入名称、shape、dtype、输出维度和有限值；ONNX Runtime 只作为离线参考，不能成为生产 fallback。

`setup.sh board` 只补装运行时所需的 `faiss-cpu==1.7.4` 和
`python-periphery==2.4.1`，不会在 7.4 GiB 板端安装 `requirements-models.txt`，也不会升级
NumPy、PyTorch、CANN 或驱动。下载/导出/ONNX 检查可在有模型工具的离线环境完成，再把经过
hash 校验的 ONNX/OM 明确部署到板端。2026-08-27 历史板 `192.168.8.180` 的 `shared/models` 已包含五个生产 OM、
对应 ONNX 和注册表；`/api/health` 实测为 `ready`，三个模型均为 `admitted`。若更换板卡，仍须
逐文件校验 hash，不得因为文件存在就推断准入。

## 🔧 单线程 ATC

在板端同一个 conda/CANN shell 执行。当前跨板 campaign 的目标板 `192.168.1.135` 的 CANN
版本由 `version.cfg` 实测为 `7.6.0.1.220:8.0.0`；历史 CANN `8.3.0.1.200:8.3.RC1`
只作为异常对照，准入报告必须记录当前板的版本文件，不能把其他板卡的版本号当作当前环境：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export MAX_COMPILE_CORE_NUMBER=1
export MULTI_THREAD_COMPILE=0
export TBE_PARALLEL_COMPILER=0
export TE_PARALLEL_COMPILER=1

# CANN 8.x documents MULTI_THREAD_COMPILE=0 as single-thread model
# conversion. TE_PARALLEL_COMPILER=1 is the minimum valid operator compiler
# process count. Components are converted one at a time. CANN may still show
# internal forkserver/knowledge-bank helper processes; those are not an
# operator worker pool. Evidence records the environment and confirms the
# TE/TBE worker counts rather than treating every helper PID as parallel work.
export ASCENDC_PAR_COMPILE_JOB=0
export TILINGKEY_PAR_COMPILE=0
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1

# 7.4 GiB、无 swap 的板端必须逐个组件串行转换；不要使用 --model all。
python prepare_models.py convert --model resnet50_feature__npu__mixed_fp16 \
  --component image --soc-version Ascend310B4 --allow-low-memory-single-thread
python prepare_models.py convert --model mobileclip_s0__npu__mixed_fp16 \
  --component image --soc-version Ascend310B4 --allow-low-memory-single-thread
python prepare_models.py convert --model mobileclip_s0__npu__mixed_fp16 \
  --component text --soc-version Ascend310B4 --allow-low-memory-single-thread
python prepare_models.py convert --model chinese_clip_rn50__npu__mixed_fp16 \
  --component image --soc-version Ascend310B4 --allow-low-memory-single-thread
python prepare_models.py convert --model chinese_clip_rn50__npu__mixed_fp16 \
  --component text --soc-version Ascend310B4 --allow-low-memory-single-thread
```

不使用交换空间、编译缓存或多线程图编译。`prepare_models.py` 会先探测 `atc --help`，再在
支持的参数中选择串行设置。`--enable_graph_parallel=0` 的已保存转换记录来自 **2026-08-27 历史板
`192.168.8.180` / CANN `7.6.0.1.220:8.0.0`**；当前跨板 campaign 的 8T/310B4 板也实测为
CANN `7.6.0.1.220:8.0.0`，不能把历史帮助输出当作当前板能力。若重新安装 CANN 8.3，必须
重新探测其实际支持的参数并单独保存帮助输出。
`--ac_parallel_enable` 只用于动态 shape
执行阶段的引擎并行控制，不能误记为图编译线程数。图编译仍由单进程环境变量、锁和逐组件转换
保证。CANN 内部可能出现 forkserver、知识库守护进程等辅助 PID；它们不代表并行算子编译。
低内存时按单模型、单组件分开转换，并保留每个 ATC 命令、日志、串行环境、输出大小和 SHA-256。

MobileCLIP 与 Chinese-CLIP 的文本 ONNX 当前登记为 `int64` 输入。当前 CANN 的实际 ATC 支持必须以
板端最小文本图预检为准；若该输入类型被拒绝，应在离线导出阶段改为经验证的 `int32` 合同并同步
更新 tokenizer、ACL 输入字节校验、ONNX/OM hash 和准入报告，不能只修改清单字段。

## 🔬 MobileCLIP 图像选择性混合精度

### 为什么需要候选扫描

MobileCLIP 图像组件曾采用 `must_keep_origin_dtype` 生产基线。历史诊断报告
`reports/model_pipeline/mobileclip_image_stage_diagnosis.json` 和
`mobileclip_transition5_diagnosis.json` 只用于定位误差传播：`network.5/proj/proj.0/lkb_reparam/Conv`
输出记录的余弦为 `0.7932538`，其后 SE 的 `Sigmoid` 为 `0.7223154`、主乘法链为 `0.2903332`，
而后续 `network.5/proj/proj.1/reparam_conv/Conv` 为 `0.5836687`。另一份阶段记录中
`network.5/proj/proj.1/activation/Mul_1` 为 `0.2216825`，最终 embedding 为 `0.2551974`。
这些数值来自诊断图和单个输入，证明需要逐节点排查，但不能单独证明某个节点是唯一根因。

`reports/model_pipeline/acl_mobileclip_image_force_fp32_failed.json` 还记录过一次整组件
`force_fp32` 候选的失败（embedding 余弦 `0.4368718`）。因此不能把“整网 FP32”当作自动修复，
也不能把任何未完成 ACL 验证的 OM 写入生产注册表。

早期直接输入标准正态浮点张量的报告仅是域外算子稳定性诊断：它绕过了照片解码与生产预处理，
不能作为模型准入或推广证据。最终准入改用 32 张固定 COCO-CN 图片和 4 张确定性合成 `uint8`
BGR 图片，全部经过 `NpuEmbeddingBackend.preprocess_image` 后再送入 ONNX 与 ACL。

### C0–C4 候选策略

候选使用同一份生产 ONNX、固定 `Ascend310B4` 和 `allow_fp32_to_fp16`，只通过 `--keep_dtype`
控制 FP32 白名单。每次转换必须在独立目录
`reports/precision_sweep/mobileclip_s0_image_precision/<candidate>/om` 和相应候选报告目录中完成；禁止覆盖 `models/om/`、`models/registry.json`、
生产 FAISS/SQLite 或既有准入报告。

| 候选 | FP32 白名单范围 | 目的 |
| --- | --- | --- |
| `C0` | 空白名单 | 测量全局 mixed-FP16 的真实基线 |
| `C1` | `network.5/proj/proj.0/lkb_reparam/Conv` | 验证首个疑似重参数化卷积 |
| `C2` | `C1` + `network.5` 的 SE（`ReduceMean`、两层 `Conv`、`Relu`、`Sigmoid`、`Mul`） | 验证门控分支的误差传播 |
| `C3` | `C1` + `network.5` 的 GELU/`Erf`、主乘法链和 `proj.1` 卷积 | 验证激活与残差投影 |
| `C4` | `C2` + `C3` + 两组注意力 `MatMul`/`Softmax` 和 head `MatMul` | 最宽的局部白名单，仍不是整网 FP32 |

候选配置以版本化文本文件保存为
`atc_configs/mobileclip_s0_image_precision/C0.keep_dtype.cfg` 至 `C4.keep_dtype.cfg`；`C0` 的配置文件
只作审计占位，转换时不传递 `--keep-dtype-file`。文件内容、SHA-256 和节点数量
写入候选报告。扫描顺序固定为 `C0 → C1 → C2/C3 → C4`，所有 ATC 调用串行执行。若 C1 同时
通过全部门槛，停止扩大白名单；若 C2 与 C3 都通过，选择 FP32 节点较少者，节点数相同时选择
性能更快者。任何候选失败都保留日志但不改变生产 OM。2026-08-27 历史板的最终批次
`full-serial-20260827-prod-domain-cann` 已执行 C0、C1；二者均通过，且 C0 的零节点白名单和
较低 P50 符合选择规则，因此没有继续执行 C2、C3、C4。

### 候选转换和准入命令

以下命令只生成隔离候选；`--output-om-dir`、`--keep-dtype-file` 和 `--report-dir` 不得指向
生产目录：

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

python prepare_models.py convert \
  --model mobileclip_s0__npu__mixed_fp16 \
  --component image --soc-version Ascend310B4 \
  --allow-low-memory-single-thread \
  --precision-mode allow_fp32_to_fp16 \
  --op-select-implmode high_precision_for_all \
  --without-keep-dtype \
  --output-om-dir reports/precision_sweep/mobileclip_s0_image_precision/C0/om \
  --report-dir reports/precision_sweep/mobileclip_s0_image_precision/C0

python prepare_models.py convert \
  --model mobileclip_s0__npu__mixed_fp16 \
  --component image --soc-version Ascend310B4 \
  --allow-low-memory-single-thread \
  --precision-mode allow_fp32_to_fp16 \
  --op-select-implmode high_precision_for_all \
  --keep-dtype-file atc_configs/mobileclip_s0_image_precision/C1.keep_dtype.cfg \
  --output-om-dir reports/precision_sweep/mobileclip_s0_image_precision/C1/om \
  --report-dir reports/precision_sweep/mobileclip_s0_image_precision/C1

python prepare_models.py validate-candidate \
  --model mobileclip_s0__npu__mixed_fp16 --component image \
  --om reports/precision_sweep/mobileclip_s0_image_precision/C1/om/mobileclip_s0_image.om \
  --report reports/precision_sweep/mobileclip_s0_image_precision/C1/acl_numerical_validation.json \
  --reference-dir reports/model_pipeline/references
```

串行 sweep 使用以下入口，并在每次运行前检查剩余内存、输出路径和输入 ONNX SHA-256：

```bash
python scripts/run_mobileclip_precision_sweep.py \
  --manifest reports/datasets/coco_cn_case7_manifest.json \
  --report-dir reports/precision_sweep/mobileclip_s0_image_precision
```

每个候选目录保存 `atc_conversion.json`、`acl_numerical_validation.json`、`retrieval.json` 和
`performance.json`；根目录保存 `summary.json`。候选目录与生产
`reports/model_pipeline/` 分离，避免覆盖已准入证据。`high_precision_for_all` 是算子实现选择策略，不等同于 FP32；真正的精度例外
只能来自候选 `keep_dtype` 文件。若 C4 仍失败，不扩大白名单到整网；先在隔离 ONNX 中验证
group-Conv 拆分与原图等价，再重新执行候选矩阵。

### 数值、检索和性能门槛

候选准入要求 32 张按 `image_id` 固定的 COCO-CN 图片和种子 `310`、`311`、`312`、`313` 生成的
4 张确定性 `uint8` BGR 合成图，共 36 个输入。合成图同样先经过
`NpuEmbeddingBackend.preprocess_image`；每个输出必须为 512 维有限值，归一化余弦相似度不低于
`0.995`。检索门在临时 SQLite/FAISS 中重建 500 张图库，以既有 20 条英文查询比较同轮生产
基线，Recall@1/3/5 不得下降。性能门固定单线程、20 次预热、100 次计时、3 轮重复，候选 P50
不高于基线的 90%，P95 不高于基线。

以下最终报告属于 **2026-08-27 历史板 `192.168.8.180`**：
`reports/precision_sweep/mobileclip_s0_image_precision/full-serial-20260827-prod-domain-cann/summary.json`
记录目标板 `192.168.8.180`、`Ascend310B4`、CANN `7.6.0.1.220:8.0.0`，状态为 `passed`。它使用
的生产 ONNX SHA-256 为
`baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79`，并确认候选和生产 ONNX
字节一致。

| 项目 | 生产基线 | C0（零 FP32 白名单，已选） | C1（仅 `network.5` Conv） |
| --- | ---: | ---: | ---: |
| 36 个生产域输入 | 通过 | `36/36`，全部 `>= 0.995` | `36/36`，全部 `>= 0.995` |
| 英文 Recall@1/3/5 | `0.90/1.00/1.00` | `0.90/1.00/1.00` | `0.90/1.00/1.00` |
| P50（ms） | `64.541943` | `38.913310` | `39.124595` |
| P95（ms） | `64.780770` | `39.271904` | `39.437814` |
| 状态 | 通过 | 通过并选中 | 通过，未推广 |

C0 OM 的 SHA-256 为
`4ba82838bcb13c1542b2e0b5cebb30ef754abdc2745663f92f9becc5ce943517`，大小为 `34179446` bytes。
报告至少保存 ONNX/OM/keep-dtype SHA-256、OM 大小、精确 ATC 命令、CANN 版本、节点清单、ACL 数值、
Recall、P50/P95 和失败原因。早期标准正态张量版本的报告只标为域外压力诊断，不能与本次生产域
fixture 混用。

### 2026-08-27 历史板的已完成受控推广

推广工具仍先执行无写入预检：

```bash
python scripts/promote_mobileclip_precision_candidate.py \
  --summary reports/precision_sweep/mobileclip_s0_image_precision/full-serial-20260827-prod-domain-cann/summary.json
```

只有确认摘要中的 `selected_candidate`、36 个数值样本、500 图 Recall 和性能门槛均为
`passed`，才允许显式执行 `--apply`。该历史批次已按该路径推广 C0：canonical
`models/om/mobileclip_s0_image.om` 已替换为上述 SHA-256 的 OM，注册表写入
`precision_strategy.kind=selective_mixed_precision`、`candidate_id=C0` 和组件级
`allow_fp32_to_fp16`。推广过程串行重建了 500 个 MobileCLIP 图像向量，服务健康状态恢复为
`ready`。`npu-smi` 的 `Health: Alarm` 已作为板卡诊断记录，不单独视为推广失败。

推广命令会先备份当前 MobileCLIP OM、注册表、SQLite（含 WAL/SHM）和该模型 FAISS 文件，停止同一
Case7 PID，原子替换 canonical OM，更新精度策略并串行重建 MobileCLIP embedding。健康检查或重建
失败时自动恢复备份并重启旧服务；默认成功后删除一次性回滚副本，调试时可使用 `--keep-backup`
保留。该命令不修改原始照片、其他模型或 COCO-CN 资产，也不会把未通过候选写入注册表。

## ✅ ACL 准入

```bash
python prepare_models.py validate --model all --admit
```

准入条件：

- PyACL 初始化成功；
- 输入字节数和 dtype 与合同一致；
- 输出维度正确、无 NaN/Inf；
- OM 与 ONNX 归一化向量余弦相似度不低于 `0.995`；
- `models/registry.json` 的 OM/ONNX hash 与实际文件一致。

任何转换失败都不得生成假 OM，也不得替换成其他模型。生产 API 只接受 `status=admitted` 的 NPU 命名空间。

### 2026-08-27 历史板 `192.168.8.180` 复核结果

早期全模型复核在激活 conda/CANN 后执行了 `python prepare_models.py validate --model all` 和
`python prepare_models.py validate --model all --admit`。报告
`shared/reports/model_pipeline/acl_numerical_validation.json` 中五个当时组件均通过 `0.995`
余弦门槛：MobileCLIP image `0.9979770184`、MobileCLIP text `0.9999965429`、
Chinese-CLIP image `0.9999839067`、Chinese-CLIP text `0.9999921322`、ResNet50 image
`0.9999983311`。其中 MobileCLIP image 对应的是推广前 OM，只能作为历史全模型准入证据。

当前生产 MobileCLIP image 的数值与检索准入以最终 C0 扫描报告为准：36 个生产域输入全部通过
`0.995`、500 图英文 Recall@1/3/5 保持 `0.90/1.00/1.00`、并且完成 C0 OM hash 和注册表复核。
其他四个组件仍以早期全模型报告中的 hash、输入/输出字节数和 ACL dtype 为准。数值准入不代表
COCO-CN 之外的泛化、吞吐或端到端延迟。

### MobileCLIP 跨板兼容性实验

8T/310B4 与 20T/310B1 的 MobileCLIP 图像、文本 OM 转换和四格 ACL 交叉验证采用独立
campaign，不修改生产注册表或模型。版本、固件原始字段、ATC 日志、SHA-256 和实测结论统一
记录在 [docs/12-mobileclip-cross-board-compatibility.md](./12-mobileclip-cross-board-compatibility.md)。
本次 campaign 已完成 8T 两组件转换、两板原生复验和四格 ACL 矩阵；聚合 JSON 位于
`reports/model_pipeline/mobileclip-cross-board-20260829-aggregate/compatibility_matrix.json`。
该结果只证明本次 `Ascend310B4`/`Ascend310B1`、CANN `7.6.0.1.220:8.0.0` 和固定 fixture
组合可执行，不等价于生产准入，也不能推广到未测试的 CANN、固件或 SoC。
