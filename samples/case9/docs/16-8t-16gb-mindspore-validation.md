# 16GB 8T MindSpore验证

## 范围

本记录检验16GB Orange Pi AI Pro 8T能否运行20T路径所用的同一个Case9 MindSpore Qwen1.5-0.5B worker。它不继承旧8GB板卡的失败结果，也不使用CPU回退。通过要求包括实际的Ascend上下文、模型加载、一次生成响应、回环JSON/SSE请求以及内存证据。

## 板卡与软件

| 项目 | 观测值 |
| --- | --- |
| SoC/档位 | Ascend310B4 / 8T |
| NPU上报内存 | 15,610 MB |
| CANN/驱动 | 8.0.0 / 25.2.0 |
| 操作系统 | Ubuntu 22.04.5 LTS，kernel 5.10.0+ |
| 统一环境 | `ascend` |
| Python | 3.9.25，`cpython-39` |
| User-site策略 | `PYTHONNOUSERSITE=1`，`site.ENABLE_USER_SITE=false` |
| MindSpore/MindNLP | 2.4.10 / 0.4.1 |
| 上下文门 | `device_target=Ascend`、`device_id=0`通过 |

板卡最初没有`ascend`环境。新环境从板上的Python 3.9.25 MindSpore Lite环境克隆，然后用已安装的base和user-site包闭包填充。worker进程不再读取user-site包。在确认导入的包本身和保留的元数据都报告MindSpore 2.4.10后，删除了过期的`mindspore-2.4.0.dist-info`目录。现有环境未被删除。

## 模型产物

测试使用与当前Case9路径相同的Qwen1.5-0.5B产物和worker实现。文件以板端本地复制方式放入独立的数据根目录，并依据当前产物锁完成核验。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `model.safetensors` | 1,239,173,352 | `72453a8ccb338811935ab95a3a6ffa86b586807bf5b3dc327f28b5389b5636e6` |
| `tokenizer.json` | 7,028,015 | `f7c9b2dba4a296b1aa76c16a34b8225c0c118978400d4bb66f0902d702f5b8` |
| `config.json` | 661 | `adac3bb8bf1b15c74312a2835e3796f6b352d817ff726960e1d5c2cd171da951` |

包含tokenizer元数据、词表、merges和generation config的完整7产物门在worker启动前通过。

## 尝试

### 缺少运行时锁文件

第一次worker尝试在导入或加载模型前停止，因为隔离代码检出目录中不存在`configs/release-artifacts.lock.json`。随后同步了当前运行时锁文件。这是harness/配置失败，不是硬件或模型结果。

### 受污染的NPU内存基线

第二次worker尝试通过了Python、产物、profile和导入门，但在HBM预检处停止，未加载模型：

```text
code: hbm_accounted_headroom_insufficient
SoC: Ascend310B4 / 8T
accounted free: 653 MB
required free: 8192 MB
```

在最近一次检查时，没有Case9 worker、PyACL候选或端口8086/8090进程在运行，但`npu-smi`仍报告已用13,982 / 15,610 MB。板卡运行时长起点仍为`2026-10-31 17:51:21`，表明所请求的干净重启并未发生。worker在加载权重前正确执行了故障关闭。

## BF16兼容适配器

已核验检查点包含291个BF16张量。在这块8T镜像上，已安装的MindNLP 0.4.1加载器将`ml_dtypes.bfloat16`的NumPy itemsize报告为8，而不是safetensors要求的2字节。20T镜像中相同版本标签包含较新的加载器实现。统一Case9 worker现在能够检测这一特定的宽度不匹配，并在程序中将以小端序存储的BF16位解码为float16。它不修改系统Python，也不替换8T包环境；健康响应记录`bf16_loader_compat=true`。BF16宽度有效的20T加载器不会进入该分支。

## 已观测验收

干净重启始于`2026-10-31 22:23:36`。worker使用相同的`ascend_edge_chat`实现和隔离B4实验profile。

| 验收门 | 证据 |
| --- | --- |
| 环境初始化 | observed-pass：Python 3.9.25、MindSpore 2.4.10、MindNLP 0.4.1、user-site已禁用 |
| 干净HBM预检 | observed-pass：加载前已用3727 / 15610 MB；记账可用9556 MB，最低要求8192 MB |
| 模型加载 | observed-pass：worker约57秒后就绪 |
| 资源放置 | `context_verified`：MindSpore Ascend上下文以及`npu-smi`的Ascend310B4证据；未暴露CPU/GPU放置 |
| BF16加载器 | observed-pass：兼容分支已启用 |
| 非流式生成 | observed-pass：8个完成Token，`finish_reason=length`，12秒 |
| SSE生成 | observed-pass：8个完成Token，增量分块及`[DONE]`，5秒 |
| 生成后健康状态 | observed-pass：ready/healthy，设备目标Ascend，B4模型 |
| API | observed-pass：`GET /v1/models`、JSON `/v1/chat/completions`和SSE端点 |

同一次运行的内存证据：

| 时点 | 系统RAM已用/可用 | NPU内存 |
| --- | --- | --- |
| 加载前 | 已用1297 MB/可用14007 MB | 3727 / 15610 MB |
| 加载后 | 已用8728 MB/可用6568 MB | 12703 / 15610 MB |
| worker停止前 | 已用10062 MB/可用5219 MB | 14144 / 15610 MB |
| worker停止后 | 已用1288 MB/可用14008 MB | 5355 / 15610 MB |

NPU的`Health: Alarm`状态已被记录，但不妨碍这一受测组合加载或生成。旧8GB板卡的失败仍是独立结果，不得推广到这块16GB板卡。

## 复现证据

```text
/home/HwHiAiUser/Documents/ascend-edge-chat-ms-8t/reports/8t16-mindspore-context-placement-20261031T223800/
```

该目录包含boot ID、预检和加载后健康JSON、非流式JSON响应、SSE响应、worker日志、产物哈希以及系统/NPU内存快照。冒烟测试后，worker和端口8090均已停止并释放。

## MindSpore输出Token上限

在单请求/API冒烟测试后，同一个worker使用8T实验profile重启，并将最大请求预算提高到256。包含55个输入Token的固定提示按顺序请求每个级别。每个级别都返回HTTP 200、准确的请求完成量和`finish_reason=length`。没有任何级别因EOS、超时、worker健康故障或内存错误而停止。

| 请求Token数 | 实际完成Token数 | 耗时（秒） | 结果 |
| ---: | ---: | ---: | --- |
| 8 | 8 | 12.519 | observed-pass |
| 16 | 16 | 9.404 | observed-pass |
| 32 | 32 | 18.538 | observed-pass |
| 64 | 64 | 36.973 | observed-pass |
| 128 | 128 | 74.100 | observed-pass |
| 192 | 192 | 111.660 | observed-pass |
| 256 | 256 | 149.807 | observed-pass |

在256 Token级别，系统RAM已用量从10452 MB变为10509 MB，NPU记账从14545 / 15610 MB变为14603 / 15610 MB。worker仍是同一个经验证的MindSpore/BF16适配器进程。这表明该16GB 8T板上的MindSpore输出上限至少已观测到256 Token；它并不声称256是模型或上下文的硬性上限。对于本次手动教学运行，浏览器模型预算现为256。

```text
/home/HwHiAiUser/Documents/ascend-edge-chat-ms-8t/reports/8t16-mindspore-token-ceiling-20261031/
```
