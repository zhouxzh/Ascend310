# CPU频率测量实验

## 状态与边界

CPU频率测量目前是**可选工程实验**，不是`ascend310b-debug`注册的工具，也不进入`ascend_probe`、系统信息刷新或面板启动流程。

只有在明确需要比较香橙派AI Pro超频版与公版、排查降频问题或核对固定频率时，才执行一次短时测量。普通板端调试仍使用`ascend_probe`读取SoC、NPU、CANN、驱动和外设事实。

这项测量给出的是**CPU在短时满载期间的实测运行频率**，不是NPU频率，也不是空闲瞬时频率。它不能替代性能测试、稳定性测试或温度压力测试。

## 当前镜像限制

课程使用的8T和20T镜像存在以下限制：

- `/sys/devices/system/cpu/cpufreq`没有注册可用的调频策略，无法直接读取`scaling_cur_freq`或`scaling_max_freq`。
- `/proc/cpuinfo`只有`BogoMIPS`，没有`CPU MHz`。`BogoMIPS`来自内核延时循环校准，不能当成CPU频率。
- `npu-smi info -t cpu-freq-up -i 0 -c 0`接口存在，但两块板都返回该设备不支持查询。
- 普通用户读取`/sys/kernel/debug/clk/clk_summary`被拒绝；使用`sudo`读取后，当前时钟树只显示`refclk=150MHz`，没有CPU核心时钟。
- 普通用户的`perf_event_open`返回`EACCES`，因此只能通过受控`sudo`执行PMU周期计数。

因此，不能用`BogoMIPS`换算频率，也不能把root读取的`refclk`误写成CPU频率。

## 测量原理

测量使用ARM PMU的CPU周期计数器和单调时钟：

```text
CPU频率估算值 = CPU周期增量 / 单调时钟时间增量
```

测量步骤：

1. 将当前线程绑定到一个CPU核心。
2. 打开当前线程的CPU周期计数器。
3. 启动计数器并记录起始周期、起始单调时间。
4. 执行固定时长的CPU密集循环。
5. 再次读取周期和时间，按上式得到估算频率。
6. 对每个核心重复测量，并记录测量期间的CPU温度。

单次测量容易受到调度、热降频和后台负载影响。工程结论至少需要3次重复，报告各次结果、中位数和最大值，而不是只保留一次最好结果。

## 参考测量代码

以下代码只适用于aarch64 Linux，使用Python3标准库和Linux`perf_event_open`系统调用。它不写文件，但会让一个CPU核心短时满载，必须显式确认后执行。

```python
import ctypes
import fcntl
import os
import struct
import time

CPU = 0
DURATION_NS = 300_000_000

attr = bytearray(128)
struct.pack_into("<IIQ", attr, 0, 0, 128, 0)  # hardware CPU cycles
struct.pack_into("<Q", attr, 40, 1)           # disabled=1
buffer = ctypes.create_string_buffer(bytes(attr))
libc = ctypes.CDLL(None, use_errno=True)

# aarch64 __NR_perf_event_open
fd = libc.syscall(241, buffer, 0, -1, -1, 0)
if fd < 0:
    raise OSError(ctypes.get_errno(), "perf_event_open")

os.sched_setaffinity(0, {CPU})
fcntl.ioctl(fd, 0x2400)  # PERF_EVENT_IOC_ENABLE

started = time.monotonic_ns()
cycles0 = struct.unpack("<Q", os.read(fd, 8))[0]
value = 0
while time.monotonic_ns() - started < DURATION_NS:
    value += 1
cycles1 = struct.unpack("<Q", os.read(fd, 8))[0]
elapsed_ns = time.monotonic_ns() - started

print({
    "cpu": CPU,
    "elapsed_ns": elapsed_ns,
    "cycles": cycles1 - cycles0,
    "estimated_mhz": round((cycles1 - cycles0) / elapsed_ns * 1000, 1),
    "loop": value,
})
os.close(fd)
```

测量代码必须以现有`sudo`配置运行。密码只写入SSH进程stdin，不写入脚本、命令行参数、日志或Git。不要为了这项实验启用root SSH账户。

## 在20T板上的观察

在课程的一块20T板上，以CPU0、300ms满载、单次测量的方式得到：

```text
elapsed_ns=300011250
cycles=478569765
estimated_mhz=1595.2
```

这只是一次`observed`样本，说明该板在该次测量中运行在约1.6GHz，不能单独证明是否超频。8T板尚未执行本次测量，应标记为`untested`。

## 超频比较方法

比较超频版和公版时，必须固定以下条件：

- 相同板型和CPU型号；
- 相同操作系统、内核、固件和散热条件；
- 相同CPU核心、负载程序、测量时长和重复次数；
- 同时记录CPU温度、是否发生降频和后台负载。

只有在相同条件下得到稳定差异，才能写“实测频率不同”。官方网页只描述“4核64位处理器”，不能替代同型号公版板的实测基线。8T与20T的结果也必须分开保存。

## 以后接入插件时的约束

如果以后把该实验接入插件，应遵守以下约束：

- 作为独立按钮或显式命令，不进入自动探测和面板启动刷新；
- CPU核心、测量时长和重复次数使用固定范围，不接受任意脚本；
- 先显示将执行的命令、核心、时长和sudo需求，再等待确认；
- 默认使用`base`环境，不把测量和NPU环境验收混在一起；
- 保存频率、周期、时间、温度、板型、核心和重复次数；
- 不将测量结果写成NPU性能或业务性能结论；
- 测量失败时报告`EACCES`、PMU不可用、热降频或调度噪声，不猜测频率。
