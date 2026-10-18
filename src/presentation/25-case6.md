---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "案例 6：智能小车"
---

<!-- _class: cover -->

# 案例 6：智能小车

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从底盘控制到传感器闭环


对应教材：`src/experiment/case6.md`

配套样例：`samples/case6`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 说明智能小车的硬件与软件边界
- 走通车道线检测与场景分类两条模型链
- 完成 ONNX 到 OM 的部署与 Web 控制界面
- 用板端记录验证系统是否工作

> 本讲主线：硬件 → 模型 → 部署 → 界面 → 验证

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 系统与模型
---

<!-- _class: center -->

## 项目简介

- 本项目基于昇腾 310B 平台，构建一个智能小车的视觉感知系统。系统结合
- 经典计算机视觉和深度学习两种方法：使用 OpenCV 的 Canny 边缘
- 检测 + Hough 变换进行车道线检测，使用 ResNet18 卷积神经网络在 NPU 上

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 1. 项目简介</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 硬件准备

- 昇腾 310B 开发者套件：核心计算单元，运行场景分类模型
- USB 摄像头（可选）：用于实时画面采集，也可以用测试图片
- 显示屏（可选）：用于浏览器访问 Gradio 界面

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 2.1. 硬件准备</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 软件环境

- 操作系统：Ubuntu，按教材要求准备板端环境。
- CANN：Ascend Toolkit，版本必须与驱动、固件配套。
- Python 与模型转换工具：按样例依赖安装并固定版本。
- 依赖完成后先在统一 shell 中确认 Python、CANN 与转换工具可用。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 2.2. 软件环境</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 车道线检测原理

- 车道线检测是一个经典的计算机视觉问题。车道线本质上是图像中的直线
- 或平滑曲线，具有明显的边缘特征和固定的几何约束（位于画面下半部
- 分，大致呈八字形）。这些特征使得经典 CV 方法——Canny 边缘检测 +

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 2.3. 车道线检测原理</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 驾驶场景分类

- 车道线检测告诉小车"路在哪里"，但还需要知道"是什么路"——高速公路还是
- 城市街道？前方是否有交叉口？这需要语义层面的理解。
- 本项目使用 ResNet18 对 5 种典型驾驶场景进行分类：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 2.4. 驾驶场景分类</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 部署、界面与验证
---

<!-- _class: center -->

## 模型转换与昇腾部署

- #### 步骤 1：导出 ONNX
- 这一步构建 ResNet18 模型，将原本 1000 类的 ImageNet 分类头替换为 5 类
- 驾驶场景分类头，然后用固定输入形状 (1, 3, 224, 224) 导出 ONNX。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 2.5. 模型转换与昇腾部署</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## Web 界面

- 道路感知：上传道路图像（或使用示例图片），系统同时执行车道线检测
- 和场景分类。左侧显示输入图像，右侧显示感知报告（车道检测状态、场景
- 标签、置信度进度条、Top-3 预测、驾驶建议），下方显示车道线叠加效果图。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 2.6. Web 界面</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 板端实测记录

- 本节记录 8T 与 20T 两块开发板上的启动与页面结果。原始日志与截图保存在 docs/case-board-runs-20261017/case6/。
- 证据等级：Gradio 启动链路与页面渲染为 observed-pass。
- 本轮未验证：未上传道路图像，车道线检测与场景分类推理均为 untested。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 板端实测记录</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 如何验证系统正常工作

- 打开浏览器访问[本地界面](http://127.0.0.1:7860)
- 切换到「道路感知」页签
- 上传一张包含清晰车道线的道路图像

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a> · 如何验证系统正常工作</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- 硬件、模型和界面分属不同证据层
- 车道线检测与场景分类不能互相替代
- 部署成功不等于驾驶功能已经验收
- 未验证项必须标为 untested 或 blocked。

> 下一讲：用同一方法补齐案例 7 的局域网协同链路。

<div class="source">教材：<a href="../experiment/case6.md"><code>src/experiment/case6.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
