---
marp: true
size: 16:9
theme: ascend310
paginate: true
header: "《昇腾310B实战》教材配套演示"
footer: "附录 2：昇腾 310B Linux 操作与命令教程"
---
<!-- _class: cover -->

# 附录 2：昇腾 310B Linux 操作与命令教程

昇腾310B 教材配套演示

专题安排：3 课时，每课时 45 分钟

---

## 专题安排

| 课时 | 时长 | 主题 | 本讲重点 |
| --- | --- | --- | --- |
| 第1课时 | 45分钟 | Shell 与命令安全 | 命令四问、路径、权限、删除边界 |
| 第2课时 | 45分钟 | 文件、权限与软件 | 权限、APT、Conda、Python 路径 |
| 第3课时 | 45分钟 | CANN、服务与日志 | CANN 环境、npu-smi、ATC、进程、日志、有序停止 |

---

## 串口登录：板端的第一条可复现路径

<figure>
<img src="../appendix/img1/serial.png" alt="Orange Pi AIpro 串口登录终端">
<figcaption>Micro USB 串口登录示例，速率 115200；来源：<a href="../appendix/appendix1.md">src/appendix/appendix1.md</a>；图片：<code>src/appendix/img1/serial.png</code></figcaption>
</figure>

---

## Linux 练习对应的仓库入口

| 练习 | 源文件/目录 | 证据边界 |
| --- | --- | --- |
| 命令四问与路径安全 | <a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> | 本地命令与路径检查 |
| CANN/NPU 快速诊断 | <a href="../../samples/chapter5/check_cann.py"><code>samples/chapter5/check_cann.py</code></a> | 必须在板端运行 |
| WebRTC 设备与日志 | <a href="../../samples/chapter5/WebRTC/"><code>samples/chapter5/WebRTC/</code></a> | 摄像头/编码器板端证据 |

来源索引：<a href="../appendix/appendix2.md">附录2</a> · <a href="../appendix/appendix5.md">附录5</a> · <a href="../../samples/README.md">samples/README.md</a>

---

## 第1课时（45分钟）：Shell 与命令安全

教学目标：

- 执行命令前先回答“命令四问”
- 能确认机器、用户、shell、conda 环境和当前路径
- 能安全查看文件、目录、权限和磁盘空间
- 能说明删除操作的安全边界

45分钟安排：5分钟命令四问，15分钟路径与文件，15分钟日志与删除边界，10分钟课堂练习。

---

## 命令的四个审查问题

执行任何命令前，先回答四个问题：

1. 命令运行在哪台机器、哪个 shell 和哪个 conda 环境中？
2. 输入文件、输出目录和日志是否在预期的绝对路径内？
3. 这一步证明的是语法、路由、转换、数值一致性、性能，还是硬件现象？
4. 命令是否会安装软件、修改设备状态、覆盖文件或删除数据？

只有在四个问题都有答案时，命令输出才适合作为教学或实验记录。

---

## 启动一个可审计的 Shell

```bash
set -euo pipefail
pwd
whoami
hostname
printf 'shell=%s\n' "$SHELL"
```

说明：

- `set -e`：未处理的失败命令会停止脚本。
- `set -u`：暴露未定义变量。
- `set -o pipefail`：保留管道前段命令的失败状态。
- `pwd`、`whoami`、`hostname`：确认当前目录、用户和主机。
- `printf`：输出当前 shell 路径，避免把错误解释器当成目标环境。

---

## 确认当前路径与真实路径

```bash
cd ~/Documents/Ascend310/samples/case1
pwd
realpath .
readlink -f frontend/dist/index.html
```

说明：

- `pwd` 显示当前工作目录。
- `realpath .` 把相对路径解析成绝对路径。
- `readlink -f` 解析符号链接，适合在删除、同步或启动服务前确认实际目标。
- 路径决定操作范围，应先用这些命令确认位置，再执行后续命令。

---

## 列出文件并限定扫描范围

```bash
ls -la
find . -maxdepth 2 -type f -print | sort
```

说明：

- `ls -la` 显示隐藏文件、权限、所有者和文件大小。
- `find . -maxdepth 2` 只扫描当前目录下两层，避免误把整个家目录或挂载盘纳入操作。
- `-type f` 只列出普通文件，`-print | sort` 让输出按名称排序，便于核对。

---

## 文件属性与磁盘空间

```bash
stat -c '%A %U:%G %s %n' models/example.om
file models/example.om
du -sh models data reports
df -h .
```

说明：

- `stat` 同时查看权限、所有者、字节数和文件名。
- `file` 只给出格式线索，不能证明模型可被 ACL 加载。
- `du` 查看目录占用，`df` 查看文件系统剩余空间，两者含义不同。

---

## 创建、复制与移动文件

```bash
run_date="$(date --iso-8601=date)"
mkdir -p "reports/board/$run_date"
cp -a config.example.yaml config.yaml
mv reports/old.json reports/archive/old.json
```

说明：

- `date --iso-8601=date` 生成适合归档的日期。
- `mkdir -p` 自动创建多级目录，目录已存在时不报错。
- `cp -a` 尽量保留文件元数据；覆盖前应先检查目标是否已存在。
- `mv` 用于移动或重命名，移动报告前先确认目标归档目录。

---

## 权限修改与命令定位

```bash
chmod u+x scripts/run_service.sh
test -r models/example.om && echo 'model is readable'
command -v python
command -v atc || true
```

说明：

- `chmod u+x` 只给文件所有者增加执行权限，不要对整棵家目录使用宽泛的 `chmod -R 777`。
- `test -r` 检查文件是否可读，失败时流程应停止或给出明确提示。
- `command -v` 检查实际使用的可执行文件，能发现 PATH 中混入的错误 Python、ATC 或脚本版本。
- `|| true` 只在明确允许命令失败时使用。

---

## 文本、日志与检索

```bash
printf '%s\n' 'starting board smoke' | tee reports/board/smoke.log
grep -n 'ERROR\|WARN' reports/board/smoke.log
rg -n 'atc|acl|sha256|0\.0\.0\.0' samples/case*/README.md samples/case*/docs
head -n 40 reports/board/smoke.log
tail -n 80 reports/board/smoke.log
wc -l reports/board/smoke.log
```

说明：

- `tee` 把输出同时写入文件和终端。
- `grep` 适合对单份日志做过滤，`rg` 适合在仓库内检索命令和接口。
- `head`、`tail` 分别查看日志开头和结尾。
- `wc -l` 统计行数，用于判断日志是否完整写入。

---

## 日志片段、归档与压缩包检查

```bash
awk '{print NR ":" $0}' reports/board/smoke.log | tail -n 20
sed -n '1,80p' reports/board/smoke.log
run_date="$(date --iso-8601=date)"
tar -czf "reports/board/smoke-$run_date.tar.gz" reports/board/smoke.log
unzip -l artifact.zip
```

说明：

- `awk` 给日志行编号，`sed -n '1,80p'` 打印指定行区间。
- `tar -czf` 创建带日期后缀的压缩归档。
- `unzip -l` 只列出压缩包内容，不解压到当前目录。
- 归档前先确认内容和路径；压缩包不应包含照片、掌纹模板、数据库、模型二进制或密钥。

---

## 删除操作的边界：先解析绝对路径

```bash
target_dir="$(realpath -- "$PALMPRINT_ROOT/data/captures")"
case "$target_dir" in
  "$PALMPRINT_ROOT"/data/captures) ;;
  *) printf 'refuse to remove: %s\n' "$target_dir" >&2; exit 1 ;;
esac
find "$target_dir" -maxdepth 1 -type f -print
rm -f -- "$target_dir"/*.jpg
```

说明：

- `realpath` 先把路径解析为绝对路径，再进入删除逻辑。
- `case` 分支只在路径精确等于预期目录时继续，否则拒绝删除。
- `find` 先打印将要删除的文件列表，由操作者确认后再执行。
- `rm -f --` 只删除明确的文件；`rm` 不提供回收站。

---

## 删除边界：不应直接执行的命令模式

以下模式不应未经审查直接执行：

- `rm -rf` 指向家目录、仓库根目录、变量未验证的路径或正在运行的部署目录。
- `rsync --delete` 同步到未确认的远端目录，可能删除模型、数据和报告。
- `pkill python`、`killall` 或模糊 `kill`，可能终止其他案例和系统服务。
- `sudo pip install`、`sudo python`，会绕过 conda 并改变系统包所有权。
- 在开发机运行 CANN、ATC、PyACL、OM、摄像头或 NPU 命令，并把失败归因于代码。

安全顺序是：确认机器和环境，解析绝对路径，预览输入与目标，执行最小范围命令，保存原始输出。

---

## 第1课时课堂练习

1. 在隔离目录中执行 `pwd`、`realpath .`、`ls -la`、`stat`，保存一份路径与权限记录。
2. 用 `date --iso-8601=date` 创建带日期的报告目录，并复制一个配置文件。
3. 用 `tar` 归档一次日志，用 `unzip -l` 检查压缩包内容。
4. 写一个删除保护脚本：传入错误路径时必须拒绝，传入预期目录时才列出文件。
5. 对每条命令写出：运行在哪台机器、影响哪些路径、证明什么结论。

---

## 第2课时（45分钟）：文件、权限与软件

教学目标：

- 用最小权限原则修改文件权限
- 能区分 APT 系统包与 Conda 环境
- 能正确激活 conda 并安装 Python 依赖
- 能核对解释器、PYTHONPATH 和 LD_LIBRARY_PATH

45分钟安排：5分钟权限检查，15分钟 APT 与 Conda，15分钟 Python 环境，10分钟课堂练习。

---

## 权限查看与最小修改

```bash
ls -la
stat -c '%A %U:%G %s %n' scripts/run_service.sh
chmod u+x scripts/run_service.sh
```

说明：

- `ls -la` 的第一列是权限位，第三、四列是所有者和组。
- `stat -c` 用稳定格式输出权限、所有者和字节数。
- `chmod u+x` 只给文件所有者增加执行权限。
- 不要对整棵家目录执行宽泛的 `chmod -R 777`，否则会破坏数据目录和脚本的所有权边界。

---

## 设备文件与用户组

```bash
groups
ls -l /dev/video*
sudo usermod -aG video "$USER"
```

说明：

- `groups` 查看当前用户属于哪些组。
- `ls -l /dev/video*` 确认摄像头设备节点是否存在。
- `sudo usermod -aG video "$USER"` 把当前用户加入 `video` 组，重新登录后生效。
- 不要用 `sudo` 启动整个 Python 服务来绕过权限，否则会改变数据目录所有权和环境解析。

---

## APT 软件包操作

```bash
sudo apt-get update
sudo apt-get install -y libsigrok-dev sigrok-cli gcc pkg-config libfftw3-single3 rtl-sdr
```

说明：

- `sudo apt-get update` 更新软件源索引。
- `sudo apt-get install -y` 安装系统包；`-y` 会跳过交互确认，只应在明确知道安装列表时使用。
- 软件包操作改变系统状态，应在维护窗口中由管理员执行，并记录 Ubuntu 版本和安装结果。
- 在已经准备好的板端环境中，优先确认包是否存在，不要为了通过示例而重复安装或升级 CANN 相关包。

---

## 查询软件包与可执行文件

```bash
dpkg -s python3-dev python3-pip 2>/dev/null | grep -E '^(Package|Status):'
command -v python
```

说明：

- `dpkg -s` 查询 Debian/Ubuntu 软件包状态，`2>/dev/null` 隐藏未安装时的错误输出。
- `grep -E '^(Package|Status):'` 只保留包名和状态两行。
- `command -v python` 确认实际使用的解释器路径，避免 pip、python 来自不同环境。

---

## 加载 Conda 并激活环境

```bash
load_conda() {
  if [[ $- == *u* ]]; then
    set +u
    source /usr/local/miniconda3/etc/profile.d/conda.sh
    set -u
  else
    source /usr/local/miniconda3/etc/profile.d/conda.sh
  fi
}
load_conda
conda activate base
python --version
python -c 'import sys; print(sys.executable)'
```

说明：

- 先加载 Conda shell 函数，再激活目标环境；两步必须在同一个 shell 中完成。
- 若此前开启了 `set -u`，函数只在加载 Conda 时暂时关闭它，再恢复原状态。
- `python --version` 和 `sys.executable` 用于确认解释器版本和实际路径。

---

## 按项目需要创建独立环境

```bash
conda create -n case9-acl-om python=3.9
conda activate case9-acl-om
```

说明：

- `conda create -n` 创建独立环境，`python=3.9` 指定 Python 版本。
- 当前板端主线通常复用已验证的环境，不应无计划地创建新环境。
- 只有当项目门禁明确要求独立环境时，才执行该命令；不要用其他版本的同名环境替代。

---

## 使用当前解释器安装 Python 依赖

```bash
python -m pip install -r requirements.txt
python -m pip list
```

说明：

- `python -m pip` 能保证 pip 对应当前解释器。
- `pip`、`pip3` 是历史命令，在教程中使用时必须先用 `command -v` 核对。
- `pip list` 查看当前环境已安装包，用于核对版本和依赖是否完整。

---

## Python 路径变量

```bash
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/lib64:${LD_LIBRARY_PATH:-}"
python -c 'import sys; print("\n".join(sys.path))'
```

说明：

- `PYTHONNOUSERSITE=1` 避免用户目录中的包遮蔽板端环境。
- `PYTHONPATH` 和 `LD_LIBRARY_PATH` 只应加入已经确认的项目或 CANN 目录。
- `sys.path` 输出实际导入搜索路径，可核对是否混入了错误版本。
- 持久化修改环境变量应经过部署评审，不要在脚本中随意追加路径。

---

## 第2课时课堂练习

1. 查看一个脚本的权限位，只给所有者增加执行权限，并记录修改前后差异。
2. 查询 `python3-dev`、`python3-pip` 是否已安装，不执行无计划的安装。
3. 在同一终端加载 Conda、激活 `base`，打印 `python --version` 和 `sys.executable`。
4. 使用 `python -m pip list` 核对环境，并说明 `PYTHONNOUSERSITE` 的作用。
5. 写出一段 45 秒口述：APT 改变什么、Conda 改变什么、PATH 改变什么。

---

## 第3课时（45分钟）：CANN、服务与日志

教学目标：

- 在板端同一 shell 中加载 CANN
- 用 `npu-smi info` 检查设备并读取 soc_version
- 理解 ATC、OM 与文件摘要验证
- 能查看服务、端口、日志并有序停止进程

45分钟安排：5分钟 CANN 加载，15分钟 npu-smi 与 ATC，15分钟服务与日志，10分钟课堂练习。

---

## 加载 CANN 环境

```bash
load_cann() {
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  if [[ $- == *u* ]]; then
    set +u
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    set -u
  else
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
  fi
}
load_cann
command -v atc
python -c 'import acl; print("PyACL import: ok")'
```

说明：

- `set_env.sh` 设置 ACL、运行时和工具链的库路径。
- 只在另一个终端执行它，不能保证启动服务的 shell 继承环境。
- `command -v atc` 确认 ATC 在 PATH 中。
- 若 `import acl` 失败，应记录解释器、CANN 路径和完整错误，不添加 CPU 或随机输出回退。

---

## 板端系统状态基线

```bash
uname -a
cat /etc/os-release
hostname
date --iso-8601=seconds
free -h
swapon --show
df -h
uptime
ps -p 1 -o pid,comm,args
npu-smi info
```

说明：

- `uname`、`os-release`、`hostname` 记录操作系统和主机信息。
- `free -h`、`swapon --show`、`df -h` 检查内存、交换空间和磁盘。
- `uptime`、`ps -p 1` 查看运行时长和系统首个进程。
- 这些命令记录的是系统基线，不能单独证明 NPU 推理或模型精度。

---

## npu-smi：NPU 设备状态

```bash
npu-smi info
```

示例输出：

```text
npu-smi 23.0.0  Version: 23.0.0
+-----------+--------+--------+-------+-------------------+
| NPU Name  | Health | Power  | Temp  | Hugepages-Usage   |
| Chip      | Bus-Id | AICore | Mem   |                   |
+===========+========+========+=======+===================+
| 0 310B4   | Alarm  | 0.0W   | 58C   | 15 / 15           |
| 0 0       | NA     | 0%     | 2500/7545MB             |
+-----------+--------+--------+-------+-------------------+
```

说明：

- 查询结果的 `Name` 值为 `310B4` 时，ATC 的 `--soc_version` 应配置为 `Ascend310B4`。
- `Health: Alarm` 应作为诊断背景保存，但不能单独判定 ATC、ACL、性能或精度失败。
- ACL 初始化失败、设备不存在、ATC 非零退出、段错误、资源泄漏等必须单独报告。

---

## chapter5 快速诊断脚本

```bash
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
python samples/chapter5/check_cann.py
```

预期输出：

```text
ACL init OK  soc=Ascend310B4  cann=8.3.RC1
```

说明：

- 脚本先执行 `acl.init()`，再绑定设备 0、创建上下文并查询 CANN 版本。
- 最后检查 DVPP 的 VENC 相关函数是否可访问。
- 该脚本验证 CANN 环境、NPU 设备和 DVPP 驱动是否正常，是板端运行前的快速诊断。

---

## ATC：ONNX 转 OM

```bash
export TE_PARALLEL_COMPILER=1
export MAX_COMPILE_CORE_NUMBER=1
atc \
  --model=models/resnet18_scene.onnx \
  --framework=5 \
  --output=models/resnet18_scene \
  --soc_version=Ascend310B4 \
  --input_format=NCHW \
  --input_shape=input:1,3,224,224
```

说明：

- `--framework=5` 表示输入是 ONNX。
- `--soc_version=Ascend310B4` 必须与板端设备匹配。
- `--input_format=NCHW` 和 `--input_shape` 必须来自该模型的合同。
- `TE_PARALLEL_COMPILER`、`MAX_COMPILE_CORE_NUMBER` 用于限制编译并行度。
- 更多参数可通过 `atc --help` 查询，不能从另一个模型直接复制参数。

---

## ATC 关键参数

| 参数 | 作用 | 注意事项 |
| --- | --- | --- |
| `--framework` | 指定原始模型框架 | 0=Caffe，1=MindSpore，3=TensorFlow，5=ONNX |
| `--input_shape` | 定义静态输入 Shape | 按 `"name:n,c,h,w"` 写法，字符串需加引号 |
| `--input_format` | 声明输入数据排布 | 如 `NCHW`、`NHWC`，需与导出和输入保持一致 |
| `--soc_version` | 选择目标芯片 | 例：`Ascend310B4`，可通过 `npu-smi info` 查询 |
| `--precision_mode` | 控制混合精度策略 | 常用值：`force_fp16`、`allow_mix_precision` 等 |
| `--op_select_implmode` | 算子实现优先级 | 支持 `high_performance`、`high_precision` 等 |
| `--insert_op_conf` | 下沉 AIPP/自定义算子 | 指定 JSON/YAML，支持色域转换、归一化 |
| `--output_type` | 重指定输出 dtype | 可全局或按节点设置，便于后处理 |

---

## ATC 成功输出与失败边界

```text
ATC start working now, please wait for a moment.
...
ATC run success, welcome to the next use.
```

说明：

- 上面的输出只表示 ATC 转换流程结束，不表示数值精度或性能已经达标。
- 若出现 `BrokenPipeError`，通常是编译阶段内存不足导致进程被系统终止。
- 可先限制编译资源：`export TE_PARALLEL_COMPILER=1`、`export MAX_COMPILE_CORE_NUMBER=1`。
- 转换失败时保存失败命令和模型合同，不生成伪 OM，也不擅自替换模型。

---

## 编译内存不足时的临时处理

```bash
dd if=/dev/zero of=/swapfile bs=1M count=8192
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
```

说明：

- `dd` 创建交换文件，`chmod 600` 限制文件权限。
- `mkswap` 初始化交换文件，`swapon` 启用它。
- 如果设备使用 TF 卡作为主要存储介质，不建议频繁使用 Swap，闪存会因高频读写加速磨损。
- 仅建议在必要时临时开启，或优先降低编译并行度。

---

## OM 文件与摘要验证

```bash
stat -c '%s %n' models/*.om
sha256sum models/example.om
bundle_root=/path/to/repro-bundle
cd "$bundle_root"
sha256sum -c SHA256SUMS.txt
```

说明：

- `stat -c` 查看 OM 文件大小和名称。
- `sha256sum` 计算单个文件的 SHA-256 摘要。
- `sha256sum -c SHA256SUMS.txt` 只在生成该清单的 bundle 根目录中执行。
- 摘要检查不能替代 OM 加载和数值烟测；模型、ONNX、OM、数据集和真实报告默认不提交到 Git。

---

## 启动服务与网络边界

```bash
python app.py --host 127.0.0.1 --port 5000
python -m palmprint_workbench.api --host 127.0.0.1 --port 7860
```

说明：

- 示例服务都绑定明确的本地地址和端口，优先监听回环地址 `127.0.0.1`。
- `0.0.0.0` 会把服务暴露到所有网卡，只有在可信实验网络中才由操作者显式指定。
- `--share` 可能创建公网隧道，只适合短时、无敏感数据的演示，不应作为生产启动参数。
- 启动前先确认端口是否被占用，启动后应查看日志确认监听成功。

---

## 进程、端口与日志

```bash
ps -ef | grep -E 'python|uvicorn' | grep -v grep
ss -ltnp | grep -E ':5000|:7860|:8080'
tail -f logs/service.log
```

说明：

- `ps -ef | grep -E` 查看匹配的服务进程，`grep -v grep` 排除检索命令自身。
- `ss -ltnp` 显示 TCP 监听端口和对应进程，可确认端口是否被目标服务占用。
- `tail -f` 持续跟踪日志，适合启动服务时观察错误。
- 不要使用 `pkill python`、`killall python` 或按模糊名称批量终止进程。

---

## 有序停止服务

```bash
service_pid=12345
case "$service_pid" in
  ''|*[!0-9]*) printf 'invalid PID\n' >&2; exit 2 ;;
esac
test "$service_pid" -gt 1
ps -fp "$service_pid"
readlink -f "/proc/$service_pid/cwd"
kill -TERM "$service_pid"
```

说明：

- 先校验 PID 是合法正整数，再查看进程和工作目录，确认它就是目标服务。
- 确认后才执行 `kill -TERM`，先给进程正常终止的机会。
- 停止后等待端口释放；不要对多个实验同时使用宽泛的 `kill`。
- 涉及 systemd 的服务应先使用 `systemctl status` 判断服务关系。

---

## 系统日志与诊断

```bash
systemctl status ssh sshd systemd-logind --no-pager
journalctl -b -u ssh --no-pager | tail -n 80
dmesg -T | tail -n 200
```

说明：

- `systemctl status` 查看 systemd 单元状态，`--no-pager` 避免分页等待。
- `journalctl -b -u ssh` 查看本次启动的 SSH 日志，`tail` 只看最后 80 行。
- `dmesg -T` 查看内核日志并显示可读时间。
- 若命令只用于定位故障，应保存输出和时间戳，不要把系统日志全文复制到公开仓库。

---

## HTTP 检查与证据边界

```bash
curl --fail --silent --show-error http://127.0.0.1:5000/api/health
curl --fail --silent http://127.0.0.1:7860/api/bootstrap
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/video_feed
```

说明：

- `curl` 的 `--fail` 在 HTTP 错误时让命令失败，`--silent --show-error` 减少干扰但保留错误。
- `-o /dev/null -w '%{http_code}\n'` 只输出 HTTP 状态码。
- HTTP 200 只证明路由返回了预期状态，不能证明 NPU 推理、任务精度或性能已经达标。
- 健康检查、ACL 烟测、数值一致性和性能测量必须分别记录。

---

## 常见故障速查

| 分类 | 现象 | 先执行 | 再判断 |
| --- | --- | --- | --- |
| ATC | `E19001: Op Not Supported` | 确认 CANN 版本 + onnxsim 简化 | 升级、替换结构或自定义算子 |
| ACL | `aclmdlLoadFromFile failed` | 校验文件 hash 和权限 | 修正权限或重新生成 OM |
| Runtime | OOM / alloc 失败 | 统计输入分布 | 降 batch、分桶或复用内存 |
| 性能 | H2D 高占比 >25% | 检查多次小拷贝 | 合并缓冲、AIPP 下沉或批量化 |
| 精度 | Top1 下降 >1% | 对比 ONNX 输出 | 统一 Normalize 与 Layout |
| 安全 | 日志泄露敏感路径 | `grep` 审计 | 结构化日志脱敏 |

---

## 课堂任务

1. 完成路径、文件、权限、日志和归档练习，保存每次命令的机器、用户、目录和输出。
2. 写出常用命令对照表：路径、权限、软件、CANN、进程、日志各至少 5 条。
3. 在板端同一 shell 中加载 CANN，执行 `command -v atc` 和 `import acl` 检查。
4. 运行 `npu-smi info`，记录 `Name`、`Health`、内存和 soc_version 判断过程。
5. 查看服务进程、端口和日志，确认 PID 后执行有序停止。
6. 对每条 ATC、HTTP 或设备检查命令，标注它证明的是语法、路由、转换、数值还是硬件现象。

---

## 交付物

- `linux/appendix2/linux-commands.md`
- `linux/appendix2/safety-rules.md`
- `linux/appendix2/cann-npu-check.md`
- `linux/appendix2/service-log-check.md`

交付物中应包含原始命令、执行位置、日期、关键输出和结论；不得包含真实 token、密码、个人图像或未经审查的敏感路径。

---

## 验收标准

- 能回答“命令四问”，并说明每条命令改什么、影响什么、验证什么。
- 能独立完成路径解析、文件复制、权限修改、日志归档和删除保护。
- 能区分 APT、Conda、python -m pip 与 PATH 变量的作用。
- 能在板端同一 shell 中加载 CANN，运行 `npu-smi info` 并正确读取 soc_version。
- 能区分 HTTP 状态、ATC 转换、ACL 烟测、数值一致性和性能结论。
- 能确认目标 PID 后有序停止服务，不使用 `pkill python` 或宽泛删除。
