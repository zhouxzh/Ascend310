---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 8：实验异常解析"
---

<!-- _class: cover -->

# 附录 8：实验异常解析

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从现象、直接故障到可能原因

对应教材：`src/appendix/appendix8.md`

---

<!-- _class: center -->

## 本讲要回答的问题

- 哪些异常已在实验中实际出现？
- 已确认的故障环节与未定位的原因各是什么？
- 哪些对照能排除错误归因？
- OM 能跨板运行时，如何检查计算结果？

<div class="source">教材：<a href="../appendix/appendix8.md"><code>src/appendix/appendix8.md</code></a></div>

---

## 六类实验异常

| 实验路径 | 实测异常 |
| --- | --- |
| 系统启动 | `systemd` 段错误、SSH 变慢 |
| Violin ONNX 转 OM | ATC 的 TBE/CannKB 初始化失败 |
| PolyU-B 评测 | 进程退出码 `139` |
| H.264/H.265 编码 | VENC 建通道返回 `507018` |
| 板载音频 | ALSA 硬锁、整板 `OS_PANIC` |
| YOLOv10n、YOLO26n ONNX 转 OM | mixed FP16 OM 推理输出波动 |

不同实验路径的失败分别分析，不能合并成单一板卡故障。

---

## 已确认的故障环节

- Violin：旧环境中 ATC 初始化或预编译失败；已编译 OM 仍可推理。
- PolyU-B：进程以 `139` 退出，尚无崩溃函数的堆栈。
- VENC：建通道返回 `507018`，VPC/JPEG 通过。
- 音频：ALSA 锁路径 hard lockup，随后 `OS_PANIC`。

<div class="source">案例原始记录：<code>samples/case3/docs/</code>、<code>samples/case4/docs/</code>、<code>samples/case9/docs/</code></div>

---

## 尚未定位的原因与检验

| 假设 | 能区分它的证据 |
| --- | --- |
| 旧镜像/文件系统触发 `systemd` 崩溃 | 清洁镜像与存储、内核错误对照 |
| 环境或资源使 CannKB 子进程退出 | 第一条错误、子进程退出和资源快照 |
| 评测在 ACL 清理阶段段错误 | 最后成功调用和原生堆栈 |
| VENC 属性或配套环境有问题 | 无摄像头单通道测试和设备日志 |
| 音频驱动/并发导致锁无法释放 | 厂商黑匣子和受控音频 A/B |

这些是待验证的解释，不是已确认根因。

---

## 20T 与 8T 的检测模型对照

| 固定输入的比较 | 结果 |
| --- | --- |
| 17 个现存 OM 组合的跨板 ACL 执行 | 全部通过 |
| 其中 15 个组合的两种来源 OM | 两板及重复捕获逐位相同 |
| YOLOv10n、YOLO26n mixed FP16 | 8T 稳定；20T 本板与外板 OM 都波动 |
| COCO NanoDet-Plus-M FP32 | 缺少 OM，未测试 |

可加载、重复稳定、对 ONNX 数值正确、真实图像质量，是四种不同结果。

---

## 两板都失败的外设对照

- SN0002 摄像头麦克风在 20T、8T 上都只采集到 22 个样本便失败。
- 8T 上另一款 UGREEN 摄像头可完成 16 kHz 音频采集。
- 两板复现同一外设失败，削弱了“20T 独有 NPU 故障”的解释。
- UAC 固件/描述符、USB 音频时钟和驱动配套仍需分别验证。

<div class="source">详细记录：<code>samples/case9/docs/09-troubleshooting.md</code></div>

---

## YOLO mixed FP16 OM 在 20T 推理不稳定

- 两个 YOLO 的 mixed FP16 ONNX 转 OM 成功，ACL 执行成功。
- 20T 上相同输入重复推理时输出波动；8T 上稳定。
- YOLOv10n：第一处注意力 `MatMul` 路径附近。
- YOLO26n：`/model.14/Resize` 路径附近。
- 单算子小图稳定；等价改写的 OM 重复稳定，但仍未过 ONNX 数值门限。

可能涉及整图融合、缓冲区或执行时序；尚未证明某个独立算子内核有缺陷。

<div class="source">详细协议：<code>samples/case4/docs/08-detector-onnx-om-consolidated-20261113.md</code></div>

---

## 怎样检验一个原因

1. 固定 SoC、系统/内核、驱动/固件、CANN、环境、模型和输入。
2. 保存完整命令、退出码、设备日志与原始输出。
3. 先同板重复，再做精度及跨板对照。
4. 分开写转换、运行、数值、质量和外设结果。
5. 为每个可能原因写出预期信号和能推翻它的对照。

出现退出码 `139`、设备 reset 或整板重启，应停止当前批次并保留现场。

<div class="source">教材：<a href="../appendix/appendix8.md"><code>附录 8</code></a></div>
