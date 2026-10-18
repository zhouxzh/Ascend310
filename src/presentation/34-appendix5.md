---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 5：ROS2 基础"
---

<!-- _class: cover -->

# 附录 5：ROS2 基础

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从节点通信到可复核的机器人链路


对应教材：`src/appendix/appendix5.md`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 理解节点、话题、服务与动作
- 创建工作空间与功能包
- 使用命令行、rqt、rviz2 与 bag
- 说明 ROS2 与 310B 推理的边界
- 结论必须绑定版本与运行环境。

> 本讲主线：通信模型 → 工作空间 → 工具 → 设备 → 边界

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 通信模型与工作空间
---

<!-- _class: center -->

## 定位、DDS、去中心化发现与教材边界

- ROS2 是一个开源的机器人软件框架，用于把机械结构、嵌入式控制、传感器驱动与上层算法组织成可复用的软件模块。教程把其生态划分为四个部分：
- 通信（Plumbing）：在节点之间传递消息的中间件，是整个框架的核心实现。
- 工具（Tools）：launch、调试、可视化、绘图、数据记录与回放等开发工具。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 1. 定位、DDS、去中心化发现与教材边界</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 工作空间、功能包与构建系统

- 教程给出的 ROS2 工作空间结构如下：
- --packages-select 只构建指定的一个或多个功能包，不处理其他包的依赖关系，适合功能包改动后的快速迭代。
- package.xml 描述包名、版本、描述、维护者以及构建与运行依赖。下列内容取自教程的 Python 功能包示例：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 2. 工作空间、功能包与构建系统</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 节点、通信模型、接口与计算图

- 话题（Topic）把使用同名、同类型消息的节点关联起来，构成通信的基础。话题的发布方与订阅方是多对多关系，适合持续更新、逻辑处理较少的数据传输场景，例如传感器数据流。
- 这个例子也说明了接口的排障意义。话题名相同但消息类型不同，两个节点不会成功通信；反过来，只要接口类型一致，发布方与订阅方可以用不同编程语言实现。
- 把运行中的节点、话题、服务、动作与参数作为整体观察，就得到系统的计算图。计算图是排障的主要视角：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 3. 节点、通信模型、接口与计算图</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 命令行工具

- 服务列表与类型查询用于确认服务端点是否存在、接口是否符合预期。list 输出运行中的服务，find 按服务类型查找，type 输出指定服务使用的接口类型。
- ros2 service call 向服务发送请求，参数为服务名、接口类型与请求内容。教程用海龟仿真器的 /spawn 服务演示了这种调用方式，请求字段采用 YAML 形式的字典。
- dump 把节点参数写入文件，load 从文件把参数加载到节点。这一对命令常用于保存可复现的实验配置，把当前参数集作为记录的一部分留存。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 4. 命令行工具</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## rqt、rviz2 与 ros2 bag

- 两种方式都可以启动 rqt。rqt 是基于 Qt 的图形界面框架，以插件形式提供节点图、话题监视、图像查看与 TF 树等工具。启动后通过 plugins 菜单添加所需插件。
- 教程列出的四个常用插件与用途：
- rviz2 是 ROS2 的数据可视化工具，用于把抽象数据以图形方式呈现。教程中的典型用法包括：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 5. rqt、rviz2 与 ros2 bag</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 设备、多机与 310B 边界
---

<!-- _class: center -->

## TF2 坐标变换与 URDF

- 移动机器人上的传感器与执行器分布在不同位置，测量数据必须转换到统一坐标系才能组合使用。TF2 提供管理坐标系相对关系与查询变换的工具。
- 两个坐标系相对位置固定时使用静态变换，例如雷达与 base_link 之间的安装位置。教程给出的命令行用法：
- 子主题：7.1 TF2 的作用

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 7. TF2 坐标变换与 URDF</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 相机、雷达、IMU 与串口底盘接口

- 本节整理厂商教程中出现的话题与接口。这些接口属于 WHEELTEC 平台的 documented 配置，本教材没有在昇腾 310B 上运行过这些节点。
- 电池电压、超声波与自动回充的其余话题在文档中以功能描述出现而未给出完整话题名，使用时以 ros2 topic list 的实际输出为准。
- 雷达驱动的统一输出接口是 sensor_msgs/msg/LaserScan。厂商教程给出的确认流程是：先启动雷达，再读取话题，最后用 rviz2 可视化。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 8. 相机、雷达、IMU 与串口底盘接口</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 多机通信与 ROS_DOMAIN_ID

- 修改域号或网络配置后，先停止再启动 daemon 可以避免旧发现信息残留。daemon 只服务于命令行工具的查询，不参与节点之间的业务通信。
- 多机通信通常要求主机与机器人在同一子网，且网络允许组播。跨网段、NAT、禁用组播的无线网络都会影响发现过程。
- ip addr 查看本机地址与子网掩码，ip route 查看路由，ping 确认基础可达性。三者的组合可以判断两台机器是否真正处于同一网段，而不只是“看起来在同一个网络里”。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 10. 多机通信与 ROS_DOMAIN_ID</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## ROS2 与 310B 推理集成（proposed）

- 集成目标可以用一条数据流概括：
- 以下每一条都是未验证前提，必须在板端逐项确认：
- 板端系统发行版与所选 ROS2 发行版的官方支持列表匹配，且 Python 版本与运行时依赖不冲突。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 12. ROS2 与 310B 推理集成（proposed）</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 课堂练习与验收标准

- 话题通信：实现发布方与订阅方节点，用 ros2 run 分别启动，用 ros2 topic echo 观察消息内容，并记录 ros2 topic info 的发布方与订阅方数量。
- 服务与动作：用海龟仿真器练习 ros2 service call 与 ros2 action send_goal，说明两者在调用形式与反馈方式上的差别。
- 参数：读取并修改一个节点的参数，用 ros2 param dump 保存参数文件，再用 ros2 param load 恢复，比较前后状态。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a> · 13. 课堂练习与验收标准</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- ROS2 的通信模型决定系统结构
- 工作空间与包是构建的最小单位
- 310B 推理集成仍属 proposed 边界
- 先证明通信与工具链，再讨论 310B 集成。
- 未验证项必须标为 untested 或 blocked。

> 下一讲：进入工具箱与 FAQ。

<div class="source">教材：<a href="../appendix/appendix5.md"><code>src/appendix/appendix5.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
