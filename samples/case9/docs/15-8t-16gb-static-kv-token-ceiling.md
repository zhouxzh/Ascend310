# 16GB 8T B4静态KV Token上限

## 测试范围

本记录独立于20T B1测试活动。它在由操作员连接、NPU上报内存为15,610 MB的Orange Pi AI Pro 8T上测量本地B4 fixed-KV候选。

该板没有仓库统一的`ascend` Python 3.9.25环境。因此，实验使用现有的`case9-acl-om`环境，其中包含Python 3.9.16、NumPy 1.26.4、tokenizers 0.19.1、CANN 8.0.0，并禁用user-site。启动器报告`EXPERIMENTAL_LEGACY_ENV`。结果属于探索性结果，不继承正式8T发布或20T活动的环境证据。

| 项目 | 实测值 |
| --- | --- |
| 板卡 | Ascend310B4 / 8T |
| NPU上报内存 | 15,610 MB |
| CANN/驱动 | 8.0.0 / 25.2.0 |
| Python环境 | `case9-acl-om`，Python 3.9.16，`PYTHONNOUSERSITE=1` |
| 模型 | Qwen2.5 0.5B fixed-KV，1024 Token上下文 |
| B4 OM | 1,266,010,586字节，`f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| Controller契约 | `fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498` |
| Runtime契约 | `923255c717165a2529e15563f2acd5cb1e31b67769e2a4afa4d3233053e310b6` |
| 执行路径 | 端口8086上的隔离回环worker；屏蔽EOS；禁用句子停止 |

## 强制解码结果

报告同时采集来自`/proc/meminfo`的Linux系统RAM和来自`npu-smi info`的NPU内存。每个级别使用全新的worker和全新的48张量拆分KV缓存。只有当实际完成量等于请求级别、结束原因为`finish_reason=length`、boot ID不变且请求后worker健康状态无异常时，该级别才算通过。

| 请求Token数 | 实际完成Token数 | 结束原因 | 耗时（秒） | 系统RAM前/后已用 | NPU内存前/后 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 80 | 80 | `length` | 223.195 | 3334 / 3367 MB | 6906 / 6946 MB | observed-pass |
| 128 | 128 | `length` | 519.934 | 3334 / 3368 MB | 6917 / 6952 MB | observed-pass |
| 256 | 256 | `length` | 1969.424 | 3331 / 3367 MB | 6916 / 6955 MB | observed-pass |
| 512 | N/A | N/A | 观察到超过4,682秒 | 运行中RAM已用3393 MB | 初始6924/取消时6991 | 操作员在78:02后取消；无最终结果 |

80 Token和128 Token报告在各自请求前后均使用boot ID `1271d365-4364-4aad-8eb2-7c3280fecdf3`。worker健康状态保持ready and healthy；watchdog、restart-required和runtime-error字段均无异常。两个已完成级别结束后，端口8086均已释放。

```text
run/reports/8t-b4-16gb-force-continue-80-20261031T191500Z/results.json
run/reports/8t-b4-16gb-force-continue-128-20261031T193000Z/results.json
run/reports/8t-b4-16gb-force-continue-256-20261031T200000Z/results.json
```

256 Token worker在1969.424秒（约32.8分钟）后完成。它准确返回256个完成Token，结束原因为`finish_reason=length`；boot ID未变，请求后健康状态无异常，端口8086已释放。第一次512 Token启动尝试在SSH超时前没有创建目录或进程。确认没有worker或端口残留后，以PID 10501启动了一次512 Token实验。其初始健康检查确认了force模式、`max_tokens=512`、B4 SoC和实验性legacy环境策略。它在78分2秒后仍保持健康，但尚未返回结果，因此作为低价值的多小时实验被操作员取消。取消时`MemTotal - MemAvailable`为3393 MB，NPU内存为6991 MB。TERM释放了端口8086。连接恢复后，没有runner或worker PID残留；`free -m`报告已用1271 MB、可用14029 MB，同时NPU记账已降至5179 MB。

## 内存解读

NPU数值是OM加载期间设备记账的内存。系统RAM数值是worker活跃期间的`MemTotal - MemAvailable`。它与`free`命令`used`列不是同一指标。128 Token worker释放后，`free -m`报告已用1,271 MB、可用14,030 MB，而`npu-smi`报告的设备记账值仍高于空闲快照。这种保留记账仅作为观察结果记录；由于该设备无法进行进程级NPU内存查询，不能将其归因于仍在运行的worker。

## 复现边界

本次本地诊断的源代码由controller同步而来。B4模型已经存在于板上，并在完成字节数和SHA-256核验前，以板端本地复制方式放入独立的候选目录。这不是一次干净的GitHub/Hugging Face发布复现。

runner必须使用显式的legacy标签：

```bash
ASCEND_EDGE_CHAT_TOKEN_CEILING_CONDA_ENV=case9-acl-om \
ASCEND_EDGE_CHAT_TOKEN_CEILING_ALLOW_LEGACY_ENVIRONMENT=1 \
ASCEND_EDGE_CHAT_TOKEN_CEILING_FORCE_CONTINUE=1 \
ASCEND_EDGE_CHAT_TOKEN_CEILING_CANDIDATE_ID=qwen2.5-0.5b-instruct-static-kv-1024-b4-local \
ASCEND_EDGE_CHAT_TOKEN_CEILING_CATALOG="$PWD/configs/8t-backend-catalog.json" \
ASCEND_EDGE_CHAT_TOKEN_CEILING_REPORT_KIND=8t-b4-16gb-static-kv-force-ceiling \
bash scripts/verify/run-20t-b1-static-kv-token-ceiling.sh --levels 128
```

## 剩余验收门槛

- 在解码路径优化前，不要恢复512/768；当前观测到的强制运行时上限为256 Token。
- 不要将8T延迟或内存数值与20T报告合并。
- 在将结果视为发布证据前，应从公开固定revision和统一的Python 3.9.25环境重新运行。
