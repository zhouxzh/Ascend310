# MobileCLIP 8T/20T 跨板转换与兼容性验证

本报告记录 campaign `ai-album-mobileclip-compat-20260829-8t20t-mobileclip` 的隔离实验。实验对象是 MobileCLIP-S0 图像和文本两个组件，生产服务、生产 OM、注册表、SQLite、FAISS 和原始照片均未修改。ATC、ACL 和跨板执行全部在板端串行完成，没有 CPU fallback、swap 或编译缓存。

## 1. 实验边界

| 角色 | 地址 | 实测 SoC | 用途 |
| --- | --- | --- | --- |
| 8T | `192.168.1.135` | `Ascend310B4` | 重新 ATC 图像/文本并做原生、跨板验证 |
| 20T | `192.168.1.95` | `Ascend310B1` | 复用历史 B1 OM，做原生复验和跨板验证 |

固定输入合同如下：图像为 FP32 `image:1,3,256,256`，输出 512 维；文本为 INT64 `text:1,77`，输出 512 维 FP32。图像 fixture 是按 `image_id` 固定的 32 张 COCO-CN 图片加 seed `310`--`313` 的 4 张压力输入，共 36 个；文本 fixture 是固定的 20 条英文查询。

## 2. 板端版本证据

原始命令输出保存在各板的 `environment/system-status.txt`，聚合视图保存在 `reports/model_pipeline/mobileclip-cross-board-20260829-aggregate/environment/`。

| 字段 | 8T `192.168.1.135` | 20T `192.168.1.95` |
| --- | --- | --- |
| `npu-smi` 型号 | `310B4` | `310B1` |
| `npu-smi` Software Version | `25.2.0` | `25.2.0` |
| `npu-smi` Firmware Version | `NA` | `NA` |
| `npu-smi` Compatibility | `not_reported_by_npu-smi` | `not_reported_by_npu-smi` |
| CANN Toolkit | `/usr/local/Ascend/ascend-toolkit/8.0.0` | `/usr/local/Ascend/ascend-toolkit/8.0.0` |
| CANN component/runtime | `7.6.0.1.220:8.0.0` | `7.6.0.1.220:8.0.0` |
| Python / PyACL | Python 3.9.2，`import acl` 通过 | Python 3.9.2，`import acl` 通过 |
| Driver `Version` | `25.2.0` | `25.2.0` |
| `ascendhal_version` | `7.35.23` | `7.35.23` |
| `tsfw_version` | `1.0` | `1.0` |
| `Innerversion` | `V100R001C21SPC008B208` | `V100R001C21SPC008B208` |
| 内核 | `5.10.0+ #32`，aarch64 | `5.10.0+ #2`，aarch64 |
| 主内存 | `7,912,599,552` bytes | `24,823,164,928` bytes |
| swap | `0` bytes | `0` bytes |
| `Health` | `Alarm` | `Alarm` |

版本号首先以两板直接执行的 `npu-smi info` 为准：8T 输出 `npu-smi 25.2.0 / Version: 25.2.0`、设备名 `310B4`；20T 输出同一软件版本、设备名 `310B1`。`npu-smi info -t board -i 0` 是该工具提供板级固件字段的查询，原文在两板均为 `Firmware Version : NA`。因此本报告不是说 `npu-smi` 没有固件字段，而是如实记录当前命令和设备返回的 `NA`；没有用驱动版本或固件安装包 hash 推断实际固件版本。`npu-smi` 的 `Compatibility` 字段也没有出现在原始输出中，故使用 `not_reported_by_npu-smi`。`Health: Alarm` 只作诊断记录，不是本实验的单独失败条件。

聚合器以 `soc_detected`/`npu_model` 和 `runtime_role` 作为硬件身份依据，并要求二者一致；`compiler_role` 只描述产生 OM 的 ATC 目标，不用于推断运行板。这样即使复用 campaign 目录执行跨板验证，也不会把验证时的编译标签误写成板卡型号。

`atc --version` 在两板的 CANN 8.0 工具链中返回 `ERROR: unknown command line flag 'version'`，所以没有伪造 ATC 版本号；ATC 版本证据使用 `version.cfg`、工具链路径和完整转换日志。原始环境文件：

- 8T：`../reports/model_pipeline/board-20260829-192.168.1.135-8t20t-mobileclip/environment/system-status.txt`
- 20T：`../reports/model_pipeline/board-20260829-192.168.1.95-8t20t-mobileclip/environment/system-status.txt`

## 3. CANN、驱动、固件与目标 SoC

`--soc_version` 是 ATC 的目标 SoC 合同，决定算子实现和执行计划；CANN Toolkit/ATC 负责转换，板端 CANN Runtime/PyACL、驱动和固件负责加载与执行。运行时 CANN 不应低于生成 OM 所用版本，驱动、固件和 CANN 必须使用官方兼容矩阵中的配套组合。版本不配套可能表现为 ATC 算子不支持、OM 加载拒绝、ACL 执行失败、数值偏差或设备重置，但一次错误不能直接归因于固件。

本次两板都观测到 CANN `7.6.0.1.220:8.0.0` 和 driver/npu-smi `25.2.0`，因此矩阵主要检验 `Ascend310B4` 与 `Ascend310B1` 目标 OM 的可执行性，而不是 CANN 版本差异。固件字段均为 `NA`，只能说明“固件版本未取得”，不能说明固件已匹配。

### 3.1 同一 8T 板的 CANN 8.3/8.0 对照

历史异常和本次复验使用同一块 `192.168.1.135`（310B4/8T）。两轮的直接
`npu-smi info` 软件版本均为 `25.2.0`，设备也都识别为 310B4；已知变化是用户空间
CANN 栈从 `8.3.0.1.200:8.3.RC1` 更换为 `7.6.0.1.220:8.0.0`。对照结果如下：

| 同一 8T 板 | CANN 栈 | MobileCLIP 图像结果 | 证据边界 |
| --- | --- | --- | --- |
| 历史异常批次 | `8.3.0.1.200:8.3.RC1` | C0--C4 均未达到 `0.995`；单输入余弦为 `0.2134858668`--`0.6712348461` | 隔离精度扫描，非生产准入 |
| 当前复验批次 | `7.6.0.1.220:8.0.0` | 36/36 ACL 通过；图像余弦最低 `0.9999415278`，文本 20/20 通过 | 跨板 campaign 数值门 |

在 `npu-smi` 软件/驱动版本未变化而 CANN 栈变化后异常消失，现有证据支持“CANN
8.3 工具链（包括其 ATC/算子 lowering/OPP 组件）与该模型存在兼容性问题”这一工作假设。
这是一项强相关的版本对照，不是严格的单变量因果证明：CANN 安装可能同时替换多个
用户空间组件，且本轮没有在同一系统上来回切换并重复完整矩阵。固件字段是 `NA`，所以
不能把这次异常归因于固件，也不能仅凭该对照宣称所有 CANN 8.3 环境必然失败。后续若需
锁定算子根因，应在相同 ONNX、相同 `--soc_version` 和相同单线程参数下，对两套 CANN
逐组件重放并保留完整 ATC/ACL 日志。

官方规则参考：

- [ATC `--soc_version` 参数说明](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910/devaids/atctool/atlasatcparam_16_0036.html)
- [CANN 升级说明](https://www.hiascend.com/document/detail/en/canncommercial/800/softwareinst/instg/instg_0028.html)，同时升级顺序为 firmware -> driver -> CANN
- [`npu-smi` 版本查询说明](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910/softwareinst/instg/instg_0064.html)

## 4. ONNX 与 ATC 证据

| 组件 | 本地 ONNX | 大小 | SHA-256 | 输入/输出 |
| --- | --- | ---: | --- | --- |
| image | `models/onnx/mobileclip_s0_image.onnx` | 45,562,918 | `baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79` | FP32 `[1,3,256,256]` -> FP32 512 |
| text | `models/onnx/mobileclip_s0_text.onnx` | 169,791,644 | `e4b8323616dfd9a9972279a2be0c5a90089d6e3e49fac50c233abcd3eec67ac1` | INT64 `[1,77]` -> FP32 512 |

8T 两个组件分别以 `--soc_version=Ascend310B4` 转换，实际 ATC 合同为：

```text
--framework=5
--precision_mode=allow_fp32_to_fp16
--op_select_implmode=high_precision_for_all
--enable_graph_parallel=0
--op_compiler_cache_mode=disable
```

所有转换设置 `MAX_COMPILE_CORE_NUMBER=1`、`MULTI_THREAD_COMPILE=0`、`TBE_PARALLEL_COMPILER=0`、`TE_PARALLEL_COMPILER=1`、`ASCENDC_PAR_COMPILE_JOB=0`、`TILINGKEY_PAR_COMPILE=0`、`OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`、`NUMEXPR_NUM_THREADS=1`、`CMAKE_BUILD_PARALLEL_LEVEL=1` 和 `MAKEFLAGS=-j1`。8T 转换日志和命令位于 `../reports/model_pipeline/board-20260829-192.168.1.135-8t20t-mobileclip/native/`。

20T 本轮没有重复 ATC；使用历史 `Ascend310B1` OM 的原始命令和转换报告进行 ACL 原生复验。历史命令仍保存在板端 `/home/HwHiAiUser/Documents/ai-album-atc-20260829-20t/{c0,text}/report/command.txt`，本轮不把历史报告当作新的 20T 转换证据。

转换证据文件的 SHA-256 也已记录：8T image/text 的 `atc_conversion.json` 为
`d51f7a218dc45bb3e05bc8875783587b5e4b9843b3eded4258979c7700a0455b` /
`856b7fe513035665b4153065cb177bb4b662623c00b55779379bcde711a634af`，对应 ATC 日志为
`04470045a2270ddaa72d75e2f574325bb079c93e51b45cff1b39ec90ed768f85` /
`4dfe2ac5d4ec88c33565b826904818b665d4ca9ad0f153ba132efc918dcda2d`。20T 历史 image/text
转换 JSON 为 `b493cd13b0804e0bea0a01eda4e455cac1713c65c6de63d06673b2a9f18a677a` /
`9d4108784a7d0bbfa6908079d583035f6ff4e3122b3654c1a0d4adba977a81b6`，历史 ATC 日志为
`2f1a756fa46867b91f567960b1bd492dbf6028e6c1978d2f4061de1701873bd8` /
`451770ad47b4ff4345d17f26326fac089f9e879576c5bdfed34de1e39b2bdde5`。

## 5. OM 归档与哈希

本地归档目录为 `../models/om/compatibility/20260829-8t20t-mobileclip/`。聚合目录中的四个 OM 和两份 ONNX 使用硬链接，避免制造第二份物理副本；修正来源字段后的 `artifact_manifest.json` SHA-256 为 `c78d3f41a86059ed6a77304b8ec358ca452668db0f0b3e325262ad5f2736919b`，严格哈希校验通过。

| 编译目标 | 组件 | 大小 bytes | SHA-256 |
| --- | --- | ---: | --- |
| `Ascend310B4` / 8T | image | 34,179,432 | `6d294c0d8ac069c12728eb3b2f29c8c06c9cff4003e1e81121181b89e6cf4eb6` |
| `Ascend310B4` / 8T | text | 137,089,078 | `eaee8ed5b0695542da24cede466d34556ffbcf3048001c41e47bd0d9a4184382` |
| `Ascend310B1` / 20T | image | 34,179,368 | `096b5ced17bf5386be3478a2bb38b32365b6d2c23d3ec1355122669d2ddcd95d` |
| `Ascend310B1` / 20T | text | 137,089,021 | `d9cbee64b10f7c3fcf0a77c1706d8ea9af3da3d4b922ac27249136ede95c8f77` |

本地完整聚合报告为 `../reports/model_pipeline/mobileclip-cross-board-20260829-aggregate/compatibility_matrix.json`，本次归档快照 SHA-256 为 `829d0561c145c431a20daa20612054a9f6cfba1ec111a39edcca397a1c614fda`。

## 6. ACL 数值门

每个样本均检查 ACL 初始化、输入字节数/dtype、512 维输出、有限值和归一化余弦 `>= 0.995`。所有原生和跨板单元均通过：

| 组件 | 样本数 | 通过数 | 最低余弦 | 最高余弦 |
| --- | ---: | ---: | ---: | ---: |
| image | 36 | 36 | `0.9999415278434753` | `0.9999895095825195` |
| text | 20 | 20 | `0.999996542930603` | `0.9999986886978149` |

8T 原生窗口为 `16:12:39`--`16:12:49 +08:00`；20T 原生窗口为 `16:13:41`--`16:13:51 +08:00`。对应 ACL JSON 和日志仍保留在两个 `board-*` 报告目录中。`passed_count`、余弦范围和输出字节合同已在本地聚合 `cells/` 视图中复核。

## 7. 四格跨板矩阵

以下状态是每个组件独立执行的结果；没有重新编译，也没有 CPU fallback。

| 编译目标 OM | 8T / 310B4 运行 | 20T / 310B1 运行 |
| --- | --- | --- |
| 8T OM (`Ascend310B4`) | image `passed` 36/36；text `passed` 20/20 | image `passed` 36/36；text `passed` 20/20 |
| 20T OM (`Ascend310B1`) | image `passed` 36/36；text `passed` 20/20 | image `passed` 36/36；text `passed` 20/20 |

因此图像四格共 144/144、文本四格共 80/80 样本通过，所有单元的最低余弦均高于 `0.995`。完整单元记录和原始日志路径见聚合报告的 `matrix` 字段；失败分类仍限定为 `load_rejected`、`execute_failed`、`output_contract_mismatch`、`numerical_mismatch` 和 `non_finite`，本轮没有出现这些失败。

## 8. 结论边界

在本次观测到的两块板、同一 CANN/driver 组合和固定 fixture 上，8T 新生成的图像/文本 OM 与 20T 历史 OM 均能在两块板上加载并通过 ACL 数值门。也就是说，这一对 `Ascend310B4`/`Ascend310B1` OM 在本实验环境中表现出可执行兼容性。

这不是对所有 310B 型号、固件或 CANN 版本的普遍承诺。生产注册表没有更新，四个 OM 也没有自动替换生产 OM；需要正式准入、检索和性能门后才能部署。两板 `Firmware Version=NA`，所以不能把本结果写成“固件版本已确认”或用固件字段解释其他环境中的 MobileCLIP 异常；CANN 8.3/8.0 的版本对照线索单独记录在第 3.1 节。

## 9. 复现实验清单

- [x] 两板 SSH、SoC、CANN、driver、固件查询原文、内存和 swap 已保存。
- [x] 图像/文本 ONNX 合同和 SHA-256 已核对。
- [x] 8T 图像/文本 ATC 串行完成，命令和日志完整。
- [x] 8T 原生图像 36/36、文本 20/20 ACL 通过。
- [x] 20T 历史 OM 原生图像 36/36、文本 20/20 ACL 复验通过。
- [x] 两套 OM 在另一块板的图像/文本跨板单元均通过。
- [x] OM 已回传本地，板端与本地大小和 SHA-256 一致。
- [x] 无生产注册表、服务、数据库、FAISS、照片或生产 OM 改动。
- [x] 聚合器严格模式、脚本语法和专项测试通过。

## 10. 运行入口

板端只需在 CANN/conda 环境中执行隔离 campaign 脚本；脚本默认单线程、无缓存并拒绝生产路径：

```bash
bash scripts/run_mobileclip_cross_board_campaign.sh --help
python scripts/aggregate_mobileclip_compatibility.py \
  --campaign-root /path/to/isolated-campaign --strict
```

四种矩阵单元使用显式的编译目标和运行角色。`--soc-version` 描述 OM 的
ATC 目标，`--runtime-label` 描述当前实际运行板；验证模式会从
`npu-smi` 读取 SoC 并拒绝角色不一致的执行：

```bash
# 8T/310B4 原生转换并验证
bash scripts/run_mobileclip_cross_board_campaign.sh --mode native \
  --soc-version Ascend310B4 --artifact-label 8t-310b4 \
  --runtime-label 8t-310b4 --campaign-root /path/to/8t-campaign

# 20T/310B1 原生复验历史 OM
bash scripts/run_mobileclip_cross_board_campaign.sh --mode validate \
  --soc-version Ascend310B1 --artifact-label 20t-310b1 \
  --runtime-label 20t-310b1 --om-dir /path/to/20t-om \
  --campaign-root /path/to/20t-campaign --allow-existing --cell-id 20t-native

# 20T OM 在 8T 上运行（交叉验证）
bash scripts/run_mobileclip_cross_board_campaign.sh --mode validate \
  --soc-version Ascend310B1 --artifact-label 20t-310b1 \
  --runtime-label 8t-310b4 --om-dir /path/to/20t-om \
  --campaign-root /path/to/8t-campaign --allow-existing --cell-id 20t-on-8t

# 8T OM 在 20T 上运行（交叉验证）
bash scripts/run_mobileclip_cross_board_campaign.sh --mode validate \
  --soc-version Ascend310B4 --artifact-label 8t-310b4 \
  --runtime-label 20t-310b1 --om-dir /path/to/8t-om \
  --campaign-root /path/to/20t-campaign --allow-existing --cell-id 8t-on-20t
```

示例中的 `/path/to/*` 必须是板端隔离目录；不能指向 Case7 生产的
`models/om`、`data`、`photos`、`reports` 或正在运行的服务目录。完成各板
证据同步后，在控制机执行上面的 `aggregate ... --strict`，再核对
`artifact_manifest.json` 和 `compatibility_matrix.json` 的 SHA-256。

本报告不要求重新运行已经完成的转换；重新实验时应生成新的 campaign id，并保留每个板的原始 `system-status.txt`、ATC 日志、ACL JSON 和 hash 清单。
