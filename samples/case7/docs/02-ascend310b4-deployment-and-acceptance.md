# Ascend 310B4 部署与验收记录

*目标板、发布流程、模型/数据验收和当前实测证据。*

---

## 🎯 目标环境

| 项目 | 值 |
| --- | --- |
| SSH | `HwHiAiUser@192.168.1.135` |
| 项目目录 | `/home/HwHiAiUser/Documents/ai-album` |
| 芯片 | Ascend 310B4 / 8T |
| 系统 | Orange Pi AIpro |
| CANN | `7.6.0.1.220:8.0.0`（2026-08-29 跨板 campaign 当前实测） |
| 触摸屏 | QDtech MPI1001，HDMI 1920x1080@60 Hz |
| 服务 | FastAPI，`0.0.0.0:7860` |

`npu-smi` 的 `Health: Alarm` 只作为诊断记录。只要设备可见、PyACL 初始化成功、模型可执行，它不单独阻断部署或准入。

### 2026-08-28 历史操作板状态（CANN 8.3）

历史快照中的操作板为 `HwHiAiUser@192.168.1.135`，已完成 Case7 部署，CANN 为
`8.3.0.1.200:8.3.RC1`。当前受管图库只有 20 张照片；MobileCLIP、Chinese-CLIP 和
ResNet50 三个模型各有 20 条 embedding。英文与中文 NPU 搜索以及服务重启恢复已经通过。
这是一轮小图库功能复核，不是 500 张 COCO-CN 重建、Recall 或性能复测；下文所有 500 图、
Recall 和 P50/P95 数字均保留为明确日期和板地址的历史证据，不能外推到当前板。

### CANN 8.3 异常与 CANN 8.0 复验的版本对照

同一块 8T 板（`192.168.1.135`）在历史 CANN 8.3 安装后出现 MobileCLIP 图像
`allow_fp32_to_fp16` 转换结果异常：C0--C4 的单输入 ACL 余弦仅为
`0.2134858668`--`0.6712348461`，均低于 `0.995`。重新安装为 CANN
`7.6.0.1.220:8.0.0` 后，直接执行的 `npu-smi info` 仍为 `25.2.0`（设备仍为
`310B4`），但同一 ONNX 的 8T 原生图像 36/36 和文本 20/20 ACL 数值门通过；这说明
板级 `npu-smi` 软件/驱动版本没有改变，变化集中在 CANN 用户空间工具链及其配套组件。

因此当前工程把 CANN 8.3 视为该异常的主要嫌疑版本，而不是把固件作为未经证实的原因。
这是版本相关性证据，不是完整因果证明；CANN 安装可能同时更新 ATC、OPP、TBE/算子
实现和 Runtime，仍需在完全相同的系统条件下回放才能逐项隔离。`npu-smi info -t board -i 0`
提供固件字段，但两轮记录均为 `Firmware Version : NA`，不能从该字段缺失推断固件版本。

### 2026-08-28 新板 MobileCLIP 图像精度扫描

为诊断历史快照中的 CANN `8.3.0.1.200:8.3.RC1` 在新板上的行为，停止 Case7 服务后，使用生产
MobileCLIP 图像 ONNX（SHA-256
`baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79`）逐个执行 C0--C4
候选转换。所有候选均使用 `Ascend310B4`、`allow_fp32_to_fp16`、
`high_precision_for_all`、`--op_compiler_cache_mode=disable`、`--ac_parallel_enable=0`，
以及 `MULTI_THREAD_COMPILE=0`、`MAX_COMPILE_CORE_NUMBER=1`、`TE_PARALLEL_COMPILER=1` 的
串行环境；输出和报告均位于隔离目录
`shared/reports/precision_sweep/mobileclip_s0_image_precision/board-20260828-192.168.1.135/`。

| 候选 | FP32 白名单节点数 | OM 大小（bytes） | OM SHA-256 | ACL 余弦（1 个固定输入） | 结果 |
| --- | ---: | ---: | --- | ---: | --- |
| C0 | 0 | 34208184 | `1e770a3bb40379d768042cd8754f9d508a0f8e55c7de3db8a829041644358566` | 0.2134858668 | 失败 |
| C1 | 1 | 34227522 | `84026cae4d3299ec726f8fffebdc92c912fec61bab4e9ee885073510933ad770` | 0.3825977445 | 失败 |
| C2 | 7 | 34495846 | `c3d51f06b0e7a572d785a3564d4738f7a2b5b4cc7169c742253c3808f52afb70` | 0.3103438020 | 失败 |
| C3 | 12 | 34761415 | `a465c84720f543c42058bbd5f4f3b38f43f4c8937e01c6a5160d6857008827d8` | 0.3635685146 | 失败 |
| C4 | 29 | 40161094 | `3bc72167dd42117c0c6a2a0aa9367ca27d8ca1878f2c6e8a24a236d833c44848` | 0.6712348461 | 失败 |

五个候选的 ACL 输出均为 512 维且有限值正常，但都未达到 `0.995` 门槛；上表每个候选
只执行了一个固定输入的板端烟测，不是 36 输入准入结果。候选未进入注册表、未替换生产
`models/om/mobileclip_s0_image.om`，也没有执行候选的 COCO-CN Recall 或性能测试。因此
不能用这些结果宣称新板已经通过选择性混合精度。

### 新板全精度回退与 group-conv 隔离证据

作为可运行性诊断，已有的
`models/om/mobileclip_s0_image_must_keep_high_precision.om` 在新板上以 32 张固定 fixture
和种子 `310`--`313` 共 36 个输入验证：36/36 通过，输出均为 512 维有限值，余弦范围为
`0.9999661446`--`0.9999938011`。该 OM 的 SHA-256 为
`09a670455e017cbad969a8eb7383dcc8e4ac00f73b68362d62440595ae393858`，大小为
`64903967` bytes。它是 `must_keep_origin_dtype` 类的高精度回退证据，不是
`allow_fp32_to_fp16` 选择性候选；尚未完成其新板 COCO-CN Recall 和性能门槛，也未写入生产
注册表。

在 C0--C4 均失败后，另行验证了 canonical group-conv 拆分候选。原始 ONNX（467 个节点）
改写为 472 个节点，改写 ONNX SHA-256 为
`65a822d2b816b7111c0896e35740c44d39562f8fa9d25a0d8366b17625d987f5`，大小为
`45565471` bytes；离线等价性 fixture 验证为 36/36，最小余弦
`0.9999999999999998`、最大绝对误差 `0.0`。在新板以同一串行 ATC 策略转换后的隔离 OM
SHA-256 为 `440673e0338e2953b93d06f29f6131e5ef0b4ea8921338653036fcc0c94cb3cd`，大小为
`34235140` bytes；ACL 36/36 通过，余弦范围为 `0.9999415278`--`0.9999895096`。
该候选同样未做 Recall/性能测试、未推广到生产；证据文件为
`shared/reports/precision_sweep/mobileclip_s0_image_precision/board-20260828-192.168.1.135/group_conv/`。

### 2026-08-29 20T/310B1 CANN 8.0 MobileCLIP 对照转换与 ACL 一致性

本节记录独立测试板 `HwHiAiUser@192.168.1.95`（Ascend 310B1 / 20T）。它不是
310B4 / 8T 的部署或生产准入证据，不能与本文件的 310B4 结果合并。板端实测环境为
CANN `7.6.0.1.220:8.0.0`，`npu-smi`/驱动 `25.2.0`，
`ascendhal_version=7.35.23`、`tsfw_version=1.0`、
`Innerversion=V100R001C21SPC008B208`；`npu-smi` 板级 `Firmware Version=NA`，
且 `Health: Alarm` 仅作诊断记录。没有根据驱动版本推断固件版本。

图像组件复用隔离目录中的 C0 OM，文本组件在同一隔离根目录单独执行一次 ATC。两条组件
均使用 `Ascend310B1`、`--framework=5`、`allow_fp32_to_fp16`、
`high_precision_for_all`、`--enable_graph_parallel=0` 和
`--op_compiler_cache_mode=disable`；文本转换使用完整单线程环境记录。

| 组件 | ONNX SHA-256 | OM 大小（bytes） | OM SHA-256 | ACL fixture | ACL 通过 | 余弦范围 | 结论 |
| --- | --- | ---: | --- | ---: | ---: | --- | --- |
| MobileCLIP 图像 | `baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79` | 34179368 | `096b5ced17bf5386be3478a2bb38b32365b6d2c23d3ec1355122669d2ddcd95d` | 36（32 图像 + seed 310--313） | 36/36 | 0.9999415278--0.9999895096 | 通过 |
| MobileCLIP 文本 | `e4b8323616dfd9a9972279a2be0c5a90089d6e3e49fac50c233abcd3eec67ac1` | 137089021 | `d9cbee64b10f7c3fcf0a77c1706d8ea9af3da3d4b922ac27249136ede95c8f77` | 20 条固定 COCO-CN 英文查询 | 20/20 | 0.9999965429--0.9999986887 | 通过 |

两个 OM 的 ACL 输出均为 512 维、2048 bytes、ACL dtype `0`（float32），每个样本均有限且
归一化余弦不低于 `0.995`。文本参考由固定 tokenizer 生成 `[1,77] int64` 输入，离线参考后端
为 `onnx.reference.ReferenceEvaluator`。本轮未执行 `--admit`，未修改生产服务、
`models/registry.json`、SQLite 或 FAISS，也未执行 Recall 或性能测试；结果只证明
310B1/CANN 8.0 这一组合的 ATC/ACL 一致性；结合同一 8T 板 `npu-smi info` 软件版本不变而
CANN 8.3 异常在 CANN 8.0 复验中消失，当前证据把问题范围优先缩小到 CANN 8.3 用户空间
转换栈，但仍不构成唯一根因证明。完整 JSON、TXT、ATC 日志和 fixture 清单已同步到本地
`reports/model_pipeline/board-20260829-192.168.1.95-8t20t-mobileclip/`，板端原始根目录为
`/home/HwHiAiUser/Documents/ai-album-atc-20260829-20t/`。
图像 36 输入的 fixture 清单、文本 20 条查询清单及其 SHA-256 以本次 campaign 的验证报告为准，统一见 docs/12。
逐样本 ACL JSON 保存在 `reports/model_pipeline/board-20260829-192.168.1.95-8t20t-mobileclip/validation/`，
板端验证使用 campaign 根目录下的 `references/image/` 与 `references/text/`；原始环境、内存、NPU
和进程快照保存在同一 campaign 的 `environment/system-status.txt`。固定清单的具体 SHA-256 与四格
矩阵映射统一见 `docs/12-mobileclip-cross-board-compatibility.md`。

## 🚀 发布流程

本机执行：

```bash
bash scripts/deploy_ascend8t.sh --ssh-target HwHiAiUser@192.168.1.135
bash scripts/deploy_ascend8t.sh --ssh-target HwHiAiUser@192.168.1.135 --apply
```

脚本使用 `releases/<release-id>`、`current` 和 `shared` 结构。先在 7861 启动备用版本并检查健康接口，再切换 `current`；不使用 `--delete`，不覆盖 `shared/models`、`shared/data`、`shared/photos` 或 `shared/reports`，只停止本项目 PID。

板端启动：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Documents/ai-album/current
bash setup.sh board
bash scripts/run_smart_album_service.sh --root "$PWD"
```

启动脚本预检 `acl`、FAISS、OpenCV、FastAPI、multipart 和生产注册表，然后启动 NPU-only FastAPI 服务。

PhotoFrame 功能代码在 `case7-photoframe-20260823m` 之后又加入了严格整数 ID 校验和发布回滚加固；
后续发布不覆盖共享模型、数据、照片或报告资产。以下是 **2026-08-27 历史板
`192.168.8.180`** 的系统快照：
`shared/reports/system-status-20260827-111232.txt`（SHA-256 `925635bce52a6838c48536ddaee970d1eadf2c7aae31b90ca03f0fd3fb84c2e9`）。板端 CANN `7.6.0.1.220:8.0.0`
的 `atc --help` 同时提供 `--enable_graph_parallel` 和 `--ac_parallel_enable`；转换器按探测结果选择
受支持的串行参数。已同步的 ATC 转换记录包含 `--enable_graph_parallel=0`，并配合单进程环境变量、
`MAX_COMPILE_CORE_NUMBER=1` 和逐组件转换，未使用并行编译。

## 🧪 COCO-CN 证据边界

固定 manifest SHA-256 为 `0be8de06a3ddf946b5c4ba2332276bd215783b36d9d0078453bde00e9911d08a`；检索报告 SHA-256 为 `be31ab5f002b129140c08048ae0784689a088f73225b94dceb941a107b9b8402`。

| 查询语言 | 模型 | Recall@1 | Recall@3 | Recall@5 | 结论 |
| --- | --- | ---: | ---: | ---: | --- |
| 英文 | MobileCLIP-S0 | 0.90 | 1.00 | 1.00 | 通过 |
| 中文 | Chinese-CLIP RN50 | 0.80 | 0.95 | 1.00 | 通过 |

上表是 **2026-08-27 历史板 `192.168.8.180`** 完成的 COCO-CN/NPU 准入报告，作为历史检索证据保存；它不代表当前操作板重新测得的 Recall。
ResNet50 仅用于经典相似图，不参与语义模型混合。

### 2026-08-27 历史板 `192.168.8.180` 实测（推广前基线）

历史板 `192.168.8.180` 的推广前 `/api/health` 返回 `status=ready`、`backend=npu`，并报告
500 张可用照片、每个生产模型 500 条 embedding、104 张含人脸照片和三个 `admitted` 模型。
服务运行于发布 `20260827-newboard` 的 `7860` 端口；`npu-smi 25.2.0` 识别为 `310B4`，
`Health: Alarm` 仍只作诊断记录。该次快照同时记录无 swap、可用内存约 5.1 GiB 和根分区可用 82 GiB。
快照中的 `xrandr` 记录为 HDMI-1/HDMI-B-1 各 `1024x768@60Hz`；这只是当前 X 会话的输出模式，
不替代 QDtech MPI1001 的 `1920x1080` 原生分辨率和触摸交互验收。

板端推广前 ACL 数值复核报告为 `shared/reports/model_pipeline/acl_numerical_validation.json`，阈值为
`0.995`，五个组件均通过：

| 模型组件 | 余弦相似度 | OM SHA-256（板端） | 结果 |
| --- | ---: | --- | --- |
| MobileCLIP image（推广前） | 0.9979770184 | `b1273323d239a7d3dfa0faa0f03e41817b84e026980b9a178da543a48773450b` | 通过 |
| MobileCLIP text | 0.9999965429 | `3d460edb17c7f8407971c5dd1e3a6a6042aa7223b83abe51fe2cf746e050a7f1` | 通过 |
| Chinese-CLIP image | 0.9999839067 | `e15ddfb09bd00b0c1c7beeceb3f5e6edb32577e5419feef171fc48d6db8c420f` | 通过 |
| Chinese-CLIP text | 0.9999921322 | `c72af4ce543193478c81b4823e4fb2a0e7ed0d7f30ec7a5913c8bf12c2b570b1` | 通过 |
| ResNet50 image | 0.9999983311 | `5bbedd1332632779d387d0e9d1d1f0ff29d01b8d6caa53bb11c2aeec4551e77a` | 通过 |

同一板端发布中，`python -m unittest discover -s tests -v` 共通过 84 项；完整输出保存为
`shared/reports/unittest-board-20260827-111600.txt`（SHA-256 `7d0b7c08e730a224121e43bb3790ecb4d20ccbbebb89db4675f3f65b10dfc764`）。这是代码/协议测试证据，
不等同于 COCO-CN Recall 或性能结果。随后在同一板端单线程重建三模型各 500 条向量并运行固定查询集，
报告保存为 `shared/reports/datasets/coco_cn_case7_retrieval_20260827_newboard.json`，其 SHA-256 为
`04662e3b88bd95cbed1e4e4eb04165997c4b69bd88ec6e5eeaed41b64b6dac4d`：英文 Recall@1/3/5 为
`0.90/1.00/1.00`，中文为 `0.80/0.95/1.00`，两种语言的 Recall@3 均达到 `0.80` 门槛；
重建统计为 `discovered=500,indexed=500,unchanged=0,duplicates=0,skipped=0,unavailable=0`。
该报告是上述历史板的检索证据，不应与其他板的性能表混用。

### 2026-08-27 历史板 `192.168.8.180` 的选择性精度推广后状态

在上述基线之后，板端执行了独立的 MobileCLIP 图像分支 C0/C1 串行扫描。最终批次
`shared/reports/precision_sweep/mobileclip_s0_image_precision/full-serial-20260827-prod-domain-cann/summary.json`
状态为 `passed`，C0（零 FP32 `keep_dtype` 节点）被选中并推广；C1 也通过但 P50 略高，未替换生产模型。
C0 使用的 ONNX SHA-256 为
`baaffc19bb5af33aa3ec05180e9e9c43c4a1f01a3ea1b64737aaaf325dca1b79`，生产 OM SHA-256 为
`4ba82838bcb13c1542b2e0b5cebb30ef754abdc2745663f92f9becc5ce943517`，大小 `34179446` bytes。
36/36 个生产域输入满足 `0.995` 余弦门槛，500 张图库的英文 Recall@1/3/5 为
`0.90/1.00/1.00`；C0 P50/P95 为 `38.913310/39.271904 ms`，同轮基线为
`64.541943/64.780770 ms`。推广过程串行重建了 500 个 MobileCLIP 图像 embedding，其他模型、照片和
COCO-CN 数据未改变。

推广后服务发布为 `releases/20260827-181057`，最新系统快照为
`shared/reports/system-status-20260827-181321.txt`（SHA-256
`dd346874c8518d0f6ebb154a0268bba6de088c49d711f9bdcfda7cec2988ceda`），
`/api/health` 实测为 `ready`，三个模型均为 `admitted`，每个模型 500 条 embedding；
`/api/models` 暴露 MobileCLIP 图像 `precision_strategy.candidate_id=C0` 和
`precision_mode=allow_fp32_to_fp16`。`Health: Alarm` 仍只作诊断记录。

### 2026-08-27 历史板 `192.168.8.180` 重启恢复与天气解耦实测

在发布 `releases/20260827-185703` 上，先读取运行中的本机显示状态，再只停止并重启
Case7 服务，等待 ACL/模型初始化完成后重新读取状态。报告为
`shared/reports/display-restart-recovery-20260827.json`（SHA-256
`69c9fd4f8e89b9a09980edbf9b8919bc35a9f08f51e14420258643e114ade36b`）。重启前后均为
`photo_id=194`、`selection_revision=3`，健康状态均为 `ready`，500 张照片和三个模型各
500 条 embedding 未变化。随后等待天气调度 tick，仍为 `photo_id=194`；revision 按设计变为
4 并更新 ETag，说明天气刷新不会触发本机换图，但天气相关显示状态仍可失效更新。

## 📊 历史性能证据（`192.168.8.180`）

历史报告路径为 `shared/reports/benchmarks/coco_cn_case7_performance.json`，包含单线程 20 次预热、100 次计时、3 轮重复。
以下表格是历史运行记录，不是当前操作板目标值；下方日期化报告仍属于同一历史板：

| 操作 | P50 | P95 |
| --- | ---: | ---: |
| MobileCLIP 图像编码 | 55.898 | 56.106 |
| Chinese-CLIP 图像编码 | 39.611 | 39.868 |
| ResNet50 图像编码 | 31.468 | 31.600 |
| MobileCLIP 文本编码 | 12.568 | 12.663 |
| Chinese-CLIP 文本编码 | 11.988 | 12.048 |
| FAISS 搜索 | 0.413 | 0.449 |
| 自动英文文本 API | 18.448 | 19.563 |
| 自动中文文本 API | 18.011 | 18.417 |

### 2026-08-27 历史板 `192.168.8.180` 性能实测

历史板完整报告为 `shared/reports/benchmarks/coco_cn_case7_performance_20260827_newboard.json`，SHA-256 为
`35428162b60153520bfb371819d9eca11d5759401ff4b2ee5ace728a8544695d`。协议为单线程、预热 20 次、
计时 100 次、重复 3 轮，API 地址为 `http://127.0.0.1:7860`；报告 `errors=[]`。

| 操作 | P50 (ms) | P95 (ms) |
| --- | ---: | ---: |
| MobileCLIP 图像编码 | 55.452 | 55.662 |
| Chinese-CLIP 图像编码 | 39.535 | 39.726 |
| ResNet50 图像编码 | 31.499 | 31.644 |
| MobileCLIP 文本编码 | 12.556 | 12.659 |
| Chinese-CLIP 文本编码 | 12.024 | 12.087 |
| FAISS 搜索 | 0.429 | 0.447 |
| MobileCLIP 相似图 | 56.533 | 56.904 |
| 自动英文文本 API | 19.536 | 20.030 |
| 自动中文文本 API | 19.084 | 19.599 |

测量进程 RSS 从约 64.7 MiB 增至 681.5 MiB；系统无 swap，前后可用内存约 5.76/5.24 GiB。
该表只描述上述历史板的本次性能，不推断当前板的性能、长期稳定性或真实电子纸刷新速度。

## 🖥️ 触摸屏与 E6 证据

Firefox kiosk 使用 `DISPLAY=:0`、现有 Xauthority 和 `?mode=touchscreen` 启动。当前页面为纯 FastAPI 静态页面，无额外前端运行时依赖；触摸屏 UI 的详细操作见 [07-touchscreen-ui-and-operations.md](./07-touchscreen-ui-and-operations.md)。

E6 dry-run 已验证 800x480 PNG、192000 bytes 帧、六色编号和高半字节优先打包。驱动板型号和 GPIO/SPI 接线尚未确认，因此真实 E6 刷新仍是待验收项。

## 🧪 验收命令

```bash
bash scripts/collect_system_status.sh
python -m unittest discover -s tests -v
python -m py_compile app.py server_config.py photo_index.py smart_selector.py
curl http://127.0.0.1:7860/api/health
curl http://127.0.0.1:7860/api/models
curl http://127.0.0.1:7860/api/index/stats
bash scripts/launch_touchscreen_kiosk.sh
```

PhotoFrame 五分钟测试支持两条互斥链路。当前实测目标是 Waveshare PhotoPainter；E1002 仅是历史对照。上游 API 资料中的服务器主动推送使用 `POST /api/display-image`，但这不代表当前设备已运行该端点；只有操作者确认固件、IP 和实机响应后，310B 才能向该根 URL 建立连接。URL Rotation 兼容模式则由终端主动请求 310B。两者都不允许根据错误响应猜测固件。把固定的 20 张测试图片放入 `shared/incoming/photoframe-test/` 后执行：

```bash
# 在电脑端（Git Bash/WSL 的 OpenSSH）进入测试目录，只传输这一个固定批次；不要同步整个 Pictures 目录。
cd "/c/Users/zhoux/Pictures/电子相册"
ssh HwHiAiUser@192.168.1.135 'mkdir -p /home/HwHiAiUser/Documents/ai-album/shared/incoming/photoframe-test'
scp CIMG2780.JPG CIMG2781.JPG CIMG2782.JPG CIMG2783.JPG CIMG2784.JPG \
  CIMG2785.JPG CIMG2786.JPG CIMG2787.JPG CIMG2788.JPG CIMG2789.JPG \
  CIMG2790.JPG CIMG2791.JPG CIMG2792.JPG CIMG2793.JPG CIMG2794.JPG \
  CIMG2795.JPG CIMG2796.JPG CIMG2797.JPG CIMG2798.JPG CIMG2799.JPG \
  HwHiAiUser@192.168.1.135:/home/HwHiAiUser/Documents/ai-album/shared/incoming/photoframe-test/

# 然后在板端 current 发布目录执行：
cd /home/HwHiAiUser/Documents/ai-album/current
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test \
  --limit 20 \
  --profile-id waveshare_photopainter_73 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860
```

启用服务器主动推送时，追加已确认的设备地址：

```bash
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test --limit 20 \
  --profile-id waveshare_photopainter_73 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860 \
  --push-url http://<WAVESHARE-IP> --push-protocol photoframe_api --push-now
```

`<WAVESHARE-IP>` 必须由串口日志中的 `sta ip`、设备网页或路由器租约确认后替换；脚本不扫描局域网。该脚本的上传、三模型索引、播放列表和主动推送队列均为单线程；报告写入 `shared/reports/`。具体 direct push、URL Rotation、Demo `/dataUP` 和证据边界见 [03](./03-album-server-api-and-esp32-protocol.md)、[04](./04-photopainter-7in3-integration.md)、[11](./11-photoframe-active-push.md) 和 [13](./13-photopainter-serial-ip-and-wifi.md)。

报告只记录实际执行结果。模型转换、ACL 数值、检索准确率、性能、API、UI 和实屏刷新不得相互替代。

历史运行基线（发布 `case7-photoframe-20260823v`）另存为：

```text
shared/reports/system-status-20260823-201237.txt
shared/reports/unittest-board-20260823-201255.txt   # 63 tests, OK
shared/reports/model-check-board-20260823-201742.txt # check requires onnx
```

2026-08-27 历史板 `192.168.8.180` 的证据文件：

```text
shared/reports/system-status-20260827-101214.txt
shared/reports/model_pipeline/acl_numerical_validation.json
shared/reports/model_pipeline/onnx_check.json
shared/reports/model_pipeline/atc_conversion.json
```
