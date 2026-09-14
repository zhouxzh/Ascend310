---
title: "附录 6：ROS2 基础教程"
author: [周贤中]
subject: "Markdown"
keywords: [ROS2, Humble, DDS, colcon, launch, TF2, URDF, SSH, VNC]
lang: zh-cn
---

# 附录 6：ROS2 基础教程

本附录以 ROS2 Humble 为主要示例版本，介绍机器人软件框架的定位、DDS 通信底层、工作空间与功能包、四种通信模型、命令行工具、可视化与数据记录、Python launch、TF2 与 URDF、传感器与底盘接口、SSH/VNC 远程操作、多机通信、程序自启动，以及一条明确标记为未验证的 ROS2 与昇腾 310B 推理服务集成路径。

主要内容取自 WHEELTEC R550 ROS2 系列教程中的《ROS2 入门教程》《ROS2 与 STM32 运动底盘通信》《ROS2 环境配置更换 URDF 模型教程》《ROS2 小车上手操作》《ROS2 查看小车底盘数据教程》《ROS2 查看小车雷达数据教程》，并按本文档所属教材的语境重新组织。概念性内容可对照教程中列出的 ROS2 官方文档入口。

## 教程定位与证据边界

在开始使用命令之前，先固定本附录的证据边界。以下四条是全文的前提，后续任何章节都不与之冲突。

- `samples/case1` 至 `samples/case9` 当前不包含经过验证的 ROS2 运行时链路。仓库中检索不到 `rclpy`、`rclcpp`、`colcon`、`ros2 run` 等运行时实现与验证记录，因此本书已有案例与 ROS2 之间不存在可直接引用的集成结果。
- 本附录没有在昇腾 310B 开发板上验证过 ROS2。文中命令来自厂商教程与 ROS2 官方文档语境，用于建立基础概念与操作习惯，不等价于板端实测结论。
- 第 12 节的 ROS2 与昇腾 310B 推理服务集成路径明确标记为 proposed 与 untested。只有完成板端实测、留存日志与输入协议之后，才能把该路径升级为已验证结论。
- 本附录不给出延迟、吞吐、精度等性能数字。这类数字必须绑定模型、精度、硬件算力档位、数据集、预热、统计方法与原始报告路径，缺少任何一项都不能作为教材结论。

章节中的 `documented`、`inferred`、`observed-pass`、`observed-fail`、`untested` 标记沿用本书统一的证据分级方式。厂商教程给出的接口与命令属于 `documented`；跨平台迁移判断属于 `inferred`；本文中的集成路径属于 `untested`。

### 命令来源与参考资料

| 来源 | 用途 | 证据等级 |
| ---- | ---- | -------- |
| `ROS2 入门教程.pdf` | 体系框架、通信模型、命令行、rqt 与 rviz2、bag、launch、TF2、URDF | documented |
| `ROS2-WHEELTEC 机器人的 ROS 和 STM32 通信过程` | 底盘串口协议、设备别名、话题与服务接口 | documented |
| `ROS2 环境配置更换 urdf 模型教程` | URDF 替换流程与常见位置偏差 | documented |
| `ROS2 小车上手操作.pdf` | 远程登录、编译选项、硬件检查顺序 | documented |
| `ROS2 查看小车底盘数据教程.pdf` | `/odom`、`/imu/data_raw` 与 IMU 可视化 | documented |
| `ROS2 查看小车雷达数据教程.pdf` | `/scan`、rviz2 LaserScan 与固定坐标系 | documented |
| ROS2 官方文档 <https://docs.ros.org/en/humble/index.html> | DDS、发现机制、环境变量与工具行为的背景说明 | documented |

教程资料中出现的车型、雷达型号、相机型号与设备别名属于 WHEELTEC 默认配置。更换硬件或修改配置后，话题名、串口别名与坐标系可能变化，必须以实际 `ros2 topic list` 与 `ros2 node info` 的输出为准。

## 1. 定位、DDS、去中心化发现与教材边界

### 1.1 ROS2 在机器人软件中的位置

ROS2 是一个开源的机器人软件框架，用于把机械结构、嵌入式控制、传感器驱动与上层算法组织成可复用的软件模块。教程把其生态划分为四个部分：

- 通信（Plumbing）：在节点之间传递消息的中间件，是整个框架的核心实现。
- 工具（Tools）：launch、调试、可视化、绘图、数据记录与回放等开发工具。
- 功能（Capabilities）：传感器驱动、运动规划、导航等可复用能力集合。
- 社区（Community）：贡献功能包、文档与经验的组织与个人。

教程归纳的 ROS2 特性包括开源免费、面向多机器人系统、跨平台（Linux、Windows、macOS、RTOS，乃至无操作系统的微控制器）、面向实时控制需求、适应不同网络环境、可用于产品化部署，以及覆盖设计到部署的项目管理机制。这些是框架的设计目标，是否在具体平台上成立需要单独验证。

### 1.2 DDS 与去中心化发现

ROS2 没有自行实现通信底层，而是直接采用 DDS 作为消息传递机制。教程给出的两点结论是：节点之间不再通过中心管理节点关联，节点可以直接相互发现并建立对等通信；通信的实时性、可靠性与连续性由 DDS 的传输质量策略承担。

从使用角度看，需要记住三个可观察事实：

- 发现是分布式的。两个节点只要在同一 DDS 域、网络可达且话题名与消息类型一致，就可以自动发现对方并开始通信，不需要启动额外的调度程序。
- 域由环境变量分组。`ROS_DOMAIN_ID` 相同的节点才会参与同一发现范围，多组实验可以用不同域号相互隔离。
- 发现依赖网络条件。默认使用组播，跨网段、NAT 或禁用组播的网络可能让同一域内的节点互相看不见。

这三个事实解释了第 10 节的多机配置，也解释了为什么排障时第一步总是确认域号、网络与接口类型，而不是先怀疑话题代码。

### 1.3 与昇腾 310B 教材的边界

本教材的主线是在昇腾 310B 上完成模型部署与应用构建。ROS2 在本教材中的定位是机器人侧的软件组织方式，而不是已经接入的运行时依赖：

- 现有案例的推理入口、HTTP 接口与前端工作台都不经过 ROS2，也不需要 ROS2 才能运行。
- 若要把案例中的推理能力接入机器人系统，需要单独设计适配节点，并接受第 12 节列出的全部未验证前提。
- 教材正文中的 CANN、ATC、OM、ACL 证据边界继续有效，ROS2 的引入不会放宽这些边界。

## 2. 工作空间、功能包与构建系统

### 2.1 工作空间目录结构

教程给出的 ROS2 工作空间结构如下：

```text
WorkSpace --- 自定义的工作空间
    |--- build：存储中间文件的目录，该目录下会为每一个功能包创建一个单独子目录
    |--- install：安装目录，该目录下会为每一个功能包创建一个单独子目录
    |--- log：日志目录，用于存储日志文件
    |--- src：用于存储功能包源码的目录
        |-- C++ 功能包
            |-- package.xml：包信息，比如包名、版本、作者、依赖项
            |-- CMakeLists.txt：配置编译规则，比如源文件、依赖项、目标文件
            |-- src：C++ 源文件目录
            |-- include：头文件目录
            |-- msg / srv / action：消息、服务、动作接口目录
        |-- Python 功能包
            |-- package.xml：包信息，比如包名、版本、作者、依赖项
            |-- setup.py：与 C++ 功能包的 CMakeLists.txt 类似
            |-- setup.cfg：功能包基本配置文件
            |-- resource：资源目录
            |-- test：存储测试相关文件
            |-- 功能包同名目录：Python 源文件目录
```

`src` 是唯一需要手工维护的目录。`build`、`install`、`log` 由构建与运行过程生成，可以删除后重新编译，也可以在版本控制中忽略。手工修改 `build` 或 `install` 中的内容不会带来稳定结果，重新编译就会覆盖。

### 2.2 创建工作空间并编译

```bash
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws
colcon build
```

第一条命令创建源码目录，第二条命令进入工作空间根目录，第三条命令在根目录执行编译。`colcon` 按当前目录作为工作空间根，因此 `colcon build` 必须在包含 `src` 的目录中执行，在 `src` 内执行不会得到预期结果。

```bash
source /opt/ros/humble/setup.bash
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
```

第一行为当前终端加载 ROS2 环境，之后 `ros2`、`rviz2`、`rqt` 等命令才能被找到；第二行把同样的加载动作写入 `~/.bashrc`，使新终端自动生效。官方文档也建议在需要时单独 `source` 工作空间的 `install/setup.bash`，让工作空间中的功能包覆盖系统同名包。

```bash
colcon build --packages-select <package1> <package2>
```

`--packages-select` 只构建指定的一个或多个功能包，不处理其他包的依赖关系，适合功能包改动后的快速迭代。

```bash
colcon build --symlink-install
colcon build --symlink-install --packages-select <package_name>
```

厂商教程推荐使用 `--symlink-install`。它通过符号链接安装资源文件，编译后 `src` 中的 `yaml` 配置与 `launch.py` 文件修改可以直接生效，不需要每次都重新编译。只使用普通 `colcon build` 时，任意文件改动后都需要重新编译。

### 2.3 创建功能包

```bash
ros2 pkg create --build-type ament_python <pkg_name> rclpy std_msgs sensor_msgs
ros2 pkg create --build-type ament_cmake cpp_pubsub
ros2 pkg create --build-type ament_cmake cpp_pubsub --dependencies rclcpp std_msgs
```

第一行创建 Python 功能包并声明 `rclpy`、`std_msgs`、`sensor_msgs` 依赖；第二行创建 C++ 功能包；第三行在创建时直接声明 `rclcpp` 与 `std_msgs` 依赖，避免之后再手工补依赖。

### 2.4 `package.xml`：包元信息与依赖声明

`package.xml` 描述包名、版本、描述、维护者以及构建与运行依赖。下列内容取自教程的 Python 功能包示例：

```xml
<?xml version="1.0"?>
<package format="3">
  <name>py_pubsub</name>
  <version>0.0.0</version>
  <description>TODO: Package description</description>
  <maintainer email="noah@todo.todo">noah</maintainer>
  <license>TODO: License declaration</license>
  <depend>rclpy</depend>
  <depend>std_msgs</depend>
  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

`<depend>` 声明运行与构建都需要的能力，`<test_depend>` 声明仅测试阶段使用的能力，`<export>` 中的 `build_type` 决定构建方式。Python 功能包使用 `ament_python`，C++ 功能包使用 `ament_cmake`。源文件中用到的每一个消息包都应在依赖中登记，否则编译或运行阶段会报找不到类型。

### 2.5 `CMakeLists.txt`：C++ 功能包的编译规则

教程的 C++ 话题通信功能包配置如下：

```cmake
cmake_minimum_required(VERSION 3.8)
project(cpp_pubsub)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

add_executable(talker src/publisher_member_function.cpp)
ament_target_dependencies(talker rclcpp std_msgs)

add_executable(listener src/subscriber_member_function.cpp)
ament_target_dependencies(listener rclcpp std_msgs)

install(TARGETS
talker
listener
DESTINATION lib/${PROJECT_NAME})

ament_package()
```

`add_executable` 把源文件编译为目标可执行文件，`ament_target_dependencies` 把该目标与消息库、客户端库链接起来，`install(TARGETS ... DESTINATION lib/${PROJECT_NAME})` 决定 `ros2 run` 能通过哪个路径找到它。三个环节缺少任意一个，`ros2 run` 都会找不到可执行文件。

### 2.6 `setup.py`：Python 功能包的入口点

```python
from setuptools import setup

package_name = 'py_pubsub'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='noah',
    maintainer_email='noah@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'talker = py_pubsub.publisher_member_function:main',
            'listener = py_pubsub.subscriber_member_function:main',
        ],
    },
)
```

`data_files` 中的 `resource` 与 `package.xml` 让 ROS2 的包索引能够发现该功能包；`entry_points` 的 `console_scripts` 把 Python 模块映射为可执行名，因此 `ros2 run py_pubsub talker` 实际调用的是 `publisher_member_function` 中的 `main` 函数。功能包同名目录中还必须存在 `__init__.py`，否则模块无法被正确导入。

## 3. 节点、通信模型、接口与计算图

### 3.1 节点与话题

节点（Node）是一切通信对象的载体。教程的表述是：雷达驱动节点负责发布雷达消息，摄像头驱动节点负责发布图像消息，一个完整的机器人系统由许多协同工作的节点组成，并且单个可执行文件可以包含一个或多个节点。

话题（Topic）把使用同名、同类型消息的节点关联起来，构成通信的基础。话题的发布方与订阅方是多对多关系，适合持续更新、逻辑处理较少的数据传输场景，例如传感器数据流。

### 3.2 四种通信模型

| 模型 | 交互方式 | 关系 | 典型用途 |
| ---- | -------- | ---- | -------- |
| 话题 | 发布方发布数据，订阅方接收数据，数据流单向 | 多对多 | 持续更新的传感器数据与状态流 |
| 服务 | 客户端发送请求，服务端返回结果 | 一对多 | 偶然发生、有逻辑处理、需要应答的操作 |
| 动作 | 客户端发送目标，服务端持续反馈并给出最终结果，可取消 | 一对多 | 耗时的请求响应，需要连续反馈 |
| 参数 | 服务端保存数据，客户端读取与修改 | 共享 | 节点配置项与运行期可调参数 |

选择依据可以简化为三个问题：数据是否持续产生，调用是否需要等待结果，过程是否需要中间反馈。持续产生用话题，需要等待单次结果用服务，需要等待长时间任务并观察进度用动作，需要调整节点配置用参数。

### 3.3 消息、服务与动作接口

通信需要数据载体，ROS2 把载体称为接口（interface），接口文件有 `msg`、`srv`、`action` 三种。教程用激光雷达说明统一接口的价值：不同厂家雷达的驱动方式与扫描速率各不相同，但都把自己的数据转换成 `sensor_msgs/msg/LaserScan`，于是导航程序不必为每种雷达单独适配。

这个例子也说明了接口的排障意义。话题名相同但消息类型不同，两个节点不会成功通信；反过来，只要接口类型一致，发布方与订阅方可以用不同编程语言实现。

### 3.4 计算图

把运行中的节点、话题、服务、动作与参数作为整体观察，就得到系统的计算图。计算图是排障的主要视角：

- `ros2 node list` 与 `ros2 node info` 给出节点与其连接关系。
- `ros2 topic list`、`ros2 service list`、`ros2 action list` 给出通信端点。
- `rqt` 的 Node Graph 插件把计算图以图形方式呈现。

定位问题时先看计算图是否符合预期，再进入具体话题的数据内容，可以避免在错误方向上反复读取数据。

## 4. 命令行工具

教程给出的常用命令包括 `ros2 pkg`、`ros2 run`、`ros2 node`、`ros2 topic`、`ros2 interface`、`ros2 service`、`ros2 action`、`ros2 param`。所有子命令都可以用 `-h` 或 `--help` 查看帮助，例如 `ros2 node -h` 与 `ros2 node list --help`。

### 4.1 运行节点与启动文件

```bash
ros2 run pkg_name node_name
ros2 run cpp_pubsub listener
ros2 run cpp_pubsub talker
```

`ros2 run` 的第一个参数是功能包名，第二个参数是该包中的可执行文件名。上面的两行分别启动订阅方与发布方，通常需要打开两个终端，先启动订阅方再启动发布方可以立即看到数据。教程中 Python 功能包的用法一致，只需把包名换成 `py_pubsub`。

```bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py
```

`ros2 launch` 的第一个参数是功能包名，第二个参数是 launch 文件名。这条命令一次启动底盘通信、IMU 处理、EKF 状态估计、TF 变换与机器人模型等多个节点，是厂商教程中启动小车底层的标准入口。

### 4.2 节点

```bash
ros2 node list
ros2 node info /wheeltec_robot
```

`ros2 node list` 输出当前运行中的节点；`ros2 node info` 输出指定节点发布与订阅的话题、提供的服务及其消息类型。厂商教程建议在启动底层后用第二条命令确认节点运行状态，这一步能同时验证串口节点是否上线、话题接口是否与文档一致。

### 4.3 话题

```bash
ros2 topic list
ros2 topic echo /odom
ros2 topic echo /imu/data_raw
ros2 topic echo /scan
```

第一条命令列出当前话题。后续三条分别读取底盘里程计、IMU 原始数据与激光雷达扫描数据。`ros2 topic echo` 会持续打印消息内容，确认数据正常后应使用 `Ctrl+C` 结束，避免长期占用终端。

```bash
ros2 topic hz /scan
ros2 topic bw /scan
ros2 topic info /scan
ros2 topic type /scan
ros2 topic find sensor_msgs/msg/LaserScan
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}, angular: {z: 0.0}}"
```

`hz` 输出发布频率，`bw` 输出带宽占用，`info` 输出发布方与订阅方数量，`type` 输出接口类型，`find` 按类型反查话题。`pub` 向指定话题发布消息，上例向底盘速度话题发送一个前进指令；在真实机器人上执行前必须确认车体周围安全，并准备好停止手段。

### 4.4 服务

```bash
ros2 service list
ros2 service find turtlesim/srv/Spawn
ros2 service type /spawn
```

服务列表与类型查询用于确认服务端点是否存在、接口是否符合预期。`list` 输出运行中的服务，`find` 按服务类型查找，`type` 输出指定服务使用的接口类型。

```bash
ros2 service call /spawn turtlesim/srv/Spawn "{x: 1.0,y: 2.0,theta: 0.3,name: turtle2}"
```

`ros2 service call` 向服务发送请求，参数为服务名、接口类型与请求内容。教程用海龟仿真器的 `/spawn` 服务演示了这种调用方式，请求字段采用 YAML 形式的字典。

### 4.5 动作

```bash
ros2 action list
ros2 action info /turtle1/rotate_absolute
ros2 action send_goal /turtle1/rotate_absolute turtlesim/action/RotateAbsolute theta:\ 0.0
```

`ros2 action list` 输出运行中的动作，`info` 输出动作的客户端与服务端信息，`send_goal` 发送动作目标。动作与服务的差别在于是否有中间反馈与取消能力，因此 `send_goal` 的输出通常包含中间反馈与最终结果两部分。

### 4.6 参数

```bash
ros2 param list
ros2 param describe turtlesim background_b
ros2 param get turtlesim background_b
ros2 param set turtlesim background_b 10
```

`param list` 输出可用参数，`describe` 输出参数的描述信息，`get` 与 `set` 分别读取和修改参数值。教程用海龟仿真器的背景色参数演示了完整流程。参数属于运行期配置，修改后一般不需要重新编译，但重新启动节点时是否保留取决于节点实现与参数文件。

```bash
ros2 param dump /turtlesim
ros2 param load /turtlesim /tmp/turtlesim_params.yaml
```

`dump` 把节点参数写入文件，`load` 从文件把参数加载到节点。这一对命令常用于保存可复现的实验配置，把当前参数集作为记录的一部分留存。

### 4.7 接口

```bash
ros2 interface list
ros2 interface packages
ros2 interface package sensor_msgs
ros2 interface show sensor_msgs/msg/LaserScan
ros2 interface proto sensor_msgs/msg/LaserScan
```

`list` 输出所有可用接口，`packages` 输出包含接口消息的功能包，`package` 输出指定功能包下的接口，`show` 输出接口定义格式，`proto` 输出接口消息原型。读取 `show` 的输出是理解话题内容最快的方式，字段含义、单位与嵌套结构都在其中。

### 4.8 功能包与记录回放

```bash
ros2 pkg list
ros2 pkg executables rqt_gui
ros2 pkg prefix turn_on_wheeltec_robot
ros2 pkg xml turn_on_wheeltec_robot
```

`pkg list` 输出可用功能包，`pkg executables` 输出指定包的可执行文件列表，`pkg prefix` 输出包的安装前缀路径，`pkg xml` 输出包的清单内容。`ros2 pkg executables` 与 `ros2 run` 配合使用，可以在不读源码的情况下确认节点名称。

```bash
ros2 bag record /scan
ros2 bag info rosbag2_lidar
ros2 bag play rosbag2_lidar
```

`ros2 bag` 用于记录与回放机器人数据，详细用法见第 5 节。这三条命令分别完成录制、查看元信息与回放。

## 5. rqt、rviz2 与 ros2 bag

### 5.1 rqt

```bash
rqt
ros2 run rqt_gui rqt_gui
```

两种方式都可以启动 rqt。rqt 是基于 Qt 的图形界面框架，以插件形式提供节点图、话题监视、图像查看与 TF 树等工具。启动后通过 plugins 菜单添加所需插件。

教程列出的四个常用插件与用途：

| 插件 | 用途 |
| ---- | ---- |
| Node Graph | 查看节点之间的连接关系，等价于图形化的计算图 |
| Topic Monitor | 监视话题的发布状态与数据 |
| Image View | 查看图像话题的数据流 |
| TF Tree | 查看坐标变换树的连接与完整性 |

### 5.2 rviz2

```bash
rviz2
```

`rviz2` 是 ROS2 的数据可视化工具，用于把抽象数据以图形方式呈现。教程中的典型用法包括：

- 添加 LaserScan 显示组件，选择 `/scan` 话题，把 Fixed Frame 设为 `laser`，即可看到雷达点云。Fixed Frame 定义可视化数据的基准坐标系，雷达数据会按照它做坐标转换。
- 添加 Imu 显示组件，选择 `/imu/data_raw` 话题，观察姿态数据。厂商教程说明镜像中默认已安装该插件，若没有找到，可按发行版安装：

```bash
sudo apt install ros-humble-rviz-imu-plugin
```

在 rviz2 中看不到数据时，最常见的原因是 Fixed Frame 选错、坐标变换缺失或话题尚未发布。排查顺序是先确认 `ros2 topic list` 中存在目标话题，再用 `ros2 topic echo` 确认有数据，最后检查 TF 树是否包含所需坐标系。

### 5.3 ros2 bag

教程给出的 bag 子命令包括 `record`、`info`、`play`、`convert`、`list` 与 `reindex`。

```bash
ros2 bag record /topic-name
ros2 bag record topic-name1 topic-name2
ros2 bag record -a
```

第一行记录单个话题，第二行记录多个话题，第三行记录所有话题。录制话题较多时数据量增长很快，建议先明确需要记录的话题集合，并预先确认磁盘剩余空间。

```bash
ros2 bag info rosbag2_lidar
ros2 bag play rosbag2_lidar
ros2 bag play rosbag2_lidar -r 10
ros2 bag play rosbag2_lidar -l
ros2 bag play rosbag2_lidar --topics /topic-name
```

`info` 输出 bag 文件的相关信息，`play` 回放数据，`-r 10` 表示 10 倍速播放，`-l` 表示循环播放，`--topics` 指定只回放某个话题。回放期间可以用 `ros2 topic echo /topic-name` 验证数据是否按预期出现。

bag 文件属于实验记录，可能包含传感器原始数据。是否纳入版本控制取决于数据规模与隐私边界；本教材建议把 bag 文件留在本地产物目录，只在必要时保留小型、脱敏的样例。

## 6. Python launch 文件与参数传递

### 6.1 launch 文件的定位

一个机器人系统往往需要同时启动多个节点，并且每个节点都要按场景配置参数。launch 文件通过统一的语法与规则组织这些启动动作。教程指出 ROS2 的 launch 文件支持 xml、yaml 与 python 三种格式，其中 Python 格式可以借助语言特性实现条件判断、参数化配置与更复杂的启动逻辑。

launch 文件的基本结构是把每个节点、文件或脚本抽象成一个 action，再由 `LaunchDescription` 统一返回：

```python
def generate_launch_description():
    return LaunchDescription([
        action_1,
        action_2,
        # ...
        action_n
    ])
```

`generate_launch_description` 是启动器的入口函数，返回值是所有待执行动作的描述列表。缺少这个函数名或返回值类型不正确，`ros2 launch` 会直接报错。

### 6.2 组合节点与包含其他 launch 文件

教程给出的示例展示了包含子 launch 文件与启动节点的组合方式：

```python
import os
from pathlib import Path
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
import launch_ros.actions

def generate_launch_description():
    launch_dir = os.path.join(
        get_package_share_directory('turn_on_wheeltec_robot'),
        'launch'
    )
    imu_config = Path(
        get_package_share_directory('turn_on_wheeltec_robot'),
        'config', 'imu.yaml'
    )

    wheeltec_robot = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(launch_dir, 'base_serial.launch.py')),
        launch_arguments={'akmcar': 'false'}.items(),
    )

    imu_filter_node = launch_ros.actions.Node(
        package='imu_filter_madgwick',
        executable='imu_filter_madgwick_node',
        parameters=[imu_config]
    )

    return LaunchDescription([
        wheeltec_robot,
        imu_filter_node
    ])
```

`get_package_share_directory` 取得功能包安装后的共享目录路径，`IncludeLaunchDescription` 复用已有 launch 文件，`launch_arguments` 把参数传给被包含的文件，`launch_ros.actions.Node` 启动单个节点，`parameters=[imu_config]` 从 YAML 文件加载节点参数。用参数文件而不是把数值写死在代码里，可以让同一份代码适配不同车型与传感器。

### 6.3 真实底盘启动文件的结构

厂商的 `turn_on_wheeltec_robot.launch.py` 是这一模式的实际应用，其结构可以概括为：

```python
carto_slam = LaunchConfiguration('carto_slam', default='false')
carto_slam_dec = DeclareLaunchArgument('carto_slam', default_value='false')

wheeltec_robot = IncludeLaunchDescription(
    PythonLaunchDescriptionSource(os.path.join(launch_dir, 'base_serial.launch.py')),
    launch_arguments={'akmcar': 'false'}.items(),
)

robot_ekf = IncludeLaunchDescription(
    PythonLaunchDescriptionSource(os.path.join(launch_dir, 'wheeltec_ekf.launch.py')),
    launch_arguments={'carto_slam': carto_slam}.items(),
)

base_to_link = launch_ros.actions.Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    name='base_to_link',
    arguments=['0', '0', '0', '0', '0', '0', 'base_footprint', 'base_link'],
)

ld = LaunchDescription()
ld.add_action(carto_slam_dec)
ld.add_action(wheeltec_robot)
ld.add_action(base_to_link)
ld.add_action(robot_ekf)
return ld
```

`DeclareLaunchArgument` 声明一个可以在命令行覆盖的启动参数，`LaunchConfiguration` 读取该参数的当前值。`carto_slam` 因此成为一个开关：默认取 `false`，使用 Cartographer 的功能包可以把它改成 `true`，被包含的 EKF launch 文件据此选择不同的参数文件。

这种参数回传写法在阅读大型启动文件时很常见，理解 `DeclareLaunchArgument`、`LaunchConfiguration` 与 `launch_arguments` 三者关系，就能沿着启动链条找到最终生效的配置。

```bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py carto_slam:=true
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py --show-args
```

命令行使用 `参数名:=值` 的形式覆盖启动参数，`--show-args` 列出该 launch 文件接受的参数及默认值。修改参数前先查看参数清单，可以避免拼写错误导致的静默失效。

### 6.4 参数传递的三种方式

- YAML 参数文件：把参数集中写在 `config/*.yaml`，由 `parameters=[<path>]` 加载。厂商的 `imu.yaml`、`ekf.yaml`、`ekf_carto.yaml` 都属于这一类。
- 命令行覆盖：用 `参数名:=值` 覆盖 launch 参数，适合临时切换车型或功能开关。
- 启动文件内直接传字典：在 `Node` 的 `parameters` 中直接给出参数键值，适合少量、与场景强绑定的配置。

参数来源越多，越需要在启动日志中确认最终生效值。ROS2 节点在参数不符时会给出警告或使用默认值，这两类信息都应当保留在日志中。

## 7. TF2 坐标变换与 URDF

### 7.1 TF2 的作用

移动机器人上的传感器与执行器分布在不同位置，测量数据必须转换到统一坐标系才能组合使用。TF2 提供管理坐标系相对关系与查询变换的工具。

完整的坐标变换由广播方与监听方两部分组成：每个广播方发布一组坐标系相对关系，监听方把多组关系融合成一棵坐标树。ROS 中的坐标变换基于右手坐标系，变换关系可以分解为平移与旋转两部分。

### 7.2 静态坐标变换

两个坐标系相对位置固定时使用静态变换，例如雷达与 `base_link` 之间的安装位置。教程给出的命令行用法：

```bash
# 发布 A 到 B 的位姿
# 格式：ros2 run tf2_ros static_transform_publisher <x偏移> <y偏移> <z偏移> <偏航角> <俯仰角> <横滚角> <父坐标系> <子坐标系>
ros2 run tf2_ros static_transform_publisher 0 0 3 0 0 3.14 A B

# 监听/获取 TF 关系
ros2 run tf2_ros tf2_echo A B
```

`static_transform_publisher` 按固定顺序接收平移、旋转与父子坐标系参数，`tf2_echo` 打印两个坐标系之间的实时变换。参数顺序写错会得到看起来“有数据但方向不对”的结果，因此修改安装参数后应同时用 `tf2_echo` 与 rviz2 交叉确认。

教程给出的 launch 写法把雷达安装位置写成静态变换：

```python
launch_ros.actions.Node(
    package='tf2_ros',
    executable='static_transform_publisher',
    name='base_to_laser',
    arguments=['0.048', '0', '0.18', '0', '0', '0', 'base_footprint', 'laser'],
)
```

这组数值表示雷达相对 `base_footprint` 的 x、y、z 偏移与零旋转。不同车型的安装位置不同，厂商的 `robot_mode_description.launch.py` 为每种车型分别给出激光雷达与相机的平移参数。

### 7.3 坐标树与可视化

```bash
ros2 run tf2_tools view_frames
rqt
```

`view_frames` 生成坐标树的报告文件，rqt 的 TF Tree 插件以图形方式显示同样的结构。检查坐标树时关注三件事：是否只有一个根坐标系、每个子坐标系是否只有一条到根的路径、传感器坐标系是否都已挂接到机器人本体。

### 7.4 URDF 模型描述

URDF 用 XML 描述机器人的连杆（link）与关节（joint）。教程把小车简化为车体、左右轮子、激光雷达与相机、计算平台等部分，建模过程就是把这些部分逐一描述并组合起来。

连杆描述刚体的外观与物理属性：

```xml
<link name="left_wheel_link">
    <visual>
        <origin xyz="0 0 0" rpy="0 0 0" />
        <geometry>
            <cylinder radius="0.0325" length="0.025"/>
        </geometry>
        <material name="black">
            <color rgba="0 0 0 1"/>
        </material>
    </visual>
</link>
```

`origin` 设置连杆的偏移与旋转，`geometry` 设置形状，示例使用圆柱体表示车轮，`material` 设置视觉颜色。`name` 必须在文件中唯一，否则模型树无法正确解析。

关节描述两个刚体之间的连接方式：

```xml
<joint name="left_wheel_joint" type="continuous">
    <origin xyz="0 0.08 0.0325" rpy="1.57 0 0"/>
    <parent link="base_link"/>
    <child link="left_wheel_link"/>
    <axis xyz="0 1 0"/>
</joint>
```

`parent` 与 `child` 定义连接方向，`origin` 描述子连杆相对父连杆的偏移与旋转，`axis` 给出关节运动轴的单位向量。关节类型共有六种，常见的有 `continuous`（连续旋转）、`revolute`（有角度限制的旋转）、`prismatic`（滑动）、`fixed`（固定）与 `planar`、`floating`。选择错误会让模型在可视化中表现异常，或在运动学计算中给出与实际不符的自由度。

所有 `link` 与 `joint` 最终放在一个 `robot` 标签内，构成完整的机器人模型。模型文件由 `robot_state_publisher` 读取并发布到 TF 树，关节状态由 `joint_state_publisher` 提供，厂商的 `robot_mode_description.launch.py` 中可以看到这两个节点的配置方式。

### 7.5 替换 URDF 模型的流程

教程给出的模型替换流程可以归纳为四步：

1. 把导出模型包中的 `meshes` 目录复制到 `wheeltec_robot_urdf` 功能包的 `meshes` 目录，并按车型重命名。
2. 把导出的 `urdf` 文件复制到该功能包的 `urdf` 目录。
3. 打开 urdf 文件，把所有网格文件路径替换为新车型的 `meshes` 目录路径。
4. 在 `robot_mode_description.launch.py` 中新增该车型的配置块，车型名与 urdf 名保持一致，并按实测调整坐标参数，然后单独编译相关功能包。

模型替换后最常见的两个问题是相机模型脱离车体、车体部分出现在地面以下。教程给出的处理方式是：修改 `turn_on_wheeltec_robot.launch.py` 中 `base_footprint` 的 z 坐标调整整体高度；修改 `robot_mode_description.launch.py` 中对应车型的 xyz 参数调整相机与雷达位置；若仍有偏移，再检查父坐标系应使用 `base_footprint` 还是 `base_link`。

单独编译相关功能包时使用：

```bash
colcon build --symlink-install --packages-select wheeltec_robot_urdf
colcon build --symlink-install --packages-select turn_on_wheeltec_robot
```

模型文件与启动文件分属两个功能包，改动哪个就重新编译哪个。使用 `--symlink-install` 时，仅修改 launch 文件或 YAML 参数可以省去重新编译步骤。

## 8. 相机、雷达、IMU 与串口底盘接口

本节整理厂商教程中出现的话题与接口。这些接口属于 WHEELTEC 平台的 `documented` 配置，本教材没有在昇腾 310B 上运行过这些节点。

### 8.1 接口总览

| 接口 | 消息类型 | 方向 | 说明 | 来源 |
| ---- | -------- | ---- | ---- | ---- |
| `/odom` | `nav_msgs/msg/Odometry` | 底盘节点发布 | 里程计位姿与速度 | 底盘数据教程 |
| `/imu/data_raw` | `sensor_msgs/msg/Imu` | 底盘节点发布 | 板载 IMU 原始数据 | 底盘数据教程 |
| `/scan` | `sensor_msgs/msg/LaserScan` | 雷达驱动发布 | 单线激光雷达扫描数据 | 雷达数据教程 |
| `/cmd_vel` | `geometry_msgs/msg/Twist` | 上层节点发布 | 目标线速度与角速度 | STM32 通信文档 |
| `/red_vel` | 速度消息 | 上层节点发布 | 红外对接速度 | STM32 通信文档 |
| `/robot_recharge_flag` | 标志消息 | 上层节点发布 | 回充命令标志 | STM32 通信文档 |

电池电压、超声波与自动回充的其余话题在文档中以功能描述出现而未给出完整话题名，使用时以 `ros2 topic list` 的实际输出为准。

### 8.2 激光雷达

雷达驱动的统一输出接口是 `sensor_msgs/msg/LaserScan`。厂商教程给出的确认流程是：先启动雷达，再读取话题，最后用 rviz2 可视化。

```bash
ros2 launch turn_on_wheeltec_robot wheeltec_lidar.launch.py
ros2 topic echo /scan
rviz2
```

第一条命令启动雷达驱动，第二条读取扫描数据，第三条启动可视化工具。在 rviz2 中添加 LaserScan 显示组件并选择 `/scan` 话题，把 Fixed Frame 设为 `laser`，即可观察到点云。

雷达型号在 `wheeltec_param.yaml` 中选择；较旧版本代码的型号设置位置可能在 `wheeltec_lidar.launch.py`。型号配置错误时驱动可能启动但不发布数据，因此启动后必须用话题输出确认，而不能只看终端是否停止报错。

### 8.3 IMU

```bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py
ros2 topic echo /imu/data_raw
```

底盘节点启动后会持续发布 IMU 数据。厂商教程说明镜像中已包含 rviz 的 IMU 可视化插件，安装命令见第 5.2 节。

底盘节点同时运行 `Quaternion_Solution.cpp` 中的轻量级姿态解算模块，把加速度计与陀螺仪数据融合为四元数姿态。上层若需要滤波后的姿态，厂商的启动文件会额外运行 `imu_filter_madgwick` 节点并加载 `config/imu.yaml`。

### 8.4 串口底盘

ROS 主控通过 USB 与 STM32 控制器通信，设备在系统中会被识别为 `/dev/ttyCH343USB0` 或 `/dev/ttyUSB0` 等名称。教程指出这些名称在每次连接时可能变化，且默认可能没有读写权限，因此使用固定的 udev 别名单一入口。

| 传感器 | 设备号 | 别名 |
| ------ | ------ | ---- |
| 串口版雷达 | 0001 | `/dev/wheeltec_lidar` |
| STM32 控制器 | 0002 | `/dev/wheeltec_controller` |
| 惯导 IMU | 0003 | `/dev/wheeltec_IMU` |
| 语音传感器 | 0004 | `/dev/wheeltec_mic` |
| GNSS 接收机 | 0005 | `/dev/wheeltec_gnss` |

别名脚本存放在 `turn_on_wheeltec_robot` 功能包的 `wheeltec_udev.sh` 文件中：

```bash
sudo chmod 777 wheeltec_udev.sh
sudo sh wheeltec_udev.sh
```

执行后重新插拔设备，再用 `ll /dev` 确认别名出现。此后无论接入哪个 USB 接口，代码都可以使用固定别名访问设备。注意 `chmod 777` 只用于厂商脚本的首次赋权，长期使用应改为更严格的权限设置。

底盘节点 `wheeltec_robot_node` 的数据流如下：

- 通过串口接收 STM32 上报的编码器、IMU、超声波与电池电压数据，按 24 字节数据帧接收并校验帧尾与校验和，解析后转换为国际单位。
- 把速度积分得到位姿，发布里程计、IMU 与电池电压话题。
- 订阅 `/cmd_vel`，把目标速度换算为整数并按协议打包成 11 字节发送帧，计算校验值后写入固定位置，帧尾为 `0x7D`。
- 节点退出时在析构函数中向串口发送一帧目标速度为零的指令，再关闭串口。

退出时自动下发零速度是重要安全设计。上层程序异常退出后如果仍保持旧速度，底盘会继续运动，因此集成新节点时应保留这条停止路径，并避免用强制结束方式绕过析构。

```bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py
ros2 node info /wheeltec_robot
```

第二条命令可以一次性确认该节点发布与订阅的话题列表及其消息类型，是核对底盘接口最直接的方式。

### 8.5 相机

厂商教程把相机作为可选的传感器功能包启动，`turn_on_wheeltec_robot` 功能包中包含 `wheeltec_camera.launch.py`，并为 Astra 相机提供 `config/camera_info.yaml` 内参文件。

```bash
ros2 launch turn_on_wheeltec_robot wheeltec_camera.launch.py
ros2 topic list
```

相机驱动通常以 `sensor_msgs/msg/Image` 发布图像、以 `sensor_msgs/msg/CameraInfo` 发布内参，订阅方需要同时使用两者才能完成几何还原。具体话题名取决于驱动实现，以上命令中的 `ros2 topic list` 输出才是最终依据。使用 `ros2 topic info` 与 `ros2 interface show` 可以进一步确认消息类型与字段。

相机内参文件与图像话题的对应关系必须在集成时核对。用错内参会让测距、跟随与视觉伺服出现系统性偏差，而且这种偏差在画面上通常不易察觉。

## 9. SSH 与 VNC 远程操作 ROS2

### 9.1 何时使用 SSH

SSH 适合命令行操作：编译功能包、启动 launch 文件、读取话题、查看节点信息、观察日志。它的带宽占用低、可脚本化、输出可以完整复制到记录中，是远程调试机器人的默认选择。

```bash
ssh -Y wheeltec@192.168.0.100
```

`-Y` 启用可信 X11 转发，使远程的图形程序可以显示在本机。转发 rviz2 与 rqt 的延迟通常明显高于本地运行，因此只有在无法直接使用 VNC 或只做少量图形检查时才值得使用。若本机没有运行 X 服务，图形程序无法启动，但命令行部分不受影响。

```bash
ping 192.168.0.100
scp local_file wheeltec@192.168.0.100:~/
```

第一条命令确认网络可达，第二条把本地文件复制到远端用户目录。厂商教程说明小车的默认 IP 为 `192.168.0.100`，默认热点名称包含 WHEELTEC 字样；热点密码以随车资料为准，首次使用时应当修改。修改网络配置后 IP 可能变化，连接前先确认当前地址。

### 9.2 何时使用 VNC

VNC 适合需要完整桌面环境的场景：持续观察 rviz2 的点云与模型、使用 rqt 的图形插件、比对窗口中的模型与真实机器人。它以图像传输为主，带宽占用高于 SSH，刷新率与延迟受网络质量影响明显。

```bash
vncserver :1 -geometry 1280x720 -depth 24
vncviewer 192.168.0.100:1
```

第一条命令在远端启动显示号为 1 的 VNC 会话，第二条在本机连接该会话。VNC 服务端实现取决于板端安装（TigerVNC、x11vnc 等），参数与启动方式可能不同，使用前应确认远端已安装并配置对应服务。厂商教程中远程登录以 SSH 为主，VNC 属于需要时启用的补充手段。

### 9.3 选择建议

| 任务 | 推荐方式 | 原因 |
| ---- | -------- | ---- |
| 编译功能包、查看构建日志 | SSH | 输出为文本，可复制进记录，带宽占用低 |
| `ros2 topic echo`、`ros2 param` 调试 | SSH | 命令行工具即可完成，无需图形界面 |
| 启动 launch 并观察终端输出 | SSH | 可以直接看到节点错误与警告 |
| 长时间观察 rviz2 点云或模型 | VNC | 图像持续刷新，X11 转发的延迟难以接受 |
| rqt 插件组合调试 | VNC | 多窗口交互在完整桌面下更可靠 |
| 批量文件同步 | SSH 与 `scp` | 可以配合校验与定向复制 |

两种方式可以同时使用：用 SSH 启动节点并读取日志，用 VNC 观察图形结果。混用时注意不要在两侧重复启动同一节点，重复的节点名会让话题出现多个发布方，从而给出难以解释的数据。

### 9.4 远程操作的安全边界

- 厂商默认账号与默认密码应在首次部署后修改，不要在公开文档中留存口令。
- 只在受控网络内开放 SSH 与 VNC 端口，避免直接暴露到公网。
- 远程发送底盘速度指令前确认车体周围无人，并保持一个可用的停止终端。
- 远程会话断开不等于节点停止。断开前用 `Ctrl+C` 有序结束节点，或确认已配置的服务状态。

## 10. 多机通信与 ROS_DOMAIN_ID

### 10.1 域号与发现范围

ROS2 节点通过 DDS 在同一域内相互发现。`ROS_DOMAIN_ID` 相同的节点才会参与同一发现范围，不同域之间默认互不可见。这为多组实验提供了隔离手段：同一网络中可以并行运行若干互不干扰的 ROS2 系统。

```bash
export ROS_DOMAIN_ID=42
echo "export ROS_DOMAIN_ID=42" >> ~/.bashrc
```

第一条命令为当前终端设置域号，第二条写入 `~/.bashrc` 使其对新终端生效。参与通信的所有机器与终端必须使用相同域号。官方文档建议在常用范围内选择域号并避免冲突；本教材建议把域号写入实验记录，避免复现时遗漏导致“节点互相看不见”。

```bash
ros2 daemon stop
ros2 daemon start
```

修改域号或网络配置后，先停止再启动 daemon 可以避免旧发现信息残留。daemon 只服务于命令行工具的查询，不参与节点之间的业务通信。

### 10.2 同网段与网络检查

多机通信通常要求主机与机器人在同一子网，且网络允许组播。跨网段、NAT、禁用组播的无线网络都会影响发现过程。

```bash
ip addr
ip route
ping 192.168.0.100
```

`ip addr` 查看本机地址与子网掩码，`ip route` 查看路由，`ping` 确认基础可达性。三者的组合可以判断两台机器是否真正处于同一网段，而不只是“看起来在同一个网络里”。

```bash
echo $ROS_DOMAIN_ID
echo $ROS_LOCALHOST_ONLY
echo $RMW_IMPLEMENTATION
```

第一条确认当前域号，第二条确认是否被限制在本地回环，第三条确认当前使用的 DDS 实现。`ROS_LOCALHOST_ONLY=1` 会把通信限制在本机，跨机调试时它必须为未设置或 `0`。两台机器上的 `RMW_IMPLEMENTATION` 应保持一致，混用不同实现会带来发现与类型支持差异。

```bash
ros2 node list
ros2 topic list
ros2 topic info /scan
```

在多机场景中，这三条命令是最直接的验证方式：如果本机能看到对方的节点与话题，说明域号、网络与发现配置一致；如果只看到本机节点，问题在发现层而不是话题内容层。

```bash
ros2 doctor --report
```

`ros2 doctor` 汇总环境变量、网络与中间件的检查结果，可作为多机排障的起点。它是 ROS2 官方工具，报告的条目较多，应按“域号与本地限制、网络地址、中间件实现”的顺序阅读。

### 10.3 常见发现失败原因

- 两台机器域号不同，或其中一台把域号写进了不同的 shell 配置。
- 网络使用 NAT 或客户端隔离，组播无法到达对端。
- 防火墙阻止了组播与 DDS 使用的端口。
- 主机名无法解析，发现过程无法完成地址交换。
- 一端启用了本地回环限制。
- 两端使用不同的 DDS 实现或类型支持配置。

发现失败时的处理顺序是先确认域号与环境变量，再确认网段与组播，最后才考虑配置初始对等点或发现服务器。基础网络问题没有排除之前，改中间件参数通常只会增加变量。

## 11. 自启动、日志与进程管理

### 11.1 日志位置

ROS2 的日志分为三层：

- 构建日志：`colcon build` 的输出，工作空间的 `log` 目录中按功能包与时间保存。
- 节点运行日志：默认写入 `~/.ros/log`，可通过环境变量覆盖到指定目录。
- 终端输出：launch 启动的节点会把警告与错误打印到终端，这是排障时最先查看的位置。

```bash
ls -lt ~/.ros/log | head
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py --ros-args --log-level info
```

第一条命令列出最近的运行日志目录。第二条为通过 launch 启动的节点设置日志级别，出现异常时可以临时提高到 `debug` 获取更详细的信息。日志级别是诊断手段，不应长期使用 `debug`，否则会掩盖真正的错误信息。

### 11.2 进程查看与有序停止

```bash
pgrep -af ros2
pgrep -af wheeltec_robot
ps -ef | grep ros2
```

`pgrep` 按名称查找进程并显示完整命令行，`ps` 与 `grep` 的组合用于交叉确认。停止节点前必须先确认目标 PID 属于本次启动的进程，避免误停其他会话中的节点。

```bash
kill <PID>
```

终止单个进程时优先使用 `kill` 发送默认信号，让节点执行回调与析构流程。底盘节点在析构函数中会下发零速度指令，强制结束会跳过这一步，因此只有在进程无响应时才考虑更强的方式，并在之后确认底盘已停止。

```bash
ss -ltnp
```

这条命令列出本机监听端口及其进程。机器人上常同时运行 ROS2 节点、Web 服务与调试工具，确认端口占用可以避免不同服务相互冲突。

### 11.3 自启动方案

ROS2 节点本身没有内建开机自启机制，需要借助系统服务或桌面会话配置。推荐使用 systemd 服务，因为它可以定义依赖、重启策略与日志去处。下面是一个模板，属于 proposed 方案，需要在目标平台上实际验证后再启用：

```ini
[Unit]
Description=WHEELTEC ROS2 bringup
After=network-online.target

[Service]
Type=simple
User=wheeltec
ExecStart=/bin/bash -lc 'source /opt/ros/humble/setup.bash && source /home/wheeltec/wheeltec_ros2/install/setup.bash && ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py'
Restart=on-failure
RestartSec=5
Environment=ROS_DOMAIN_ID=42

[Install]
WantedBy=multi-user.target
```

模板中的关键点有四个：`ExecStart` 用登录 shell 加载 ROS2 与工作空间环境；`User` 指定运行账号；`ROS_DOMAIN_ID` 显式声明域号；`Restart` 只对异常退出重启，避免正常停止后又被拉起。保存为服务单元后可用下列命令启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wheeltec-ros2.service
systemctl status wheeltec-ros2.service
journalctl -u wheeltec-ros2.service -n 100
```

前两条命令重新加载单元并启用服务，后两条查看服务状态与最近日志。启用前必须确认串口设备别名已经建立、网络已就绪、启动文件不需要交互输入，否则服务会在开机后反复失败。

桌面会话下的自启动可以写入 `~/.config/autostart`，适合需要图形界面的场景，例如开机后自动启动 rviz2。无论采用哪种方式，都应保证同一节点只被启动一次，多个实例会产生重复发布方，给数据解释带来困难。

### 11.4 运行状态检查清单

- 服务或终端中确认 launch 已启动且没有报错。
- `ros2 node list` 中节点数量与预期一致。
- `ros2 topic echo` 目标话题有数据。
- 出现异常时先读日志，再考虑重启。
- 重启前确认旧进程已经退出，避免遗留进程占用串口。

## 12. proposed / untested：ROS2 与昇腾 310B 推理服务的集成路径

> **证据标记：proposed + untested。** 本节描述的是设计路径，不是已验证方案。本教材没有在昇腾 310B 上运行过 ROS2，也没有把本仓库的推理代码接入任何 ROS2 节点。文中不包含性能、精度与延迟结论；这些数据必须在板端按完整协议测量并留存日志后才能给出。

### 12.1 目标形态

集成目标可以用一条数据流概括：

```text
ROS2 传感器节点  -->  图像或点云话题  -->  推理适配节点  -->  结果话题 / 服务
                                              |
                                              +--> 现有 OM + ACL 运行时
```

推理适配节点承担三件事：订阅标准传感器话题、把消息转换为现有运行时接受的输入、把推理结果发布回 ROS2。现有案例的推理代码与其 HTTP 接口保持不变，ROS2 只作为机器人侧的组织方式接入。

### 12.2 前置条件

以下每一条都是未验证前提，必须在板端逐项确认：

- 板端系统发行版与所选 ROS2 发行版的官方支持列表匹配，且 Python 版本与运行时依赖不冲突。
- 板端可以正常加载 CANN 环境并完成一次既有模型的推理，作为接入前的基线。
- 传感器驱动能够在 ROS2 中发布标准消息类型。
- 串口设备别名、权限与 udev 规则已经生效。
- 计算资源余量足以同时运行传感器驱动、推理进程与上层节点，这一点需要实测而不能估算。

### 12.3 建议的实现步骤

1. 建立独立工作空间，避免与案例目录混放。

```bash
mkdir -p ~/ros2_npu_ws/src
cd ~/ros2_npu_ws
colcon build
source install/setup.bash
```

工作空间独立后，ROS2 的构建产物与案例中的 Python 环境互不干扰；`install/setup.bash` 需要为每个新终端加载。

2. 用一个最小节点验证运行时可达性，只做订阅与日志输出，不加载模型。这一步把 ROS2 环境问题与模型问题分开。

3. 增加模型加载，复用现有 OM 与 ACL 执行路径，进程启动时完成一次预热推理，避免在每帧回调中重复初始化。

4. 把转换与推理放在明确的边界内：图像消息先转为数组，按既有输入契约完成预处理，输出按既有后处理逻辑转换为 ROS2 消息。不要在这一层发明新的数值约定。

5. 用 launch 文件组合传感器驱动、适配节点与上层节点，模型路径、输入尺寸与精度作为参数传入，便于复现与切换。

```python
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    model_path = LaunchConfiguration('model_path')
    return LaunchDescription([
        DeclareLaunchArgument('model_path', default_value=''),
        Node(
            package='npu_bridge',
            executable='inference_node',
            parameters=[{'model_path': model_path}],
        ),
    ])
```

这段启动文件把模型路径参数化，便于在同一节点上比较不同模型，而不需要修改代码。参数留空时必须由节点给出明确错误，避免加载到错误模型后静默运行。

6. 结果消息应携带模型标识与输入来源，便于后续核对每个输出对应哪个模型与哪一帧输入。缺少这些字段会让记录无法回溯。

### 12.4 风险与失败模式

| 风险 | 表现 | 建议处理 |
| ---- | ---- | -------- |
| Python 版本或依赖冲突 | 导入失败、ABI 不匹配 | 先在隔离环境中确认依赖组合，再接入 ROS2 |
| 中间件与网络配置冲突 | 节点互相不可见 | 按第 10 节顺序检查域号、网络与本地限制 |
| 串口权限或别名缺失 | 底盘节点启动失败 | 检查 udev 规则与设备别名 |
| 回调阻塞 | 话题频率下降、延迟累积 | 把推理放在独立线程或进程，并限制队列长度 |
| 内存增长 | 长时间运行后分配失败 | 复用缓冲，避免在每帧创建大对象 |
| 环境变量缺失 | 健康检查通过但推理失败 | 在启动脚本中显式加载 CANN 与工作空间环境 |
| 时间戳不一致 | 多传感器数据难以对齐 | 统一时间源并检查消息头的时间戳 |

任何一项在板端复现失败，都应记录为对应层的失败，而不是直接推导为“ROS2 不可用”或“NPU 不支持”。分层记录是本书既有证据规则在 ROS2 场景下的延续。

### 12.5 验收门槛

集成路径只有在下列门槛全部通过后，才能从 untested 升级为已验证：

- 板端完成 ROS2 环境导入与节点启动，日志完整保存。
- 适配节点在真实传感器数据下连续运行，无未处理异常。
- 推理输出与离线基线在同一输入上的数值对比通过，对比协议与允许误差记录在案。
- 进程退出时串口停止指令正常下发，机器人可靠停止。
- 断开与重连传感器后，节点行为符合预期。
- 所有结论绑定模型版本、精度、输入契约与日志路径。

## 13. 课堂练习与验收标准

### 13.1 练习任务

1. 工作空间与功能包：创建 `~/ros2_ws/src`，新建一个 Python 功能包与一个 C++ 功能包，执行 `colcon build`，并确认 `ros2 pkg list` 能列出它们。
2. 话题通信：实现发布方与订阅方节点，用 `ros2 run` 分别启动，用 `ros2 topic echo` 观察消息内容，并记录 `ros2 topic info` 的发布方与订阅方数量。
3. 服务与动作：用海龟仿真器练习 `ros2 service call` 与 `ros2 action send_goal`，说明两者在调用形式与反馈方式上的差别。
4. 参数：读取并修改一个节点的参数，用 `ros2 param dump` 保存参数文件，再用 `ros2 param load` 恢复，比较前后状态。
5. 数据记录：录制一个话题的 bag 文件，用 `ros2 bag info` 查看元信息，再回放并验证数据可用。
6. launch 文件：编写一个包含两个节点并传入参数的 Python launch 文件，用 `--show-args` 查看参数列表。
7. 坐标变换：发布一个静态变换，用 `tf2_echo` 验证结果，再在 rviz2 中观察。
8. URDF 检查：在一份 URDF 中找出所有 `link` 与 `joint`，说明关节类型的选择依据；修改一个坐标参数并观察可视化结果。
9. 接口核对：对一个实际运行的话题，用 `ros2 topic type` 与 `ros2 interface show` 输出其完整字段，并解释每个字段的含义。
10. 远程操作：分别用 SSH 与 VNC 完成一次远程任务，记录两种方式在延迟、可读性和操作便利性上的差异。
11. 多机通信：在两台机器上设置相同的域号，验证节点可见性；再改成不同域号，记录观察结果。
12. 集成设计：为第 12 节的集成路径写一份设计说明，明确节点职责、话题接口、参数与验收门槛。该设计不要求运行，但必须标注为未验证方案。

### 13.2 验收标准

| 项目 | 验收信号 | 证据形式 | 未通过时的处理 |
| ---- | -------- | -------- | -------------- |
| 工作空间 | `colcon build` 成功，`source install/setup.bash` 后功能包可被检索 | 终端输出记录 | 检查目录结构与依赖声明 |
| 话题通信 | 订阅方收到发布方消息，主题名与类型一致 | 两个终端的输出 | 检查话题名、消息类型与域号 |
| 服务调用 | 服务调用返回结果，字段符合接口定义 | 调用命令与返回值 | 检查接口类型与请求字段 |
| 参数操作 | 参数修改生效并可保存恢复 | 参数文件与前后取值 | 检查参数名与节点是否支持运行期修改 |
| bag 记录 | 录制文件可查看、可回放 | `ros2 bag info` 输出 | 检查录制话题与磁盘空间 |
| launch 文件 | 一条命令启动多个节点，参数按预期生效 | 启动日志与参数列表 | 检查 action 组织与参数传递路径 |
| 坐标变换 | `tf2_echo` 输出与设计参数一致，坐标树完整 | 命令输出与坐标树报告 | 检查父子坐标系与参数顺序 |
| 底盘接口 | 节点信息中的话题与文档一致，速度指令可被安全停止 | `ros2 node info` 输出 | 检查串口别名、权限与协议版本 |
| 远程操作 | SSH 与 VNC 均能完成各自适用任务 | 操作记录与说明 | 检查网络、认证与服务配置 |
| 多机通信 | 同域可见、异域不可见，结论可重复 | 两次对照记录 | 检查域号、网段与本地限制 |
| 集成设计 | 设计标注为未验证，字段与门槛完整 | 设计文档 | 补齐接口定义或证据边界 |

验收时只记录实际观察到的结果。命令没有执行就不要填写为通过，输出没有留存就不要写成结论。ROS2 相关的板端集成结论必须同时说明运行平台、模型版本、接口类型与日志路径，缺一项都只能作为待验证记录。
