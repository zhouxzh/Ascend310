---
marp: true
theme: ascend310
paginate: true
size: 16:9
header: "《昇腾310B实战》教材配套演示"
footer: "课程总览"
---

<!-- _class: cover -->

# 《昇腾310B实战》课程导览

## 十章理论、九个案例、七个附录

演示文稿按教材结构拆分。每个理论章节、实践案例和附录都有独立放映文件，旧版专题统一进入附录归档。

<div class="source">来源：`src/book/README.md`、`src/experiment/README.md`、`src/appendix/README.md`；课件清单：`src/presentation/catalog.mjs`</div>

<!--
授课提示：先说明本课程不是命令清单，而是“原理、实现、证据”三条线同步推进。
-->

---

## 课程按三条线组织

| 学习线 | 主要内容 | 完成标志 |
|---|---|---|
| 理论 | CANN、PyTorch、PyACL、MindSpore、DVPP、算子、性能、量化、交付 | 能解释组件边界 |
| 实践 | 九个完整案例，从视觉到音频、信号、机器人和大模型 | 能在板端跑通一条链路 |
| 证据 | 源文件、命令、截图、日志、版本、失败边界 | 能说明结果证明了什么 |

<div class="source">来源：`src/book/`、`src/experiment/`、`samples/`</div>

<!--
授课提示：强调“跑通”不等于“验收通过”；实验结果要绑定硬件和软件版本。
-->

---

## 十章理论形成一条能力链

| 阶段 | 章节 | 能力 |
|---|---|---|
| 入门 | 第1章、第2章 | 认识310B，完成ONNX到OM |
| 框架 | 第3章、第4章、第5章 | PyTorch、PyACL、MindSpore开发 |
| 媒体与算子 | 第6章、第7章 | DVPP处理与自定义算子 |
| 工程化 | 第8章、第9章、第10章 | 性能、量化与项目交付 |

<div class="source">来源：`src/book/chapter1.md` 至 `src/book/chapter10.md`</div>

<!--
授课提示：让学生先记住四段能力，不要一上来背十个章名。
-->

---

## 第1章到第5章的教学重点

| 章节 | 课堂主线 | 代表性产出 |
|---|---|---|
| 第1章 | 边缘场景、生态与310B规格 | 硬件选型说明 |
| 第2章 | CANN、ATC与msame | OM转换和推理记录 |
| 第3章 | torch_npu环境与网络迁移 | NPU训练或推理结果 |
| 第4章 | Device、Context、Stream与内存 | PyACL推理程序 |
| 第5章 | Tensor、Cell、静态图与MindIR | 可交付模型文件 |

<div class="source">教材：`src/book/chapter1.md` 至 `src/book/chapter5.md`</div>

<!--
授课提示：点出每章都可以落到一个可检查的产物，避免理论课与实验课脱节。
-->

---

## 第6章到第10章的教学重点

| 章节 | 课堂主线 | 代表性产出 |
|---|---|---|
| 第6章 | VENC、VDEC、VPC与JPEG | 媒体处理程序和性能记录 |
| 第7章 | TBE DSL、Tiling与自定义算子 | 算子工程与验证日志 |
| 第8章 | Profiling与流水线优化 | 可复现的基线报告 |
| 第9章 | PTQ、QAT与精度对齐 | 量化对比报告 |
| 第10章 | 指标、评测、交付和回归 | 项目验收包 |

<div class="source">教材：`src/book/chapter6.md` 至 `src/book/chapter10.md`</div>

<!--
授课提示：第8章到第10章必须带同一套报告口径，性能数字不能离开测试协议。
-->

---

## 九个案例覆盖不同的系统形态

| 案例 | 任务 | 关键系统能力 |
|---|---|---|
| 1 | 智能考勤机 | 人脸检测、特征比对、FastAPI与React |
| 2 | 目标跟踪检测 | 检测、DeepSORT、卡尔曼滤波与匹配 |
| 3 | 智能电子琴 | 多模型DDSP、音频设备与Web工作台 |
| 4 | 掌纹识别 | NPU-only服务、候选准入与评测界面 |
| 5 | 智能数据采集分析仪 | 连续采集、DFT、时频检测与追溯 |

<div class="source">教材：`src/experiment/case1.md` 至 `src/experiment/case5.md`；实现：`samples/case1` 至 `samples/case5`</div>

<!--
授课提示：强调第5章是不同算法、不同硬件接口和多条证据链的组合。
-->

---

## 另外四个案例把系统推进到设备与网络

| 案例 | 任务 | 关键系统能力 |
|---|---|---|
| 6 | 智能小车 | 视觉推理、执行器控制与Web驾驶界面 |
| 7 | 智能相册 | 多模型检索、触摸屏与电子墨水输出 |
| 8 | 实时手势识别 | YOLOv10、摄像头、OM与WebRTC |
| 9 | 智能聊天机器人 | 模型工件锁、OpenAI兼容网关与SSE |

第6章到第10章的方法分别进入这些案例：媒体处理、性能、量化、交付与回归。

<div class="source">教材：`src/experiment/case6.md` 至 `src/experiment/case9.md`；实现：`samples/case6` 至 `samples/case9`</div>

<!--
授课提示：讲清案例不是章节的重复，而是把多章能力装进同一个运行系统。
-->

---

## 附录承担基础、工具和补充知识

| 附录 | 内容 |
|---|---|
| 附录 1 | 开发板、系统、启动、网络与基础环境 |
| 附录 2 | Ubuntu、SSH、VNC、NFS、Docker与板端操作 |
| 附录 3 | Python标准库、CSV、函数与异常 |
| 附录 4 | Vibe Coding、DSH使用与代码审查 |
| 附录 5 | ROS2通信、TF2、URDF与远程操作 |
| 附录 6 | 工具箱、FAQ、参数模板与术语 |
| 附录 7 | CANN、驱动、固件升级与版本验收 |

<div class="source">来源：`src/appendix/appendix1.md` 至 `src/appendix/appendix7.md`</div>

<!--
授课提示：附录不是选读材料的堆放区；每份附录都对应课程中的一个缺口。
-->

---

## 旧版内容只保留在附录归档

旧版 10 份专题课件不再承担新版章节主线：

- 与新章节标题、案例边界或界面不一致的页面
- 只记录旧部署路径或旧模型状态的内容
- 课程结构调整后失去上下文的独立专题

旧版原文仍可追溯，但课堂入口统一放在“附录归档：旧版专题课件”。

<div class="source">归档索引：`src/presentation/89-legacy-archive.md`；旧版源文件保留在 `src/presentation/`</div>

<!--
授课提示：这里明确版本关系，避免学生把旧课件的命令和新版正文混用。
-->

---

## 每份课件都必须能追溯

| 证据 | 课件中的写法 |
|---|---|
| 教材 | 标明 `src/book/`、`src/experiment/` 或 `src/appendix/` 路径 |
| 代码 | 标明 `samples/` 下的入口文件 |
| 界面 | 标明页面、开发板档位、视口与隐私场景 |
| 运行 | 标明确切命令、退出状态、日志与生成物 |
| 性能 | 标明模型、精度、warmup、循环数、重复次数和统计方法 |

没有真实截图或板端结果时，课件写“待采集”或“untested”，不拿示意图替代证据。

<div class="source">规范：`AGENTS.md` 的“Evidence-driven hypothesis and empirical validation”和“Verification gates”</div>

<!--
授课提示：这一页是整门课的实验纪律，后续每个案例都按同一口径检查。
-->

---

## 截图按双板证据集管理

| 字段 | 示例含义 |
|---|---|
| 开发板 | 8T / Ascend310B4 或 20T / Ascend310B1 |
| 页面 | URL、页面名称、操作步骤 |
| 视口 | 统一桌面或移动尺寸，不使用随机缩放 |
| 数据 | 合成、匿名或公开测试数据 |
| 文件 | 文件名、SHA-256、采集时间 |
| 边界 | 截图证明了什么，不证明什么 |

两块板的结果分开保存。性能排名、模型准入和截图证据都不能跨档位自动继承。

<div class="source">截图清单：`src/presentation/screenshots/capture-manifest.json`；采集脚本：`scripts/capture_presentation_ui.mjs`</div>

<!--
授课提示：在第一次截图前先确定数据隐私和板卡档位，事后无法从图片里补回这些信息。
-->

---

## 课堂使用顺序

1. 先读对应教材正文，明确本讲要解决的问题。
2. 打开同名课件，按教学主线推进，不把课件当教材全文。
3. 在 `samples/` 找到命令与代码，只在目标开发板上执行硬件检查。
4. 保存截图、日志和命令输出，并在下一页核对证据边界。
5. 离开课堂前完成一次复述：输入、处理、输出、失败点。

<div class="source">全书入口：`README.md`；样例入口：`samples/README.md`；写作与验证规则：`AGENTS.md`</div>

<!--
授课提示：最后让学生用自己的话复述“输入、处理、输出、失败点”四格，检查是否真正理解。
-->
