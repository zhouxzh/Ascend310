---
marp: true
size: 16:9
theme: ascend310
paginate: true
header: "《昇腾310B实战》教材配套演示"
footer: "附录 6：ROS2 基础教程"
---
<!-- _class: cover -->

# 附录 6：ROS2 基础教程

面向昇腾310B 教材的机器人软件栈入门

专题安排：3 课时，每课时 45 分钟

| 课时 | 主题 | 完成后的能力 |
| --- | --- | --- |
| 第1课时 | 定位与工程组织 | 能说清 ROS2 的定位，并读懂工作空间与功能包 |
| 第2课时 | 通信与命令行 | 能用 CLI 观察节点、话题、服务、动作与参数 |
| 第3课时 | 集成与远程操作 | 能启动系统、查 TF/URDF，并说明远程联调边界 |

---

## 本专题回答的三个问题

本专题不追求把 ROS2 全部讲完，而是让读者能回答三类问题：

- ROS2 在一台机器人上解决什么问题，它和单一可执行程序有什么区别。
- 机器人数据从哪里来、经过哪些节点、最后被谁消费，怎样用命令行观察。
- 当传感器、底盘、坐标变换和远程主机同时出现时，怎样组织启动、怎样记录证据、哪些结论还不能下。

这三个问题会在后续机器人章节反复出现，尤其是把感知结果转成底盘运动指令的环节。

---

## 三课时路线

| 课时 | 45 分钟内安排 | 课堂产出 |
| --- | --- | --- |
| 第1课时 | ROS2 定位、DDS、去中心化发现、工作空间、功能包、colcon 与构建清单 | 一张功能包结构与依赖图 |
| 第2课时 | 节点、话题、服务、动作、参数、消息、计算图与 CLI 实操 | 一份命令行观察记录 |
| 第3课时 | launch、TF2、URDF、传感器/底盘话题、SSH/VNC、多机通信、自启动与集成路径 | 一份启动与联调报告 |

每课时的讲解、命令演示和课堂任务都在同一课时内完成，不把实操推到课后。

---

## 资料来源与证据边界

本专题使用的外部资料以轮趣科技 ROS2 系列教程为主：

- `ROS2入门教程.pdf`：ROS2 概述、体系框架、通信工具、通信机制、launch、TF2、URDF。
- `1.ROS2入门与编程开发教程` 下的编号子目录：功能包源码、`package.xml`、`CMakeLists.txt`、`setup.py` 与 launch 文件。
- `2.ROS2机器人应用视频教程：上手使用、雷达与2D导航`：仅用于话题与接口上下文，例如底盘、雷达与相机相关话题的组织方式。

这些资料描述的是第三方 ROS2 机器人平台与通用 ROS2 机制，不是昇腾310B 板级验证记录。本专题据此讲清概念与命令，不把外部平台的运行结果转写为昇腾310B 的结果。

---

## 第1课时（45分钟）：从 ROS2 定位开始

机器人系统同时包含机械结构、硬件、嵌入式软件和上层软件，各部分由不同的人、不同的语言、不同的时钟节奏实现。ROS2 在这些部件之间提供一套可复用、可观察、可组合的软件框架。

它继承 ROS 生态的功能包与工具，同时用新的架构设计满足现代机器人对实时性、安全性、标准性和可靠性的要求。学习 ROS2 的第一步是建立“谁和谁通信、通信什么、谁负责启动”的模型。

---

## ROS2 的特性与四层生态

ROS2 的设计目标与生态结构可以放在一起理解：

| 特性 | 对开发的意义 |
| --- | --- |
| 开源免费、多机器人系统 | 功能包可复用，并提供多机器人协同的标准机制 |
| 跨平台、实时性 | 可运行于 Linux、Windows、macOS、RTOS 甚至 MCU，面向实时控制 |
| 网络连接、产品化 | 面向多种网络环境，并可直接集成到消费级产品 |
| 项目管理 | 覆盖设计、开发、调试、测试到部署的工程流程 |

生态分为通信（Plumbing）、工具（Tools）、功能（Capabilities）与社区（Community）：通信负责节点间传数据，工具提供 launch、调试与可视化，功能提供可复用能力，社区提供资料与协作。

这些是框架的设计目标与能力范围，不等于某个具体平台在某一版本上全部达到，更不等于昇腾310B 已经验证过这些特性。

---

## ROS2 的设计重点

- **去中心化**：节点之间对等通信、相互发现。
- **DDS 通信底层**：直接采用 DDS，改善实时性、可靠性与连续性。
- **新的设计与工程方式**：随社区发展引入新的实现方式。
- **更广的应用场景**：面向多机器人编队、嵌入式平台、实时性、网络质量与产品化等问题。

后续命令、建包方式和调试手段都按 ROS2 规则进行。

---

## DDS 与去中心化发现

DDS 是 ROS2 采用的通信机制，位于节点与网络之间。节点通过 DDS 发布和订阅数据，中间件负责发现对端、匹配话题类型、传输消息并处理服务质量。

没有中心 master 节点带来三个推论：

- **发现是分布式的**。新节点加入后由中间件参与发现与匹配，而不是向中心注册。
- **没有单一故障点，但没有单一真相点**。节点列表来自发现结果，可能随网络与时间变化。
- **网络与域配置直接决定能否见面**。域不一致、网段不通或组播受限时，节点可以各自正常运行却互相看不见。

因此“本机能看到节点”不能推出“另一台机器也能看到节点”，必须单独验证。

---

## 工作空间的目录结构与环境加载

功能包不能单独构建，需要放在工作空间中。教程给出的结构是：

```text
WorkSpace/
|-- src/       功能包源码目录
|-- build/     中间文件，每个功能包一个子目录
|-- install/   安装目录，每个功能包一个子目录
|-- log/       构建与运行日志
```

只有 `src` 是人工维护的源码目录。工作空间是存放代码、参数和脚本的工程容器，也是后续构建与环境加载的基准目录。

每次使用前先加载环境：

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
```

第一行加载发行版环境，第二行把当前工作空间构建出的功能包加入可查找列表。顺序不能颠倒：先有发行版，再有本地工作空间覆盖。

---

## 创建并首次构建工作空间

从创建目录到完成第一次构建：

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws
colcon build
ros2 pkg list
```

`mkdir -p` 创建 `src` 目录，父目录不存在时一并创建；`cd` 进入工作空间根目录；`colcon build` 从工作空间根目录构建其中的功能包；`ros2 pkg list` 列出环境可见的功能包。

首次只放少量功能包再构建，便于快速定位错误。构建命令必须在工作空间根目录执行，而不是在单个功能包内执行。

把 `source` 写入 `~/.bashrc` 可以省去手动加载，但会改变后续所有终端的行为，属于需要记录的系统状态修改。

---

## 为什么使用功能包组织代码

机器人包含移动控制、视觉感知、自主导航等功能。功能包把不同功能的代码分开组织，降低耦合、便于维护，也便于把一个功能分享给他人复用。

一个工作空间可以包含多个功能包，每个功能包是一个独立的构建与应用单元，包含自己的依赖声明、构建配置和运行入口。

理解功能包的边界，等价于理解“谁能依赖谁”：依赖写错，构建就失败；入口写错，找到包也找不到可执行文件。

---

## 功能包结构与三份构建清单

C++ 功能包与 Python 功能包的目录组成不同：

```text
cpp_pubsub/
|-- package.xml       包信息、作者、依赖
|-- CMakeLists.txt    源文件、依赖、目标与安装规则
|-- src/ include/     源文件与头文件
|-- msg/ srv/ action/ 接口定义

py_pubsub/
|-- package.xml       包信息与依赖
|-- setup.py          源文件入口与资源安装规则（类似 CMakeLists.txt）
|-- setup.cfg
|-- resource/ test/
|-- py_pubsub/        Python 源码目录
```

`package.xml` 描述包本身与依赖；`CMakeLists.txt` 与 `setup.py` 分别描述 C++ 与 Python 包怎样构建、安装并暴露入口；`msg`、`srv`、`action` 存放自定义接口定义。

判断功能包是否完整，最直接的方法是看依赖声明、构建配置、源码与入口点是否齐全且一致。

---

## package.xml 与 setup.py

来自 `py_pubsub` 的真实清单（省略测试依赖）：

```xml
<package format="3">
  <name>py_pubsub</name>
  <version>0.0.0</version>
  <depend>rclpy</depend>
  <depend>std_msgs</depend>
  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

```python
setup(
    name='py_pubsub',
    packages=['py_pubsub'],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/py_pubsub']),
        ('share/py_pubsub', ['package.xml']),
    ],
    entry_points={
        'console_scripts': [
            'talker = py_pubsub.publisher_member_function:main',
            'listener = py_pubsub.subscriber_member_function:main',
        ],
    },
)
```

`name` 是命令查找包时使用的名字；`depend` 声明构建与运行依赖；`export` 中的 `build_type` 区分 Python 包与 CMake 包。

`data_files` 让包清单与资源索引被安装，否则包存在但索引不到；`console_scripts` 把可执行名映射到 Python 模块的 `main`，左边是 `ros2 run` 使用的名字，右边是实际代码位置。

---

## CMakeLists.txt：依赖、目标与安装

来自 `cpp_pubsub` 的真实配置（保留关键段落）：

```cmake
cmake_minimum_required(VERSION 3.8)
project(cpp_pubsub)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

add_executable(talker src/publisher_member_function.cpp)
ament_target_dependencies(talker rclcpp std_msgs)

install(TARGETS talker
  DESTINATION lib/${PROJECT_NAME})
```

`find_package` 查找依赖；`add_executable` 声明由哪个源文件生成哪个可执行目标；`ament_target_dependencies` 把依赖连接到该目标；`install` 决定构建产物安装到功能包的哪个位置，也决定了 `ros2 run` 能否找到它。

---

## 用 ros2 pkg 创建功能包

从工作空间的 `src` 目录创建两种类型的包：

```bash
cd ~/ros2_ws/src
ros2 pkg create --build-type ament_cmake cpp_pubsub \
  --dependencies rclcpp std_msgs
ros2 pkg create --build-type ament_python py_pubsub \
  --dependencies rclpy std_msgs
ros2 pkg list
ros2 pkg executables py_pubsub
```

前两条命令分别创建 CMake 包与 Python 包，并直接声明依赖；`ros2 pkg list` 列出环境可见的功能包；`ros2 pkg executables` 列出指定包提供的可执行入口。

创建位置必须在 `src` 下。`ros2 pkg list` 看不到刚创建的包时，先确认是否已构建并加载了 `install/setup.bash`。

---

## colcon：构建、选择与并行

常用构建方式：

```bash
cd ~/ros2_ws
colcon build --symlink-install
colcon build --packages-select py_pubsub
colcon build --parallel-workers 2
rosdep install --from-paths src --ignore-src -r -y
```

`--symlink-install` 用符号链接安装，适合频繁修改 launch 与 Python 文件的开发阶段；`--packages-select` 只构建指定包；`--parallel-workers 2` 限制并行构建数量，便于在低资源机器上稳定复现；`rosdep` 按包清单安装系统依赖。

修改 launch 文件后是否需要重新构建，取决于安装方式。使用符号链接安装可以省去这一轮构建，但依赖与接口变化仍需重新构建。

---

## 构建失败的排查顺序

构建报错时按固定顺序缩小范围，避免盲目重装：

| 现象 | 先查什么 | 下一步 |
| --- | --- | --- |
| 找不到依赖包 | `package.xml` 与 `find_package` 是否一致 | 补齐依赖后重新构建 |
| 找不到可执行文件 | `CMakeLists.txt` 的 `install` 或 `setup.py` 入口点 | 修正入口并重新构建 |
| 改了代码但行为不变 | 是否重新构建、是否加载了当前工作空间 | 重新构建并 `source install/setup.bash` |
| 部分包失败、其余成功 | 是否使用了 `--packages-select` | 单独构建失败包，保留其余成功结果 |

课堂要求：记录执行命令、完整错误首段和工作空间路径，再判断是依赖问题、配置问题还是代码问题。

---

## 第2课时（45分钟）：从节点到计算图

节点是 ROS2 中通信对象的构建单位。一般每个节点对应一个单一功能模块，例如雷达驱动节点负责发布雷达消息，摄像头驱动节点负责发布图像消息。

一个完整的机器人系统由许多协同工作的节点组成。单个可执行文件可以包含一个或多个节点，因此“进程数”和“节点数”并不一一对应。

把正在运行的节点与它们之间的话题、服务连接画出来，就得到计算图。计算图是调试 ROS2 系统时最先要看的一张图。

---

## 计算图与四种通信模型

观察运行中系统并判断该用哪种通信模型：

```bash
ros2 node list
ros2 topic list -t
rqt
```

`ros2 node list` 输出当前发现的节点名；`ros2 topic list -t` 输出话题名称及其消息类型；`rqt` 打开工具箱，用 Node Graph 插件查看节点与连接的图形关系。

ROS2 常用的通信模型有四种，它们的适用场景不同：

| 模型 | 结构（关系） | 适用场景 |
| --- | --- | --- |
| 话题 | 发布订阅、单向（多对多） | 不断更新、少逻辑处理的数据，如雷达、图像、里程计 |
| 服务 | 请求响应（一对多） | 偶然发生、需要明确结果的数据，如触发一次配置或查询 |
| 动作 | 目标、反馈、结果（一对多） | 耗时任务、需要连续反馈并允许取消，如旋转到指定角度 |
| 参数 | 共享数据（服务端与客户端） | 运行时可查询或修改的配置值，如阈值、增益、坐标系名称 |

选择模型时先问数据是否单向、是否需要应答、是否需要过程反馈、是否只是配置值。用错模型不会立刻报错，但会让后续调试变困难。

---

## 消息与接口文件

通信需要数据载体，ROS2 中称为接口（interfaces）。常用接口文件有三种：

```text
msg     话题消息定义
srv     服务请求与响应定义
action  动作目标、反馈与结果定义
```

统一接口的最大价值是解耦。教程给出的例子是激光雷达：如果没有统一接口，更换一个型号就要改一次导航程序；定义了 `sensor_msgs/msg/LaserScan` 之后，各厂家把自己的雷达数据转换成该格式，上层程序不必随硬件改变。

接口是软硬件之间的合同，改接口等于改合同，必须同时检查发布方与订阅方。

---

## 用 ros2 interface 查看接口

查看已有接口的定义与来源：

```bash
ros2 interface list
ros2 interface packages
ros2 interface package std_msgs
ros2 interface proto sensor_msgs/msg/LaserScan
ros2 interface show sensor_msgs/msg/LaserScan
```

`list` 列出所有可用接口；`packages` 列出包含接口的功能包；`package` 缩小到某个包的接口；`proto` 输出接口原型；`show` 输出完整字段定义。

看到一个话题后，先用 `ros2 topic info` 拿到类型，再用 `ros2 interface show` 查字段，是读代码前最有效的两步。

---

## 节点与话题命令行

观察节点、话题类型、频率与内容：

```bash
ros2 node list
ros2 node info /talker
ros2 topic list -t
ros2 topic info /odom
ros2 topic hz /scan
ros2 topic bw /scan
ros2 topic echo /odom --once
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0}, angular: {z: 0.0}}"
```

`node list` 与 `node info` 分别列出节点并查看其接口；`topic list -t` 显示话题与类型；`info` 显示发布订阅数量；`hz` 测发布频率；`bw` 测带宽；`echo --once` 只取一条消息；`pub` 向话题发布一条消息。

课堂强调：向 `/cmd_vel` 这类控制话题发布消息会直接产生运动指令。演示时必须让机器人处于安全状态并先发布零速度。

---

## 服务、动作与参数命令行

三类通信模型的观察与调用方式：

```bash
ros2 service list -t
ros2 service type /spawn
ros2 service call /spawn turtlesim/srv/Spawn \
  "{x: 1.0, y: 2.0, theta: 0.3, name: 'turtle2'}"

ros2 action list -t
ros2 action info /turtle1/rotate_absolute
ros2 action send_goal /turtle1/rotate_absolute \
  turtlesim/action/RotateAbsolute "{theta: 0.0}" --feedback

ros2 param list
ros2 param describe turtlesim background_b
ros2 param get turtlesim background_b
ros2 param set turtlesim background_b 10
ros2 param dump /turtlesim
```

`service list -t`、`type` 与 `call` 分别列出服务、查询类型、发送请求并打印响应；动作的 `list -t`、`info` 与 `send_goal --feedback` 分别列出动作、查看接口、发送目标并接收连续反馈；`param list`、`describe`、`get`、`set`、`dump` 用于列出、描述、读取、修改与导出参数。

调用服务或动作会改变对端状态，`set` 是运行时修改且通常不跨重启保留。课堂要求：先 `dump` 记录初始值，再修改，最后说明怎样恢复。

---

## ros2 bag 与可视化工具

bag 用于录制与回放证据，`rviz2` 与 `rqt` 把抽象数据变成可见对象：

```bash
ros2 bag record /scan /odom
ros2 bag record -a
ros2 bag info rosbag2_lidar
ros2 bag play rosbag2_lidar -r 10 -l
ros2 bag play rosbag2_lidar --topics /scan

rviz2
rqt
ros2 run rqt_gui rqt_gui
```

`record` 录制指定话题或全部话题；`info` 查看包内话题与时长的摘要；`play` 回放，`-r 10` 十倍速、`-l` 循环、`--topics` 只回放指定话题。录制前确认磁盘余量，录制后记录话题列表、时长与文件摘要。

`rviz2` 面向三维可视化，用于观察坐标系、传感器数据与机器人模型；`rqt` 是插件式工具箱，常用插件为 Node Graph、Topic Monitor、Image View 与 TF Tree。课堂上以 Node Graph 与 Topic Monitor 为主，先看结构再看数据。

---

## 常见现场问题的命令行定位

用最小命令集区分故障类型：

| 现象 | 先执行 | 判断 |
| --- | --- | --- |
| 找不到节点 | `ros2 node list` | 节点没启动，或启动在另一个网络域 |
| 看不到数据 | `ros2 topic list -t`、`ros2 topic hz` | 没有发布方，或发布频率为零 |
| 数据字段不对 | `ros2 topic info`、`ros2 interface show` | 类型或字段理解错误 |
| 调用无响应 | `ros2 service list -t` | 服务端未启动或名称不匹配 |
| 任务没有进度 | `ros2 action info ...` | 目标未接受或反馈未发布 |

记录时保存命令与原始输出，而不是只留下结论。

---

## 第3课时（45分钟）：launch 组织多节点启动

一个机器人系统往往需要同时启动多个节点，并且每个节点都需要特定配置。ROS2 的 launch 文件负责同时启动和配置多个可执行文件。

launch 文件有 XML、YAML 和 Python 三种格式。Python 更灵活，可以使用条件判断、变量与库函数，适合表达复杂启动逻辑，因此在本专题的示例中占主要位置。

课堂原则：单个节点用 `ros2 run`，一组相关节点用 `ros2 launch`，launch 文件与节点代码一样需要审查和版本管理。

---

## ros2 launch 与 ros2 run

启动已写好的系统与单个节点：

```bash
ros2 launch turn_on_wheeltec_robot \
  turn_on_wheeltec_robot.launch.py
ros2 launch turn_on_wheeltec_robot wheeltec_lidar.launch.py
ros2 launch turn_on_wheeltec_robot wheeltec_camera.launch.py
ros2 launch turn_on_wheeltec_robot wheeltec_sensors.launch.py

ros2 run wheeltec_robot_keyboard wheeltec_keyboard
ros2 run tf2_tools view_frames
ros2 run nav2_map_server map_saver_cli -f ~/map
ros2 run web_video_server web_video_server
```

`ros2 launch` 的参数顺序是“功能包名 + launch 文件名”，四条命令依次启动底盘与基础节点、单独雷达、单独相机、以及三者组合。文件名写错时错误指向文件，功能包名写错时错误指向包查找。

`ros2 run` 的参数顺序是“功能包名 + 可执行文件名”，四条命令依次启动键盘控制、生成 TF 树文件、保存地图、把图像话题转成浏览器可看的视频流。运行控制类节点前必须确认安全条件：场地清空、机器人可断电、速度参数已知。

---

## launch 文件的 Python 结构

Python launch 文件把每个节点、文件或脚本抽象成一个 action，统一描述后返回：

```python
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    talker = Node(package='py_pubsub', executable='talker')
    return LaunchDescription([talker])
```

`Node` 描述“启动哪个包里的哪个可执行文件”；`LaunchDescription` 收集需要在启动时执行的全部 action。函数名 `generate_launch_description` 是约定的入口，不能被改名或省略。

复杂 launch 文件中的 `IncludeLaunchDescription` 用于包含另一个 launch 文件，从而把系统拆成可组合的启动单元。

---

## 真实 launch 文件中的三类内容

来自教程的 `turn_on_wheeltec_robot.launch.py`（节选）：

```python
bringup_dir = get_package_share_directory('turn_on_wheeltec_robot')
launch_dir = os.path.join(bringup_dir, 'launch')

wheeltec_robot = IncludeLaunchDescription(
    PythonLaunchDescriptionSource(
        os.path.join(launch_dir, 'base_serial.launch.py')),
    launch_arguments={'akmcar': 'false'}.items(),
)

imu_filter_node = launch_ros.actions.Node(
    package='imu_filter_madgwick',
    executable='imu_filter_madgwick_node',
    parameters=[imu_config],
)
```

`get_package_share_directory` 从安装目录定位功能包资源，避免写死绝对路径；`IncludeLaunchDescription` 复用已有 launch 文件并传参；`Node` 与 `parameters` 启动单个节点并加载参数文件。

这三类内容构成大多数启动文件：定位资源、组合已有启动、启动新节点。

---

## 启动参数与条件

启动文件可以声明参数并使用条件控制是否启动某个分支：

```python
carto_slam_dec = DeclareLaunchArgument(
    'carto_slam', default_value='false')
carto_slam = LaunchConfiguration('carto_slam', default='false')
```

`DeclareLaunchArgument` 声明一个可在命令行覆盖的参数并给出默认值；`LaunchConfiguration` 在后续 action 中读取该参数。

命令行覆盖方式为 `ros2 launch <包> <文件> carto_slam:=true`。课堂要求：记录使用了哪些覆盖参数，否则同一份 launch 文件在不同机器上会得到不同系统。

---

## TF2：坐标变换的发布与查询

移动机器人上，传感器与执行器位于不同位置。雷达测到的点、相机看到的物体、底盘报告的位姿，必须在统一坐标系下才能组合使用。TF2 提供管理和查询坐标系变换关系的工具。

完整的坐标变换由广播方与监听方组成：广播方发布坐标系之间的相对关系，监听方把多组关系融合为一棵坐标树。ROS 中的坐标变换基于右手坐标系，关于坐标系变换关系可以分解为平移和旋转两部分。

两个坐标系相对位置固定时使用静态变换：

```bash
ros2 run tf2_ros static_transform_publisher \
  0 0 3 0 0 3.14 A B
ros2 run tf2_ros tf2_echo A B
ros2 run tf2_tools view_frames
rviz2
```

`static_transform_publisher` 的参数依次是 x、y、z 偏移量与偏航、俯仰、横滚角，最后是父级坐标系与子级坐标系；`tf2_echo` 查询两个坐标系之间的实时变换；`view_frames` 生成完整的 TF 树；`rviz2` 用于可视化检查。

排查顺序是先在命令行确认变换存在，再到 `rviz2` 里检查显示配置，避免把可视化配置问题误判为 TF 缺失。坐标树断了，数据看起来还在，但无法被正确解释。

---

## 在 launch 中发布基座到传感器的变换

来自教程 `robot_mode_description.launch.py` 的真实片段：

```python
launch_ros.actions.Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    name='base_to_laser',
    arguments=['0.048', '0', '0.20', '0', '0', '0',
               'base_footprint', 'laser'],
)
```

这段 action 声明雷达相对 `base_footprint` 的固定安装位置：向 x 方向偏移 0.048 米、向 z 方向抬高 0.20 米，姿态角为零。

安装尺寸写错会让建图与导航在几何上整体偏移，且在单个传感器数据里看不出来。课堂要求：修改任何安装尺寸都要与实物测量值对照并记录来源。

---

## URDF：描述机器人结构与加载

URDF 描述机器人模型，把车体、左右轮子、激光雷达、相机与计算平台等部件组合起来。教程把建模过程概括为：把每个部件描述清楚，再通过关节连接。

URDF 中两类核心标签：`link` 描述刚体部分的外观与物理属性，包括尺寸、颜色、形状、质量、惯性矩阵与碰撞参数；`joint` 连接刚体并描述相对运动，包括关节类型、原点、父子关系与运动轴。

来自教程的真实结构（节选）：

```xml
<link name="left_wheel_link">
  <visual>
    <origin xyz="0 0 0" rpy="0 0 0" />
    <geometry><cylinder radius="0.0325" length="0.025"/></geometry>
  </visual>
</link>

<joint name="left_wheel_joint" type="continuous">
  <origin xyz="0 0.08 0.0325" rpy="1.57 0 0"/>
  <parent link="base_link"/>
  <child link="left_wheel_link"/>
  <axis xyz="0 1 0"/>
</joint>
```

`origin` 给出子连杆相对父连杆的偏移与旋转；`parent` 与 `child` 定义连接方向；`axis` 是关节运动轴的单位向量；`type="continuous"` 表示可以连续旋转的关节。URDF 中的关节有六种运动类型，选错类型会让运动学结果与实际结构不符。

URDF 通过 `robot_state_publisher` 进入系统：

```python
launch_ros.actions.Node(
    package='robot_state_publisher',
    executable='robot_state_publisher',
    arguments=[os.path.join(
        get_package_share_directory('wheeltec_robot_urdf'),
        'urdf', 'mini_mec_robot.urdf')],
)
```

`robot_state_publisher` 读取 URDF，结合关节状态发布各连杆坐标系之间的变换。URDF 加载成功但模型不动时，先检查是否有 `joint_states` 发布方，再检查 `rviz2` 的 Fixed Frame 设置。

---

## 传感器与底盘话题

底盘与传感器是 ROS2 与真实物理世界之间的边界。启动底盘后先列出话题，再逐个确认含义：

```bash
ros2 topic list -t
ros2 topic info /odom
ros2 topic echo /odom
ros2 topic echo /imu/data_raw
ros2 topic hz /scan
ros2 topic echo /scan --once
```

`/odom` 是底盘里程计，描述位置与速度估计；`/imu/data_raw` 是惯性测量单元原始数据；`/scan` 是激光雷达扫描数据。`info` 确认类型与发布者，`hz` 确认数据是否持续更新，`echo --once` 读取一条样例。

三类数据分别来自不同驱动节点，排查时要先确认对应节点是否运行，再怀疑算法层。

---

## 运动指令与安全边界

底盘控制通常通过速度指令话题下发。验证链路时使用零速度：

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0}, angular: {z: 0.0}}"
ros2 topic echo /odom --once
ros2 topic info /cmd_vel
```

第一条发布一条零线速度、零角速度的指令，用于确认话题类型与链路；`echo /odom` 读取一条里程计数据，确认底盘仍在反馈；`topic info` 确认该话题的发布方与订阅方数量。

课堂安全规则：任何非零速度指令都必须在清空场地、确认急停方式之后执行，并且只由一名操作者下达。

---

## 远程操作：SSH

SSH 提供命令行远程登录，把开发机变成板端的终端。资料中给出的登录形式是：

```bash
ssh -Y wheeltec@192.168.0.100
```

`ssh` 发起加密远程登录；`-Y` 启用可信 X11 转发，使远端图形程序可以在本机显示；`wheeltec@192.168.0.100` 是用户名与目标地址。

登录前提是开发机与机器人处于可互通的网络。SSH 适合启动节点、查看话题、构建工作空间与保存日志，因为它传输的是文本，带宽占用低、输出便于复制到实验记录。

课堂要求：不在共享文档中记录密码，登录信息按实验环境单独管理。

---

## 远程操作：VNC

VNC 提供远程图形桌面，用于必须在图形界面完成的操作，例如 `rviz2`、`rqt` 与可视化参数调整。

典型使用流程：

```bash
xrandr --fb 1024x768
rviz2
rqt
```

`xrandr --fb` 调整虚拟桌面的分辨率，用于匹配远程窗口大小或避免显示区域超出屏幕；`rviz2` 与 `rqt` 在远端桌面内启动，操作体验接近本地图形环境。

VNC 传输的是图形画面，同样的网络条件下带宽占用高于 SSH。课堂建议只在必须查看图形界面时使用，并把节点启动与日志采集留在 SSH 中完成。

---

## SSH 与 VNC 对照

| 维度 | SSH | VNC |
| --- | --- | --- |
| 交互形式 | 命令行终端 | 图形桌面 |
| 带宽占用 | 低 | 高 |
| 主要用途 | 启动节点、查看话题、构建、采集日志 | rviz2、rqt 与图形化调试 |
| 自动化友好度 | 高，输出可复制与脚本化 | 低，依赖人工操作画面 |
| 网络不稳时 | 可恢复、可重连 | 容易卡顿或断开 |
| 典型风险 | 误在错误终端执行控制命令 | 画面延迟造成误判操作时机 |

课堂选择原则：能用命令行完成的用 SSH，必须看图的用 VNC，两者记录的证据类型不同。

---

## ROS2 多机通信的条件与验证

官方资料中的 SSH 登录地址是 `192.168.0.100`，属于该资料自己的网络与账号配置。把它当作格式示例，不要在任何环境下直接照抄。

ROS2 多机通信依赖中间件发现机制。两台机器要互相看见节点，至少需要满足：网络可达且没有被隔离在互不可见的网段；两台机器的 `ROS_DOMAIN_ID` 一致；同一话题的消息接口定义匹配；多机场景下节点重名与时间不同步会干扰排查。

按从下到上的顺序逐层验证：

```bash
ip addr
ping -c 3 192.168.0.100
echo $ROS_DOMAIN_ID
ros2 node list
ros2 topic list -t
```

`ip addr` 确认本机地址与接口；`ping` 确认网络层可达；`echo` 打印当前终端使用的域标识，两台机器必须一致；`ros2 node list` 与 `ros2 topic list -t` 确认 ROS2 发现层是否已经建立。

只在同一台机器上看到节点，说明该机器的本地环境正常；这不能证明多机发现已经打通，必须分别在两台机器上执行同样的检查并保存输出。

---

## 自启动与开机运行

现场演示与长期运行的机器人需要开机后自动拉起系统。基本思路是把启动步骤写成一个可重复执行的入口：

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_ws/install/setup.bash
ros2 launch turn_on_wheeltec_robot \
  turn_on_wheeltec_robot.launch.py
```

前两行加载发行版与工作空间环境，第三行启动系统。三行必须处于同一个 shell 会话中，否则启动进程可能继承不到正确环境。

自启动会让系统在没有人登录时改变机器人的物理状态，因此需要明确边界：

- 明确哪些节点必须开机启动，哪些只在调试时手动启动；保留手动停止与回滚路径。
- 保留启动日志，确保失败时能判断失败发生在环境加载、启动还是运行阶段。
- 对运动相关节点设置安全默认值，避免自启动时立即产生非零速度。
- 自启动的每一次变更都要记录变更前后状态与验证方法，只做静态检查与手动触发的分步验证，不在无人看管的机器人上直接启用。

这些要求与附属的板端操作规范一致：可以自动化，但不能失去可观察性。

---

## 提出的未验证集成路径：ROS2 + 昇腾310B

以下是一条**提出但尚未验证**的集成路径，用来给出后续实验方向，不是已完成的结果：

```text
机器人主控（ROS2）
  -> 图像/雷达话题订阅节点
  -> 昇腾310B 推理进程
  -> 统一的检测或分类结果消息
  -> 决策或控制节点
  -> 底盘速度指令话题
```

该路径把 ROS2 作为数据与启动骨架，把昇腾310B 上运行的模型推理作为一个计算环节，再用标准话题把结果送回机器人系统。

验证按最小风险顺序推进，每一步单独记录证据：

1. 只启动 ROS2，确认节点、话题与启动方式正常，不接入任何 NPU 推理。
2. 只验证昇腾310B 侧的模型推理能力，使用固定输入，不依赖机器人实时数据。
3. 用录制好的话题数据替代实时传感器，验证接口类型与字段转换正确。
4. 接入实时话题，测量端到端时延、吞吐与资源占用。
5. 接入控制链路前先观察推理输出，确认没有异常值再考虑闭环。

需要验证的关键点包括：ROS2 发行版在目标系统上的可用性、推理进程与 ROS2 环境的共存方式、消息序列化开销、端到端时延与失败时的降级行为。

---

## 明确证据边界

以下表述必须区分清楚，不得混用：

| 表述 | 当前状态 |
| --- | --- |
| ROS2 的基础机制与命令 | 有公开文档与教程支持，本专题按其通用规则讲解 |
| 第三方机器人平台上的话题与接口 | 来自外部资料，属于该平台的实现 |
| ROS2 在昇腾310B 上的运行情况 | 本专题未验证 |
| ROS2 + NPU 端到端推理链路 | 本专题未验证，属于提出路径 |
| 任何时延、频率或精度指标 | 未在本专题采集，不得引用为结果 |

课堂要求：写报告时注明结论来自文档、来自观察还是尚未验证。文档中的机制说明不能被改写为板级实测结论。

---

## 课堂任务

第1课时：阅读提供的功能包，画出目录结构，标注 `package.xml`、`CMakeLists.txt` 或 `setup.py` 的作用，并用 `ros2 pkg list` 与 `ros2 pkg executables` 验证。

第2课时：启动一个自带示例系统，用 `ros2 node list`、`ros2 topic list -t`、`ros2 topic hz`、`ros2 topic echo --once` 采集观察记录，然后分别对话题、服务、动作与参数各写一条实际使用的命令并解释其作用。

第3课时：阅读一个 launch 文件，说明它启动了哪些节点、使用了哪些参数；用 `ros2 run tf2_tools view_frames` 或 `tf2_echo` 检查坐标关系；用 SSH 完成一次节点启动与日志采集，并说明本次没有使用 VNC 或使用 VNC 的原因。

三个课时的任务都要保存命令与原始输出，不允许只提交结论。

---

## 交付物

| 交付物 | 内容 |
| --- | --- |
| `ros2/lesson1-package-map.md` | 工作空间与功能包结构、构建清单、依赖关系说明 |
| `ros2/lesson2-cli-observations.txt` | 节点、话题、服务、动作、参数命令与原始输出 |
| `ros2/lesson2-interface-notes.md` | 至少两个消息接口的字段说明与使用场景 |
| `ros2/lesson3-launch-review.md` | launch 文件结构分析、参数列表与启动结果 |
| `ros2/lesson3-tf-notes.md` | 实际坐标树、父子关系与检查命令 |
| `ros2/lesson3-remote-access.md` | SSH 与 VNC 的使用条件、执行内容与限制说明 |
| `ros2/evidence-boundary.md` | 已观察、来自文档与未验证三类结论的清单 |

报告中应删除密码、真实 IP 之外的隐私信息与不必要的个人标识。

---

## 验收标准

- 能说明 ROS2 的定位、四层生态结构，以及 DDS 与去中心化发现带来的直接影响。
- 能读懂工作空间与功能包结构，解释 `package.xml`、`CMakeLists.txt`、`setup.py` 与 `colcon build` 的分工。
- 能正确区分话题、服务、动作与参数，并为每类模型写出可执行命令和适用场景。
- 能使用 `ros2 node`、`ros2 topic`、`ros2 service`、`ros2 action`、`ros2 param`、`ros2 interface`、`ros2 bag`、`rviz2`、`rqt` 中的至少六类命令完成一次观察任务。
- 能解释 launch、TF2 与 URDF 各自的职责，并说明坐标树断开时会出现什么现象。
- 能分别说明 SSH 与 VNC 的用途、带宽特征与适用边界，并说明 ROS2 多机通信需要哪些条件。
- 交付物中有完整命令与原始输出，未验证内容明确标注为未验证，不把外部平台结果或文档描述写成昇腾310B 实测结果。
